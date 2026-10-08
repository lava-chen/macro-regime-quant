"""Regression tests for the CBOE/finance-vix importer (no network)."""

import runpy
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

MODULE = runpy.run_path(str(Path(__file__).resolve().parents[2] / "scripts/update_vix.py"))
generate_payloads = MODULE["generate_payloads"]
check_existing_history = MODULE["check_existing_history"]
parse_daily = MODULE["parse_daily"]

FIXTURE = (
    "DATE,OPEN,HIGH,LOW,CLOSE\n"
    "02/26/2026,21.1,22,20,20.5\n"
    "02/27/2026,20.1,22,19,19.5\n"
    "03/02/2026,18.1,20,17,18.5\n"
)


class VixUpdateTests(unittest.TestCase):
    def test_normalization_and_completed_month(self):
        results = generate_payloads(FIXTURE, min_rows=3)
        self.assertEqual(results["vix-daily.csv"].splitlines()[1],
                         "2026-02-26,21.100000,22.000000,20.000000,20.500000")
        self.assertEqual(results["vix-monthly.csv"].splitlines()[-1],
                         "2026-03-02,18.500000")
        self.assertEqual(results["vix_close.csv"].splitlines()[1],
                         "2026-02-26,20.500000,2026-02-27,fixed_lag")
        self.assertEqual(results["vix_monthly_close.csv"].splitlines()[-1],
                         "2026-02-27,19.500000,2026-02-28,fixed_lag")
        self.assertEqual(len(results["vix_monthly_close.csv"].splitlines()), 2)

    def test_reject_duplicate_date(self):
        duplicate = FIXTURE + "03/02/2026,18.1,20,17,18.5\n"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            parse_daily(duplicate, min_rows=1)

    def test_reject_truncation(self):
        with self.assertRaisesRegex(ValueError, "incomplete"):
            generate_payloads(FIXTURE, min_rows=5000)

    def test_never_overwrite_with_stale_snapshot(self):
        newer = FIXTURE + "03/03/2026,17.1,19,16,17.5\n"
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "vix-daily.csv").write_text(
                generate_payloads(newer, min_rows=3)["vix-daily.csv"], encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "shorter/stale"):
                check_existing_history(root, generate_payloads(FIXTURE, min_rows=3))


if __name__ == "__main__":
    unittest.main()
