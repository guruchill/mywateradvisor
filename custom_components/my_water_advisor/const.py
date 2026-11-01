"""Constants for the My Water Advisor integration."""

from datetime import date

DOMAIN = "my_water_advisor"

# API details taken from the mywateradvisor2.com web app bundle.
BASE_URL = "https://customerportal-api.harmonyencoremdm.com"
APP_ID = "3a869241-d476-40f6-a923-d789d63db11d"

# Time zone of the naive hourly timestamps returned by the API.
# PENDING CONFIRMATION by the tap test (31 Oct 2026 and after 2 Nov 2026).
# Do not run the live backfill until this is confirmed.
API_TIMEZONE = "America/Chicago"

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_DEVICE_ID = "device_id"
CONF_METER_COUNT = "meter_count"
CONF_METER_SN = "meter_sn"
CONF_NAME = "name"
CONF_REHEARSAL = "rehearsal"
CONF_BACKFILL_START = "backfill_start"
CONF_POLL_HOURS = "poll_hours"
CONF_LOOKBACK_DAYS = "lookback_days"

DEFAULT_NAME = "Water (My Water Advisor)"
DEFAULT_BACKFILL_START = date(2025, 11, 1)

POLL_HOURS_OPTIONS = ["1", "2", "4", "6", "12", "18", "24"]
LOOKBACK_DAYS_OPTIONS = ["1", "7", "14", "30"]

# Largest hourly date range requested in one API call (60 days verified).
MAX_CHUNK_DAYS = 60

UNIT_GALLONS = "gal"
UNIT_CLASS_VOLUME = "volume"

SERVICE_DELETE_STATISTICS = "delete_statistics"
