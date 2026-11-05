"""
Migrate dashboard subscriptions to Report Manager reports.

This script checks every dashboard the API user can access. For each one
with an active subscription, it builds and creates an equivalent Report
Manager report via the /api/v1/report_manager/reports endpoint.

By default the script runs as a dry run: it fetches, filters, and builds the
report payloads and logs what it would do, but makes no changes. Pass
--apply to actually create the reports.

Pass --dashboards=id,id,id (or a single --dashboards=id) to limit the run to
specific dashboard oid(s) instead of every dashboard the API user can access.

Pass --owned to limit the run to dashboards owned by the config.yaml token's
own user, instead of every dashboard the API user can access (an admin token
can access dashboards owned by other users too).

Note: Report Manager's REST API always creates reports owned by the token
user (config.yaml's API token), and has no supported way to change that.

Every run writes a timestamped JSON log under logs/, one line per action.
This records every dashboard considered (including why it was skipped, for
example no subscription, an inactive subscription, or no subscribed
recipients) and every report created, skipped, or failed, including the
new report's _id and source dashboard oid. That is enough to manually undo
a run later. The script does not implement the undo itself.

Before running:
1. Edit config.yaml with your Sisense domain and API token.
2. Install dependencies (pysisense, requests, tqdm).
"""
import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from pysisense import AccessManagement, SisenseClient
from tqdm import tqdm

LOG_DIR = Path(__file__).parent / "logs"
CONFIG_PATH = Path(__file__).parent / "config.yaml"
_RULE_WIDTH = 64

# Colors are skipped when NO_COLOR is set or stdout is not a real terminal
# (for example, piped to a file). This keeps logs and CI output plain text.
_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()
_BOLD, _DIM, _RED, _GREEN, _YELLOW, _CYAN = "1", "2", "31", "32", "33", "36"


def _style(text, *codes):
    if _NO_COLOR:
        return text
    return "".join(f"\033[{c}m" for c in codes) + text + "\033[0m"


def setup_run_logger():
    """Create one log file per run, with a JSON line per action.

    A later undo script can grep the file, for example by
    `"action": "report_created"`, to get the report _id and dashboard oid
    needed to reverse a run."""
    LOG_DIR.mkdir(exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = LOG_DIR / f"migration_{run_id}.log"

    logger = logging.getLogger(f"rm_migration.{run_id}")
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler(log_path)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)

    logger.info(json.dumps({"action": "run_started", "run_id": run_id}))
    return logger, log_path


def print_banner(*, environment, is_ssl, operating_system, user_email, user_id, mode, owned_only, log_path,
                 name_format="<dashboard title>"):
    """Print the header shown before any work starts.

    Shows the environment, the authenticated identity (email), and whether
    the run will actually make changes."""
    rule = _style("=" * _RULE_WIDTH, _CYAN)
    mode_text = (
        _style("APPLY - reports will be created", _BOLD, _RED)
        if mode == "apply"
        else _style("DRY RUN - no changes will be made", _BOLD, _GREEN)
    )
    scope_text = (
        _style(f"owned by {user_email or user_id} only", _CYAN)
        if owned_only
        else "every dashboard the token can access"
    )

    print(rule)
    print(_style("  Subscription to Reports", _BOLD))
    print(rule)
    print(f"  Environment : {_style(environment, _BOLD)}  ({'HTTPS' if is_ssl else 'HTTP'} - {operating_system})")
    print(f"  Token user  : {_style(user_email or 'unknown', _BOLD)}  ({user_id or 'unknown'})")
    print(f"  Mode        : {mode_text}")
    print(f"  Dashboards  : {scope_text}")
    print(f"  Report name : {_style(name_format, _BOLD)}")
    print(f"  Log file    : {_style(str(log_path), _DIM)}")
    print(rule)


def print_summary(counts, mode):
    """Print a closing tally of the run's outcome."""
    rule = _style("-" * _RULE_WIDTH, _CYAN)
    print(rule)
    print(_style("  Summary", _BOLD))
    if mode == "apply":
        print(f"  Created         : {_style(str(counts['created']), _BOLD, _GREEN)}")
        print(f"  Already existed : {_style(str(counts['skipped_existing']), _YELLOW)}")
        failed = counts['failed']
        print(f"  Failed          : {_style(str(failed), _BOLD, _RED) if failed else failed}")
    else:
        print(f"  Would create    : {_style(str(counts['dry_run']), _BOLD, _CYAN)}")
        print(_style("  Pass --apply to actually create these reports.", _DIM))
    print(rule)


_DOW_NAMES = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']


def _dow_field_to_names(dow_field):
    """Convert the legacy cron's numeric day-of-week field (for example
    "1" or "1,3") to a list of day-name abbreviations."""
    if dow_field in ('*', ''):
        return []
    names = []
    for token in dow_field.split(','):
        token = token.strip()
        if token.isdigit():
            names.append(_DOW_NAMES[int(token) % 7])
    return names


def _to_iso_datetime_with_tz(date_str, tz_name):
    """Convert a subscription's bare "startAt" date (for example
    "2025-12-05") into the full date-time Report Manager's
    schedule.time.startDate requires, with the given timezone's actual UTC
    offset (for example "2025-12-05T00:00:00-08:00")."""
    naive = datetime.fromisoformat(date_str.split('T')[0])
    try:
        tz = ZoneInfo(tz_name) if tz_name else timezone.utc
    except Exception:
        tz = timezone.utc
    return naive.replace(tzinfo=tz).isoformat(timespec='milliseconds')


def _build_schedule(subscription):
    """Convert the legacy subscription's cron into Report Manager's
    schedule shape.

    The legacy "schedule" field is a 6-field, seconds-first cron with a
    numeric day-of-week (for example "0 0 0 * * 1"). Report Manager uses a
    5-field cron with day-name abbreviations (for example "0 0 * * mon")."""
    parts = (subscription.get('schedule') or '').split()
    if len(parts) != 6:
        return {}

    _, minute_s, hour_s, dom, month, dow = parts
    minute = int(minute_s) if minute_s.isdigit() else 0
    hour = int(hour_s) if hour_s.isdigit() else 0
    dow_names = _dow_field_to_names(dow)

    if dow_names:
        frequency = "week"
        days = {name: (name in dow_names) for name in _DOW_NAMES}
        cron = f"{minute} {hour} * * {','.join(dow_names)}"
    elif dom != '*':
        frequency = "month"
        days = None
        cron_month = str(int(month) + 1) if month.isdigit() else month
        cron = f"{minute} {hour} {dom} {cron_month} *"
    else:
        frequency = "day"
        days = None
        cron = f"{minute} {hour} * * *"

    time_body = {
        "frequency": frequency,
        "onTime": hour,
        "onTime_min": minute,
    }
    if frequency == "day":
        # "every N" is the recurrence interval (every N days). A real,
        # working "day" frequency report has this field; a real "week"
        # frequency report does not (it uses "days" instead), so this is
        # only added for "day". This script only ever migrates a plain,
        # non-skipping cadence, so it's always 1.
        time_body["every"] = 1
    if days is not None:
        time_body["days"] = days
    if subscription.get('startAt'):
        time_body["startDate"] = _to_iso_datetime_with_tz(
            subscription.get('startAt'), subscription.get('tzName')
        )

    return {
        "cron": cron,
        "timezone": subscription.get('tzName', ''),
        "time": time_body,
    }


def _is_existing_report_error(response_body):
    """Return True for the 400 the API returns when a report with the
    target name already exists.

    This is expected when rerunning over already migrated dashboards. It
    is not a real failure."""
    error = (response_body or {}).get('error') or {}
    if error.get('clientMessage', {}).get('id') == '@reportManager/error/api/report/exist':
        return True
    return 'ExistingReportsError' in (error.get('moreInfo') or {})


def _existing_report_id(response_body):
    error = (response_body or {}).get('error') or {}
    existing = (error.get('moreInfo') or {}).get('ExistingReportsError') or []
    if existing:
        return existing[0].get('_id')
    ids = (error.get('clientMessage', {}).get('values') or {}).get('ids') or []
    return ids[0] if ids else None


def _response_body(response):
    """Return a report-creation response's parsed JSON body, never raising.

    A None response (pysisense's result for a network error or timeout)
    and a body that isn't JSON (for example an HTML error page from a load
    balancer) both come back as a small dict describing what happened, so
    it can still be logged and the run can carry on."""
    if response is None:
        return {"error": "No response from server (network error or timeout). See logs/pysisense.log."}
    try:
        return response.json()
    except ValueError:
        return {"error": "Response body is not JSON.", "body": (response.text or "")[:500]}


def _created_report_id(body):
    """Return the new report's _id from a successful creation response,
    or None if the body doesn't have the expected shape."""
    if isinstance(body, list):
        body = body[0] if body else None
    return body.get('_id') if isinstance(body, dict) else None


def map_subscription_to_report(d, users_map, *, name_override=None):
    """Build a Report Manager report payload from one dashboard's legacy
    subscription (d['subscription']) and shares (d['shares']).

    `d` is a single dashboard object as returned by
    GET /api/v1/dashboards/admin (see get_all_dashboards_paginated).
    `users_map` is a dict of user `USER_ID` to user object (see
    gen_users_map). It is used to resolve a share's user id into an
    account (email, first name, last name) for "user" type recipients,
    without making a separate API call per share.
    `name_override`, when given, replaces the default report name (the
    dashboard's own title). Used to disambiguate two different dashboards
    that share the same title (see _disambiguate_report_names), since
    Report Manager's own "report already exists" check is by name, not by
    dashboard oid.
    """
    subscription = d.get('subscription') or {}
    context = subscription.get('context') or {}
    report_type = subscription.get('reportType') or {}
    email_settings = subscription.get('emailSettings') or {}

    recipients = []
    for s in d.get('shares', []):
        if not s.get('subscribe'):
            continue

        share_id = s.get('shareId')
        share_type = s.get('type', 'user')

        # Groups resolve fine from just an "_id". A "user" recipient needs
        # the actual account (email, name). A bare shareId comes back as
        # an unresolved recipient with "_id": "".
        if share_type == 'user' and share_id:
            user = users_map.get(share_id, {})
            recipients.append({
                "_id": share_id,
                "email": user.get('EMAIL'),
                "firstName": user.get('FIRST_NAME'),
                "lastName": user.get('LAST_NAME'),
                "type": "user",
            })
        else:
            recipients.append({
                "_id": share_id,
                "type": share_type,
            })

    # Report Manager's own reports embed the dashboard's owner, title, and
    # full shares list alongside the oid, not just the oid on its own. The
    # dashboard's own display name here is "{title} ({owner's email})"
    # when the owner resolves to a known user, matching a real report's
    # shape for this same dashboard/subscription.
    dashboard_owner_id = d.get('owner')
    dashboard_title = d.get('title')
    owner_email = users_map.get(dashboard_owner_id, {}).get('EMAIL')
    dashboard_display_name = f"{dashboard_title} ({owner_email})" if owner_email else dashboard_title

    # Data-change-triggered subscriptions ("isDataChange": true, usually
    # "type": "onUpdate") fire on data update, not on a cron. Their
    # "schedule" field is just a placeholder. Real cron types ("daily",
    # "monthly", and so on) are converted with _build_schedule.
    is_data_change_triggered = subscription.get('isDataChange', False) or subscription.get('type') == 'onUpdate'
    schedule = {}
    if not is_data_change_triggered and subscription.get('schedule'):
        schedule = _build_schedule(subscription)

    # report_type.inline marks a legacy "Embedded in Email" subscription.
    # Report Manager has no inline-embed equivalent, so it maps to PDF
    # instead of a "dashboard link" (URL) report. The legacy subscription's
    # own reportType.pdf rendering options are not forwarded: a real report
    # for this same subscription has PDF: true with no nested "pdf" object,
    # so the two systems don't share that sub-schema.
    report_type_body = {
        "PDF": bool(email_settings.get('isPdf', False)) or bool(report_type.get('inline', False)),
        "CSV": False,
        "URL": False,
        "XLS": False,
        "JASPER_PDF": False,
        "JASPER_XLS": False,
    }

    return {
        "name": name_override or d.get('title'),
        "enabled": subscription.get('active', True),
        "schedule": schedule,
        "dashboards": [
            {
                "dashboardOid": context.get('dashboardid', d.get('oid')),
                "owner": dashboard_owner_id,
                "title": dashboard_title,
                "name": dashboard_display_name,
                "shares": d.get('shares', []),
            }
        ],
        "recipients": recipients,
        "events": ["every_ec_update"] if is_data_change_triggered else [],
        "priority": "normal",
        "runOnFinish": {
            "fileShare": {
                "overwriteExisting": False,
            }
        },
        "reportType": report_type_body,
    }


def _has_subscribed_recipient(d):
    """Return True if at least one of the dashboard's shares has
    `subscribe: true`.

    A Report Manager report needs either recipients or an "Archive to"
    path. This script never sets an archive path, so an active
    subscription with no subscribed shares fails report creation with
    "Specify either 'Archive to' path or recipients"."""
    return any(s.get('subscribe') for s in d.get('shares', []))


def _disambiguate_report_names(candidates):
    """Return {dashboard_oid: report_name} for candidates whose default
    report name (the dashboard's own title) collides with another
    candidate's.

    Report Manager's "report already exists" check is by name, not by
    dashboard oid, so two different dashboards that happen to share a
    title would otherwise generate the same report name: the second one
    to run would be silently treated as already migrated
    (report_create_skipped_existing) even though its own subscription
    was never actually created.

    Within a group of colliding titles, the dashboard with the
    lexicographically smallest oid keeps the plain name (so a dashboard
    already migrated under that name in an earlier run keeps matching
    it); the rest get " (2)", " (3)", and so on appended. Ordering by
    oid, rather than by fetch order, keeps this stable across runs.
    """
    by_name = {}
    for d in candidates:
        name = d.get('title')
        by_name.setdefault(name, []).append(d.get('oid'))

    overrides = {}
    for name, oids in by_name.items():
        if len(oids) < 2:
            continue
        for index, oid in enumerate(sorted(oids), start=1):
            if index > 1:
                overrides[oid] = f"{name} ({index})"
    return overrides


def _read_name_affixes(config):
    """Read the optional 'report_name_prefix' / 'report_name_suffix'
    entries from config.yaml.

    Returns (prefix, suffix), each a stripped string ('' when unset).
    Raises ValueError for a non-string value, so a typo like
    `report_name_prefix: 2025` fails before any report is created."""
    affixes = []
    for key in ("report_name_prefix", "report_name_suffix"):
        value = (config or {}).get(key)
        if value is None:
            affixes.append("")
        elif not isinstance(value, str):
            raise ValueError(f"'{key}' in config.yaml must be text, got {value!r}.")
        else:
            affixes.append(value.strip())
    return tuple(affixes)


def build_report_name(base_name, prefix="", suffix=""):
    """Join the optional prefix and suffix around a report's base name
    (the dashboard title, or its disambiguated variant), separated by a
    single space. For example, prefix "Subscription -" and base "Sales"
    give "Subscription - Sales"."""
    return " ".join(part for part in (prefix, base_name, suffix) if part)


def get_all_dashboards_paginated(api_client, page_size=50):
    """Fetch every dashboard visible to the authenticated user, 50 at a
    time, using skip/limit.

    Paging keeps each request small. This avoids timeouts on a slow load
    balancer and scales to environments with hundreds of dashboards."""
    found_dashboards = []
    seen_oids = set()
    skip = 0
    with tqdm(desc="Fetching dashboards", unit=" dashboard") as pbar:
        while True:
            response = api_client.get(
                "/api/v1/dashboards/admin",
                params={"dashboardType": "owner", "skip": skip, "limit": page_size},
            )
            if response is None:
                raise RuntimeError("Failed to retrieve dashboards: no response from server.")
            if not response.ok:
                raise RuntimeError(
                    f"Failed to retrieve dashboards. HTTP {response.status_code}: {response.text[:500]}"
                )
            page = response.json()
            if not isinstance(page, list):
                raise TypeError(f"Failed to retrieve dashboards. Unexpected response: {page}")

            # Keep only dashboards not seen on an earlier page. This
            # prevents an infinite loop if a server ignores skip and
            # resends the same list every time.
            new_dashboards = [d for d in page if d.get("oid") not in seen_oids]
            found_dashboards.extend(new_dashboards)
            seen_oids.update(d.get("oid") for d in new_dashboards)
            pbar.update(len(new_dashboards))

            # Stop on a short page (or a server that ignored limit and
            # returned everything at once), or when a page adds nothing new.
            if len(page) != page_size or not new_dashboards:
                break
            skip += page_size

    return found_dashboards


def gen_users_map(access_management):
    """Fetch every user once with pysisense's
    AccessManagement.get_users_all() and index them by "USER_ID".

    This lets map_subscription_to_report resolve "user" type shares with a
    dict lookup, instead of a GET /api/v1/users/{shareId} call per share."""
    users = access_management.get_users_all()
    return {u["USER_ID"]: u for u in users if isinstance(u, dict) and u.get("USER_ID")}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Migrate dashboard subscriptions to Report Manager reports."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually create the reports. Without this flag, the script only "
             "logs/prints what would be created (dry run) and makes no changes.",
    )
    parser.add_argument(
        "--dashboards",
        type=str,
        default=None,
        help="Comma-separated dashboard oid(s) to limit the run to, e.g. "
             "--dashboards=64f1...,650a... or --dashboards=64f1.... Without "
             "this flag, every dashboard the API user can access is considered.",
    )
    parser.add_argument(
        "--owned",
        action="store_true",
        help="Limit the run to dashboards owned by config.yaml's own token "
             "user, instead of every dashboard the API user can access "
             "(which, for an admin token, includes dashboards owned by "
             "other users too).",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print each candidate dashboard's raw data and the report "
             "body built from it, and on a failed report creation, print "
             "the full API response instead of just the status code. Use "
             "this to compare a failing dashboard against a working one "
             "when the API returns an opaque error (e.g. a 500 with no "
             "useful message).",
    )
    return parser.parse_args()


def _parse_dashboard_ids(raw):
    """Parse --dashboards=id,id,id (or a single id) into a set of oids.

    Returns None if the flag was not passed."""
    if not raw:
        return None
    return {token.strip() for token in raw.split(',') if token.strip()}


def main():
    args = parse_args()
    mode = "apply" if args.apply else "dry-run"

    # setup_run_logger() creates one timestamped log file per run, under
    # logs/, that records every action below as a JSON line.
    logger, log_path = setup_run_logger()
    logger.info(json.dumps({"action": "mode", "mode": mode}))

    # Set up the pysisense API client from config.yaml next to this script,
    # so the script works no matter which folder it is run from.
    api_client = SisenseClient(config_file=CONFIG_PATH, debug=True)
    access_management = AccessManagement(api_client=api_client, debug=True)

    # Resolve the token's own identity up front. This is needed for the
    # --owned filter, and it is logged on every run so it is always clear
    # which environment and account a run acted against.
    my_user = access_management.get_my_user()
    my_user_id = my_user.get('_id') if isinstance(my_user, dict) else None
    my_user_email = my_user.get('email') if isinstance(my_user, dict) else None
    if not my_user_id:
        raise RuntimeError(f"Failed to resolve the token's own identity: {my_user}")

    # api_client.config holds the whole parsed config.yaml, so keys beyond
    # pysisense's own (like report_name_prefix) are readable here.
    name_prefix, name_suffix = _read_name_affixes(api_client.config)

    logger.info(json.dumps({
        "action": "run_context",
        "environment": api_client.base_url,
        "is_ssl": api_client.is_ssl,
        "operating_system": api_client.operating_system,
        "token_user_email": my_user_email,
        "token_user_id": my_user_id,
        "mode": mode,
        "owned_only": args.owned,
        "report_name_prefix": name_prefix,
        "report_name_suffix": name_suffix,
    }))
    print_banner(
        environment=api_client.base_url,
        is_ssl=api_client.is_ssl,
        operating_system=api_client.operating_system,
        user_email=my_user_email,
        user_id=my_user_id,
        mode=mode,
        owned_only=args.owned,
        log_path=log_path,
        name_format=build_report_name("<dashboard title>", name_prefix, name_suffix),
    )

    found_dashboards = get_all_dashboards_paginated(api_client)
    logger.info(json.dumps({"action": "dashboards_fetched", "count": len(found_dashboards)}))

    if args.owned:
        found_dashboards = [d for d in found_dashboards if d and d.get('owner') == my_user_id]
        logger.info(json.dumps({
            "action": "dashboards_filtered_by_token_owner",
            "owner_user_id": my_user_id,
            "matched_count": len(found_dashboards),
        }))
        print(_style(f"Limiting run to dashboards owned by {my_user_email or my_user_id}; "
                     f"{len(found_dashboards)} matched.", _CYAN))

    dashboard_ids = _parse_dashboard_ids(args.dashboards)
    if dashboard_ids is not None:
        found_dashboards = [d for d in found_dashboards if d and d.get('oid') in dashboard_ids]
        matched_oids = {d.get('oid') for d in found_dashboards}
        missing_oids = dashboard_ids - matched_oids
        logger.info(json.dumps({
            "action": "dashboards_filtered_by_id",
            "requested_oids": sorted(dashboard_ids),
            "matched_count": len(found_dashboards),
            "missing_oids": sorted(missing_oids),
        }))
        print(_style(f"Limiting run to {len(dashboard_ids)} requested dashboard(s); "
                     f"{len(found_dashboards)} matched.", _CYAN))
        if missing_oids:
            print(_style(f"Warning: dashboard id(s) not found or not accessible: "
                          f"{', '.join(sorted(missing_oids))}", _BOLD, _YELLOW))

    users_map = gen_users_map(access_management)
    logger.info(json.dumps({"action": "users_map_generated", "count": len(users_map)}))

    # Only migrate dashboards whose legacy subscription is still active and
    # has at least one subscribed share. A report needs either recipients
    # or an archive path, and this script sets neither for a subscription
    # with no subscribed shares, so those are skipped instead of sent to
    # the API to fail. Every dashboard is logged with why it was kept or
    # skipped, so a dashboard silently missing a report can still be
    # diagnosed later.
    candidates = []
    for d in found_dashboards:
        if not d:
            logger.info(json.dumps({"action": "dashboard_skipped_empty"}))
            continue

        oid = d.get('oid')
        title = d.get('title')
        subscription = d.get('subscription')

        if not subscription:
            logger.info(json.dumps({
                "action": "candidate_skipped_no_subscription",
                "dashboard_oid": oid,
                "dashboard_title": title,
            }))
            continue
        if not subscription.get('active'):
            logger.info(json.dumps({
                "action": "candidate_skipped_subscription_inactive",
                "dashboard_oid": oid,
                "dashboard_title": title,
            }))
            continue
        if not _has_subscribed_recipient(d):
            logger.info(json.dumps({
                "action": "candidate_skipped_no_recipients",
                "dashboard_oid": oid,
                "dashboard_title": title,
            }))
            continue

        logger.info(json.dumps({
            "action": "candidate_found",
            "dashboard_oid": oid,
            "dashboard_title": title,
        }))
        candidates.append(d)
    print(_style(f"Found {len(candidates)} dashboard(s) with an active subscription and subscribed recipients.", _BOLD))

    if not candidates:
        logger.info(json.dumps({"action": "no_dashboard_found"}))
        print(_style("Nothing to migrate.", _YELLOW))
        return

    counts = {"created": 0, "skipped_existing": 0, "failed": 0, "dry_run": 0}

    name_overrides = _disambiguate_report_names(candidates)
    for oid, name in name_overrides.items():
        logger.info(json.dumps({
            "action": "candidate_name_disambiguated",
            "dashboard_oid": oid,
            "report_name": build_report_name(name, name_prefix, name_suffix),
        }))

    # One report creation attempt per candidate dashboard. --apply gates
    # the actual POST, so a dry run can be reviewed safely before going live.
    for d in tqdm(candidates, desc="Migrating subscriptions", unit=" dashboard"):
        base_name = name_overrides.get(d.get('oid')) or d.get('title')
        report_name = build_report_name(base_name, name_prefix, name_suffix)
        report_body = map_subscription_to_report(d, users_map, name_override=report_name)

        if args.debug:
            tqdm.write(_style(
                f"[debug] dashboard {d.get('oid')} ('{d.get('title')}'):\n"
                f"{json.dumps(d, indent=2, default=str)}", _DIM,
            ))
            tqdm.write(_style(
                f"[debug] report body:\n{json.dumps(report_body, indent=2)}", _DIM,
            ))

        if not args.apply:
            logger.info(json.dumps({
                "action": "report_create_skipped_dry_run",
                "dashboard_oid": d.get('oid'),
                "dashboard_title": d.get('title'),
                "source_subscription": d.get('subscription'),
                "report_body": report_body,
            }))
            counts["dry_run"] += 1
            tqdm.write(_style(f"[dry run] Would create report for '{d.get('title')}'", _CYAN))
            continue

        logger.info(json.dumps({
            "action": "report_create_attempted",
            "dashboard_oid": d.get('oid'),
            "dashboard_title": d.get('title'),
            "report_body": report_body,
        }))

        response = api_client.post("/api/v1/report_manager/reports", [report_body])
        # pysisense returns None (instead of raising) on a network error or
        # timeout. Treat that, like any other bad response, as a failure for
        # this one dashboard, so the rest of the run still goes ahead.
        status_code = response.status_code if response is not None else None
        created_report = _response_body(response)

        if response is not None and response.ok:
            created_id = _created_report_id(created_report)
            logger.info(json.dumps({
                "action": "report_created",
                "report_id": created_id,
                "dashboard_oid": d.get('oid'),
                "dashboard_title": d.get('title'),
            }))
            counts["created"] += 1
            tqdm.write(_style(f"Created report {created_id} for '{d.get('title')}'", _GREEN))
        elif status_code == 400 and _is_existing_report_error(created_report):
            existing_id = _existing_report_id(created_report)
            logger.info(json.dumps({
                "action": "report_create_skipped_existing",
                "report_id": existing_id,
                "dashboard_oid": d.get('oid'),
                "dashboard_title": d.get('title'),
            }))
            counts["skipped_existing"] += 1
            tqdm.write(_style(f"Skipped '{d.get('title')}' - report already exists ({existing_id})", _YELLOW))
        else:
            logger.info(json.dumps({
                "action": "report_create_failed",
                "dashboard_oid": d.get('oid'),
                "dashboard_title": d.get('title'),
                "status_code": status_code,
                "response": created_report,
            }))
            counts["failed"] += 1
            reason = f"HTTP {status_code}" if status_code is not None else "no response from server"
            tqdm.write(_style(f"Failed to create report for '{d.get('title')}': {reason}", _BOLD, _RED))
            if args.debug:
                tqdm.write(_style(
                    f"[debug] response:\n{json.dumps(created_report, indent=2, default=str)}", _DIM,
                ))

    print_summary(counts, mode)


if __name__ == "__main__":  # pragma: no cover
    main()