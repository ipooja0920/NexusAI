"""A re-screened Capital IQ export that drops or moves columns stops with a
readable message (which columns, which letters), not an IndexError."""
import unittest

import pandas as pd

from nexus.capitaliq_layout import check_or_exit, fill_warnings, problems, where
from nexus.settings import Settings

GOOD = ["Company Name", "Exchange:Ticker", "Industry Classifications", "Company Type",
        "Geographic Locations", "Total Employees [Latest Annual]",
        "Total Revenue [LTM] ($USDmm, Historical rate)", "Competitors", "Offices",
        "Geographic Region", "Business Description", "Website", "Company Status"]
# the October 2026 re-screen: no description, other order, 10 columns
NEW = ["Company Name", "Exchange:Ticker", "Industry Classifications", "Company Type",
       "Geographic Locations", "Company Status", "Revenues [LTM] ($USDmm, Historical rate)",
       "Website", "Number of Employees [Latest Annual]", "Offices"]


class TestLayout(unittest.TestCase):

    def setUp(self):
        self.ciq = Settings().capitaliq

    def test_good_export(self):
        self.assertEqual(problems(GOOD, self.ciq), [])

    def test_rescreened_export(self):
        p = " ".join(problems(NEW, self.ciq))
        self.assertIn("col_description: column K does not exist", p)
        self.assertIn("col_status: column M does not exist", p)
        w = where(NEW)
        self.assertIsNone(w["col_description"])
        self.assertEqual((w["col_status"], w["col_website"], w["col_address"]), ("F", "H", "J"))
        with self.assertRaises(SystemExit) as e:
            check_or_exit(NEW, self.ciq, "companies_raw.xlsx")
        msg = str(e.exception)
        self.assertIn("Re-export with these data items: Business Description", msg)
        self.assertIn("col_status: F", msg)

    def test_empty_revenue_is_flagged(self):
        df = pd.DataFrame([["A", "-", "x", "y", "z", "5", "-", "", "addr", "", "desc", "w", "Operating"]] * 20,
                          columns=GOOD)
        w = " ".join(fill_warnings(df, self.ciq))
        self.assertIn("col_revenue", w)
        self.assertNotIn("col_description", w)


if __name__ == "__main__":
    unittest.main()
