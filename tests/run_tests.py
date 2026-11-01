"""End-to-end rehearsal against a real Home Assistant 2026.10.4 core + recorder (SQLite)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
import os
import shutil
import sqlite3
import sys
import tempfile
from zoneinfo import ZoneInfo

from aiohttp import web

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from fake_api import FakeApi  # noqa: E402

from homeassistant import loader  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

import logging
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logging.getLogger("custom_components.my_water_advisor").setLevel(logging.INFO)
TZ = ZoneInfo("America/Chicago")
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)


async def boot(cfg: str) -> HomeAssistant:
    hass = HomeAssistant(cfg)
    await hass.config.async_set_time_zone("America/Chicago")
    hass.config.skip_pip = True
    loader.async_setup(hass)
    from homeassistant import bootstrap, config_entries
    hass.config_entries = config_entries.ConfigEntries(hass, {})
    from homeassistant.helpers import recorder as rec_helper
    rec_helper.async_initialize_recorder(hass)
    assert await bootstrap.async_load_base_functionality(hass)
    await hass.async_start()
    assert await async_setup_component(hass, "network", {})
    db = os.path.join(cfg, "home-assistant_v2.db")
    assert await async_setup_component(hass, "recorder", {"recorder": {"db_url": f"sqlite:///{db}"}})
    await hass.async_block_till_done()
    return hass


async def flush(hass: HomeAssistant) -> None:
    """Wait until every task queued so far in the recorder has been processed."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.tasks import SynchronizeTask
    await hass.async_block_till_done()
    fut = hass.loop.create_future()
    get_instance(hass).queue_task(SynchronizeTask(fut))
    await fut
    await hass.async_block_till_done()


def db_rows(db: str, stat_id: str) -> list[tuple[float, float, float]]:
    con = sqlite3.connect(db)
    try:
        return con.execute(
            "SELECT s.start_ts, s.state, s.sum FROM statistics s JOIN statistics_meta m "
            "ON s.metadata_id = m.id WHERE m.statistic_id = ? ORDER BY s.start_ts", (stat_id,)
        ).fetchall()
    finally:
        con.close()


def table_bytes(db: str) -> dict[str, int]:
    con = sqlite3.connect(db)
    try:
        return dict(con.execute(
            "SELECT name, SUM(pgsize) FROM dbstat WHERE name IN "
            "('statistics','ix_statistics_statistic_id_start_ts','ix_statistics_start_ts',"
            "'statistics_meta','ix_statistics_meta_statistic_id') GROUP BY name").fetchall())
    finally:
        con.close()


async def main() -> None:
    cfg = tempfile.mkdtemp(prefix="ha_rehearsal_")
    os.makedirs(os.path.join(cfg, "custom_components"))
    shutil.copytree(os.path.join(REPO, "custom_components", "my_water_advisor"),
                    os.path.join(cfg, "custom_components", "my_water_advisor"))
    db = os.path.join(cfg, "home-assistant_v2.db")

    now_local = datetime(2026, 10, 31, 12, 23)  # naive local
    now_utc = now_local.replace(tzinfo=TZ).astimezone(UTC)
    api = FakeApi(date(2025, 10, 1), now_local)
    runner = web.AppRunner(api.app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 18765)
    await site.start()

    hass = await boot(cfg)

    # Point the integration at the fake API.
    import importlib
    sys.path.insert(0, cfg)
    const = importlib.import_module("custom_components.my_water_advisor.const")
    const.BASE_URL = "http://127.0.0.1:18765"
    from custom_components.my_water_advisor.api import MwaClient, MwaAuthError
    from custom_components.my_water_advisor.sync import WaterSync
    from custom_components.my_water_advisor.transform import local_hour_to_utc
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    # ---- 1. DST mapping unit checks
    m = local_hour_to_utc(datetime(2026, 3, 8, 2), TZ)
    check("spring 02:00 -> 03:00 CDT (08:00Z)", m == datetime(2026, 3, 8, 8, tzinfo=UTC), str(m))
    m = local_hour_to_utc(datetime(2025, 11, 2, 1), TZ)
    check("fall 01:00 -> first 01:00 CDT (06:00Z)", m == datetime(2025, 11, 2, 6, tzinfo=UTC), str(m))
    m = local_hour_to_utc(datetime(2026, 1, 15, 7), TZ)
    check("winter 07:00 CST -> 13:00Z", m == datetime(2026, 1, 15, 13, tzinfo=UTC), str(m))

    # Unrelated pre-existing statistic, to prove undo touches nothing else.
    from homeassistant.components.recorder.models import StatisticData, StatisticMeanType, StatisticMetaData
    from homeassistant.components.recorder.statistics import async_add_external_statistics
    async_add_external_statistics(hass, StatisticMetaData(
        mean_type=StatisticMeanType.NONE, has_sum=True, name="other", source="other",
        statistic_id="other:keep_me", unit_class="volume", unit_of_measurement="gal"),
        [StatisticData(start=datetime(2026, 1, 1, h, tzinfo=UTC), state=1.0, sum=float(h + 1)) for h in range(24)])
    await flush(hass)
    before_other = db_rows(db, "other:keep_me")
    bytes_before = table_bytes(db)

    session = async_get_clientsession(hass)

    # ---- 2. bad password rejected
    bad = MwaClient(session, "a@b.c", "wrong", "dev1")
    try:
        await bad.login(); check("bad password rejected", False)
    except MwaAuthError as e:
        check("bad password rejected", True, str(e))

    client = MwaClient(session, "a@b.c", "secret", "dev1")
    sync = WaterSync(hass, client, meter_count=12345, name="Water (My Water Advisor)",
                     rehearsal=True, backfill_start=date(2025, 11, 1), lookback_days=7, tz=TZ)
    sid = sync.statistic_id
    check("rehearsal statistic id", sid == "my_water_advisor:water_12345_rehearsal", sid)

    # ---- 3. failure mid-backfill writes nothing (atomic)
    api.fail_hourly_after = 3
    try:
        await sync.async_run(now_utc); check("API failure aborts", False)
    except Exception as e:
        check("API failure aborts", True, type(e).__name__)
    await flush(hass)
    check("nothing written after failure", db_rows(db, sid) == [])
    api.fail_hourly_after = None

    # ---- 4. full backfill
    n = await sync.async_run(now_utc)
    await flush(hass)
    rows = db_rows(db, sid)
    first_utc = datetime(2025, 11, 1, tzinfo=TZ).astimezone(UTC)
    last_complete = now_utc.replace(minute=0, second=0, microsecond=0) - timedelta(hours=1)
    expected_hours = int((last_complete - first_utc).total_seconds() // 3600) + 1
    # Spring day loses no wall hour in UTC; fall-back day: one UTC hour (second 01:00) has no row.
    check("backfill row count", len(rows) == n == expected_hours - 1,
          f"n={n} rows={len(rows)} expected={expected_hours - 1}")
    exp_total = api.total(date(2025, 11, 1), now_local.date())
    check("backfill total equals source total", abs(rows[-1][2] - exp_total) < 1e-6,
          f"sum={rows[-1][2]:.3f} source={exp_total:.3f}")
    # per-day totals in local time
    by_day: dict[date, float] = {}
    for ts, st, _ in rows:
        d = datetime.fromtimestamp(ts, UTC).astimezone(TZ).date()
        by_day[d] = by_day.get(d, 0) + st
    bad_days = [d for d in by_day if abs(by_day[d] - api.total(d, d)) > 1e-6]
    check("every day's total matches", not bad_days, f"{len(by_day)} days, mismatches={bad_days[:3]}")
    # running sum consistent
    run = 0.0; ok = True
    for _, st, sm in rows:
        run += st; ok &= abs(run - sm) < 1e-6
    check("running sum consistent", ok)
    starts = [r[0] for r in rows]
    check("no future/current hour imported", max(starts) == last_complete.timestamp(),
          datetime.fromtimestamp(max(starts), UTC).isoformat())
    spring = [datetime.fromtimestamp(t, UTC).astimezone(TZ) for t, _, _ in rows
              if datetime.fromtimestamp(t, UTC).astimezone(TZ).date() == date(2026, 3, 8)]
    check("spring-forward day has 23 hourly rows", len(spring) == 23, str(len(spring)))
    fall = [1 for t, _, _ in rows if datetime.fromtimestamp(t, UTC).astimezone(TZ).date() == date(2025, 11, 2)]
    check("fall-back day has 24 hourly rows (repeat hour merged)", len(fall) == 24, str(len(fall)))

    # ---- 5. re-run is idempotent
    n2 = await sync.async_run(now_utc); await flush(hass)
    rows2 = db_rows(db, sid)
    check("re-run: same row count", len(rows2) == len(rows), f"{len(rows2)} (re-fetched {n2})")
    check("re-run: same final sum", abs(rows2[-1][2] - rows[-1][2]) < 1e-6)

    # ---- 6. late/revised data within lookback is corrected, sums recomputed
    later_local = now_local + timedelta(hours=5)
    api.now_local = later_local
    rev_day = now_local.date() - timedelta(days=3)
    api.values[(rev_day, 10)] += 42.0
    later_utc = later_local.replace(tzinfo=TZ).astimezone(UTC)
    await sync.async_run(later_utc); await flush(hass)
    rows3 = db_rows(db, sid)
    exp3 = api.total(date(2025, 11, 1), later_local.date())
    check("revision + new hours: total matches source", abs(rows3[-1][2] - exp3) < 1e-6,
          f"sum={rows3[-1][2]:.3f} source={exp3:.3f}")
    run = 0.0; ok = True
    for _, st, sm in rows3:
        run += st; ok &= abs(run - sm) < 1e-6
    check("revision: running sum consistent", ok, f"rows={len(rows3)}")

    # ---- 7. token expiry -> automatic re-login
    api.valid_tokens.clear(); logins_before = api.logins
    await sync.async_run(later_utc); await flush(hass)
    check("401 triggers re-login", api.logins == logins_before + 1)

    # ---- 8. gap longer than lookback (HA offline 10 days, lookback 7)
    gap_local = later_local + timedelta(days=10)
    api.now_local = gap_local
    d = later_local.date()
    while d <= gap_local.date():
        for h in range(24):
            api.values.setdefault((d, h), 3.0)
        d += timedelta(days=1)
    gap_utc = gap_local.replace(tzinfo=TZ).astimezone(UTC)
    await sync.async_run(gap_utc); await flush(hass)
    rows4 = db_rows(db, sid)
    exp4 = api.total(date(2025, 11, 1), gap_local.date())
    check("gap > lookback filled, total matches", abs(rows4[-1][2] - exp4) < 1e-6,
          f"sum={rows4[-1][2]:.3f} source={exp4:.3f}")

    # ---- 9. hourly vs daily mismatch aborts without writing
    snapshot = db_rows(db, sid)
    api.daily_offset = 1.0
    try:
        await sync.async_run(gap_utc); check("daily mismatch aborts", False)
    except Exception as e:
        check("daily mismatch aborts", True, str(e)[:80])
    await flush(hass)
    check("nothing changed after mismatch", db_rows(db, sid) == snapshot)
    api.daily_offset = 0.0

    # ---- 10. size measurement (rehearsal rows)
    bytes_after = table_bytes(db)
    added = {k: bytes_after.get(k, 0) - bytes_before.get(k, 0) for k in bytes_after}
    nrows = len(db_rows(db, sid))
    check("size measured", True, f"{nrows} rows, bytes added by table/index: {added}, total={sum(added.values())}")

    # ---- 11. config flow + service via the real integration loader
    assert await async_setup_component(hass, "my_water_advisor", {})
    await hass.async_block_till_done()
    res = await hass.config_entries.flow.async_init("my_water_advisor", context={"source": "user"})
    check("config flow shows form", res["type"] == "form", str(res.get("type")))
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {
        "email": "a@b.c", "password": "wrong", "name": "Water (My Water Advisor)",
        "backfill_start": "2025-11-01", "rehearsal": True, "poll_hours": "6", "lookback_days": "7"})
    check("config flow rejects bad password", res.get("errors") == {"base": "invalid_auth"}, str(res.get("errors")))
    res = await hass.config_entries.flow.async_configure(res["flow_id"], {
        "email": "a@b.c", "password": "secret", "name": "Water (My Water Advisor)",
        "backfill_start": "2025-11-01", "rehearsal": False, "poll_hours": "6", "lookback_days": "7"})
    check("config flow creates entry", res["type"] == "create_entry", f"{res.get('title')} {res.get('options')}")
    await hass.async_block_till_done()

    # ---- 12. undo service deletes only the rehearsal statistic
    await hass.services.async_call("my_water_advisor", "delete_statistics",
                                   {"statistic_id": sid}, blocking=True)
    await flush(hass)
    check("undo: rehearsal rows gone", db_rows(db, sid) == [])
    con = sqlite3.connect(db)
    meta = con.execute("SELECT statistic_id FROM statistics_meta").fetchall(); con.close()
    check("undo: rehearsal metadata gone", (sid,) not in meta, str(meta))
    check("undo: unrelated statistic untouched", db_rows(db, "other:keep_me") == before_other)
    try:
        await hass.services.async_call("my_water_advisor", "delete_statistics",
                                       {"statistic_id": "other:keep_me"}, blocking=True)
        check("undo refuses other integrations' statistics", False)
    except Exception as e:
        check("undo refuses other integrations' statistics", True, type(e).__name__)

    await hass.async_stop()
    await runner.cleanup()
    fails = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(fails)}/{len(RESULTS)} checks passed")
    sys.exit(1 if fails else 0)


try:
    asyncio.run(main())
except SystemExit as e:
    sys.stdout.flush(); os._exit(e.code or 0)
except BaseException:
    import traceback; traceback.print_exc(); sys.stdout.flush(); os._exit(2)
