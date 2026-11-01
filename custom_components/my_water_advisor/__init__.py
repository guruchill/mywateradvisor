"""My Water Advisor: imports hourly water use into long-term statistics."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
import logging
from zoneinfo import ZoneInfo

import voluptuous as vol

from homeassistant.components.recorder import get_instance
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.typing import ConfigType

from .api import MwaClient, MwaError
from .const import (
    API_TIMEZONE,
    CONF_BACKFILL_START,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_LOOKBACK_DAYS,
    CONF_METER_COUNT,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_POLL_HOURS,
    CONF_REHEARSAL,
    DOMAIN,
    SERVICE_DELETE_STATISTICS,
)
from .sync import WaterSync
from .transform import DataCheckError

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

DELETE_SCHEMA = vol.Schema({vol.Required("statistic_id"): cv.string})


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the explicit undo service."""

    async def _delete(call: ServiceCall) -> None:
        stat_id: str = call.data["statistic_id"]
        if not stat_id.startswith(f"{DOMAIN}:"):
            raise ServiceValidationError(
                f"Only statistics created by {DOMAIN} can be deleted here"
            )
        done = asyncio.Event()
        get_instance(hass).async_clear_statistics(
            [stat_id], on_done=lambda: hass.loop.call_soon_threadsafe(done.set)
        )
        try:
            async with asyncio.timeout(60):
                await done.wait()
        except TimeoutError as err:
            raise HomeAssistantError("Recorder did not confirm the delete in 60s") from err
        _LOGGER.warning("Deleted statistic %s on request", stat_id)

    hass.services.async_register(DOMAIN, SERVICE_DELETE_STATISTICS, _delete, DELETE_SCHEMA)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data, opts = entry.data, entry.options
    client = MwaClient(
        async_get_clientsession(hass),
        data[CONF_EMAIL],
        data[CONF_PASSWORD],
        data[CONF_DEVICE_ID],
    )
    sync = WaterSync(
        hass,
        client,
        meter_count=data[CONF_METER_COUNT],
        name=data[CONF_NAME],
        rehearsal=data[CONF_REHEARSAL],
        backfill_start=date.fromisoformat(data[CONF_BACKFILL_START]),
        lookback_days=int(opts[CONF_LOOKBACK_DAYS]),
        tz=ZoneInfo(API_TIMEZONE),
    )

    async def _run(_now=None) -> None:
        try:
            await sync.async_run()
        except (MwaError, DataCheckError) as err:
            _LOGGER.error("Sync failed, nothing written this run: %s", err)
        except Exception:
            _LOGGER.exception("Unexpected sync error, nothing written this run")

    entry.async_on_unload(
        async_track_time_interval(
            hass, _run, timedelta(hours=int(opts[CONF_POLL_HOURS])),
            name=f"{DOMAIN} sync", cancel_on_shutdown=True,
        )
    )
    entry.async_create_background_task(hass, _run(), f"{DOMAIN} initial sync")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Removing the integration does NOT delete imported statistics.
    return True
