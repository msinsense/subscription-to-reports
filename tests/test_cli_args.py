import pytest

import main


class TestParseDashboardIds:
    def test_none_when_flag_not_passed(self):
        assert main._parse_dashboard_ids(None) is None

    def test_none_for_empty_string(self):
        assert main._parse_dashboard_ids("") is None

    def test_single_id(self):
        assert main._parse_dashboard_ids("abc123") == {"abc123"}

    def test_multiple_ids(self):
        assert main._parse_dashboard_ids("a,b,c") == {"a", "b", "c"}

    def test_whitespace_and_empty_tokens_are_dropped(self):
        assert main._parse_dashboard_ids(" a , , b ,") == {"a", "b"}


class TestParseArgs:
    def test_defaults(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["main.py"])
        args = main.parse_args()
        assert args.apply is False
        assert args.dashboards is None
        assert args.owned is False
        assert args.debug is False

    def test_debug_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["main.py", "--debug"])
        assert main.parse_args().debug is True

    def test_apply_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["main.py", "--apply"])
        assert main.parse_args().apply is True

    def test_dashboards_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["main.py", "--dashboards=a,b"])
        assert main.parse_args().dashboards == "a,b"

    def test_owned_flag(self, monkeypatch):
        monkeypatch.setattr("sys.argv", ["main.py", "--owned"])
        assert main.parse_args().owned is True
