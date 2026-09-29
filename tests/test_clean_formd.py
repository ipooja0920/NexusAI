"""Tests for clean_formd.py - SEC Form D filings to one clean row per filing.

Each test writes a few made-up filings into temp quarter folders laid out
exactly like SEC's download (ISSUERS.tsv, OFFERING.tsv, FORMDSUBMISSION.tsv)
and cleans them. No real data, no network; the suite runs in about a second.

Several cases pin down a rule that was argued over or a bug that was hit -
those say so in their docstring.
"""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from clean_formd import OUT_COLUMNS, clean

ISS_COLS = ["ACCESSIONNUMBER", "IS_PRIMARYISSUER_FLAG", "ISSUER_SEQ_KEY", "CIK",
            "ENTITYNAME", "CITY", "STATEORCOUNTRY", "ZIPCODE",
            "YEAROFINC_VALUE_ENTERED"]
OFF_COLS = ["ACCESSIONNUMBER", "INDUSTRYGROUPTYPE", "REVENUERANGE",
            "ISAMENDMENT", "PREVIOUSACCESSIONNUMBER", "SALE_DATE", "YETTOOCCUR",
            "ISEQUITYTYPE", "ISDEBTTYPE", "ISOPTIONTOACQUIRETYPE", "ISOTHERTYPE",
            "DESCRIPTIONOFOTHERTYPE", "ISBUSINESSCOMBINATIONTRANS",
            "TOTALOFFERINGAMOUNT", "TOTALAMOUNTSOLD", "TOTALNUMBERALREADYINVESTED"]
SUB_COLS = ["ACCESSIONNUMBER", "FILING_DATE", "SUBMISSIONTYPE"]


def filing(acc, company="Acme Bio, Inc.", **kw) -> dict:
    """One filing. Defaults describe an ordinary first filing (a D)."""
    f = dict(acc=acc, company=company, cik="0001234567", filed="15-MAR-2025",
             prev="", sale="2025-03-01", sold="1000000", target="5000000",
             industry="Biotechnology", yto="", biz="false", equity="true",
             debt="", other="", other_desc="", option="", investors="7",
             revenue="Decline to Disclose", year="2021", city="Storrs",
             state="CT", zip="06269", co_issuers=())
    f.update(kw)
    return f


def run(*quarters):
    """Write each list of filings as one quarter folder, then clean."""
    root = Path(tempfile.mkdtemp(prefix="nexusai_formd_"))
    for qi, filings in enumerate(quarters):
        q = root / f"2025Q{qi + 1}_d"
        q.mkdir()
        iss, off, sub = [], [], []
        for f in filings:
            # co-issuers written BEFORE the primary, so a test cannot pass
            # merely because the primary happens to come first in the file
            for j, co in enumerate(f["co_issuers"]):
                iss.append([f["acc"], "NO", str(102 + j), "0009999999", co,
                            "Boston", "MA", "02110", ""])
            iss.append([f["acc"], "YES", "101", f["cik"], f["company"], f["city"],
                        f["state"], f["zip"], f["year"]])
            off.append([f["acc"], f["industry"], f["revenue"],
                        "true" if f["prev"] else "false", f["prev"], f["sale"],
                        f["yto"], f["equity"], f["debt"], f["option"], f["other"],
                        f["other_desc"], f["biz"], f["target"], f["sold"],
                        f["investors"]])
            sub.append([f["acc"], f["filed"], "D/A" if f["prev"] else "D"])
        for name, cols, rows in (("ISSUERS", ISS_COLS, iss),
                                 ("OFFERING", OFF_COLS, off),
                                 ("FORMDSUBMISSION", SUB_COLS, sub)):
            pd.DataFrame(rows, columns=cols).to_csv(q / f"{name}.tsv",
                                                     sep="\t", index=False)
        # a table of people that must never be read
        pd.DataFrame([[filings[0]["acc"], "Jane", "Q", "Executive", "Officer"]],
                     columns=["ACCESSIONNUMBER", "FIRSTNAME", "MIDDLENAME",
                              "LASTNAME", "RELATIONSHIP_1"]) \
          .to_csv(q / "RELATEDPERSONS.tsv", sep="\t", index=False)
    return clean(root)


def row(out, acc):
    return out[out["accession_number"] == acc].iloc[0]


# ----------------------------------------------------------------------

class TestWhichRowsAreKept(unittest.TestCase):

    def test_pooled_investment_funds_are_removed(self):
        """120,216 of 196,602 real filings are funds raising from their own
        investors - not operating companies."""
        out = run([filing("A1"),
                   filing("F1", company="Big VC Fund IV, L.P.",
                          industry="Pooled Investment Fund")])
        self.assertEqual(list(out["accession_number"]), ["A1"])

    def test_only_the_primary_issuer_gets_a_row(self):
        """A filing can list extra companies. Each would otherwise be
        credited the full amount of a joint raise."""
        out = run([filing("A1", co_issuers=("Acme Holdings LLC", "Acme Sub Inc"))])
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["company"], "Acme Bio, Inc.")

    def test_a_filing_in_two_quarter_folders_appears_once(self):
        out = run([filing("A1")], [filing("A1")])
        self.assertEqual(len(out), 1)

    def test_people_are_never_read(self):
        """RELATEDPERSONS holds executives' names; it is never opened."""
        out = run([filing("A1")])
        blob = out.astype(str).to_string()
        for name in ("Jane", "Officer", "Executive"):
            self.assertNotIn(name, blob)

    def test_blank_company_names_are_dropped(self):
        out = run([filing("A1"), filing("A2", company="  ")])
        self.assertEqual(list(out["accession_number"]), ["A1"])

    def test_exactly_the_expected_columns_in_order(self):
        out = run([filing("A1")])
        self.assertEqual(list(out.columns), OUT_COLUMNS)

    def test_money_columns_read_target_total_then_new(self):
        i = OUT_COLUMNS.index("offering_target")
        self.assertEqual(OUT_COLUMNS[i:i + 3], ["offering_target",
                         "total_raised_to_date", "raised_since_last_filing"])


class TestOneFundraiseFiledSeveralTimes(unittest.TestCase):
    """RevBio's real fundraise: one D and two yearly updates (D/A), each
    reporting the running total, not new money."""

    def setUp(self):
        self.out = run([
            filing("R1", company="REVBIO, INC.", filed="18-MAY-2023",
                   sale="2023-04-24", sold="187000"),
            filing("R2", company="REVBIO, INC.", filed="10-JUL-2024",
                   sale="2023-04-24", sold="6908457", prev="R1"),
            filing("R3", company="REVBIO, INC.", filed="10-JUL-2025",
                   sale="2023-04-24", sold="10023457", prev="R2"),
        ])

    def test_all_three_share_one_fundraise(self):
        self.assertEqual(self.out["offering_id"].nunique(), 1)
        self.assertEqual(self.out["offering_id"].iloc[0], "R1")

    def test_numbered_in_order(self):
        self.assertEqual([row(self.out, a)["filing_number"] for a in ("R1", "R2", "R3")],
                         [1, 2, 3])

    def test_only_the_last_is_latest(self):
        self.assertEqual([bool(row(self.out, a)["is_latest_filing"]) for a in ("R1", "R2", "R3")],
                         [False, False, True])

    def test_raised_since_last_filing_is_the_difference(self):
        self.assertEqual([row(self.out, a)["raised_since_last_filing"] for a in ("R1", "R2", "R3")],
                         [187000, 6721457, 3115000])

    def test_new_money_adds_up_to_the_final_total(self):
        self.assertEqual(self.out["raised_since_last_filing"].sum(), 10023457)

    def test_the_link_holds_across_quarter_folders(self):
        """An update and the filing it amends often sit in different quarters."""
        out = run([filing("R1", sold="100")], [filing("R2", sold="300", prev="R1",
                                                      filed="10-JUL-2025")])
        self.assertEqual(out["offering_id"].nunique(), 1)
        self.assertEqual(row(out, "R2")["raised_since_last_filing"], 200)


class TestSeparateFundraises(unittest.TestCase):

    def test_same_day_first_filings_are_separate_fundraises(self):
        """Agilix filed three unlinked D's on one day for raises begun in
        2016, 2021 and 2023. Same company, three fundraises."""
        out = run([filing("G1", sale="2016-12-09", sold="435000", filed="15-JUL-2024"),
                   filing("G2", sale="2021-09-07", sold="2460000", filed="15-JUL-2024"),
                   filing("G3", sale="2023-05-14", sold="805000", filed="15-JUL-2024")])
        self.assertEqual(out["offering_id"].nunique(), 3)
        self.assertTrue((out["filing_number"] == 1).all())
        self.assertTrue(out["is_latest_filing"].all())

    def test_same_day_original_and_update_numbered_by_link_not_id(self):
        """A D and its D/A filed the same day, where the update's SEC ID
        sorts FIRST. A date-then-ID sort once numbered the update 1 and the
        original 2 - seven real fundraises."""
        out = run([filing("0002-ORIGINAL", sold="500", filed="12-SEP-2025"),
                   filing("0001-UPDATE", sold="500", filed="12-SEP-2025",
                          prev="0002-ORIGINAL")])
        self.assertEqual(row(out, "0002-ORIGINAL")["filing_number"], 1)
        self.assertEqual(row(out, "0001-UPDATE")["filing_number"], 2)
        self.assertTrue(row(out, "0001-UPDATE")["is_latest_filing"])


class TestRaisedSinceLastFiling(unittest.TestCase):

    def test_first_filing_counts_everything_so_far(self):
        self.assertEqual(row(run([filing("A1", sold="750000")]), "A1")
                         ["raised_since_last_filing"], 750000)

    def test_update_of_a_filing_before_our_data_is_unknown(self):
        """The previous filing predates 2023, so there is nothing to subtract
        from. Unknown must stay blank, not become the whole total."""
        out = run([filing("A2", sold="900000", prev="OLD-2021-FILING")])
        self.assertTrue(pd.isna(row(out, "A2")["raised_since_last_filing"]))

    def test_a_downward_correction_is_zero_not_negative(self):
        """About 3% of real updates report a LOWER total - a correction,
        not money going back."""
        out = run([filing("A1", sold="500000"),
                   filing("A2", sold="400000", prev="A1", filed="15-MAR-2026")])
        self.assertEqual(row(out, "A2")["raised_since_last_filing"], 0)

    def test_impossible_zero_after_sales_began_makes_the_next_amount_unknown(self):
        """ROM Technologies reported $0 in 2025 on a raise selling since
        2014, then $448m in 2026. Subtracting from that $0 credited 12 years
        of money to 2026 - 236 filings, $4.7bn."""
        out = run([filing("M1", sale="2014-09-15", sold="0", filed="02-SEP-2025"),
                   filing("M2", sale="2014-09-15", sold="448056161",
                          filed="19-FEB-2026", prev="M1")])
        self.assertTrue(pd.isna(row(out, "M2")["raised_since_last_filing"]))

    def test_genuine_zero_before_any_sale_keeps_the_next_amount(self):
        """Filed before selling anything ($0 is true), first sale came
        after. The next filing's money really is new."""
        out = run([filing("Z1", sale="", yto="true", sold="0", filed="01-MAR-2025"),
                   filing("Z2", sale="2025-06-01", sold="2000000",
                          filed="01-SEP-2025", prev="Z1")])
        self.assertEqual(row(out, "Z2")["raised_since_last_filing"], 2000000)


class TestValues(unittest.TestCase):

    def test_filing_date_parsed_from_sec_format(self):
        self.assertEqual(row(run([filing("A1", filed="18-MAY-2023")]), "A1")
                         ["filing_date"], date(2023, 5, 18))

    def test_first_sale_date_parsed_and_blank_kept_blank(self):
        out = run([filing("A1", sale="2023-04-24"), filing("A2", sale="")])
        self.assertEqual(row(out, "A1")["first_sale_date"], date(2023, 4, 24))
        self.assertTrue(pd.isna(row(out, "A2")["first_sale_date"]))

    def test_cik_loses_leading_zeros(self):
        self.assertEqual(row(run([filing("A1", cik="0001952448")]), "A1")["cik"],
                         "1952448")

    def test_zip_cleaning(self):
        out = run([filing("A1", zip="02110-1234"), filing("A2", zip="06269"),
                   filing("A3", zip="ABCDE")])
        self.assertEqual(row(out, "A1")["zip"], "02110")
        self.assertEqual(row(out, "A2")["zip"], "06269")   # leading zero kept
        self.assertEqual(row(out, "A3")["zip"], "")

    def test_indefinite_target_is_kept_as_the_word(self):
        self.assertEqual(row(run([filing("A1", target="Indefinite")]), "A1")
                         ["offering_target"], "Indefinite")

    def test_security_types(self):
        out = run([filing("E", equity="true"),
                   filing("D", equity="", debt="true"),
                   filing("S", equity="", other="true",
                          other_desc="Simple Agreement for Future Equity"),
                   filing("B", equity="true", debt="true"),
                   filing("O", equity="", option="true")])
        got = {a: row(out, a)["security_type"] for a in "EDSBO"}
        self.assertEqual(got, {"E": "equity", "D": "debt", "S": "SAFE",
                               "B": "equity; debt", "O": "option/warrant"})

    def test_merger_flag(self):
        """Neumora's $474m filing is flagged - shares handed over in a
        merger, not new cash."""
        out = run([filing("A1", biz="true"), filing("A2", biz="false")])
        self.assertTrue(row(out, "A1")["is_business_combination"])
        self.assertFalse(row(out, "A2")["is_business_combination"])

    def test_yet_to_occur_flag(self):
        out = run([filing("A1", yto="true", sale="", sold="0"), filing("A2")])
        self.assertTrue(row(out, "A1")["first_sale_yet_to_occur"])
        self.assertFalse(row(out, "A2")["first_sale_yet_to_occur"])

    def test_company_founded_year(self):
        out = run([filing("A1", year="2021"), filing("A2", year="")])
        self.assertEqual(row(out, "A1")["company_founded_year"], 2021)
        self.assertTrue(pd.isna(row(out, "A2")["company_founded_year"]))


if __name__ == "__main__":
    unittest.main()
