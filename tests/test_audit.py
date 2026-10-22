import csv
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from soc_ledger.audit import Settings, audit_csv

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = Settings(capacity_mwh=10, max_charge_mw=2, max_discharge_mw=2, initial_soc_mwh=5)
HEADER = ["interval_start_utc", "duration_h", "charge_mw", "discharge_mw", "soc_end_mwh"]


def audit_rows(rows, settings=DEFAULT):
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "rows.csv"
        with path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(HEADER)
            writer.writerows(rows)
        return audit_csv(path, settings)


class AuditTests(unittest.TestCase):
    def test_clean_example(self):
        r = audit_csv(ROOT / "examples/dispatch.csv", DEFAULT)
        self.assertEqual(r["status"], "PASS")
        self.assertEqual(r["intervals"], 5)
        self.assertEqual(len(r["findings"]), 0)

    def test_known_anomalies(self):
        r = audit_csv(ROOT / "examples/dispatch-with-errors.csv", DEFAULT)
        self.assertEqual([f["code"] for f in r["findings"]], ["SOC_RESIDUAL", "SIMULTANEOUS", "SOC_RESIDUAL"])

    def test_power_and_soc_limits(self):
        r = audit_rows([["2026-01-01T00:00:00Z", 1, 3, 4, 11]])
        self.assertEqual({f["code"] for f in r["findings"]}, {"CHARGE_POWER", "DISCHARGE_POWER", "SIMULTANEOUS", "SOC_BOUNDS", "SOC_RESIDUAL"})

    def test_gap_does_not_infer_unobserved_energy(self):
        r = audit_rows([["2026-01-01T00:00:00Z", 1, 0, 0, 5],
                        ["2026-01-01T03:00:00Z", 1, 0, 0, 8],
                        ["2026-01-01T04:00:00Z", 1, 0, 0, 8]])
        self.assertEqual([f["code"] for f in r["findings"]], ["GAP"])
        self.assertIsNone(r["series"][1]["residual_mwh"])
        self.assertEqual(r["series"][2]["residual_mwh"], 0)

    def test_overlap_does_not_calculate_residual(self):
        r = audit_rows([["2026-01-01T00:00:00Z", 1, 0, 0, 5],
                        ["2026-01-01T00:30:00Z", 1, 0, 0, 8]])
        self.assertEqual([f["code"] for f in r["findings"]], ["OVERLAP"])

    def test_subhourly_efficiency(self):
        r = audit_rows([["2026-01-01T00:00:00+00:00", 0.5, 2, 0, 5.95],
                        ["2026-01-01T00:30:00Z", 0.5, 0, 1.9, 4.95]])
        self.assertEqual(r["status"], "PASS")

    def test_tolerance_boundary(self):
        r = audit_rows([["2026-01-01T00:00:00Z", 1, 0, 0, 5.005]])
        self.assertEqual(r["status"], "PASS")

    def test_invalid_numeric_and_nonfinite(self):
        for value in ("NaN", "inf", "oops", ""):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_rows([["2026-01-01T00:00:00Z", 1, value, 0, 5]])

    def test_invalid_timestamp_and_timezone(self):
        for value in ("2026-01-01T00:00:00", "2026-01-01T01:00:00+01:00", "invalid"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                audit_rows([[value, 1, 0, 0, 5]])

    def test_invalid_duration_or_negative_power(self):
        for row in (["2026-01-01T00:00:00Z", 0, 0, 0, 5],
                    ["2026-01-01T00:00:00Z", 1, -1, 0, 5],
                    ["2026-01-01T00:00:00Z", 1, 0, -1, 5]):
            with self.subTest(row=row), self.assertRaises(ValueError):
                audit_rows([row])

    def test_settings_validation(self):
        for update in ({"capacity_mwh": -1}, {"initial_soc_mwh": 11},
                       {"charge_efficiency": 0}, {"discharge_efficiency": 1.1},
                       {"tolerance_mwh": -1}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                Settings(**{**DEFAULT.__dict__, **update})

    def test_missing_or_duplicate_header(self):
        for header in (["interval_start_utc"], HEADER + ["charge_mw"]):
            with tempfile.TemporaryDirectory() as d:
                path = Path(d) / "bad.csv"
                path.write_text(",".join(header) + "\n", encoding="utf-8")
                with self.assertRaises(ValueError):
                    audit_csv(path, DEFAULT)

    def test_empty_csv(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "empty.csv"
            path.write_text(",".join(HEADER) + "\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                audit_csv(path, DEFAULT)

    def test_cli_exit_codes_and_json(self):
        base = [sys.executable, "-m", "soc_ledger"]
        args = ["--capacity-mwh", "10", "--max-charge-mw", "2", "--max-discharge-mw", "2", "--initial-soc-mwh", "5"]
        clean = subprocess.run(base + [str(ROOT / "examples/dispatch.csv")] + args, capture_output=True, text=True)
        self.assertEqual(clean.returncode, 0, clean.stderr)
        self.assertEqual(json.loads(clean.stdout)["status"], "PASS")
        dirty = subprocess.run(base + [str(ROOT / "examples/dispatch-with-errors.csv")] + args, capture_output=True, text=True)
        self.assertEqual(dirty.returncode, 1, dirty.stderr)
        self.assertEqual(json.loads(dirty.stdout)["status"], "FAIL")
        bad = subprocess.run(base + [str(ROOT / "examples/dispatch.csv")] + args[:-1] + ["-1"], capture_output=True, text=True)
        self.assertEqual(bad.returncode, 2)
        self.assertEqual(json.loads(bad.stderr)["status"], "INPUT_ERROR")

    def test_no_input_mutation(self):
        path = ROOT / "examples/dispatch.csv"
        before = path.read_bytes()
        audit_csv(path, DEFAULT)
        self.assertEqual(before, path.read_bytes())


if __name__ == "__main__":
    unittest.main()
