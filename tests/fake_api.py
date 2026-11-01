"""Fake My Water Advisor API reproducing the behaviour observed on the real one."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiohttp import web

APP_ID = "3a869241-d476-40f6-a923-d789d63db11d"
TZ = ZoneInfo("America/Chicago")


class FakeApi:
    def __init__(self, first: date, now_local: datetime) -> None:
        self.first = first
        self.now_local = now_local  # naive local wall clock
        self.values: dict[tuple[date, int], float] = {}
        self.estimated: set[date] = set()
        self.valid_tokens: set[str] = set()
        self.logins = 0
        self.fail_hourly_after: int | None = None
        self.hourly_calls = 0
        self.extra_row_day: date | None = None
        self.daily_offset = 0.0
        d = first
        while d <= now_local.date():
            for h in range(24):
                # deterministic, with an irrigation-like spike at 07:00
                v = ((d.toordinal() * 31 + h * 17) % 23) / 2
                if h == 7 and d.weekday() in (1, 3):
                    v += 300
                self.values[(d, h)] = v
            d += timedelta(days=1)

    # API returns 0 for hours not yet happened / not in data
    def cons(self, d: date, h: int) -> float:
        if datetime(d.year, d.month, d.day, h) >= self.now_local.replace(minute=0, second=0, microsecond=0):
            return 0
        return self.values.get((d, h), 0)

    def total(self, start: date, end_incl: date) -> float:
        t = 0.0
        d = start
        while d <= end_incl:
            t += sum(self.cons(d, h) for h in range(24))
            d += timedelta(days=1)
        return t

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/consumer/login", self.login)
        app.router.add_get("/consumer/meters", self.meters)
        app.router.add_get("/consumption/hourly/{m}/{a}/{b}", self.hourly)
        app.router.add_get("/consumption/daily/{m}/{a}/{b}", self.daily)
        return app

    async def login(self, req: web.Request) -> web.Response:
        body = await req.json()
        assert req.headers.get("x-app-id") == APP_ID
        assert set(body) == {"email", "type", "pw", "deviceId", "osType", "app"}
        if body["pw"] != "secret":
            return web.json_response({"error": "Wrong email or password", "code": 401})
        self.logins += 1
        tok = f"tok{self.logins}"
        self.valid_tokens.add(tok)
        return web.json_response({"token": tok})

    def _auth(self, req: web.Request) -> bool:
        return req.headers.get("x-access-token") in self.valid_tokens

    async def meters(self, req: web.Request) -> web.Response:
        if not self._auth(req):
            return web.json_response({"error": "Unauthorized request, token does not exists", "code": 401}, status=401)
        return web.json_response([{"meterCount": 12345, "meterSn": "00000000", "fullAddress": "TEST"}])

    async def hourly(self, req: web.Request) -> web.Response:
        if not self._auth(req):
            return web.json_response({"error": "Unauthorized", "code": 401}, status=401)
        self.hourly_calls += 1
        if self.fail_hourly_after is not None and self.hourly_calls > self.fail_hourly_after:
            return web.Response(status=500, text="boom")
        a = date.fromisoformat(req.match_info["a"])
        b = date.fromisoformat(req.match_info["b"])
        end = a + timedelta(days=1) if a == b else b  # multi-day: end exclusive
        rows = []
        d = a
        while d < end:
            for h in range(24):
                rows.append({"meterCount": 12345, "estimationType": 1 if d in self.estimated else 0,
                             "meterStatusDesc": "-", "dateTime": f"{d.isoformat()}T{h:02d}:00:00",
                             "cons": self.cons(d, h)})
            if d == self.extra_row_day:  # simulate a 25-row fall-back day
                rows.append({"meterCount": 12345, "estimationType": 0, "meterStatusDesc": "-",
                             "dateTime": f"{d.isoformat()}T01:00:00", "cons": 5.0})
            d += timedelta(days=1)
        return web.json_response(rows)

    async def daily(self, req: web.Request) -> web.Response:
        if not self._auth(req):
            return web.json_response({"error": "Unauthorized", "code": 401}, status=401)
        a = date.fromisoformat(req.match_info["a"])
        b = date.fromisoformat(req.match_info["b"])
        rows = []
        d = a
        while d <= b:  # inclusive
            t = sum(self.cons(d, h) for h in range(24)) + (5.0 if d == self.extra_row_day else 0)
            rows.append({"meterCount": 12345, "consDate": f"{d.isoformat()}T00:00:00", "cons": t,
                         "estimationType": 0, "commonCons": 0.0, "meterStatusDesc": "-"})
            d += timedelta(days=1)
        if rows and self.daily_offset:
            rows[0]["cons"] += self.daily_offset
        return web.json_response(rows)
