"""Reproducible, synthetic 8,760-row microbenchmark; no field data."""
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
import statistics
import tempfile
import time

from soc_ledger.audit import Settings, audit_csv

with tempfile.TemporaryDirectory() as folder:
    path = Path(folder) / "8760.csv"
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["interval_start_utc", "duration_h", "charge_mw", "discharge_mw", "soc_end_mwh"])
        for hour in range(8760):
            writer.writerow([(start + timedelta(hours=hour)).isoformat(), 1, 0, 0, 5])
    settings = Settings(capacity_mwh=10, max_charge_mw=2, max_discharge_mw=2, initial_soc_mwh=5)
    durations = []
    for _ in range(15):
        t = time.perf_counter()
        result = audit_csv(path, settings)
        durations.append((time.perf_counter() - t) * 1000)
    print(f"{result['intervals']} synthetic rows, {len(result['findings'])} findings; "
          f"median {statistics.median(durations):.1f} ms / 15 runs")
