"""Pure functions: API rows -> UTC hourly buckets -> cumulative statistics."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

HOUR = timedelta(hours=1)


class DataCheckError(Exception):
    """API data failed a consistency check; nothing is written."""


def local_hour_to_utc(naive: datetime, tz: ZoneInfo) -> datetime:
    """Map a naive local wall-clock hour to its UTC hour start.

    - Normal hours map directly.
    - Spring-forward (non-existent 02:00) maps to 03:00 local, so its volume
      is added to the 03:00 hour.
    - Fall-back (repeated 01:00) maps to the first 01:00 (fold=0), so any
      volume reported for 01:00 lands in that single hour.
    """
    aware = naive.replace(tzinfo=tz, fold=0)
    roundtrip = aware.astimezone(UTC).astimezone(tz).replace(tzinfo=None)
    if roundtrip != naive:  # non-existent local time
        aware = (naive + HOUR).replace(tzinfo=tz, fold=0)
    return aware.astimezone(UTC)


def rows_to_buckets(
    rows: list[dict[str, Any]], tz: ZoneInfo, now_utc: datetime
) -> dict[datetime, float]:
    """Sum API hourly rows into UTC hour buckets.

    Only hours that have fully elapsed (bucket end <= now) are kept; the API
    returns 0 for future hours, which must never be imported.
    """
    buckets: dict[datetime, float] = defaultdict(float)
    for row in rows:
        naive = datetime.fromisoformat(row["dateTime"])
        if naive.minute or naive.second or naive.microsecond or naive.tzinfo:
            raise DataCheckError(f"Unexpected hourly timestamp {row['dateTime']}")
        cons = row.get("cons")
        if cons is None:
            raise DataCheckError(f"Missing value at {row['dateTime']}")
        start = local_hour_to_utc(naive, tz)
        if start + HOUR <= now_utc:
            buckets[start] += float(cons)
    return dict(buckets)


def check_day_counts(rows: list[dict[str, Any]], days: list[date]) -> None:
    """Every requested day must be present with 23 to 25 hourly rows."""
    counts: dict[date, int] = defaultdict(int)
    for row in rows:
        counts[datetime.fromisoformat(row["dateTime"]).date()] += 1
    for day in days:
        if not 23 <= counts.get(day, 0) <= 25:
            raise DataCheckError(f"{day}: {counts.get(day, 0)} hourly rows")
    extra = set(counts) - set(days)
    if extra:
        raise DataCheckError(f"Unrequested days returned: {sorted(extra)}")


def check_against_daily(
    hourly: list[dict[str, Any]], daily: list[dict[str, Any]], days: list[date]
) -> None:
    """Hourly totals must equal the daily figures for completed days."""
    h: dict[date, float] = defaultdict(float)
    for row in hourly:
        h[datetime.fromisoformat(row["dateTime"]).date()] += float(row["cons"])
    d = {datetime.fromisoformat(r["consDate"]).date(): float(r["cons"]) for r in daily}
    for day in days:
        if day not in d:
            raise DataCheckError(f"{day}: missing from daily data")
        if abs(h.get(day, 0.0) - d[day]) > 0.01:
            raise DataCheckError(
                f"{day}: hourly total {h.get(day, 0.0):.3f} != daily {d[day]:.3f}"
            )


def build_statistics(
    buckets: dict[datetime, float], base_sum: float
) -> list[dict[str, Any]]:
    """Ordered rows with running sum, starting from base_sum."""
    running = base_sum
    out = []
    for start in sorted(buckets):
        running += buckets[start]
        out.append({"start": start, "state": buckets[start], "sum": running})
    return out


def date_chunks(start: date, end_exclusive: date, max_days: int) -> list[tuple[date, date]]:
    """Split [start, end_exclusive) into chunks of at most max_days days."""
    out = []
    cur = start
    while cur < end_exclusive:
        nxt = min(cur + timedelta(days=max_days), end_exclusive)
        out.append((cur, nxt))
        cur = nxt
    return out
