# RM Migration (API only)

A Python helper that migrates dashboard **subscriptions** to **Report Manager** reports, using only the Sisense REST API.

It needs nothing beyond an admin API token: no database access, tunnels, or direct writes to internal collections.

For every dashboard your API user can access, the script:

1. Fetches the dashboard (including its legacy `subscription` and `shares`).
2. Skips it unless the subscription is `active` and has at least one subscribed recipient.
3. Builds an equivalent Report Manager report payload (schedule, recipients, report type) from the subscription and shares.
4. Creates the report via `POST /api/v1/report_manager/reports`, or, by default, just logs what it *would* create (see [Dry run vs. `--apply`](#dry-run-vs---apply)).

## How the mapping works

| Report field | Comes from | Notes |
| --- | --- | --- |
| `name` | the dashboard's own `title`, plus the optional `report_name_prefix` / `report_name_suffix` | See [Customizing report names](#3-customizing-report-names-optional). Report names must be unique. If several dashboards share a title, the extra ones get ` (2)`, ` (3)`, and so on, in a stable order across runs. |
| `enabled` | `subscription.active` | |
| `schedule.cron` / `schedule.timezone` | `subscription.schedule` / `subscription.tzName` | Converted to Report Manager's cron format (e.g. `0 0 8 * * 1` → `0 8 * * mon`). Empty for subscriptions triggered by data updates. |
| `schedule.time.startDate` | `subscription.startAt` | Converted from a bare date (`"2025-08-28"`) to a full ISO date-time with the subscription timezone's offset, since the API's `format: date-time` validation rejects a bare date. |
| `schedule.time.every` | fixed value `1` | Only included for `"day"` frequency, matching a real report's shape. A `"week"` frequency report uses `days` instead. |
| `dashboards[0].owner` / `.title` / `.shares` | the dashboard's own `owner` / `title` / `shares` | Copied through unmodified. A real report embeds these alongside `dashboardOid`, not just the oid on its own. |
| `dashboards[0].name` | `{dashboard title} ({owner's email})` | Falls back to just the title when the owner id doesn't resolve to a known user. |
| `recipients` | `shares[]` where `subscribe: true` | Group shares resolve from just `{_id, type}`. **User** shares are resolved against a users map (all users fetched once up front via `pysisense`'s `AccessManagement.get_users_all()`, indexed by `USER_ID`) to get a real `email`/`firstName`/`lastName`. A bare `_id` alone comes back as an unresolved recipient. |
| `reportType.PDF` | `subscription.emailSettings.isPdf` / `subscription.reportType.inline` | Legacy "Embedded in Email" subscriptions have no Report Manager equivalent and map to PDF. `subscription.reportType.pdf` (the legacy rendering options) is never forwarded; the two systems don't share that sub-schema. |
| `events` | `subscription.isDataChange` / `subscription.type` | `["every_ec_update"]` for data-change-triggered subscriptions, `[]` otherwise. |
| `runOnFinish`, and the rest of `reportType`'s boolean flags | fixed defaults | Required by the Report Manager schema. Omitting them causes the report's edit page to fail to render (white screen) in the Sisense UI. |

Every dashboard is logged with why it was kept or skipped: no `subscription` at all (`candidate_skipped_no_subscription`), an inactive one (`candidate_skipped_subscription_inactive`), or an active subscription with no subscribed shares (`candidate_skipped_no_recipients`). Report Manager requires either recipients or an "Archive to" path, and this script sets neither, so an empty `recipients` list would fail report creation. A dashboard that passes all three checks is logged as `candidate_found`.

## Requirements

- **Python 3.13+**
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- A Sisense user with admin-level dashboard access and a valid API token
- The Report Manager add-on installed and enabled

## Setup

### 1. Install dependencies

Using `uv` (recommended, respects `.python-version`):

```bash
uv sync
```

Or using plain `pip` inside a virtualenv (requires Python 3.13+):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure `config.yaml`

Copy the example file and fill in your own values. The script always reads `config.yaml` from its own folder (next to `main.py`), so you can run it from any directory:

```bash
cp config.example.yaml config.yaml
```

```yaml
domain: "192.168.1.100"   # IP address or hostname, no protocol, no port
is_ssl: false             # true for HTTPS (port 443), false for HTTP (default port 30845)
# port: 30845             # optional, HTTP port when is_ssl is false (omit to use 30845)
token: "your-api-token-here"  # Sisense Admin API token
# operating_system: linux  # "linux" (default) or "windows", controls OS-specific API endpoints

# report_name_prefix: "Subscription -"   # optional, see "Customizing report names"
# report_name_suffix: "(Migrated)"       # optional
```

You can follow the pysisense [definitions](https://github.com/sisense/pysisense/blob/main/config.yaml.example).

> **Never commit a real token**, treat it like a password.

### 3. Customizing report names (optional)

By default each report is named exactly like its dashboard. Set `report_name_prefix` and/or `report_name_suffix` in `config.yaml` to add your own text before or after the dashboard title. The parts are joined with a single space.

| `report_name_prefix` | `report_name_suffix` | Dashboard `Sales` becomes |
| --- | --- | --- |
| *(not set)* | *(not set)* | `Sales` |
| `"Subscription -"` | *(not set)* | `Subscription - Sales` |
| *(not set)* | `"- Report"` | `Sales - Report` |
| `"[RM]"` | `"(Migrated)"` | `[RM] Sales (Migrated)` |

The banner shows the resulting pattern (`Report name : Subscription - <dashboard title>`) before anything runs, so check it in a dry run first. If two dashboards share a title, the ` (2)` goes after the title and before the suffix, e.g. `[RM] Sales (2) (Migrated)`.

Pick the naming before the first `--apply`. Report Manager detects existing reports by name, so changing the prefix or suffix later makes a re-run create a second copy of every report instead of skipping them.

## Running the script

### Dry run vs. `--apply`

By default the script runs as a **dry run**: it fetches and filters dashboards, builds every report payload, and logs/prints what it would create, but makes **no** changes.

```bash
uv run python main.py
```

Pass `--apply` to actually create the reports:

```bash
uv run python main.py --apply
```

### Limiting the run to specific dashboards

Pass `--dashboards` with a comma-separated list of dashboard `oid`s (or a single `oid`):

```bash
uv run python main.py --dashboards=64f1a2b3c4d5e6f7a8b9c0d1
uv run python main.py --dashboards=64f1a2b3c4d5e6f7a8b9c0d1,650a1b2c3d4e5f6a7b8c9d0e --apply
```

Useful for re-testing a single dashboard without re-running the migration over the whole environment. Any requested `oid` not found (or not accessible to the API user) is printed as a warning and logged as `dashboards_filtered_by_id`.

### Limiting the run to the token's own dashboards

By default the script considers every dashboard the API user can access. For an admin token that is every dashboard in the environment. Pass `--owned` to only migrate dashboards owned by the token's own user:

```bash
uv run python main.py --owned
uv run python main.py --owned --apply
```

The script resolves the token's identity via `GET /api/users/loggedin` (`AccessManagement.get_my_user()`), then keeps only dashboards whose `owner` matches that user's `_id`. It can be combined with `--dashboards`; both filters apply.

### Debugging a failing dashboard with `--debug`

Pass `--debug` to print each candidate dashboard's raw data and the report body built from it, and, if a report creation fails, the full API response:

```bash
uv run python main.py --dashboards=<failing-oid> --apply --debug
```

### What you will see

- A banner showing the **environment**, the **token's own email/user id**, the **mode** (dry run vs apply), the `--owned` scope, the report name pattern, and the log file path.
- A progress bar while paging through dashboards (`Fetching dashboards`), 50 at a time.
- A count of dashboards with an active subscription and subscribed recipients.
- A second progress bar while migrating (`Migrating subscriptions`).
- Color-coded per-dashboard lines: green `Created report ...`, cyan `[dry run] Would create report for ...`, yellow `Skipped '...' - report already exists (...)`, or red `Failed to create report for ...`.
- A closing summary tallying created/already-existed/failed (apply mode) or would-create (dry run).

Colors are skipped when `NO_COLOR` is set or stdout is not a terminal.

## Known limitations

### Reports are owned by the token user

Report Manager's REST API has no supported way to set or change a report's owner. Every report is created as owned by the user whose token made the call. For an admin token migrating dashboards owned by many users, every migrated report ends up owned by the admin. Recipients, schedule and dashboard are all migrated correctly; only the owner differs.

### Re-runs may return `500` instead of "already exists"

Re-running `--apply` over already-migrated dashboards is expected to return a `400` "report already exists", which the script logs as `report_create_skipped_existing` (not a failure). However, some environments return a bare `500 Internal Server Error` for the same name collision, which cannot be told apart from a genuine failure through the API alone. To avoid this, use `--dashboards` to re-run only the dashboards that actually failed, rather than re-running the whole environment.

## Logs

Every run writes a timestamped log to `logs/migration_<UTC-timestamp>.log`, one JSON line per action. Actions include `run_started`, `mode`, `run_context` (environment base URL, SSL/OS, the token's own email/user id, mode, `--owned`, report name prefix/suffix), `dashboards_fetched`, `dashboards_filtered_by_token_owner`, `dashboards_filtered_by_id`, `dashboard_skipped_empty`, `candidate_skipped_no_subscription`, `candidate_skipped_subscription_inactive`, `candidate_skipped_no_recipients`, `candidate_found`, `candidate_name_disambiguated`, `report_create_attempted`, `report_created`, `report_create_skipped_existing`, `report_create_skipped_dry_run`, `report_create_failed`, and `no_dashboard_found`.

The script does not implement an undo, but the log has what's needed to build one: grep for `"action": "report_created"` to get the `report_id` (and source `dashboard_oid`/`dashboard_title`) of every report created.

`logs/pysisense.log` is the `pysisense` library's own low-level HTTP debug log, not specific to a single run. pysisense writes it under the folder you run the script from, so it ends up next to the run logs only when you run from the script's folder.

## Testing

The logic in `main.py` is covered by an offline test suite under `tests/`. No real Sisense server is touched; `SisenseClient` and `AccessManagement` are faked (see `tests/conftest.py`).

```bash
uv sync --group dev
uv run pytest
```

## Troubleshooting

**`422` on creation, `INVALID_FORMAT` for `schedule.time.startDate`**

The subscription's `startAt` was not in the date-time format the API expects. The script already converts it (`_to_iso_datetime_with_tz`). If you still see this, check the dashboard's raw subscription data for an unexpected `startAt` format.

**`400` "Report with the same name already exists"**

Expected on a re-run over dashboards already migrated. Logged as `report_create_skipped_existing`, not a failure.

**Report created, but its edit page is a blank/white screen in the Sisense UI**

Usually means a required field was left out of the report body (`events`, `runOnFinish`, a `reportType` key, or a recipient's `_id`/`email` missing or unresolved). Compare the report's payload in the log against a known-working report's shape.

**"no response from server" or `502`/`504` on creation**

A network error, timeout, or load-balancer error page for one dashboard. The script logs it as `report_create_failed` (with the error text or the first 500 characters of the response body) and carries on with the remaining dashboards. Re-run just those dashboards later with `--dashboards`. For the underlying network error, check `logs/pysisense.log`.

**`500` "Internal Server Error" on creation**

First check whether a report with the same name already exists (see [Re-runs may return `500`](#re-runs-may-return-500-instead-of-already-exists)). If not, use `--debug` to print the exact payload and compare it against a working report for a different dashboard. Two gaps were found and fixed this way:

- `dashboards[0]` needs the dashboard's own `owner`, `title`, and `shares` embedded alongside `dashboardOid`, not just the oid.
- `subscription.reportType.pdf` (the legacy PDF rendering options) must never be forwarded into `reportType.pdf`.

If a dashboard still fails after that, the cause is likely server-side for that dashboard; grep the server logs for the failing response's `logref`.
