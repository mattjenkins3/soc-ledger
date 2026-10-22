"""Deterministic interval-by-interval battery dispatch consistency checks.

Convention: UTC interval START, duration in hours, nonnegative AC-side charge
and discharge powers in MW, and end-of-interval stored energy in MWh.
"""
from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path

REQUIRED = ("interval_start_utc", "duration_h", "charge_mw", "discharge_mw", "soc_end_mwh")


@dataclass(frozen=True)
class Settings:
    capacity_mwh: float
    max_charge_mw: float
    max_discharge_mw: float
    initial_soc_mwh: float
    charge_efficiency: float = 0.95
    discharge_efficiency: float = 0.95
    tolerance_mwh: float = 0.01
    tolerance_mw: float = 1e-9

    def __post_init__(self):
        for name in ("capacity_mwh", "max_charge_mw", "max_discharge_mw"):
            if not isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if not isfinite(self.initial_soc_mwh) or not 0 <= self.initial_soc_mwh <= self.capacity_mwh:
            raise ValueError("initial_soc_mwh must be within [0, capacity_mwh]")
        for name in ("charge_efficiency", "discharge_efficiency"):
            if not isfinite(getattr(self, name)) or not 0 < getattr(self, name) <= 1:
                raise ValueError(f"{name} must be within (0, 1]")
        for name in ("tolerance_mwh", "tolerance_mw"):
            if not isfinite(getattr(self, name)) or getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative and finite")


@dataclass(frozen=True)
class Finding:
    row: int
    code: str
    detail: str


def _utc(raw: str) -> datetime:
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid ISO 8601 timestamp: {raw!r}") from exc
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("interval_start_utc requires an explicit UTC offset (Z or +00:00)")
    return value.astimezone(timezone.utc)


def _finite(row: dict, key: str, row_no: int) -> float:
    try:
        value = float(row[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"row {row_no}: invalid {key}") from exc
    if not isfinite(value):
        raise ValueError(f"row {row_no}: {key} must be finite")
    return value


def audit_csv(path: str | Path, settings: Settings) -> dict:
    """Audit a UTF-8 CSV; raise ValueError for invalid schema/data.

    Invalid or discontinuous intervals are reported as findings. An SOC
    residual is *not* calculated across a gap/overlap: the intervening energy
    movement is unknown. Per-row bounds/power checks still run.
    """
    findings: list[Finding] = []
    intervals: list[dict] = []
    previous_end = None
    previous_soc = settings.initial_soc_mwh
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("missing or duplicate CSV headers")
        if any(name not in reader.fieldnames for name in REQUIRED):
            raise ValueError("required columns: " + ", ".join(REQUIRED))
        for row_no, row in enumerate(reader, start=2):
            if None in row or any(row[key] is None for key in REQUIRED):
                raise ValueError(f"row {row_no}: malformed CSV row")
            start = _utc(row["interval_start_utc"])
            duration = _finite(row, "duration_h", row_no)
            charge = _finite(row, "charge_mw", row_no)
            discharge = _finite(row, "discharge_mw", row_no)
            observed = _finite(row, "soc_end_mwh", row_no)
            if duration <= 0:
                raise ValueError(f"row {row_no}: duration_h must be positive")
            if duration > 24 * 366:
                raise ValueError(f"row {row_no}: duration_h exceeds one year")
            if charge < 0 or discharge < 0:
                raise ValueError(f"row {row_no}: charge_mw and discharge_mw must be nonnegative")
            end = start + timedelta(hours=duration)
            contiguous = previous_end is None or start == previous_end
            if previous_end is not None and not contiguous:
                code = "GAP" if start > previous_end else "OVERLAP"
                findings.append(Finding(row_no, code, f"expected start {previous_end.isoformat()}, got {start.isoformat()}"))
            if charge > settings.max_charge_mw + settings.tolerance_mw:
                findings.append(Finding(row_no, "CHARGE_POWER", f"{charge:g} MW exceeds charge rating"))
            if discharge > settings.max_discharge_mw + settings.tolerance_mw:
                findings.append(Finding(row_no, "DISCHARGE_POWER", f"{discharge:g} MW exceeds discharge rating"))
            if charge > settings.tolerance_mw and discharge > settings.tolerance_mw:
                findings.append(Finding(row_no, "SIMULTANEOUS", "charging and discharging in the same interval"))
            if not -settings.tolerance_mwh <= observed <= settings.capacity_mwh + settings.tolerance_mwh:
                findings.append(Finding(row_no, "SOC_BOUNDS", f"{observed:g} MWh outside physical capacity"))
            predicted = None
            residual = None
            if contiguous:
                predicted = previous_soc + (settings.charge_efficiency * charge - discharge / settings.discharge_efficiency) * duration
                residual = observed - predicted
                if abs(residual) > settings.tolerance_mwh:
                    findings.append(Finding(row_no, "SOC_RESIDUAL", f"observed - predicted = {residual:+.6g} MWh"))
            intervals.append({"row": row_no, "start_utc": start.isoformat(), "end_utc": end.isoformat(),
                              "observed_soc_mwh": observed, "predicted_soc_mwh": predicted,
                              "residual_mwh": residual})
            previous_end = end
            previous_soc = observed
    if not intervals:
        raise ValueError("CSV has no data intervals")
    return {"status": "PASS" if not findings else "FAIL", "intervals": len(intervals),
            "findings": [asdict(item) for item in findings], "series": intervals,
            "assumptions": "AC-side power; fixed efficiencies; no standby losses, auxiliary loads, degradation or hidden flows"}
