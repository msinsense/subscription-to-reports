"""A bad response on one report must not stop the run, and config.yaml must
be found next to main.py whatever folder the script is run from."""
import json
from pathlib import Path

from conftest import FakeResponse

import main


REPORTS_ENDPOINT = "/api/v1/report_manager/reports"
DASHBOARDS_ENDPOINT = "/api/v1/dashboards/admin"


class NonJsonResponse(FakeResponse):
    """A response whose body is not JSON, e.g. an HTML 502 page from a load balancer."""

    def json(self):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")


def _setup(monkeypatch, tmp_path, client, access_management, argv):
    monkeypatch.setattr(main, "SisenseClient", lambda *a, **k: client)
    monkeypatch.setattr(main, "AccessManagement", lambda *a, **k: access_management)
    monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr("sys.argv", ["main.py"] + argv)


def _read_log_lines(tmp_path):
    [log_file] = list((tmp_path / "logs").glob("migration_*.log"))
    lines = log_file.read_text().strip().split("\n")
    return [json.loads(line[line.index("{"):]) for line in lines]


class TestResponseBody:
    def test_none_response_describes_network_error(self):
        body = main._response_body(None)
        assert "No response from server" in body["error"]

    def test_non_json_body_is_kept_as_text(self):
        body = main._response_body(NonJsonResponse(status_code=502, text="<html>Bad Gateway</html>"))
        assert body == {"error": "Response body is not JSON.", "body": "<html>Bad Gateway</html>"}

    def test_non_json_body_text_is_truncated(self):
        body = main._response_body(NonJsonResponse(status_code=502, text="x" * 2000))
        assert len(body["body"]) == 500

    def test_json_body_is_returned_as_is(self):
        assert main._response_body(FakeResponse(json_data=[{"_id": "r1"}])) == [{"_id": "r1"}]


class TestCreatedReportId:
    def test_list_body(self):
        assert main._created_report_id([{"_id": "r1"}]) == "r1"

    def test_dict_body(self):
        assert main._created_report_id({"_id": "r1"}) == "r1"

    def test_empty_list(self):
        assert main._created_report_id([]) is None

    def test_unexpected_shape(self):
        assert main._created_report_id("ok") is None
        assert main._created_report_id(None) is None


class TestRunContinuesAfterBadResponse:
    def _two_dashboards(self, dashboard_factory):
        return [dashboard_factory(oid="a-oid", title="Sales"), dashboard_factory(oid="b-oid", title="Marketing")]

    def test_no_response_counts_as_failed_and_run_continues(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response,
        dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=self._two_dashboards(dashboard_factory))])
        client.queue_post(REPORTS_ENDPOINT, [None, fake_response(status_code=200, json_data=[{"_id": "r2"}])])
        _setup(monkeypatch, tmp_path, client, fake_access_management(), ["--apply"])

        main.main()

        out = capsys.readouterr().out
        assert "no response from server" in out
        assert "Created         : 1" in out
        assert "Failed          : 1" in out
        [failed] = [r for r in _read_log_lines(tmp_path) if r["action"] == "report_create_failed"]
        assert failed["status_code"] is None
        assert "No response from server" in failed["response"]["error"]

    def test_non_json_error_counts_as_failed_and_run_continues(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response,
        dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=self._two_dashboards(dashboard_factory))])
        client.queue_post(REPORTS_ENDPOINT, [
            NonJsonResponse(status_code=502, text="<html>Bad Gateway</html>"),
            fake_response(status_code=200, json_data=[{"_id": "r2"}]),
        ])
        _setup(monkeypatch, tmp_path, client, fake_access_management(), ["--apply"])

        main.main()

        out = capsys.readouterr().out
        assert "HTTP 502" in out
        assert "Created         : 1" in out
        assert "Failed          : 1" in out
        [failed] = [r for r in _read_log_lines(tmp_path) if r["action"] == "report_create_failed"]
        assert failed["response"]["body"] == "<html>Bad Gateway</html>"

    def test_success_with_non_json_body_still_counts_as_created(
        self, monkeypatch, tmp_path, capsys, fake_api_client, fake_access_management, fake_response,
        dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[dashboard_factory()])])
        client.queue_post(REPORTS_ENDPOINT, [NonJsonResponse(status_code=200, text="OK")])
        _setup(monkeypatch, tmp_path, client, fake_access_management(), ["--apply"])

        main.main()

        assert "Created         : 1" in capsys.readouterr().out
        [created] = [r for r in _read_log_lines(tmp_path) if r["action"] == "report_created"]
        assert created["report_id"] is None


class TestConfigPath:
    def test_config_path_is_next_to_main_py(self):
        assert main.CONFIG_PATH == Path(main.__file__).parent / "config.yaml"

    def test_client_is_built_from_config_path_not_cwd(
        self, monkeypatch, tmp_path, fake_api_client, fake_access_management, fake_response, dashboard_factory,
    ):
        client = fake_api_client()
        client.queue_get(DASHBOARDS_ENDPOINT, [fake_response(json_data=[])])
        seen = {}

        def fake_sisense_client(*args, **kwargs):
            seen.update(kwargs)
            return client

        _setup(monkeypatch, tmp_path, client, fake_access_management(), [])
        monkeypatch.setattr(main, "SisenseClient", fake_sisense_client)
        monkeypatch.chdir(tmp_path)

        main.main()

        assert seen["config_file"] == main.CONFIG_PATH
        assert seen["config_file"].is_absolute()
