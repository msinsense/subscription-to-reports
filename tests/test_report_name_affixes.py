"""Tests for the optional report_name_prefix / report_name_suffix config."""
import json

import pytest

import main


REPORTS_ENDPOINT = "/api/v1/report_manager/reports"
DASHBOARDS_ENDPOINT = "/api/v1/dashboards/admin"


class TestBuildReportName:
    def test_no_affixes_returns_base_name(self):
        assert main.build_report_name("Sales") == "Sales"

    def test_prefix_only(self):
        assert main.build_report_name("Sales", prefix="Subscription -") == "Subscription - Sales"

    def test_suffix_only(self):
        assert main.build_report_name("Sales", suffix="- Report") == "Sales - Report"

    def test_prefix_and_suffix(self):
        assert main.build_report_name("Sales", "[RM]", "(migrated)") == "[RM] Sales (migrated)"


class TestReadNameAffixes:
    def test_missing_keys_default_to_empty(self):
        assert main._read_name_affixes({}) == ("", "")

    def test_none_config_defaults_to_empty(self):
        assert main._read_name_affixes(None) == ("", "")

    def test_values_are_stripped(self):
        config = {"report_name_prefix": "  Subscription -  ", "report_name_suffix": " - Report "}
        assert main._read_name_affixes(config) == ("Subscription -", "- Report")

    def test_explicit_null_is_treated_as_unset(self):
        assert main._read_name_affixes({"report_name_prefix": None}) == ("", "")

    @pytest.mark.parametrize("key", ["report_name_prefix", "report_name_suffix"])
    def test_non_string_value_is_rejected(self, key):
        with pytest.raises(ValueError, match=key):
            main._read_name_affixes({key: 2025})


def _setup(monkeypatch, tmp_path, client, access_management, argv):
    monkeypatch.setattr(main, "SisenseClient", lambda *a, **k: client)
    monkeypatch.setattr(main, "AccessManagement", lambda *a, **k: access_management)
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("sys.argv", ["main.py"] + argv)


def _read_log_lines(tmp_path):
    [log_file] = list((tmp_path / "logs").glob("migration_*.log"))
    lines = log_file.read_text().strip().split("\n")
    return [json.loads(line[line.index("{"):]) for line in lines]


class TestMainAppliesAffixes:
    def test_prefix_and_suffix_applied_to_sent_names(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client(config={"report_name_prefix": "Subscription -", "report_name_suffix": "(RM)"})
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory(title="Sales")])])
        client.queue_post(REPORTS_ENDPOINT, [fake_response(status_code=200, json_data=[{"_id": "r1"}])])
        _setup(monkeypatch, tmp_path, client, fake_access_management(), ["--apply"])

        main.main()

        [(_, body)] = client.post_calls
        assert body[0]["name"] == "Subscription - Sales (RM)"
        # The embedded dashboard title is the real title, never the affixed name.
        assert body[0]["dashboards"][0]["title"] == "Sales"

    def test_affixes_combine_with_disambiguation(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client(config={"report_name_prefix": "Sub:"})
        first = dashboard_factory(oid="a-oid", title="Sales")
        second = dashboard_factory(oid="b-oid", title="Sales")
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[first, second])])
        client.queue_post(REPORTS_ENDPOINT, [
            fake_response(status_code=200, json_data=[{"_id": "r1"}]),
            fake_response(status_code=200, json_data=[{"_id": "r2"}]),
        ])
        _setup(monkeypatch, tmp_path, client, fake_access_management(), ["--apply"])

        main.main()

        names = sorted(body[0]["name"] for _, body in client.post_calls)
        assert names == ["Sub: Sales", "Sub: Sales (2)"]
        renamed = [r for r in _read_log_lines(tmp_path) if r["action"] == "candidate_name_disambiguated"]
        assert renamed[0]["report_name"] == "Sub: Sales (2)"

    def test_affixes_logged_and_shown_in_banner(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response,
        dashboard_factory,
    ):
        monkeypatch.setattr(main, "_NO_COLOR", True)
        client = fake_api_client(config={"report_name_suffix": "- Report"})
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        _setup(monkeypatch, tmp_path, client, fake_access_management(), [])

        main.main()

        assert "Report name : <dashboard title> - Report" in capsys.readouterr().out
        [context] = [r for r in _read_log_lines(tmp_path) if r["action"] == "run_context"]
        assert context["report_name_prefix"] == ""
        assert context["report_name_suffix"] == "- Report"

    def test_invalid_affix_stops_before_fetching_dashboards(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management,
    ):
        client = fake_api_client(config={"report_name_prefix": 123})
        _setup(monkeypatch, tmp_path, client, fake_access_management(), ["--apply"])

        with pytest.raises(ValueError, match="report_name_prefix"):
            main.main()
        assert not client.get_calls
