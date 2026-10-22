"""Command-line interface for soc-ledger."""
import argparse
import json
import sys
from .audit import Settings, audit_csv


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit a battery dispatch CSV for SOC consistency")
    parser.add_argument("csv", help="input CSV, with explicit UTC interval starts")
    parser.add_argument("--capacity-mwh", type=float, required=True)
    parser.add_argument("--max-charge-mw", type=float, required=True)
    parser.add_argument("--max-discharge-mw", type=float, required=True)
    parser.add_argument("--initial-soc-mwh", type=float, required=True)
    parser.add_argument("--charge-efficiency", type=float, default=0.95)
    parser.add_argument("--discharge-efficiency", type=float, default=0.95)
    parser.add_argument("--tolerance-mwh", type=float, default=0.01)
    parser.add_argument("--tolerance-mw", type=float, default=1e-9)
    args = parser.parse_args(argv)
    try:
        settings = Settings(**{key: getattr(args, key) for key in (
            "capacity_mwh", "max_charge_mw", "max_discharge_mw", "initial_soc_mwh",
            "charge_efficiency", "discharge_efficiency", "tolerance_mwh", "tolerance_mw")})
        result = audit_csv(args.csv, settings)
    except (ValueError, OSError, OverflowError) as exc:
        print(json.dumps({"status": "INPUT_ERROR", "error": str(exc)}, indent=2), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
