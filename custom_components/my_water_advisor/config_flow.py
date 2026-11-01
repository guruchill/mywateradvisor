"""Config and options flow for My Water Advisor."""

from __future__ import annotations

from typing import Any
import uuid

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    DateSelector,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MwaAuthError, MwaClient, MwaError
from .const import (
    CONF_BACKFILL_START,
    CONF_DEVICE_ID,
    CONF_EMAIL,
    CONF_LOOKBACK_DAYS,
    CONF_METER_COUNT,
    CONF_METER_SN,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_POLL_HOURS,
    CONF_REHEARSAL,
    DEFAULT_BACKFILL_START,
    DEFAULT_NAME,
    DOMAIN,
    LOOKBACK_DAYS_OPTIONS,
    POLL_HOURS_OPTIONS,
)


def _select(options: list[str]) -> SelectSelector:
    return SelectSelector(
        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
    )


# No defaults for poll interval / lookback: you choose them at setup.
OPTIONS_FIELDS = {
    vol.Required(CONF_POLL_HOURS): _select(POLL_HOURS_OPTIONS),
    vol.Required(CONF_LOOKBACK_DAYS): _select(LOOKBACK_DAYS_OPTIONS),
}


class MwaConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._meters: list[dict[str, Any]] = []
        self._device_id = str(uuid.uuid4())

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            client = MwaClient(
                async_get_clientsession(self.hass),
                user_input[CONF_EMAIL],
                user_input[CONF_PASSWORD],
                self._device_id,
            )
            try:
                await client.login()
                self._meters = await client.get_meters()
            except MwaAuthError:
                errors["base"] = "invalid_auth"
            except MwaError:
                errors["base"] = "cannot_connect"
            else:
                if not self._meters:
                    errors["base"] = "no_meters"
                else:
                    self._input = user_input
                    if len(self._meters) == 1:
                        return await self._create(self._meters[0])
                    return await self.async_step_meter()

        schema = vol.Schema(
            {
                vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
                vol.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(CONF_NAME, default=DEFAULT_NAME): str,
                vol.Required(CONF_BACKFILL_START, default=DEFAULT_BACKFILL_START.isoformat()): DateSelector(),
                vol.Required(CONF_REHEARSAL, default=False): BooleanSelector(),
                **OPTIONS_FIELDS,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input or {}),
            errors=errors,
        )

    async def async_step_meter(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Only shown if the account has more than one meter."""
        by_label = {
            f"{m['meterSn']} - {m.get('fullAddress', '')}": m for m in self._meters
        }
        if user_input is not None:
            return await self._create(by_label[user_input["meter"]])
        return self.async_show_form(
            step_id="meter",
            data_schema=vol.Schema({vol.Required("meter"): _select(list(by_label))}),
        )

    async def _create(self, meter: dict[str, Any]) -> ConfigFlowResult:
        u = self._input
        rehearsal = bool(u[CONF_REHEARSAL])
        await self.async_set_unique_id(
            f"{meter['meterCount']}_{'rehearsal' if rehearsal else 'live'}"
        )
        self._abort_if_unique_id_configured()
        title = u[CONF_NAME] + (" (rehearsal)" if rehearsal else "")
        return self.async_create_entry(
            title=title,
            data={
                CONF_EMAIL: u[CONF_EMAIL],
                CONF_PASSWORD: u[CONF_PASSWORD],
                CONF_DEVICE_ID: self._device_id,
                CONF_METER_COUNT: int(meter["meterCount"]),
                CONF_METER_SN: str(meter.get("meterSn", "")),
                CONF_NAME: u[CONF_NAME],
                CONF_BACKFILL_START: u[CONF_BACKFILL_START],
                CONF_REHEARSAL: rehearsal,
            },
            options={
                CONF_POLL_HOURS: u[CONF_POLL_HOURS],
                CONF_LOOKBACK_DAYS: u[CONF_LOOKBACK_DAYS],
            },
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MwaOptionsFlow:
        return MwaOptionsFlow()


class MwaOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                vol.Schema(OPTIONS_FIELDS), self.config_entry.options
            ),
        )
