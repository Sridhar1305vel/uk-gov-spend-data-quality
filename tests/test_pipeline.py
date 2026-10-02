import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from spendqa.checks import (check_outliers, cluster_suppliers, normalise_supplier)  # noqa: E402
from spendqa.ingest import infer_period, parse_amount, parse_dates, load_alias_map, find_header  # noqa: E402
from spendqa.pipeline import run_pipeline  # noqa: E402
from spendqa.report import build_report  # noqa: E402
from tests.fixtures import build  # noqa: E402

ALIASES = ROOT / "config" / "column_aliases.json"


class TestParsers(unittest.TestCase):
    def test_amount_formats(self):
        s = pd.Series(["£1,234.50", "(1,234.50)", "1,234.50-", "1,234.50CR", "-99", "n/a", "", " 26000 ", None])
        got = parse_amount(s).tolist()
        exp = [1234.5, -1234.5, -1234.5, -1234.5, -99.0, np.nan, np.nan, 26000.0, np.nan]
        for g, e in zip(got, exp):
            if np.isnan(e):
                self.assertTrue(np.isnan(g))
            else:
                self.assertAlmostEqual(g, e)

    def test_date_formats_and_day_first(self):
        s = pd.Series(["03/04/2025", "2025-04-03", "03-Apr-25", "3 April 2025", "45750", "garbage", ""])
        d = parse_dates(s)
        for i in range(4):
            self.assertEqual(d.iloc[i], pd.Timestamp("2025-04-03"))   # 03/04 is 3 April (UK)
        self.assertEqual(d.iloc[4], pd.Timestamp("2025-04-03"))       # Excel serial
        self.assertTrue(d.iloc[5:].isna().all())

    def test_infer_period(self):
        self.assertEqual(infer_period("spend_2025-03"), pd.Timestamp("2025-03-01"))
        self.assertEqual(infer_period("Cabinet Office March 2025"), pd.Timestamp("2025-03-01"))
        self.assertEqual(infer_period("beta_mar-25"), pd.Timestamp("2025-03-01"))
        self.assertIsNone(infer_period("summary-report"))   # 'mar' inside a word must not match

    def test_header_detection_skips_preamble(self):
        amap = load_alias_map(ALIASES)
        rows = [["Title"], ["note"], [], ["Date", "Supplier", "Amount", "Expense Type"], ["1", "2", "3", "4"]]
        self.assertEqual(find_header(rows, amap)[0], 3)


class TestSupplierClustering(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(normalise_supplier("Acme Consulting Ltd."), "ACME CONSULTING")
        self.assertEqual(normalise_supplier("ACME CONSULTING LIMITED"), "ACME CONSULTING")
        self.assertEqual(normalise_supplier("Smith & Sons PLC"), "SMITH AND SONS")

    def test_cluster_merges_near_matches_only(self):
        df = pd.DataFrame({"supplier": ["Acme Consulting Ltd", "ACME CONSULTING LIMITED", "Acme Consultng Ltd",
                                        "Acme Catering Ltd", "Zeta Holdings"]})
        c = cluster_suppliers(df).set_index("supplier")["supplier_cluster"]
        self.assertEqual(c["Acme Consulting Ltd"], c["ACME CONSULTING LIMITED"])
        self.assertEqual(c["Acme Consulting Ltd"], c["Acme Consultng Ltd"])      # typo merged
        self.assertNotEqual(c["Acme Consulting Ltd"], c["Acme Catering Ltd"])    # different company kept apart

    def test_outlier_check(self):
        rng = np.random.default_rng(0)
        amt = list(np.exp(rng.normal(10.5, 0.4, 200))) + [5_000_000_000]
        df = pd.DataFrame({"row_id": range(len(amt)), "department": "D", "amount": amt})
        flags = check_outliers(df)
        self.assertIn(len(amt) - 1, flags["row_id"].tolist())


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        raw = Path(cls.tmp.name) / "raw"
        build(raw)
        cls.db = Path(cls.tmp.name) / "out" / "spend.db"
        cls.recon = run_pipeline(raw, cls.db, ALIASES, log=lambda *_: None)
        cls.con = sqlite3.connect(cls.db)

    @classmethod
    def tearDownClass(cls):
        cls.con.close()
        cls.tmp.cleanup()

    def count(self, check):
        return self.con.execute("SELECT COUNT(*) FROM dq_flags WHERE check_name = ?", (check,)).fetchone()[0]

    def test_reconciliation_identity(self):
        self.assertTrue(self.recon["reconciled"].all())
        a = self.recon[self.recon["department"] == "dept_alpha"].iloc[0]
        self.assertEqual((a.rows_read, a.rows_loaded, a.rows_rejected), (10, 8, 2))
        b = self.recon[self.recon["department"] == "dept_beta"].iloc[0]
        self.assertEqual((b.rows_read, b.rows_loaded, b.rows_rejected), (5, 5, 0))

    def test_rejections_have_reasons(self):
        reasons = dict(self.con.execute("SELECT reason, COUNT(*) FROM rejected_rows GROUP BY reason").fetchall())
        self.assertEqual(reasons, {"summary_row": 1, "amount_unparseable": 1})

    def test_schema_drift_and_encoding_handled(self):
        enc = dict(self.con.execute("SELECT source_file, encoding FROM files").fetchall())
        self.assertEqual(enc["alpha_2025-03.csv"], "cp1252")
        delim = dict(self.con.execute("SELECT source_file, delimiter FROM files").fetchall())
        self.assertEqual(delim["beta_mar-25.csv"], ";")
        self.assertEqual(self.con.execute("SELECT COUNT(DISTINCT header_signature) FROM files").fetchone()[0], 2)

    def test_amount_and_date_values(self):
        neg = self.con.execute("SELECT amount FROM spend WHERE transaction_ref IN ('TX004','V4') ORDER BY amount").fetchall()
        self.assertEqual([x[0] for x in neg], [-27500.0, -1200.0])
        d = self.con.execute("SELECT txn_date FROM spend WHERE transaction_ref = 'V2'").fetchone()[0]
        self.assertEqual(d, "2025-03-14")

    def test_checks_fire_once_on_planted_issues(self):
        self.assertEqual(self.count("exact_duplicate"), 1)
        self.assertEqual(self.count("probable_duplicate_payment"), 1)
        self.assertEqual(self.count("missing_supplier"), 1)
        self.assertEqual(self.count("date_implausible"), 1)
        self.assertEqual(self.count("date_outside_file_period"), 2)   # July-2025 row and the 2030 row
        self.assertEqual(self.count("credit_or_negative"), 2)
        self.assertEqual(self.count("supplier_name_variant"), 3)      # 2 Acme spellings + 1 Zeta spelling

    def test_ragged_row_kept_and_marked(self):
        r = self.con.execute("SELECT ragged FROM spend WHERE transaction_ref = 'TX009'").fetchone()
        self.assertEqual(r[0], 1)

    def test_duplicate_exposure_view(self):
        rows = dict((r[0], r[1]) for r in self.con.execute("SELECT check_name, amount_gbp FROM v_duplicate_exposure"))
        self.assertEqual(rows["exact_duplicate"], 50000.0)
        self.assertEqual(rows["probable_duplicate_payment"], 30000.0)

    def test_report_is_generated(self):
        out = self.db.parent
        p = build_report(self.db, out)
        text = p.read_text(encoding="utf-8")
        self.assertIn("Every file reconciled", text)
        self.assertTrue((out / "powerbi" / "review_queue.csv").exists())


if __name__ == "__main__":
    unittest.main()
