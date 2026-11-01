"""Fetch hourly data and write it to Home Assistant long-term statistics."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, time, timedelta
import logging
from zoneinfo import ZoneInfo

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .api import MwaClient
from .const import DOMAIN, MAX_CHUNK_DAYS, UNIT_CLASS_VOLUME, UNIT_GALLONS
from .transform import (
    DataCheckError,
    build_statistics,
    check_against_daily,
    check_day_counts,
    date_chunks,
    rows_to_buckets,
)

_LOGGER = logging.getLogger(__name__)


def statistic_id_for(meter_count: int, rehearsal: bool) -> str:
    return f"{DOMAIN}:water_{meter_count}" + ("_rehearsal" if rehearsal else "")


class WaterSync:
    """One sync run = fetch window, verify, single atomic import."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: MwaClient,
        *,
        meter_count: int,
        name: str,
        rehearsal: bool,
        backfill_start: date,
        lookback_days: int,
        tz: ZoneInfo,
    ) -> None:
        self.hass = hass
        self.client = client
        self.meter = meter_count
        self.statistic_id = statistic_id_for(meter_count, rehearsal)
        self.name = name + (" (rehearsal)" if rehearsal else "")
        self.backfill_start = backfill_start
        self.lookback_days = lookback_days
        self.tz = tz
        self._lock = asyncio.Lock()

    def _local_midnight_utc(self, day: date) -> datetime:
        return datetime.combine(day, time(0), tzinfo=self.tz).astimezone(UTC)

    async def _last_stat_start(self) -> datetime | None:
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, self.statistic_id, True, {"sum"}
        )
        rows = last.get(self.statistic_id)
        if not rows:
            return None
        return dt_util.utc_from_timestamp(rows[0]["start"])

    async def _sum_before(self, window_start_utc: datetime) -> float:
        """Running sum of the last stored hour before the window (bounded query)."""
        result = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            window_start_utc - timedelta(days=2),
            window_start_utc,
            {self.statistic_id},
            "hour",
            None,
            {"sum"},
        )
        rows = result.get(self.statistic_id) or []
        if not rows or rows[-1].get("sum") is None:
            raise DataCheckError(
                f"No stored sum in the 2 days before {window_start_utc.isoformat()}"
            )
        return float(rows[-1]["sum"])

    async def async_run(self, now_utc: datetime | None = None) -> int:
        """Run one sync. Returns number of hourly rows written (0 if none)."""
        async with self._lock:
            now_utc = now_utc or dt_util.utcnow()
            today = now_utc.astimezone(self.tz).date()

            last_start = await self._last_stat_start()
            if last_start is None:
                window_start = self.backfill_start
                base_sum = 0.0
            else:
                last_local_day = last_start.astimezone(self.tz).date()
                window_start = min(
                    today - timedelta(days=self.lookback_days), last_local_day
                )
                window_start = max(window_start, self.backfill_start)
                if window_start == self.backfill_start:
                    base_sum = 0.0
                else:
                    base_sum = await self._sum_before(
                        self._local_midnight_utc(window_start)
                    )

            if window_start > today:
                return 0

            # Completed days: hourly in <=60-day chunks (end exclusive).
            rows: list[dict] = []
            past_days = [
                window_start + timedelta(days=i)
                for i in range((today - window_start).days)
            ]
            for chunk_start, chunk_end in date_chunks(window_start, today, MAX_CHUNK_DAYS):
                rows += await self.client.get_hourly(self.meter, chunk_start, chunk_end)
            check_day_counts(rows, past_days)
            if past_days:
                daily = await self.client.get_daily(
                    self.meter, past_days[0], past_days[-1]
                )
                check_against_daily(rows, daily, past_days)

            # Today (single-day request, start == end).
            today_rows = await self.client.get_hourly(self.meter, today, today)
            check_day_counts(today_rows, [today])
            rows += today_rows

            buckets = rows_to_buckets(rows, self.tz, now_utc)
            stats = build_statistics(buckets, base_sum)
            if not stats:
                return 0

            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=self.name,
                source=DOMAIN,
                statistic_id=self.statistic_id,
                unit_class=UNIT_CLASS_VOLUME,
                unit_of_measurement=UNIT_GALLONS,
            )
            # One call = one recorder job = one database transaction.
            async_add_external_statistics(
                self.hass,
                metadata,
                [StatisticData(start=s["start"], state=s["state"], sum=s["sum"]) for s in stats],
            )
            _LOGGER.info(
                "%s: queued %d hourly rows from %s (base sum %.3f)",
                self.statistic_id, len(stats), window_start, base_sum,
            )
            return len(stats)
