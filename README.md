# My Water Advisor for Home Assistant

A custom integration that brings your hourly water consumption from the **My Water Advisor** customer portal (mywateradvisor2.com) into Home Assistant, so it shows up in the Energy dashboard's water section alongside your other utilities.

The integration logs in with your portal account, fetches the hourly readings for your meter, and writes them into Home Assistant's long-term statistics. It also backfills history from a date you choose, so your Energy dashboard has a full record from day one.

## Features

- **Hourly resolution.** Every completed hour is imported as its own statistics row, in gallons.
- **Historical backfill.** On first run it imports everything from your chosen start date, in 60-day requests.
- **Self-healing.** Each run re-fetches a recent window (1 to 30 days, your choice) so late corrections and estimated readings on the portal are picked up. If Home Assistant was off for longer than that window, the gap is filled too.
- **Verified before writing.** Each run checks that every completed day has a full set of hourly rows and that the hourly total matches the portal's own daily figure. If anything doesn't add up, nothing is written and the error is logged.
- **Atomic writes.** Each run's data is written in one recorder job, which is one database transaction.
- **Daylight saving handled.** The spring-forward hour and the repeated autumn hour map onto real UTC hours, so totals stay correct.
- **Multiple meters.** If your account has more than one meter, you pick one during setup. Add the integration again for each extra meter.
- **Rehearsal mode.** Try the import into a separate test statistic first, check it against the portal, and delete it with one action before importing for real.
- **Explicit clean-up.** A `my_water_advisor.delete_statistics` action removes the imported data when you want it gone.

## Requirements

- Home Assistant 2026.10.4 or newer.
- The `recorder` integration (enabled by default).
- A My Water Advisor account with at least one meter.

No extra Python packages are needed.

## Installation

### HACS

1. In HACS, open the three-dot menu and choose **Custom repositories**.
2. Add `https://github.com/guruchill/mywateradvisor` with type **Integration**.
3. Search for **My Water Advisor** in HACS and install it.
4. Restart Home Assistant.

### Manual

1. Copy the `custom_components/my_water_advisor` folder into your Home Assistant `config/custom_components/` directory.
2. Restart Home Assistant.

## Setup

Go to **Settings > Devices & services > Add integration** and search for **My Water Advisor**.

| Field | What it does |
| --- | --- |
| Email, Password | Your My Water Advisor login. Stored in the integration's configuration entry. |
| Statistic name | The display name of the statistic. Default: `Water (My Water Advisor)`. |
| Backfill start date | The first day of history to import. Default: 2025-11-01. |
| Rehearsal | When ticked, data goes into a separate test statistic. See [Rehearsal mode](#rehearsal-mode). |
| Check every (hours) | How often to fetch new data: 1, 2, 4, 6, 12, 18 or 24 hours. |
| Re-fetch the last (days) | How many recent days to re-fetch on every run: 1, 7, 14 or 30. |

If your account has more than one meter, a second step asks which meter to use. The meter list shows each meter's serial number and address.

The integration logs in during setup to confirm your credentials and find your meters. Setup fails with a clear message if the login is rejected, the portal can't be reached, or no meters are found.

Once set up, the first sync starts straight away in the background. Depending on how far back your backfill start date is, it can take a few minutes for the full history to appear.

### Changing the schedule later

Open the integration's entry under **Settings > Devices & services** and choose **Configure** to change how often it checks and how many days it re-fetches. The integration reloads with the new values.

## Adding it to the Energy dashboard

1. Go to **Settings > Dashboards > Energy**.
2. Under **Water consumption**, choose **Add water source**.
3. Select the statistic named as you set it during setup (default `Water (My Water Advisor)`).

The statistic's ID is `my_water_advisor:water_<meter>`, where `<meter>` is the portal's internal meter number. You can see it under **Developer tools > Statistics** by searching for the statistic name.

## How syncing works

Each scheduled run:

1. Works out the window to fetch: the configured re-fetch period, extended back to the last stored hour if that is older, and never earlier than the backfill start date.
2. Fetches hourly data for that window from the portal, plus the portal's daily totals for the same days.
3. Checks every completed day has 23 to 25 hourly rows and that its hourly total equals the daily figure. Today is only checked for its row count.
4. Recalculates the running total from the last stored value before the window and writes all rows in one transaction.

What gets imported:

- **Completed hours only.** The current hour and future hours are skipped, because the portal reports them as zero until they are complete.
- **Estimated readings** are imported exactly as the portal reports them, so your totals match your bill.
- **Spring-forward day:** the portal's reading for the non-existent 02:00 hour is added to 03:00.
- **Autumn repeated hour:** all volume reported for 01:00 goes into the first 01:00.

If the portal session expires, the integration logs in again automatically.

### What it stores

- One long-term statistic per meter, with one row per hour. That is 8,760 rows a year. Long-term statistics are kept permanently by Home Assistant.
- Your portal email, password and a generated device ID, in the configuration entry.
- No entities and no devices are created.

## Rehearsal mode

Rehearsal mode lets you prove the import before committing to it.

1. Take a Home Assistant backup first (**Settings > System > Backups**).
2. Add the integration with **Rehearsal** ticked. Data goes into `my_water_advisor:water_<meter>_rehearsal`, named `<your name> (rehearsal)`.
3. After a few minutes, open **Developer tools > Statistics** and compare the rehearsal statistic with the portal: the total since your backfill start date, and a few individual days against the portal's daily view.
4. When you are satisfied, remove the rehearsal:
   1. **Developer tools > Actions**: run `my_water_advisor.delete_statistics` with `statistic_id: my_water_advisor:water_<meter>_rehearsal`.
   2. Delete the rehearsal entry from **Settings > Devices & services**.
5. Add the integration again with **Rehearsal** unticked.

A rehearsal entry and a live entry for the same meter can exist at the same time.

## Deleting imported data

Removing the integration does **not** delete its statistics. That is deliberate: Home Assistant keeps long-term statistics permanently, and deleting history should be an explicit choice.

To delete, run the action `my_water_advisor.delete_statistics`:

```yaml
action: my_water_advisor.delete_statistics
data:
  statistic_id: my_water_advisor:water_12345
```

The action:

- deletes that statistic and all of its hourly rows;
- touches nothing else;
- refuses any statistic ID that doesn't start with `my_water_advisor:`.

## Limitations

- **Time zone.** The portal's hourly timestamps carry no time zone. The integration treats them as US Central time (`America/Chicago`), which is set in `const.py` and is not yet configurable. Confirmation of this against a live meter is still outstanding, so compare a few days against the portal (rehearsal mode is made for this) before relying on the hourly breakdown.
- **Gallons only.** The statistic is written in gallons, as the portal reports it.
- **Cloud polling.** Data arrives on the schedule you choose, not in real time.
- **Read-only.** The integration never changes anything in your portal account.

## Development

The integration lives in `custom_components/my_water_advisor/`:

| File | Purpose |
| --- | --- |
| `api.py` | Portal client: login, meters, hourly and daily consumption. Re-logs in on a 401. |
| `transform.py` | Pure functions: time zone mapping, consistency checks, running totals. |
| `sync.py` | One sync run: fetch window, verify, single statistics import. |
| `config_flow.py` | Setup and options UI. |
| `__init__.py` | Entry setup, scheduling, and the delete action. |

`tests/run_tests.py` is an end-to-end test. It boots a real Home Assistant core with the recorder on SQLite, points the integration at a fake portal API (`tests/fake_api.py`) that reproduces the real API's behaviour, and runs 30 checks covering daylight-saving mapping, backfill totals, re-runs, revisions, gap filling, failed-check rollback, re-login, the config flow and the delete action. It needs a Python environment with Home Assistant installed:

```sh
python tests/run_tests.py
```
