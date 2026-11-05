"""End-to-end tests for main(): every pysisense dependency is faked
(see conftest.py) so these never touch a real Sisense server."""
import json

import pytest

import main


REPORTS_ENDPOINT = "/api/v1/report_manager/reports"
DASHBOARDS_ENDPOINT = "/api/v1/dashboards/admin"


def _install_fakes(monkeypatch, api_client, access_management):
    monkeypatch.setattr(main, "SisenseClient", lambda *a, **k: api_client)
    monkeypatch.setattr(main, "AccessManagement", lambda *a, **k: access_management)


def _run(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("sys.argv", ["main.py"] + argv)


def _read_log_lines(tmp_path):
    # Each line is "<asctime> <json>"; asctime itself contains a space
    # ("YYYY-MM-DD HH:MM:SS,mmm"), so split on the first '{' instead.
    log_dir = tmp_path / "logs"
    [log_file] = list(log_dir.glob("migration_*.log"))
    lines = log_file.read_text().strip().split("\n")
    return [json.loads(line[line.index("{"):]) for line in lines]


class TestMainDryRun:
    def test_no_candidates_prints_nothing_to_migrate(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[
            dashboard_factory(subscription=None),
        ])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, [])

        main.main()

        out = capsys.readouterr().out
        assert "Nothing to migrate." in out
        assert not client.post_calls

    def test_candidate_found_counts_as_dry_run_and_makes_no_post(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, [])

        main.main()

        out = capsys.readouterr().out
        assert "Would create    : 1" in out
        assert not client.post_calls
        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "report_create_skipped_dry_run" in actions

    def test_inactive_subscription_is_skipped(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response,
        dashboard_factory, subscription_factory,
    ):
        client = fake_api_client()
        d = dashboard_factory(subscription=subscription_factory(active=False))
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[d])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, [])

        main.main()

        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "candidate_skipped_subscription_inactive" in actions

    def test_no_subscribed_recipients_is_skipped(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response,
        dashboard_factory, share_factory,
    ):
        client = fake_api_client()
        d = dashboard_factory(shares=[share_factory(subscribe=False)])
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[d])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, [])

        main.main()

        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "candidate_skipped_no_recipients" in actions

    def test_empty_dashboard_entry_is_skipped(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        # get_all_dashboards_paginated itself calls d.get("oid") on every
        # entry, so an empty dict (falsy, but still dict-like) is what can
        # actually reach main()'s "if not d" check - a bare None would raise
        # AttributeError one layer earlier, inside pagination.
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[{}, dashboard_factory()])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, [])

        main.main()

        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "dashboard_skipped_empty" in actions

    def test_missing_own_identity_raises(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response,
    ):
        client = fake_api_client()
        _install_fakes(monkeypatch, client, fake_access_management(my_user={}))
        _run(monkeypatch, tmp_path, [])

        with pytest.raises(RuntimeError, match="Failed to resolve"):
            main.main()

    def test_owned_filter_keeps_only_matching_owner(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        mine = dashboard_factory(oid="mine", owner="user1")
        theirs = dashboard_factory(oid="theirs", owner="user2")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[mine, theirs])])
        _install_fakes(monkeypatch, client, fake_access_management(my_user={"_id": "user1", "email": "me@x.com"}))
        _run(monkeypatch, tmp_path, ["--owned"])

        main.main()

        out = capsys.readouterr().out
        assert "1 matched" in out
        actions = _read_log_lines(tmp_path)
        filtered = next(r for r in actions if r["action"] == "dashboards_filtered_by_token_owner")
        assert filtered["matched_count"] == 1

    def test_dashboards_filter_warns_on_missing_oid(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        d = dashboard_factory(oid="present")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[d])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--dashboards=present,missing"])

        main.main()

        out = capsys.readouterr().out
        assert "1 matched" in out
        assert "missing" in out


class TestMainApplyMode:
    def test_successful_creation_increments_created(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=200, json_data=[{"_id": "new-report-1"}])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        out = capsys.readouterr().out
        assert "Created         : 1" in out
        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "report_created" in actions

    def test_already_existing_report_increments_skipped_existing(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        error_body = {"error": {"clientMessage": {"id": "@reportManager/error/api/report/exist"},
                                 "moreInfo": {"ExistingReportsError": [{"_id": "existing-1"}]}}}
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=400, json_data=error_body)])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        out = capsys.readouterr().out
        assert "Already existed : 1" in out
        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "report_create_skipped_existing" in actions

    def test_unexpected_failure_increments_failed(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=500, json_data={"error": {"message": "boom"}})])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        out = capsys.readouterr().out
        assert "Failed          : 1" in out
        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "report_create_failed" in actions

    def test_debug_prints_dashboard_and_report_body(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        d = dashboard_factory(oid="dash1", title="My Dashboard")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[d])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=200, json_data=[{"_id": "r1"}])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply", "--debug"])

        main.main()

        out = capsys.readouterr().out
        assert "[debug] dashboard dash1 ('My Dashboard')" in out
        assert "[debug] report body:" in out
        assert '"name": "My Dashboard"' in out

    def test_debug_prints_full_response_on_failure(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=500, json_data={"error": {"logref": "abc-123"}})])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply", "--debug"])

        main.main()

        out = capsys.readouterr().out
        assert "[debug] response:" in out
        assert "abc-123" in out

    def test_debug_off_by_default_omits_debug_lines(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=500, json_data={"error": {"logref": "abc-123"}})])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        out = capsys.readouterr().out
        assert "[debug]" not in out

    def test_report_body_sent_matches_mapping(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        d = dashboard_factory()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[d])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=200, json_data=[{"_id": "r1"}])])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        [(endpoint, body)] = [call for call in client.post_calls if call[0] == REPORTS_ENDPOINT]
        assert endpoint == REPORTS_ENDPOINT
        assert body == [main.map_subscription_to_report(d, {})]


class TestMainDisambiguatesCollidingReportNames:
    """Two different dashboards sharing a title must not collide on
    Report Manager's own by-name "already exists" check, or the second
    one's subscription would be silently skipped instead of migrated."""

    def test_colliding_titles_get_distinct_names_sent_to_the_api(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        first = dashboard_factory(oid="a-oid", title="Sales")
        second = dashboard_factory(oid="b-oid", title="Sales")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[first, second])])
        client.queue_post(REPORTS_ENDPOINT, [
            fake_response(status_code=200, json_data=[{"_id": "r1"}]),
            fake_response(status_code=200, json_data=[{"_id": "r2"}]),
        ])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        report_calls = [call for call in client.post_calls if call[0] == REPORTS_ENDPOINT]
        names_sent = [body[0]["name"] for _, body in report_calls]
        assert sorted(names_sent) == ["Sales", "Sales (2)"]

    def test_disambiguation_is_logged(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        first = dashboard_factory(oid="a-oid", title="Sales")
        second = dashboard_factory(oid="b-oid", title="Sales")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[first, second])])
        client.queue_post(REPORTS_ENDPOINT, [
            fake_response(status_code=200, json_data=[{"_id": "r1"}]),
            fake_response(status_code=200, json_data=[{"_id": "r2"}]),
        ])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        renamed = [r for r in _read_log_lines(tmp_path) if r["action"] == "candidate_name_disambiguated"]
        assert renamed == [{
            "action": "candidate_name_disambiguated",
            "dashboard_oid": "b-oid",
            "report_name": "Sales (2)",
        }]

    def test_no_collision_leaves_names_untouched(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        first = dashboard_factory(oid="a-oid", title="Sales")
        second = dashboard_factory(oid="b-oid", title="Marketing")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[first, second])])
        client.queue_post(REPORTS_ENDPOINT, [
            fake_response(status_code=200, json_data=[{"_id": "r1"}]),
            fake_response(status_code=200, json_data=[{"_id": "r2"}]),
        ])
        _install_fakes(monkeypatch, client, fake_access_management())
        _run(monkeypatch, tmp_path, ["--apply"])

        main.main()

        report_calls = [call for call in client.post_calls if call[0] == REPORTS_ENDPOINT]
        names_sent = sorted(body[0]["name"] for _, body in report_calls)
        assert names_sent == ["Marketing", "Sales"]
        actions = [r["action"] for r in _read_log_lines(tmp_path)]
        assert "candidate_name_disambiguated" not in actions
