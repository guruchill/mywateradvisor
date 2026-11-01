"""Minimal client for the My Water Advisor customer portal API."""

from __future__ import annotations

import asyncio
from datetime import date
import logging
from typing import Any

import aiohttp

from . import const as _const
from .const import APP_ID

_LOGGER = logging.getLogger(__name__)

TIMEOUT = aiohttp.ClientTimeout(total=60)


class MwaError(Exception):
    """General API error."""


class MwaAuthError(MwaError):
    """Login rejected."""


class MwaClient:
    """Read-only client. Logs in again automatically on a 401."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        device_id: str,
        base_url: str | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._device_id = device_id
        self._base = base_url or _const.BASE_URL
        self._token: str | None = None
        self._login_lock = asyncio.Lock()

    async def login(self) -> None:
        """POST /consumer/login, as the web app's login page does."""
        body = {
            "email": self._email,
            "type": 1,
            "pw": self._password,
            "deviceId": self._device_id,
            "osType": 3,
            "app": APP_ID,
        }
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "x-app-id": APP_ID,
        }
        async with self._session.post(
            f"{self._base}/consumer/login", json=body, headers=headers, timeout=TIMEOUT
        ) as resp:
            try:
                data = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, ValueError) as err:
                raise MwaError(f"Login returned non-JSON (HTTP {resp.status})") from err
        if not isinstance(data, dict) or not data.get("token"):
            msg = data.get("error") if isinstance(data, dict) else None
            raise MwaAuthError(msg or f"Login failed (HTTP {resp.status})")
        self._token = data["token"]

    async def _get(self, path: str) -> Any:
        for attempt in (1, 2):
            if self._token is None:
                async with self._login_lock:
                    if self._token is None:
                        await self.login()
            headers = {"Accept": "application/json", "x-access-token": self._token}
            async with self._session.get(
                f"{self._base}{path}", headers=headers, timeout=TIMEOUT
            ) as resp:
                if resp.status == 401 and attempt == 1:
                    _LOGGER.debug("Token rejected, logging in again")
                    self._token = None
                    continue
                if resp.status != 200:
                    raise MwaError(f"GET {path} returned HTTP {resp.status}")
                return await resp.json(content_type=None)
        raise MwaError(f"GET {path} unauthorised after re-login")

    async def get_meters(self) -> list[dict[str, Any]]:
        data = await self._get("/consumer/meters")
        if not isinstance(data, list):
            raise MwaError("Unexpected /consumer/meters response")
        return data

    async def get_hourly(self, meter: int, start: date, end: date) -> list[dict[str, Any]]:
        """Hourly rows. Multi-day: end is exclusive. start == end returns that day."""
        data = await self._get(
            f"/consumption/hourly/{meter}/{start.isoformat()}/{end.isoformat()}"
        )
        if not isinstance(data, list):
            raise MwaError("Unexpected hourly response")
        return data

    async def get_daily(self, meter: int, start: date, end: date) -> list[dict[str, Any]]:
        """Daily rows. End is inclusive."""
        data = await self._get(
            f"/consumption/daily/{meter}/{start.isoformat()}/{end.isoformat()}"
        )
        if not isinstance(data, list):
            raise MwaError("Unexpected daily response")
        return data
