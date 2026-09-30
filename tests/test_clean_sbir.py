"""Tests for clean_sbir.py - raw SBIR awards to one clean row per award.

Every case runs against a small made-up CSV written to a temp folder, not
the real 385MB download, so the whole suite takes about a second. "Today"
is fixed rather than read from the clock, so the active-award boundary can
be tested exactly.

Several of these pin down a rule that was argued over, or a bug that was
actually hit - those say so in their docstring.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from clean_sbir import OUT_COLUMNS, clean

TODAY = date(2026, 9, 29)

# Every column clean_sbir reads, plus personal-data columns it must NOT.
BLANK = {
    "Company": "", "UEI": "", "Agency": "Department of Energy",
    "Award Title": "A title", "Abstract": "An abstract.",
    "Award Amount": "100000", "Award Year": "2025",
    "Contract End Date": "", "Number Employees": "10",
    "Company Website": "", "Address1": "1 Main St", "Address2": "",
    "City": "Storrs", "State": "Connecticut", "Zip": "06269",
    "RI Name": "",
    "Contact Name": "Jane Contact", "Contact Email": "jane@agency.gov",
    "PI Name": "Pat Investigator", "PI Email": "pat@company.com",
    "PI Phone": "5551234567",
}


def award(**over) -> dict:
    row = dict(BLANK)
    row.update(over)
    return row


def run(rows, today=TODAY):
    """Write rows to a temp CSV, clean it, return (awards, company_key)."""
    d = Path(tempfile.mkdtemp(prefix="nexusai_sbir_"))
    path = d / "award_data.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return clean(path, today)


def one(**over):
    """Clean a single award and return its row."""
    out, _ = run([award(Company="Acme Inc", **over)])
    return out.iloc[0]


# ----------------------------------------------------------------------

class TestValueCleaning(unittest.TestCase):

    def test_award_amount_becomes_a_number(self):
        self.assertEqual(one(**{"Award Amount": "608556.0000"})["award_amount"],
                         608556.0)

    def test_junk_amount_becomes_blank(self):
        self.assertTrue(pd.isna(one(**{"Award Amount": "n/a"})["award_amount"]))

    def test_end_date_becomes_a_date(self):
        self.assertEqual(one(**{"Contract End Date": "2027-12-19"})["contract_end_date"],
                         date(2027, 12, 19))

    def test_bad_or_missing_end_date_becomes_blank(self):
        for bad in ("", "not a date"):
            self.assertTrue(pd.isna(one(**{"Contract End Date": bad})["contract_end_date"]))

    def test_zip_plus_four_cut_to_five(self):
        self.assertEqual(one(Zip="01821-3934")["zip"], "01821")

    def test_zip_leading_zero_is_kept(self):
        """New England zips start with 0; read as a number they lose it."""
        self.assertEqual(one(Zip="06269")["zip"], "06269")

    def test_zip_that_is_not_five_digits_becomes_blank(self):
        for bad in ("ABCDE", "123", ""):
            self.assertEqual(one(Zip=bad)["zip"], "")

    def test_address_lines_are_joined(self):
        self.assertEqual(one(Address1="45 Manning Rd", Address2="Ste 102")["address"],
                         "45 Manning Rd Ste 102")

    def test_blank_second_line_leaves_no_trailing_space(self):
        self.assertEqual(one(Address1="45 Manning Rd", Address2="")["address"],
                         "45 Manning Rd")

    def test_line_breaks_in_abstract_are_flattened(self):
        self.assertEqual(one(Abstract="First line.\nSecond\r\n  line.")["abstract"],
                         "First line. Second line.")

    def test_award_year_is_a_whole_number_in_the_file(self):
        """Written as 2025, not 2025.0 - which is what a float column gives."""
        out, _ = run([award(Company="Acme Inc", **{"Award Year": "2025"})])
        with tempfile.NamedTemporaryFile("w+", suffix=".csv", delete=False) as fh:
            out.to_csv(fh.name, index=False)
            text = Path(fh.name).read_text()
        self.assertIn(",2025,", text)
        self.assertNotIn("2025.0", text)


class TestResearchInstitution(unittest.TestCase):

    def test_placeholders_become_blank(self):
        """'Stub' alone is on 15,222 real rows."""
        for placeholder in ("Stub", "stub", "N/A", "none", "-", ""):
            self.assertEqual(one(**{"RI Name": placeholder})["ri_name"], "",
                             placeholder)

    def test_real_names_are_kept_exactly_as_written(self):
        """An earlier version recased names and turned UNH into 'Unh' and
        MIT into 'Mit'. Abbreviations must survive untouched."""
        for name in ("UNH", "MIT", "University of Connecticut",
                     "CLEMSON UNIV.", "Johns Hopkins University Applied Physics Lab"):
            self.assertEqual(one(**{"RI Name": name})["ri_name"], name)


class TestActiveToday(unittest.TestCase):

    def test_ending_today_is_active(self):
        """The boundary. Nothing can be charged AFTER the end date, so on
        the end date itself the award is still live."""
        self.assertTrue(one(**{"Contract End Date": "2026-09-29"})["award_active_today"])

    def test_ended_yesterday_is_not_active(self):
        self.assertFalse(one(**{"Contract End Date": "2026-09-28"})["award_active_today"])

    def test_future_end_date_is_active(self):
        self.assertTrue(one(**{"Contract End Date": "2028-01-01"})["award_active_today"])

    def test_no_end_date_is_not_active(self):
        """Half the file - mostly pre-2015 awards - has no end date.
        Unknown must not read as still running."""
        self.assertFalse(one(**{"Contract End Date": ""})["award_active_today"])

    def test_active_depends_on_the_date_it_is_run(self):
        """The same award is active on one day and not the next - which is
        why the file must be rebuilt after each download."""
        rows = [award(Company="Acme Inc", **{"Contract End Date": "2026-12-31"})]
        before, _ = run(rows, today=date(2026, 12, 31))
        after, _ = run(rows, today=date(2027, 1, 1))
        self.assertTrue(before.iloc[0]["award_active_today"])
        self.assertFalse(after.iloc[0]["award_active_today"])


class TestUEI(unittest.TestCase):

    def test_old_award_borrows_uei_from_the_same_company(self):
        """UEI replaced DUNS in 2022, so older awards have none. Without
        borrowing, one company's old and new awards split in two."""
        out, _ = run([
            award(Company="Acme, Inc.", UEI="ABC123DEF456", **{"Award Year": "2024"}),
            award(Company="ACME INC", UEI="", **{"Award Year": "2012"}),
        ])
        self.assertEqual(set(out["uei"]), {"ABC123DEF456"})

    def test_a_name_shared_by_two_companies_lends_to_neither(self):
        """ADELPHI TECHNOLOGY exists in Kentucky AND in California, with
        different UEIs. An old award under that name could be either."""
        out, _ = run([
            award(Company="Adelphi Technology, LLC", UEI="EPK7KZF7WW15", State="Kentucky"),
            award(Company="Adelphi Technology Inc", UEI="FE2HY976FJW7", State="California"),
            award(Company="ADELPHI TECHNOLOGY", UEI="", **{"Award Year": "2005"}),
        ])
        old = out[out["award_year"] == 2005].iloc[0]
        self.assertEqual(old["uei"], "")

    def test_lowercase_uei_is_uppercased(self):
        """The real file has TA8TQ6JDRQN5 and a lowercase variant of it."""
        self.assertEqual(one(UEI="ta8tq6jdrqns")["uei"], "TA8TQ6JDRQNS")

    def test_a_company_that_never_had_a_uei_stays_blank(self):
        self.assertEqual(one(UEI="")["uei"], "")

    def test_borrowing_is_by_name_not_by_accident(self):
        """A different company must not pick up someone else's UEI."""
        out, _ = run([
            award(Company="Acme Inc", UEI="ABC123DEF456"),
            award(Company="Zenith Labs", UEI=""),
        ])
        zenith = out[out["company"] == "Zenith Labs"].iloc[0]
        self.assertEqual(zenith["uei"], "")


class TestOutputFile(unittest.TestCase):

    def test_exactly_the_expected_columns_in_order(self):
        out, _ = run([award(Company="Acme Inc")])
        self.assertEqual(list(out.columns), OUT_COLUMNS)
        self.assertEqual(len(OUT_COLUMNS), 16)

    def test_personal_data_never_appears(self):
        """The raw file carries contact and PI names, emails and phones.
        None of it is needed, so none of it may travel further."""
        out, _ = run([award(Company="Acme Inc")])
        for col in ("Contact Name", "Contact Email", "PI Name", "PI Email",
                    "PI Phone"):
            self.assertNotIn(col, out.columns)
        blob = out.astype(str).to_string()
        for value in ("jane@agency.gov", "pat@company.com", "Jane Contact",
                      "Pat Investigator", "5551234567"):
            self.assertNotIn(value, blob)

    def test_rows_without_a_company_are_dropped(self):
        out, _ = run([award(Company="Acme Inc"), award(Company=""),
                      award(Company="   ")])
        self.assertEqual(len(out), 1)

    def test_every_other_row_is_kept(self):
        rows = [award(Company=f"Company {i}") for i in range(12)]
        out, _ = run(rows)
        self.assertEqual(len(out), 12)

    def test_a_companys_awards_sit_together_newest_first(self):
        out, _ = run([
            award(Company="Acme Inc", UEI="AAA", **{"Contract End Date": "2025-01-01"}),
            award(Company="Zenith Labs", UEI="ZZZ", **{"Contract End Date": "2027-01-01"}),
            award(Company="Acme Inc", UEI="AAA", **{"Contract End Date": "2028-06-30"}),
            award(Company="Acme Inc", UEI="AAA", **{"Contract End Date": ""}),
        ])
        acme = out[out["uei"] == "AAA"]
        # contiguous
        self.assertEqual(list(acme.index), list(range(acme.index[0],
                                                      acme.index[0] + 3)))
        # newest end date first, undated last
        self.assertEqual(acme["contract_end_date"].iloc[0], date(2028, 6, 30))
        self.assertEqual(acme["contract_end_date"].iloc[1], date(2025, 1, 1))
        self.assertTrue(pd.isna(acme["contract_end_date"].iloc[2]))

    def test_newest_award_year_first_even_if_older_contract_runs_longer(self):
        """QA finding: sorting by end date put a 2016 award with a long
        contract ahead of a 2024 one, so 'newest' titles and the headcount
        came from the older award."""
        out, _ = run([
            award(Company="Acme Inc", UEI="AAA", **{"Award Year": "2016",
                  "Contract End Date": "2027-01-01", "Number Employees": "5"}),
            award(Company="Acme Inc", UEI="AAA", **{"Award Year": "2024",
                  "Contract End Date": "2025-06-30", "Number Employees": "40"}),
        ])
        self.assertEqual(list(out["award_year"]), [2024, 2016])
        self.assertEqual(out["number_employees"].iloc[0], 40)

    def test_placeholder_title_and_abstract_blanked(self):
        for text in ("Not Available", "not available", "Redacted", "Redacted.", "TBD"):
            r = one(**{"Award Title": text, "Abstract": text})
            self.assertEqual((r["award_title"], r["abstract"]), ("", ""), text)
        self.assertEqual(one(**{"Award Title": "Not Available Anywhere Else"})["award_title"],
                         "Not Available Anywhere Else")


if __name__ == "__main__":
    unittest.main()
