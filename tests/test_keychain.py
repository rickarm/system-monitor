"""Secrets come from the login keychain (service system-monitor), never ~/.env."""

import logging
import subprocess
from unittest.mock import patch

import keychain
import system_monitor


def _cp(stdout="", rc=0):
    return subprocess.CompletedProcess(args=[], returncode=rc, stdout=stdout, stderr="")


def test_keychain_secret_returns_value():
    with patch("keychain.subprocess.run", return_value=_cp("tok\n")) as run:
        assert keychain.keychain_secret("system-monitor", "TELEGRAM_BOT_TOKEN") == "tok"
    assert run.call_args.args[0][:3] == ["/usr/bin/security", "find-generic-password", "-s"]


def test_keychain_secret_missing_returns_empty():
    with patch("keychain.subprocess.run", return_value=_cp("", rc=44)):
        assert keychain.keychain_secret("system-monitor", "NOPE") == ""


def test_main_never_reads_secrets_from_env_file(tmp_path, caplog, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("TELEGRAM_BOT_TOKEN=leak\nTELEGRAM_CHAT_ID=1\n")
    monkeypatch.setattr(system_monitor, "ENV_FILE", env_file)
    monkeypatch.setattr(system_monitor, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(system_monitor, "CHECKS", {})
    monkeypatch.setattr(system_monitor, "keychain_secret", lambda service, account: "")
    with caplog.at_level(logging.WARNING):
        system_monitor.main(dry_run=False)
    assert "alerts disabled" in caplog.text
    assert "leak" not in caplog.text
