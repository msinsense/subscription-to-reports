import json
import logging

import main


class TestStyle:
    def test_no_color_returns_plain_text(self, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", True)
        assert main._style("hello", main._BOLD, main._RED) == "hello"

    def test_color_wraps_text_with_escape_codes(self, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", False)
        result = main._style("hello", main._BOLD)
        assert result == "\033[1mhello\033[0m"

    def test_color_supports_multiple_codes(self, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", False)
        result = main._style("x", main._BOLD, main._RED)
        assert result == "\033[1m\033[31mx\033[0m"


class TestSetupRunLogger:
    def test_creates_log_dir_and_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")

        logger, log_path = main.setup_run_logger()
        for handler in logger.handlers:
            handler.close()

        assert log_path.exists()
        assert log_path.parent == tmp_path / "logs"

    def test_writes_run_started_line(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")

        logger, log_path = main.setup_run_logger()
        for handler in logger.handlers:
            handler.close()

        contents = log_path.read_text()
        line = contents.strip().split("\n")[-1]
        # Line is "<asctime> <json>"; asctime itself contains a space
        # ("YYYY-MM-DD HH:MM:SS,mmm"), so split on the first '{' instead.
        payload = json.loads(line[line.index("{"):])
        assert payload["action"] == "run_started"
        assert "run_id" in payload

    def test_log_filename_embeds_run_id(self, tmp_path, monkeypatch):
        monkeypatch.setattr(main, "LOG_DIR", tmp_path / "logs")

        logger, log_path = main.setup_run_logger()
        for handler in logger.handlers:
            handler.close()

        assert log_path.name.startswith("migration_")
        assert log_path.name.endswith(".log")


class TestPrintBanner:
    def test_smoke_apply_mode(self, capsys, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", True)
        main.print_banner(
            environment="https://example.com", is_ssl=True, operating_system="linux",
            user_email="a@example.com", user_id="u1", mode="apply", owned_only=False,
            log_path="logs/x.log",
        )
        out = capsys.readouterr().out
        assert "APPLY - reports will be created" in out
        assert "a@example.com" in out

    def test_smoke_dry_run_mode_owned_only(self, capsys, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", True)
        main.print_banner(
            environment="https://example.com", is_ssl=False, operating_system="windows",
            user_email=None, user_id="u1", mode="dry-run", owned_only=True,
            log_path="logs/x.log",
        )
        out = capsys.readouterr().out
        assert "DRY RUN - no changes will be made" in out
        assert "owned by u1 only" in out
        assert "HTTP - windows" in out


class TestPrintSummary:
    def _counts(self, **overrides):
        base = {"created": 0, "skipped_existing": 0, "failed": 0, "dry_run": 0}
        base.update(overrides)
        return base

    def test_apply_mode(self, capsys, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", True)
        main.print_summary(self._counts(created=3, skipped_existing=1, failed=0), "apply")
        out = capsys.readouterr().out
        assert "Created         : 3" in out
        assert "Already existed : 1" in out
        assert "Failed          : 0" in out

    def test_dry_run_mode(self, capsys, monkeypatch):
        monkeypatch.setattr(main, "_NO_COLOR", True)
        main.print_summary(self._counts(dry_run=5), "dry-run")
        out = capsys.readouterr().out
        assert "Would create    : 5" in out
        assert "Pass --apply" in out
