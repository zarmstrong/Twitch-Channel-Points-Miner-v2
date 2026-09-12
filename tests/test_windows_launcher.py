import builtins
import http.server
import json
import logging
import os
import shutil
import socket
import stat
import threading
import types
from pathlib import Path

import pytest

import windows_launcher


@pytest.fixture(autouse=True)
def _isolate_shell_bypass_token_env(monkeypatch):
    # main() sets these real env vars directly (not via monkeypatch, since
    # they must actually reach the AnalyticsServer thread it starts) so
    # they survive past any one test's teardown; clearing them before every
    # test here stops one test's main() call from leaking a token/commit
    # hash into another test's assertions.
    monkeypatch.delenv(windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR, raising=False)
    monkeypatch.delenv(windows_launcher.BUILD_COMMIT_ENV_VAR, raising=False)


@pytest.fixture(autouse=True)
def _isolate_launcher_logger_state():
    # main() -> _configure_launcher_logging() mutates the module-level
    # `logger` singleton in place (adds a StreamHandler, sets
    # propagate=False) and never reverts it, since in the real app that
    # config is meant to stick for the rest of the process. Left alone
    # across tests, one earlier test's main() call permanently disables
    # propagation to the root logger, silently breaking any later test's
    # caplog assertions.
    logger = windows_launcher.logger
    original_propagate = logger.propagate
    original_handlers = list(logger.handlers)
    original_level = logger.level
    yield
    logger.propagate = original_propagate
    logger.handlers = original_handlers
    logger.setLevel(original_level)


def _fake_launch_shell_recording(calls, extract=lambda dashboard_info, initial_tab: (
    dashboard_info,
    initial_tab,
)):
    """A `launch_shell` stand-in accepting its full current signature, so
    call-site tests don't need to know about args (config_path, prompt_marker,
    needs_username, start_mining, logs_dir) they aren't exercising."""

    def fake(
        dashboard_info,
        console_buffer,
        initial_tab,
        miner_thread,
        config_path,
        prompt_marker,
        needs_username,
        start_mining,
        logs_dir,
        start_minimized=False,
    ):
        calls.append(extract(dashboard_info, initial_tab))

    return fake


_REAL_USERNAME = "a_real_twitch_user"


def test_build_install_mode_reads_bundled_marker(tmp_path, monkeypatch):
    marker = tmp_path / "install_mode.txt"
    marker.write_text("standard\n", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "bundled_file", lambda _name: marker)

    assert windows_launcher._build_install_mode() == "standard"


def test_build_install_mode_defaults_to_portable_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        windows_launcher, "bundled_file", lambda _name: tmp_path / "does-not-exist.txt"
    )

    assert windows_launcher._build_install_mode() == "portable"


def test_is_standard_build_true_only_for_standard_marker(tmp_path, monkeypatch):
    marker = tmp_path / "install_mode.txt"
    monkeypatch.setattr(windows_launcher, "bundled_file", lambda _name: marker)

    marker.write_text("standard", encoding="utf-8")
    assert windows_launcher._is_standard_build() is True

    marker.write_text("portable", encoding="utf-8")
    assert windows_launcher._is_standard_build() is False


def test_build_commit_hash_reads_bundled_file(tmp_path, monkeypatch):
    marker = tmp_path / "commit_hash.txt"
    marker.write_text("abc1234\n", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "bundled_file", lambda _name: marker)

    assert windows_launcher._build_commit_hash() == "abc1234"


def test_build_commit_hash_none_when_file_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        windows_launcher, "bundled_file", lambda _name: tmp_path / "does-not-exist.txt"
    )

    assert windows_launcher._build_commit_hash() is None


def test_build_commit_hash_none_when_build_could_not_determine_one(tmp_path, monkeypatch):
    # build_windows.bat writes the literal string "unknown" rather than
    # leaving the file absent when git wasn't available at build time.
    marker = tmp_path / "commit_hash.txt"
    marker.write_text("unknown\n", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "bundled_file", lambda _name: marker)

    assert windows_launcher._build_commit_hash() is None


def test_standard_application_directory_uses_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    result = windows_launcher._standard_application_directory()

    assert result == tmp_path / "TwitchChannelPointsMiner"


def test_standard_application_directory_falls_back_without_localappdata(monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    result = windows_launcher._standard_application_directory()

    assert result == Path.home() / "AppData" / "Local" / "TwitchChannelPointsMiner"


def test_config_root_directory_is_exe_dir_for_portable_build(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_launcher, "_is_standard_build", lambda: False)

    assert windows_launcher.config_root_directory(tmp_path) == tmp_path


def test_config_root_directory_is_standard_dir_for_standard_build(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_launcher, "_is_standard_build", lambda: True)
    monkeypatch.setattr(
        windows_launcher, "_standard_application_directory", lambda: tmp_path / "AppData"
    )

    assert windows_launcher.config_root_directory(tmp_path / "exe") == tmp_path / "AppData"


def _write_legacy_data(exe_dir):
    (exe_dir / "config").mkdir(parents=True)
    (exe_dir / "config" / "config.py").write_text("MINER_CONFIG = {}\n", encoding="utf-8")
    (exe_dir / "cookies").mkdir()
    (exe_dir / "cookies" / "someone.json").write_text("[]", encoding="utf-8")
    (exe_dir / "analytics").mkdir()
    (exe_dir / "analytics" / "someone.json").write_text("{}", encoding="utf-8")
    (exe_dir / "logs").mkdir()
    (exe_dir / "logs" / "old.log").write_text("stale log content\n", encoding="utf-8")


def test_migrate_legacy_windows_data_does_nothing_without_a_legacy_config(tmp_path):
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    standard_dir = tmp_path / "AppData"

    result = windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    assert result is None
    assert not (exe_dir / "config-legacy").exists()
    assert not standard_dir.exists()


def test_migrate_legacy_windows_data_moves_and_carries_forward(tmp_path):
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    _write_legacy_data(exe_dir)
    standard_dir = tmp_path / "AppData"

    result = windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    assert result is not None
    assert str(standard_dir) in result
    assert str(exe_dir / "config-legacy") in result

    # Archived: nothing lost, including logs.
    assert (exe_dir / "config-legacy" / "config" / "config.py").is_file()
    assert (exe_dir / "config-legacy" / "cookies" / "someone.json").is_file()
    assert (exe_dir / "config-legacy" / "analytics" / "someone.json").is_file()
    assert (exe_dir / "config-legacy" / "logs" / "old.log").is_file()
    assert (exe_dir / "config-legacy" / ".migrated").is_file()

    # Carried forward: config, cookies, analytics.
    assert (standard_dir / "config" / "config.py").is_file()
    assert (standard_dir / "cookies" / "someone.json").is_file()
    assert (standard_dir / "analytics" / "someone.json").is_file()

    # Not carried forward: old logs stay archived-only, so they're never
    # duplicated into a live folder nothing will ever clean up.
    assert not (standard_dir / "logs").exists()

    # Nothing left beside the exe - it was moved, not copied.
    assert not (exe_dir / "config").exists()
    assert not (exe_dir / "cookies").exists()
    assert not (exe_dir / "analytics").exists()
    assert not (exe_dir / "logs").exists()


def test_migrate_legacy_windows_data_is_idempotent(tmp_path):
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    _write_legacy_data(exe_dir)
    standard_dir = tmp_path / "AppData"
    windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    # A second call must be a pure no-op - re-running this against the
    # fresh standard-location data (rather than the real legacy data, which
    # no longer exists at its old path) would be actively wrong.
    canary = standard_dir / "config" / "config.py"
    canary.write_text("edited after migration\n", encoding="utf-8")

    result = windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    assert result is None
    assert canary.read_text(encoding="utf-8") == "edited after migration\n"


def test_migrate_legacy_windows_data_handles_partial_legacy_data(tmp_path):
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    (exe_dir / "config").mkdir()
    (exe_dir / "config" / "config.py").write_text("MINER_CONFIG = {}\n", encoding="utf-8")
    # No cookies/analytics/logs folders at all - e.g. never logged in yet.
    standard_dir = tmp_path / "AppData"

    result = windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    assert result is not None
    assert (standard_dir / "config" / "config.py").is_file()
    assert not (standard_dir / "cookies").exists()


def test_migrate_legacy_windows_data_resumes_after_a_partial_failure(tmp_path):
    # Simulates the state left behind by an interrupted first attempt: the
    # "config" folder already moved into the archive (so it's gone from its
    # old path, same as a fully-migrated install), but "cookies" never made
    # it there and no .migrated marker was written - the retry must not
    # mistake this for "nothing to migrate" just because config.py is gone.
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    _write_legacy_data(exe_dir)
    standard_dir = tmp_path / "AppData"
    legacy_root = exe_dir / "config-legacy"
    legacy_root.mkdir()
    shutil.move(str(exe_dir / "config"), str(legacy_root / "config"))
    assert not (exe_dir / "config").exists()
    assert (exe_dir / "cookies").exists()

    result = windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    assert result is not None
    assert (legacy_root / ".migrated").is_file()
    assert (legacy_root / "cookies" / "someone.json").is_file()
    assert (standard_dir / "config" / "config.py").is_file()
    assert (standard_dir / "cookies" / "someone.json").is_file()
    assert not (exe_dir / "cookies").exists()


def test_migrate_legacy_windows_data_overwrites_a_stale_bootstrap_template(tmp_path):
    # Reproduces a real report: the user ran the "standard" build once
    # before their legacy portable folder was discovered, so prepare_config
    # already created a blank template at the standard location. Once their
    # real config/cookies/analytics show up beside the exe and migration
    # runs, it must not mistake that pre-existing blank template for "this
    # item was already migrated" and silently leave it in place.
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    standard_dir = tmp_path / "AppData"
    (standard_dir / "config").mkdir(parents=True)
    (standard_dir / "config" / "config.py").write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username'}\n", encoding="utf-8"
    )
    _write_legacy_data(exe_dir)
    (exe_dir / "config" / "config.py").write_text(
        "MINER_CONFIG = {'username': 'the_real_user'}\n", encoding="utf-8"
    )

    result = windows_launcher._migrate_legacy_windows_data(exe_dir, standard_dir)

    assert result is not None
    assert (
        "the_real_user"
        in (standard_dir / "config" / "config.py").read_text(encoding="utf-8")
    )
    assert (standard_dir / "cookies" / "someone.json").is_file()
    assert (standard_dir / "analytics" / "someone.json").is_file()
    # The user's real config was archived too, not just moved aside and lost.
    assert (
        "the_real_user"
        in (exe_dir / "config-legacy" / "config" / "config.py").read_text(
            encoding="utf-8"
        )
    )


def test_show_migration_notice_uses_native_message_box_on_windows(monkeypatch):
    calls = []

    class FakeUser32:
        def MessageBoxW(self, hwnd, text, caption, flags):
            calls.append((hwnd, text, caption, flags))

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.ctypes, "windll", FakeWindll(), raising=False)

    windows_launcher._show_migration_notice("moved!")

    assert calls == [(0, "moved!", "Twitch Channel Points Miner", 0x40)]


def test_prepare_config_copies_template_once(tmp_path, monkeypatch):
    template = tmp_path / "template.py"
    template.write_text("MINER_CONFIG = {}\n", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "bundled_file", lambda _name: template)

    config_dir, created = windows_launcher.prepare_config(tmp_path / "application")

    config_path = config_dir / "config.py"
    assert created is True
    assert config_path.read_text(encoding="utf-8") == "MINER_CONFIG = {}\n"
    if os.name != "nt":
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600

    config_path.write_text("user configuration\n", encoding="utf-8")
    _, created_again = windows_launcher.prepare_config(tmp_path / "application")

    assert created_again is False
    assert config_path.read_text(encoding="utf-8") == "user configuration\n"


def test_application_directory_uses_source_directory(monkeypatch):
    monkeypatch.delattr(windows_launcher.sys, "frozen", raising=False)

    assert windows_launcher.application_directory() == Path(
        windows_launcher.__file__
    ).resolve().parent


def test_main_prints_and_exports_build_commit_when_available(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(windows_launcher, "_build_commit_hash", lambda: "abc1234")
    monkeypatch.setattr(
        windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe", "--convert-only"]
    )

    assert windows_launcher.main() == 0

    assert "Build: abc1234" in capsys.readouterr().out
    assert os.environ[windows_launcher.BUILD_COMMIT_ENV_VAR] == "abc1234"


def test_main_skips_build_commit_output_when_unavailable(tmp_path, monkeypatch, capsys):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(windows_launcher, "_build_commit_hash", lambda: None)
    monkeypatch.setattr(
        windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe", "--convert-only"]
    )

    assert windows_launcher.main() == 0

    assert "Build:" not in capsys.readouterr().out
    assert windows_launcher.BUILD_COMMIT_ENV_VAR not in os.environ


def test_main_forwards_command_line_arguments(tmp_path, monkeypatch):
    # --convert-only is a scripted/automation entry point (see runner.py) and
    # must keep behaving exactly like before: a synchronous call, no thread,
    # no desktop window.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text("", encoding="utf-8")
    runner_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(
        windows_launcher, "runner_main", lambda argv, **kwargs: runner_calls.append(argv) or 0
    )
    monkeypatch.setattr(
        windows_launcher.sys,
        "argv",
        ["TwitchChannelPointsMiner.exe", "--convert-only"],
    )

    assert windows_launcher.main() == 0
    assert runner_calls == [
        [
            "--config-dir",
            str(config_dir),
            "--legacy-runner",
            str(tmp_path / "run.py"),
            "--convert-only",
        ]
    ]
    # A scripted/automation run never starts a desktop shell or its
    # dashboard, so it has no reason to generate an unused bypass secret.
    assert windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR not in os.environ


def test_main_sets_a_fresh_shell_bypass_token_before_launching_the_shell(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}}}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )
    (config_dir / ".desktop_shell_onboarded").touch()
    tokens_seen = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)

    def fake_launch_shell(*args, **kwargs):
        tokens_seen.append(os.environ.get(windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR))

    monkeypatch.setattr(windows_launcher, "launch_shell", fake_launch_shell)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert len(tokens_seen) == 1
    assert tokens_seen[0]  # non-empty: a real per-launch secret was set


def _write_real_config(config_dir):
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.py").write_text(
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}}}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )


def test_main_creates_a_brand_new_standard_appdata_directory(tmp_path, monkeypatch):
    # A standard build's AppData folder may not exist at all on a truly
    # fresh install - os.chdir() requires it to exist first, unlike a
    # portable build's exe_dir, which always exists trivially already (the
    # exe is running from there).
    exe_dir = tmp_path / "exe"
    exe_dir.mkdir()
    standard_dir = tmp_path / "AppData" / "TwitchChannelPointsMiner"
    assert not standard_dir.exists()

    monkeypatch.setattr(windows_launcher, "application_directory", lambda: exe_dir)
    monkeypatch.setattr(windows_launcher, "_is_standard_build", lambda: True)
    monkeypatch.setattr(
        windows_launcher, "_standard_application_directory", lambda: standard_dir
    )
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(windows_launcher, "launch_shell", lambda *args, **kwargs: None)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert standard_dir.is_dir()
    assert (standard_dir / "config" / "config.py").is_file()


def test_main_migrates_legacy_data_and_shows_notice_for_standard_build(
    tmp_path, monkeypatch
):
    exe_dir = tmp_path / "exe"
    _write_real_config(exe_dir / "config")
    standard_dir = tmp_path / "AppData" / "TwitchChannelPointsMiner"

    monkeypatch.setattr(windows_launcher, "application_directory", lambda: exe_dir)
    monkeypatch.setattr(windows_launcher, "_is_standard_build", lambda: True)
    monkeypatch.setattr(
        windows_launcher, "_standard_application_directory", lambda: standard_dir
    )
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(windows_launcher, "launch_shell", lambda *args, **kwargs: None)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])
    notices = []
    monkeypatch.setattr(windows_launcher, "_show_migration_notice", notices.append)

    assert windows_launcher.main() == 0

    assert (standard_dir / "config" / "config.py").is_file()
    assert (exe_dir / "config-legacy" / ".migrated").is_file()
    assert len(notices) == 1


def test_main_never_migrates_for_a_portable_build(tmp_path, monkeypatch):
    exe_dir = tmp_path / "exe"
    _write_real_config(exe_dir / "config")

    monkeypatch.setattr(windows_launcher, "application_directory", lambda: exe_dir)
    monkeypatch.setattr(windows_launcher, "_is_standard_build", lambda: False)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(windows_launcher, "launch_shell", lambda *args, **kwargs: None)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])
    notices = []
    monkeypatch.setattr(windows_launcher, "_show_migration_notice", notices.append)

    assert windows_launcher.main() == 0

    assert not (exe_dir / "config-legacy").exists()
    assert notices == []


def test_main_starts_miner_thread_and_launches_shell_when_interactive(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}, 'enable_analytics': False}}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )
    # Represents a machine that has already been through onboarding once, so
    # this test can focus on thread/shell wiring with a deterministic
    # initial_tab instead of also asserting the onboarding-marker behavior
    # (covered separately below).
    (config_dir / ".desktop_shell_onboarded").touch()
    runner_calls = []
    shell_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(
        windows_launcher, "runner_main", lambda argv, **kwargs: runner_calls.append(argv) or 0
    )
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        _fake_launch_shell_recording(shell_calls),
    )
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    # The miner runs on a background thread so the shell can own the main
    # thread; joined with a short timeout, the fast stub above has long
    # finished by the time main() returns.
    assert runner_calls == [
        [
            "--config-dir",
            str(config_dir),
            "--legacy-runner",
            str(tmp_path / "run.py"),
        ]
    ]
    assert shell_calls == [(None, None)]


def test_main_defers_mining_until_username_is_submitted(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username', 'enable_analytics': False}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )
    (config_dir / ".desktop_shell_onboarded").touch()
    runner_calls = []
    shell_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(
        windows_launcher, "runner_main", lambda argv, **kwargs: runner_calls.append(argv) or 0
    )

    def fake_launch_shell(
        dashboard_info,
        console_buffer,
        initial_tab,
        miner_thread,
        config_path,
        prompt_marker,
        needs_username,
        start_mining,
        logs_dir,
        start_minimized=False,
    ):
        shell_calls.append(needs_username)
        # Mining must not have started before the (simulated) setup panel
        # submission below - a placeholder username would just fail login.
        assert runner_calls == []
        assert miner_thread.is_alive() is False
        # Stands in for WindowApi.submit_username() being called from the
        # shell's setup panel once the user enters a real username.
        start_mining()

    monkeypatch.setattr(windows_launcher, "launch_shell", fake_launch_shell)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert shell_calls == [True]
    assert runner_calls == [
        [
            "--config-dir",
            str(config_dir),
            "--legacy-runner",
            str(tmp_path / "run.py"),
        ]
    ]


def test_main_prints_guidance_when_shell_fails_before_username_is_set(
    tmp_path, monkeypatch, capsys
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username'}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )
    opened = []
    paused = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no WebView2 runtime")),
    )
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(windows_launcher, "pause_for_first_run", lambda: paused.append(True))
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    # No dashboard exists to fall back to when mining never started.
    assert opened == []
    assert paused == [True]
    assert "No Twitch username is configured yet." in capsys.readouterr().out


def test_main_enables_analytics_and_opens_config_tab_on_first_run(tmp_path, monkeypatch):
    template = tmp_path / "template.py"
    template.write_text(
        "MINER_CONFIG = {\n"
        "    'username': 'someone',\n"
        "    'enable_analytics': False,\n"
        "}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )
    shell_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "bundled_file", lambda _name: template)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        _fake_launch_shell_recording(shell_calls),
    )
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert len(shell_calls) == 1
    dashboard_info, initial_tab = shell_calls[0]
    assert initial_tab == "config"
    assert dashboard_info == {
        "host": "127.0.0.1",
        "port": windows_launcher.DEFAULT_ANALYTICS_PORT,
        "url": f"http://127.0.0.1:{windows_launcher.DEFAULT_ANALYTICS_PORT}/",
    }

    config_text = (tmp_path / "config" / "config.py").read_text(encoding="utf-8")
    assert "'enable_analytics': True," in config_text
    assert "ANALYTICS_CONFIG = {" in config_text


def test_main_opens_config_tab_for_preexisting_installer_created_config(
    tmp_path, monkeypatch
):
    # The Windows installer pre-creates config.py (and enables analytics)
    # before the exe ever runs, so `prepare_config` reports `created=False`
    # here - the onboarding view must still key off the marker file, not
    # `created`, or installer users would never see it.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}, 'enable_analytics': True}}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = {'host': '127.0.0.1', 'port': 5000}\n",
        encoding="utf-8",
    )
    shell_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        _fake_launch_shell_recording(shell_calls, extract=lambda _info, initial_tab: initial_tab),
    )
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0
    assert shell_calls == ["config"]
    assert (config_dir / ".desktop_shell_onboarded").is_file()

    shell_calls.clear()
    assert windows_launcher.main() == 0
    assert shell_calls == [None]


def test_main_leaves_existing_config_untouched_on_upgrade_launch(tmp_path, monkeypatch):
    # Simulates upgrading an existing pre-shell install: config.py already
    # exists (so `created` is False) with analytics explicitly disabled, and
    # no onboarding marker exists yet either. BUILD.md documents that an
    # existing configuration is never overwritten; this must hold here too.
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_path = config_dir / "config.py"
    # CONFIG_VERSION matches the current schema so the unrelated schema
    # migrator (which legitimately rewrites genuinely old configs, and would
    # otherwise make this assertion about a *different* mechanism) is a
    # verified no-op here - isolating the one thing under test: the
    # analytics-defaults bootstrap.
    from TwitchChannelPointsMiner.config_migration import CONFIG_VERSION

    original = (
        f"CONFIG_VERSION = {CONFIG_VERSION}\n"
        "MINER_CONFIG = {\n"
        "    'username': 'someone',\n"
        "    'enable_analytics': False,\n"
        "}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n"
    )
    config_path.write_text(original, encoding="utf-8")
    assert not (config_dir / ".desktop_shell_onboarded").is_file()

    shell_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        _fake_launch_shell_recording(shell_calls),
    )
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert config_path.read_text(encoding="utf-8") == original
    # Analytics stayed off (nothing for the Dashboard tab to show), but this
    # machine has never been through the shell before, so onboarding still
    # offers the Config tab once.
    assert shell_calls == [(None, "config")]


def test_main_falls_back_to_browser_when_shell_launch_fails(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}, 'enable_analytics': True}}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = {'host': '127.0.0.1', 'port': 5000}\n",
        encoding="utf-8",
    )
    opened = []
    paused = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no WebView2 runtime")),
    )
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(windows_launcher, "pause_for_first_run", lambda: paused.append(True))
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    # Same auth-bypass token every other dashboard-URL path attaches (main()
    # generates a fresh one per run - see SHELL_BYPASS_TOKEN_ENV_VAR - so this
    # fallback shouldn't be the one place that prompts for Basic Auth.
    assert len(opened) == 1
    assert opened[0].startswith("http://127.0.0.1:5000/?shell_token=")
    assert paused == [True]


def test_first_run_pauses_on_windows(monkeypatch):
    prompts = []
    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt))

    windows_launcher.pause_for_first_run()

    assert prompts == ["Press Enter to close this window..."]


def test_first_run_does_not_pause_on_other_platforms(monkeypatch):
    monkeypatch.setattr(windows_launcher.os, "name", "posix")
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(AssertionError("unexpected pause")),
    )

    windows_launcher.pause_for_first_run()


def test_first_run_pause_is_a_noop_without_stdin(monkeypatch):
    # A --windowed/--noconsole build has sys.stdin set to None; input() then
    # raises RuntimeError("lost sys.stdin") immediately rather than
    # EOFError, which previously went uncaught and crashed the process.
    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.sys, "stdin", None)
    monkeypatch.setattr(
        "builtins.input",
        lambda _prompt: (_ for _ in ()).throw(RuntimeError("lost sys.stdin")),
    )

    windows_launcher.pause_for_first_run()


_PRISTINE_TEMPLATE_CONFIG = (
    "MINER_CONFIG = {\n"
    "    'username': 'someone',\n"
    "    'enable_analytics': False,\n"
    "}\n"
    "ANALYTICS_CONFIG = None\n"
)


def test_ensure_windows_analytics_defaults_enables_dashboard_when_just_created(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(_PRISTINE_TEMPLATE_CONFIG, encoding="utf-8")

    password = windows_launcher.ensure_windows_analytics_defaults(
        config_path, just_created=True
    )

    assert password
    updated = config_path.read_text(encoding="utf-8")
    assert "'enable_analytics': True," in updated
    assert "'enable_analytics': False," not in updated
    assert f"'password': {password!r}" in updated
    # The original (now-shadowed) None assignment is left in place; only a
    # second, later assignment is appended - simpler and more robust than
    # rewriting the commented-out example block in place.
    assert updated.count("ANALYTICS_CONFIG") == 2


def test_ensure_windows_analytics_defaults_never_applied_when_not_just_created(tmp_path):
    # The core fix: content alone can never distinguish "a template we just
    # copied" from "a user who deliberately chose enable_analytics=False and
    # left ANALYTICS_CONFIG unset" - both produce this exact same text. Only
    # provenance (just_created) can tell them apart, so it must be checked
    # regardless of how pristine the content looks.
    config_path = tmp_path / "config.py"
    config_path.write_text(_PRISTINE_TEMPLATE_CONFIG, encoding="utf-8")

    result = windows_launcher.ensure_windows_analytics_defaults(
        config_path, just_created=False
    )

    assert result is None
    assert config_path.read_text(encoding="utf-8") == _PRISTINE_TEMPLATE_CONFIG


def test_ensure_windows_analytics_defaults_respects_deliberate_user_choice(tmp_path):
    # The specific scenario that motivated this fix: a user upgrading from a
    # pre-shell build who deliberately set enable_analytics=False on purpose
    # (not a template leftover) and never configured ANALYTICS_CONFIG. This
    # is indistinguishable, by content, from a fresh template - it must
    # survive an upgrade-path launch (just_created=False) unchanged.
    config_path = tmp_path / "config.py"
    original = (
        "MINER_CONFIG = {\n"
        "    'username': 'someone',\n"
        "    'enable_analytics': False,  # deliberately disabled, not a leftover\n"
        "}\n"
        "STREAMERS = []\n"
        "ANALYTICS_CONFIG = None\n"
    )
    config_path.write_text(original, encoding="utf-8")

    result = windows_launcher.ensure_windows_analytics_defaults(
        config_path, just_created=False
    )

    assert result is None
    assert config_path.read_text(encoding="utf-8") == original


def test_ensure_windows_analytics_defaults_leaves_unrecognized_template_alone(tmp_path):
    # Provenance says this is fine to touch, but the secondary,
    # defense-in-depth content check (_matches_template_defaults) still
    # blocks it because the content itself doesn't match what's expected -
    # e.g. the bundled template's shape changed unexpectedly.
    config_path = tmp_path / "config.py"
    original = "MINER_CONFIG = {'enable_analytics': True}\n"
    config_path.write_text(original, encoding="utf-8")

    result = windows_launcher.ensure_windows_analytics_defaults(
        config_path, just_created=True
    )

    assert result is None
    assert config_path.read_text(encoding="utf-8") == original


def test_ensure_windows_analytics_defaults_ignores_customized_analytics_config(tmp_path):
    # Secondary content check again: even with just_created=True, this must
    # never touch a config where ANALYTICS_CONFIG has already been
    # customized, even though enable_analytics is still False verbatim.
    config_path = tmp_path / "config.py"
    original = (
        "MINER_CONFIG = {\n"
        "    'enable_analytics': False,\n"
        "}\n"
        "ANALYTICS_CONFIG = {'host': '0.0.0.0', 'port': 9000, 'password': 'mypassword'}\n"
    )
    config_path.write_text(original, encoding="utf-8")

    result = windows_launcher.ensure_windows_analytics_defaults(
        config_path, just_created=True
    )

    assert result is None
    assert config_path.read_text(encoding="utf-8") == original


def test_matches_template_defaults_true_only_for_untouched_defaults():
    assert windows_launcher._matches_template_defaults(_PRISTINE_TEMPLATE_CONFIG) is True
    assert (
        windows_launcher._matches_template_defaults(
            "MINER_CONFIG = {'enable_analytics': True}\nANALYTICS_CONFIG = None\n"
        )
        is False
    )
    assert (
        windows_launcher._matches_template_defaults(
            "MINER_CONFIG = {'enable_analytics': False}\n"
            "ANALYTICS_CONFIG = {'host': '127.0.0.1'}\n"
        )
        is False
    )
    assert windows_launcher._matches_template_defaults("not valid python (((") is False


def test_needs_username_true_for_bundled_placeholder(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username'}\n", encoding="utf-8"
    )

    assert windows_launcher._needs_username(config_path) is True


def test_needs_username_true_for_blank_or_missing_value(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text("MINER_CONFIG = {'username': '   '}\n", encoding="utf-8")
    assert windows_launcher._needs_username(config_path) is True

    config_path.write_text("MINER_CONFIG = {}\n", encoding="utf-8")
    assert windows_launcher._needs_username(config_path) is True


def test_needs_username_false_for_a_real_username(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}}}\n", encoding="utf-8"
    )

    assert windows_launcher._needs_username(config_path) is False


def test_needs_username_false_for_unreadable_config(tmp_path):
    assert windows_launcher._needs_username(tmp_path / "missing.py") is False


def test_resolve_dashboard_info_returns_none_when_analytics_disabled(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {'enable_analytics': False}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )

    assert windows_launcher.resolve_dashboard_info(config_path) is None


def test_resolve_dashboard_info_builds_url_from_analytics_config(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {'enable_analytics': True}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = {'host': '127.0.0.1', 'port': 5050, 'password': 'secret'}\n",
        encoding="utf-8",
    )

    info = windows_launcher.resolve_dashboard_info(config_path)

    assert info == {"host": "127.0.0.1", "port": 5050, "url": "http://127.0.0.1:5050/"}


def test_resolve_dashboard_info_rewrites_bind_all_host_to_loopback(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {'enable_analytics': True}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = {'host': '0.0.0.0', 'port': 5000, 'password': 'secret'}\n",
        encoding="utf-8",
    )

    info = windows_launcher.resolve_dashboard_info(config_path)

    assert info["host"] == "127.0.0.1"
    assert info["url"] == "http://127.0.0.1:5000/"


def test_resolve_dashboard_info_returns_none_for_unreadable_config(tmp_path):
    assert windows_launcher.resolve_dashboard_info(tmp_path / "missing.py") is None


def test_console_buffer_tail_returns_new_entries_since_seq():
    buffer = windows_launcher.ConsoleBuffer()
    buffer.write("first\n")
    buffer.write("second\n")

    lines, next_seq = buffer.tail(0)
    assert lines == ["first\n", "second\n"]

    more_lines, more_seq = buffer.tail(next_seq)
    assert more_lines == []
    assert more_seq == next_seq

    buffer.write("third\n")
    latest_lines, latest_seq = buffer.tail(next_seq)
    assert latest_lines == ["third\n"]
    assert latest_seq > next_seq


def test_console_buffer_caps_entries_per_tail_call():
    buffer = windows_launcher.ConsoleBuffer()
    for i in range(10):
        buffer.write(f"{i}\n")

    lines, _next_seq = buffer.tail(0, max_entries=3)
    assert lines == ["7\n", "8\n", "9\n"]


def test_console_buffer_bounded_by_max_entries():
    buffer = windows_launcher.ConsoleBuffer(max_entries=3)
    for i in range(5):
        buffer.write(f"{i}\n")

    lines, _next_seq = buffer.tail(0)
    assert lines == ["2\n", "3\n", "4\n"]


def test_tee_stream_writes_to_buffer_and_underlying():
    buffer = windows_launcher.ConsoleBuffer()
    underlying_writes = []

    class Underlying:
        def write(self, text):
            underlying_writes.append(text)

        def flush(self):
            pass

    tee = windows_launcher._TeeStream(buffer, Underlying())
    tee.write("hello\n")
    tee.flush()

    assert underlying_writes == ["hello\n"]
    lines, _seq = buffer.tail(0)
    assert lines == ["hello\n"]


def test_tee_stream_tolerates_missing_underlying_stream():
    buffer = windows_launcher.ConsoleBuffer()
    tee = windows_launcher._TeeStream(buffer, None)

    tee.write("hello\n")
    tee.flush()

    lines, _seq = buffer.tail(0)
    assert lines == ["hello\n"]


def _start_http_server(handler_cls):
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


class _OkHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class _UnauthorizedHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(401)
        self.end_headers()

    def log_message(self, *args):
        pass


def test_wait_until_dashboard_ready_true_when_server_answers_200():
    server, thread = _start_http_server(_OkHandler)
    try:
        host, port = server.server_address
        assert (
            windows_launcher._wait_until_dashboard_ready(
                f"http://{host}:{port}/", timeout=1, interval=0.05
            )
            is True
        )
    finally:
        server.shutdown()
        thread.join()


def test_wait_until_dashboard_ready_times_out_when_nothing_listening():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        _host, closed_port = probe.getsockname()

    assert (
        windows_launcher._wait_until_dashboard_ready(
            f"http://127.0.0.1:{closed_port}/", timeout=0.2, interval=0.05
        )
        is False
    )


def test_wait_until_dashboard_ready_false_when_a_different_server_answers():
    # A previous copy of this app (or anything else) holding the port with
    # its own auth would answer with a real HTTP response, just not a 200
    # for *this* run's token - that must count as "not ready", not success.
    server, thread = _start_http_server(_UnauthorizedHandler)
    try:
        host, port = server.server_address
        assert (
            windows_launcher._wait_until_dashboard_ready(
                f"http://{host}:{port}/", timeout=0.2, interval=0.05
            )
            is False
        )
    finally:
        server.shutdown()
        thread.join()


def test_window_api_get_console_tail_delegates_to_buffer():
    buffer = windows_launcher.ConsoleBuffer()
    buffer.write("hello\n")
    api = windows_launcher.WindowApi(
        buffer,
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    result = api.get_console_tail(0)

    assert result == {"lines": ["hello\n"], "next_seq": 1}


def test_dashboard_url_with_bypass_appends_token_when_set(monkeypatch):
    monkeypatch.setenv(windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR, "shell-secret")

    assert (
        windows_launcher._dashboard_url_with_bypass("http://127.0.0.1:5000/")
        == "http://127.0.0.1:5000/?shell_token=shell-secret"
    )


def test_dashboard_url_with_bypass_appends_to_existing_query_string(monkeypatch):
    monkeypatch.setenv(windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR, "shell-secret")

    assert (
        windows_launcher._dashboard_url_with_bypass("http://127.0.0.1:5000/?foo=bar")
        == "http://127.0.0.1:5000/?foo=bar&shell_token=shell-secret"
    )


def test_dashboard_url_with_bypass_unchanged_when_no_token_set():
    # The autouse fixture above already clears this env var, matching
    # Docker/a plain source checkout, which never set it in the first place.
    assert (
        windows_launcher._dashboard_url_with_bypass("http://127.0.0.1:5000/")
        == "http://127.0.0.1:5000/"
    )


def test_window_api_get_dashboard_info_returns_disabled_state():
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    assert api.get_dashboard_info() == {"url": None, "enabled": False}


def test_window_api_get_dashboard_info_waits_for_port_then_returns_url(monkeypatch):
    waited = []
    monkeypatch.setattr(
        windows_launcher,
        "_wait_until_dashboard_ready",
        lambda url: waited.append(url) or True,
    )
    dashboard_info = {"host": "127.0.0.1", "port": 5000, "url": "http://127.0.0.1:5000/"}
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info,
        initial_tab="config",
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    result = api.get_dashboard_info()

    assert waited == ["http://127.0.0.1:5000/"]
    assert result == {
        "url": "http://127.0.0.1:5000/",
        "initial_tab": "config",
        "enabled": True,
    }


def test_window_api_get_dashboard_info_reports_unavailable_when_never_ready(monkeypatch):
    monkeypatch.setattr(
        windows_launcher, "_wait_until_dashboard_ready", lambda url: False
    )
    dashboard_info = {"host": "127.0.0.1", "port": 5000, "url": "http://127.0.0.1:5000/"}
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info,
        initial_tab="config",
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    result = api.get_dashboard_info()

    assert result["url"] is None
    assert result["enabled"] is True
    assert result["message"]


def test_window_api_open_in_browser_opens_dashboard_url(monkeypatch):
    opened = []
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    dashboard_info = {"host": "127.0.0.1", "port": 5000, "url": "http://127.0.0.1:5000/"}
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    api.open_in_browser()

    assert opened == ["http://127.0.0.1:5000/"]


def test_window_api_open_in_browser_noop_when_dashboard_unavailable(monkeypatch):
    monkeypatch.setattr(
        windows_launcher.webbrowser,
        "open",
        lambda _url: (_ for _ in ()).throw(AssertionError("should not open a browser")),
    )
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    api.open_in_browser()


def test_window_api_open_twitch_activate_opens_the_activation_page(monkeypatch):
    opened = []
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    api.open_twitch_activate()

    assert opened == ["https://www.twitch.tv/activate"]


def test_window_api_open_external_url_opens_http_and_https_links(monkeypatch):
    opened = []
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    api.open_external_url("https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3")
    api.open_external_url("http://example.com")

    assert opened == [
        "https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3",
        "http://example.com",
    ]


def test_window_api_open_external_url_ignores_non_http_schemes(monkeypatch):
    opened = []
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    api.open_external_url("file:///etc/passwd")
    api.open_external_url("javascript:alert(1)")
    api.open_external_url(None)

    assert opened == []


def test_window_api_enable_dashboard_delegates_to_shared_helper(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        windows_launcher,
        "_enable_dashboard_from_shell",
        lambda config_path: calls.append(config_path) or (True, "Saved."),
    )
    config_path = tmp_path / "config.py"
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=config_path,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=None,
    )

    result = api.enable_dashboard()

    assert calls == [config_path]
    assert result == {"success": True, "message": "Saved."}


def test_open_folder_creates_missing_directory(tmp_path, monkeypatch):
    # No os.startfile on this platform - only the directory-creation half of
    # the behavior is exercised here.
    monkeypatch.delattr(windows_launcher.os, "startfile", raising=False)
    target = tmp_path / "not-created-yet"

    windows_launcher._open_folder(target)

    assert target.is_dir()


def test_open_folder_launches_explorer_when_available(tmp_path, monkeypatch):
    # Gated on hasattr(os, "startfile") rather than os.name == "nt" - see
    # _open_folder's docstring for why: flipping the real os.name is a
    # landmine for pathlib's own Path() dispatch (raises NotImplementedError
    # deep inside pytest's internals on some Python versions), so this
    # never touches it.
    calls = []
    monkeypatch.setattr(windows_launcher.os, "startfile", calls.append, raising=False)
    target = tmp_path / "config"

    windows_launcher._open_folder(target)

    assert calls == [target]


def test_open_folder_swallows_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(
        windows_launcher.os,
        "startfile",
        lambda _path: (_ for _ in ()).throw(OSError("no shell available")),
        raising=False,
    )

    windows_launcher._open_folder(tmp_path / "config")  # must not raise


def test_window_api_open_config_folder_opens_configs_parent_directory(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(windows_launcher, "_open_folder", lambda path: calls.append(path))
    config_path = tmp_path / "config" / "config.py"
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=config_path,
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=tmp_path / "logs",
    )

    api.open_config_folder()

    assert calls == [config_path.parent]


def test_window_api_open_logs_folder_opens_the_logs_directory(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(windows_launcher, "_open_folder", lambda path: calls.append(path))
    logs_dir = tmp_path / "logs"
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=tmp_path / "config" / "config.py",
        needs_username=False,
        start_mining=lambda: None,
        logs_dir=logs_dir,
    )

    api.open_logs_folder()

    assert calls == [logs_dir]


def test_window_api_get_setup_info_reflects_constructor_flag():
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=None,
        needs_username=True,
        start_mining=lambda: None,
        logs_dir=None,
    )

    assert api.get_setup_info() == {"needs_username": True}


def test_window_api_submit_username_saves_and_starts_mining(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username'}\n", encoding="utf-8"
    )
    started = []
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=config_path,
        needs_username=True,
        start_mining=lambda: started.append(True),
        logs_dir=None,
    )

    result = api.submit_username(_REAL_USERNAME)

    assert result == {"success": True, "message": None}
    assert started == [True]
    assert api.get_setup_info() == {"needs_username": False}
    assert f"'username': {_REAL_USERNAME!r}" in config_path.read_text(encoding="utf-8")


def test_window_api_submit_username_reports_invalid_username_without_starting(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username'}\n", encoding="utf-8"
    )
    started = []
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(),
        dashboard_info=None,
        initial_tab=None,
        config_path=config_path,
        needs_username=True,
        start_mining=lambda: started.append(True),
        logs_dir=None,
    )

    result = api.submit_username("not a valid username!!!")

    assert result["success"] is False
    assert started == []
    assert api.get_setup_info() == {"needs_username": True}


def test_launch_shell_surfaces_missing_pywebview(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            raise ModuleNotFoundError("No module named 'webview'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ModuleNotFoundError):
        windows_launcher.launch_shell(
            None,
            windows_launcher.ConsoleBuffer(),
            None,
            threading.Thread(),
            Path("config.py"),
            Path(".shell_analytics_prompt_shown"),
            False,
            lambda: None,
            None,
        )


def test_self_test_succeeds_when_webview_importable(monkeypatch, capsys):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            return types.SimpleNamespace()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert windows_launcher.self_test() == 0
    assert "OK" in capsys.readouterr().out


def test_self_test_fails_when_webview_missing(monkeypatch, capsys):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            raise ModuleNotFoundError("No module named 'webview'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert windows_launcher.self_test() == 1
    assert "FAILED" in capsys.readouterr().out


def test_main_dispatches_to_self_test_before_touching_config(monkeypatch, tmp_path):
    # Must short-circuit before prepare_config/os.chdir/etc. run, so this
    # flag stays a pure, side-effect-free import check usable from CI.
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe", "--self-test"])
    monkeypatch.setattr(windows_launcher, "self_test", lambda: 42)
    monkeypatch.setattr(
        windows_launcher,
        "application_directory",
        lambda: (_ for _ in ()).throw(AssertionError("should not be reached")),
    )

    assert windows_launcher.main() == 42


def test_miner_thread_handle_not_alive_before_a_thread_is_assigned():
    # The state during first-run setup: the close handler must be able to
    # ask is_alive() even though start_mining() hasn't run yet.
    handle = windows_launcher._MinerThreadHandle()

    assert handle.is_alive() is False


def test_miner_thread_handle_reflects_assigned_thread():
    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)

    assert handle.is_alive() is True


class _FakeMinerThread:
    def __init__(self, alive):
        self._alive = alive

    def is_alive(self):
        return self._alive


def test_close_confirmation_allows_close_when_miner_thread_finished():
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            raise AssertionError("dialog should not be shown when miner isn't running")

    handler = windows_launcher._make_close_confirmation_handler(
        FakeWindow(), _FakeMinerThread(alive=False)
    )

    assert handler() is True


def test_close_confirmation_blocks_close_by_default_while_mining():
    class FakeWindow:
        def __init__(self):
            self.calls = []

        def create_confirmation_dialog(self, title, message):
            self.calls.append((title, message))
            return False  # simulates the user clicking Cancel

    window = FakeWindow()
    handler = windows_launcher._make_close_confirmation_handler(
        window, _FakeMinerThread(alive=True)
    )

    assert handler() is False
    assert window.calls == [
        ("Stop mining?", "Closing this window will stop mining. Are you sure?")
    ]


def test_close_confirmation_proceeds_when_user_confirms():
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            return True  # simulates the user clicking OK

    handler = windows_launcher._make_close_confirmation_handler(
        FakeWindow(), _FakeMinerThread(alive=True)
    )

    assert handler() is True


def test_close_confirmation_blocks_close_if_dialog_itself_fails():
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            raise RuntimeError("no GUI backend available")

    handler = windows_launcher._make_close_confirmation_handler(
        FakeWindow(), _FakeMinerThread(alive=True)
    )

    assert handler() is False


def test_close_confirmation_cancels_reentrant_call_while_dialog_is_open():
    """A real, unowned WinForms MessageBox doesn't disable the underlying
    form, so this handler can be reentered (e.g. the window's [X] clicked
    again) while its own dialog is still showing. The reentrant call must
    be cancelled outright rather than showing a second dialog - letting it
    through used to let a nested close actually happen, crashing once the
    outer call resumed and tried to close the (already-closed) window
    again."""

    class FakeWindow:
        def __init__(self):
            self.calls = []
            self.handler = None

        def create_confirmation_dialog(self, title, message):
            self.calls.append((title, message))
            reentrant_result = self.handler()
            assert reentrant_result is False
            return True

    window = FakeWindow()
    handler = windows_launcher._make_close_confirmation_handler(
        window, _FakeMinerThread(alive=True)
    )
    window.handler = handler

    assert handler() is True
    assert len(window.calls) == 1


class _FakeWindowConfirms:
    def create_confirmation_dialog(self, title, message):
        return True


class _FakeMiner:
    def __init__(self, raises=None):
        self.calls = []
        self._raises = raises

    def end(self, signum, frame):
        self.calls.append((signum, frame))
        if self._raises is not None:
            raise self._raises


def test_close_confirmation_stops_the_miner_gracefully_when_confirmed():
    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)
    miner = _FakeMiner()
    handle.set_miner(miner)

    handler = windows_launcher._make_close_confirmation_handler(
        _FakeWindowConfirms(), handle
    )

    assert handler() is True
    assert miner.calls == [(None, None)]


def test_close_confirmation_does_not_stop_the_miner_when_cancelled():
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            return False

    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)
    miner = _FakeMiner()
    handle.set_miner(miner)

    handler = windows_launcher._make_close_confirmation_handler(FakeWindow(), handle)

    assert handler() is False
    assert miner.calls == []


def test_close_confirmation_notifies_shutting_down_via_tray_when_confirmed():
    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)
    handle.set_miner(_FakeMiner())
    tray_icon = _FakeTrayIcon()

    handler = windows_launcher._make_close_confirmation_handler(
        _FakeWindowConfirms(), handle, tray_icon=tray_icon
    )

    assert handler() is True
    assert len(tray_icon.notifications) == 1
    message, title = tray_icon.notifications[0]
    assert title == "Twitch Channel Points Miner"
    assert "shutting down" in message.lower()


def test_close_confirmation_does_not_notify_when_cancelled():
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            return False

    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)
    handle.set_miner(_FakeMiner())
    tray_icon = _FakeTrayIcon()

    handler = windows_launcher._make_close_confirmation_handler(
        FakeWindow(), handle, tray_icon=tray_icon
    )

    assert handler() is False
    assert tray_icon.notifications == []


def test_close_confirmation_survives_a_shutdown_notify_failure():
    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)
    miner = _FakeMiner()
    handle.set_miner(miner)

    class BrokenTrayIcon:
        def notify(self, message, title=None):
            raise RuntimeError("notifications unsupported on this backend")

    handler = windows_launcher._make_close_confirmation_handler(
        _FakeWindowConfirms(), handle, tray_icon=BrokenTrayIcon()
    )

    assert handler() is True
    assert miner.calls == [(None, None)]


def test_stop_miner_gracefully_does_nothing_without_a_registered_miner():
    handle = windows_launcher._MinerThreadHandle()

    windows_launcher._stop_miner_gracefully(handle)  # must not raise


def test_stop_miner_gracefully_swallows_end_s_trailing_sys_exit():
    handle = windows_launcher._MinerThreadHandle()
    handle.set_miner(_FakeMiner(raises=SystemExit(0)))

    windows_launcher._stop_miner_gracefully(handle)  # must not propagate


def test_run_miner_thread_reports_a_runtime_error_instead_of_crashing_silently(
    monkeypatch,
):
    shown = []
    monkeypatch.setattr(
        windows_launcher,
        "runner_main",
        lambda argv, **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        windows_launcher, "_show_fatal_error_message", lambda text: shown.append(text)
    )

    windows_launcher._run_miner_thread(["--config-dir", "x"], None)  # must not raise

    assert shown and "boom" in shown[0]


class _FakeEventSlot:
    """Mirrors pywebview's own Event class closely enough for these tests:
    a list of handlers managed via +=/-= (not a single slot), with .set()
    running them all synchronously and returning True if any of them
    returned False - matching should_lock=True's behavior for
    window.events.closing specifically, including that multiple handlers
    are genuinely supported (production code relies on this to swap the
    tray Quit confirmation in for the normal hide-to-tray handler)."""

    def __init__(self):
        self._items = []

    def __iadd__(self, handler):
        self._items.append(handler)
        return self

    def __isub__(self, handler):
        self._items.remove(handler)
        return self

    def set(self, *args, **kwargs):
        results = [item() for item in self._items]
        return any(result is False for result in results)

    @property
    def handler(self):
        # Back-compat convenience for tests written against the old
        # single-handler fake: the most recently added handler, matching
        # how every one of those tests only ever adds one.
        return self._items[-1] if self._items else None


class _FakeEvents:
    def __init__(self):
        self.closing = _FakeEventSlot()
        self.minimized = _FakeEventSlot()
        self.restored = _FakeEventSlot()


class _FakeWindow:
    def __init__(self):
        self.events = _FakeEvents()
        self.hidden = False
        self.destroyed = False

    def show(self):
        self.hidden = False

    def hide(self):
        self.hidden = True

    def destroy(self):
        # Mirrors real WinForms semantics: Close() fires FormClosing (here,
        # events.closing.set()) and only actually closes if no handler
        # vetoes it - the same mechanism the window's own close button
        # already relies on to turn a close into a hide instead.
        if self.events.closing.set():
            return
        self.destroyed = True


class _FakeWebview:
    """A `webview` module stand-in whose start() actually invokes the
    callback launch_shell() passes it, matching pywebview's real contract
    (dialogs, unlike event handlers, only work once that callback runs)."""

    def __init__(self):
        self.window = _FakeWindow()
        self.create_window_args = None
        self.create_window_kwargs = None
        self.start_icon = None

    def create_window(self, *args, **kwargs):
        self.create_window_args = args
        self.create_window_kwargs = kwargs
        return self.window

    def start(self, func=None, icon=None):
        self.start_icon = icon
        if func is not None:
            func()


def _patch_fake_webview(monkeypatch, fake_webview):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            return fake_webview
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)


def test_launch_shell_enables_text_selection_and_shows_version_in_title(
    tmp_path, monkeypatch
):
    # text_select defaults to False in pywebview, which would make the
    # Console tab's whole point - reading and copying logs - impossible.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=True),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    assert fake_webview.create_window_kwargs["text_select"] is True
    assert windows_launcher.__version__ in fake_webview.create_window_args[0]


def test_launch_shell_passes_the_bundled_icon_to_webview_start(tmp_path, monkeypatch):
    # Without this, pywebview falls back to extracting whatever icon
    # Windows associates with sys.executable at runtime, which is generic
    # when run unfrozen from source and unreliable in a frozen onefile
    # build - the running window's icon must come from the bundled .ico
    # explicitly, not that fallback.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=True),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    assert fake_webview.start_icon == str(
        windows_launcher.bundled_file(
            os.path.join("assets", windows_launcher._TRAY_ICON_FILE)
        )
    )


def test_launch_shell_wires_close_confirmation_handler(tmp_path, monkeypatch):
    # Mimics pywebview's `window.events.closing += handler` protocol: `+=`
    # calls __iadd__ and reassigns its *return value* back onto
    # `window.events.closing`, so the handler itself is captured as a plain
    # attribute here rather than relied on via that reassignment.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    # This test is about the close handler, not the analytics prompt (which
    # would otherwise also run via the fake start() above); stub it out.
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=True),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    assert callable(fake_webview.window.events.closing.handler)


def test_launch_shell_runs_analytics_prompt_check_via_start_callback(tmp_path, monkeypatch):
    # create_confirmation_dialog (and other dialogs) require the GUI event
    # loop that webview.start() begins - pywebview's own docs run them via
    # a callback passed to start(), not before it's called. This confirms
    # launch_shell follows that same contract for the analytics prompt.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    calls = []
    monkeypatch.setattr(
        windows_launcher,
        "_maybe_prompt_to_enable_analytics",
        lambda window, dashboard_info, config_path, prompt_marker: calls.append(
            (window, dashboard_info, config_path, prompt_marker)
        ),
    )
    config_path = tmp_path / "config.py"
    prompt_marker = tmp_path / ".shell_analytics_prompt_shown"

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=True),
        config_path,
        prompt_marker,
        False,
        lambda: None,
        None,
    )

    assert calls == [(fake_webview.window, None, config_path, prompt_marker)]


def test_launch_shell_skips_analytics_prompt_while_username_setup_pending(tmp_path, monkeypatch):
    # Asking about the dashboard before the user has even entered a
    # username would be premature - see launch_shell's _on_started.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(
        windows_launcher,
        "_maybe_prompt_to_enable_analytics",
        lambda *a: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=False),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        True,
        lambda: None,
        None,
    )


def test_show_fatal_error_message_uses_native_message_box_on_windows(monkeypatch):
    calls = []

    class FakeUser32:
        def MessageBoxW(self, hwnd, text, caption, flags):
            calls.append((hwnd, text, caption, flags))

    class FakeWindll:
        user32 = FakeUser32()

    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.ctypes, "windll", FakeWindll(), raising=False)

    windows_launcher._show_fatal_error_message("boom")

    assert calls == [(0, "boom", "Twitch Channel Points Miner - Error", 0x10)]


def test_show_fatal_error_message_noop_on_other_platforms(monkeypatch):
    monkeypatch.setattr(windows_launcher.os, "name", "posix")
    # Accessing .windll at all (even just to fail) would be a bug on
    # non-Windows; deleting the attribute makes any such access raise
    # immediately instead of silently succeeding because ctypes.windll
    # happens to still exist from a previous test's monkeypatch.
    monkeypatch.delattr(windows_launcher.ctypes, "windll", raising=False)

    windows_launcher._show_fatal_error_message("boom")  # must not raise


def test_show_fatal_error_message_swallows_dialog_failures(monkeypatch):
    class ExplodingWindll:
        @property
        def user32(self):
            raise RuntimeError("no such API")

    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.ctypes, "windll", ExplodingWindll(), raising=False)

    windows_launcher._show_fatal_error_message("boom")  # must not raise


def test_run_shows_native_error_and_reraises_on_uncaught_failure(monkeypatch):
    shown = []
    monkeypatch.setattr(
        windows_launcher,
        "main",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(
        windows_launcher, "_show_fatal_error_message", lambda text: shown.append(text)
    )

    with pytest.raises(RuntimeError, match="boom"):
        windows_launcher.run()

    assert len(shown) == 1
    assert "boom" in shown[0]


def test_run_returns_main_result_without_showing_error_on_success(monkeypatch):
    monkeypatch.setattr(windows_launcher, "main", lambda: 0)
    monkeypatch.setattr(
        windows_launcher,
        "_show_fatal_error_message",
        lambda _text: (_ for _ in ()).throw(AssertionError("should not show on success")),
    )

    assert windows_launcher.run() == 0


def test_enable_dashboard_from_shell_writes_config_and_reports_restart_needed(
    tmp_path, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        windows_launcher,
        "secrets",
        type("FakeSecrets", (), {"token_urlsafe": staticmethod(lambda _n: "generatedpw")}),
    )

    def fake_enable_analytics_dashboard(config_path, password):
        calls.append((config_path, password))

    import TwitchChannelPointsMiner.config_editor as config_editor

    monkeypatch.setattr(
        config_editor, "enable_analytics_dashboard", fake_enable_analytics_dashboard
    )
    config_path = tmp_path / "config.py"

    success, message = windows_launcher._enable_dashboard_from_shell(config_path)

    assert success is True
    assert "restart" in message.lower()
    assert calls == [(config_path, "generatedpw")]


def test_enable_dashboard_from_shell_reports_failure_without_raising(tmp_path, monkeypatch):
    import TwitchChannelPointsMiner.config_editor as config_editor

    def raise_error(_config_path, _password):
        raise config_editor.ConfigEditError("ANALYTICS_CONFIG assignment not found")

    monkeypatch.setattr(config_editor, "enable_analytics_dashboard", raise_error)

    success, message = windows_launcher._enable_dashboard_from_shell(tmp_path / "config.py")

    assert success is False
    assert "ANALYTICS_CONFIG assignment not found" in message


def test_maybe_prompt_skips_when_analytics_already_enabled(tmp_path):
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            raise AssertionError("must not prompt when analytics is already on")

    prompt_marker = tmp_path / ".shell_analytics_prompt_shown"
    dashboard_info = {"host": "127.0.0.1", "port": 5000, "url": "http://127.0.0.1:5000/"}

    windows_launcher._maybe_prompt_to_enable_analytics(
        FakeWindow(), dashboard_info, tmp_path / "config.py", prompt_marker
    )

    assert not prompt_marker.is_file()


def test_maybe_prompt_skips_when_already_shown(tmp_path):
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            raise AssertionError("must not prompt a second time")

    prompt_marker = tmp_path / ".shell_analytics_prompt_shown"
    prompt_marker.touch()

    windows_launcher._maybe_prompt_to_enable_analytics(
        FakeWindow(), None, tmp_path / "config.py", prompt_marker
    )


def test_maybe_prompt_marks_shown_and_writes_nothing_on_no(tmp_path, monkeypatch):
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            return False  # simulates the user clicking No

    enable_calls = []
    monkeypatch.setattr(
        windows_launcher,
        "_enable_dashboard_from_shell",
        lambda config_path: enable_calls.append(config_path) or (True, "unused"),
    )
    prompt_marker = tmp_path / ".shell_analytics_prompt_shown"

    windows_launcher._maybe_prompt_to_enable_analytics(
        FakeWindow(), None, tmp_path / "config.py", prompt_marker
    )

    assert prompt_marker.is_file()
    assert enable_calls == []


def test_maybe_prompt_enables_and_marks_shown_on_yes(tmp_path, monkeypatch):
    dialogs = []

    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            dialogs.append((title, message))
            return len(dialogs) == 1  # Yes to the first (consent) dialog only

    enable_calls = []
    monkeypatch.setattr(
        windows_launcher,
        "_enable_dashboard_from_shell",
        lambda config_path: enable_calls.append(config_path)
        or (True, "Saved. Restart the app for the dashboard to start."),
    )
    prompt_marker = tmp_path / ".shell_analytics_prompt_shown"
    config_path = tmp_path / "config.py"

    windows_launcher._maybe_prompt_to_enable_analytics(
        FakeWindow(), None, config_path, prompt_marker
    )

    assert prompt_marker.is_file()
    assert enable_calls == [config_path]
    # A second, informational dialog reports the result of enabling it.
    assert len(dialogs) == 2
    assert dialogs[1] == ("Dashboard", "Saved. Restart the app for the dashboard to start.")


def test_maybe_prompt_does_not_mark_shown_if_dialog_itself_fails(tmp_path):
    class FakeWindow:
        def create_confirmation_dialog(self, title, message):
            raise RuntimeError("no GUI backend available")

    prompt_marker = tmp_path / ".shell_analytics_prompt_shown"

    windows_launcher._maybe_prompt_to_enable_analytics(
        FakeWindow(), None, tmp_path / "config.py", prompt_marker
    )

    # Not marked as "answered" - a future launch (e.g. once the GUI backend
    # works) should still get a chance to offer this.
    assert not prompt_marker.is_file()


def test_main_installer_style_disabled_config_shows_disabled_flow_end_to_end(
    tmp_path, monkeypatch
):
    # Simulates a fresh install where the installer's "Enable the analytics
    # dashboard" checkbox was left unchecked: config.py already exists (the
    # installer created it, so `created` is False here) with analytics off,
    # and neither shell marker exists yet. The exe's first run must still
    # land on the part-1/part-2 disabled-analytics flow, not a broken
    # Dashboard tab or a skipped onboarding view.
    from TwitchChannelPointsMiner.config_migration import CONFIG_VERSION

    # CONFIG_VERSION matches the current schema so the unrelated schema
    # migrator (triggered by resolve_dashboard_info's own _load_config call)
    # is a verified no-op, isolating the disabled-analytics flow under test.
    original_config = (
        f"CONFIG_VERSION = {CONFIG_VERSION}\n"
        f"MINER_CONFIG = {{'username': {_REAL_USERNAME!r}, 'enable_analytics': False}}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n"
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(original_config, encoding="utf-8")
    shell_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)

    def fake_launch_shell(
        dashboard_info,
        console_buffer,
        initial_tab,
        miner_thread,
        config_path,
        prompt_marker,
        needs_username,
        start_mining,
        logs_dir,
        start_minimized=False,
    ):
        shell_calls.append((dashboard_info, initial_tab))
        # Exercises the real prompt-gating logic (not just that launch_shell
        # was reached), using a fake window so no real dialog is shown.
        class FakeWindow:
            def create_confirmation_dialog(self, title, message):
                return False

        windows_launcher._maybe_prompt_to_enable_analytics(
            FakeWindow(), dashboard_info, config_path, prompt_marker
        )

    monkeypatch.setattr(windows_launcher, "launch_shell", fake_launch_shell)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    # Dashboard tab has nothing to show (analytics off) - the part-1 panel.
    assert shell_calls == [(None, "config")]
    # The one-time part-2 prompt ran and marked itself shown.
    assert (config_dir / ".shell_analytics_prompt_shown").is_file()
    # No config write happened (the fake dialog answered "No").
    assert (config_dir / "config.py").read_text(encoding="utf-8") == original_config

    # A later launch (prompt already shown, onboarding already touched by
    # the shell in a full run) does not prompt again.
    shell_calls.clear()
    assert windows_launcher.main() == 0
    assert shell_calls == [(None, None)]


# --- Tray icon / minimize-to-tray ---------------------------------------


def test_hide_to_tray_handler_hides_window_and_cancels_the_close():
    class FakeWindow:
        def __init__(self):
            self.hidden = False

        def hide(self):
            self.hidden = True

    window = FakeWindow()
    tray_icon = _FakeTrayIcon()
    handler = windows_launcher._make_hide_to_tray_handler(window, tray_icon)

    assert handler() is False
    assert window.hidden is True
    assert tray_icon.notifications == [
        (
            "Still running and mining in the background. "
            "Use the tray icon's Quit to stop it.",
            "Twitch Channel Points Miner",
        )
    ]


def test_hide_to_tray_handler_survives_a_notify_failure():
    class FakeWindow:
        def hide(self):
            pass

    class BrokenTrayIcon:
        def notify(self, message, title=None):
            raise RuntimeError("notifications unsupported on this backend")

    handler = windows_launcher._make_hide_to_tray_handler(FakeWindow(), BrokenTrayIcon())

    assert handler() is False


class _FakeTrayIcon:
    def __init__(self):
        self.ran = False
        self.stopped = False
        self.notifications = []

    def run(self):
        self.ran = True

    def stop(self):
        self.stopped = True

    def notify(self, message, title=None):
        self.notifications.append((message, title))


def test_launch_shell_falls_back_to_close_confirmation_when_tray_unavailable(
    tmp_path, monkeypatch
):
    # No pystray installed in this environment (the real, un-mocked case for
    # CI/Linux) - launch_shell must still leave the window closable.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=False),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    assert callable(fake_webview.window.events.closing.handler)
    # The fallback handler is the old confirm-on-close one: with no miner
    # running it allows the close outright.
    assert fake_webview.window.events.closing.handler() is True


def test_launch_shell_wires_hide_to_tray_when_tray_is_available(tmp_path, monkeypatch):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)
    fake_icon = _FakeTrayIcon()
    monkeypatch.setattr(
        windows_launcher, "_build_tray_icon", lambda on_show, on_quit: fake_icon
    )

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=True),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    # Closing the window now just hides it - never a "stop mining?" prompt.
    window = fake_webview.window
    assert window.events.closing.handler() is False
    assert getattr(window, "hidden", False) is True


def test_launch_shell_logs_minimize_and_restore_events(tmp_path, monkeypatch, caplog):
    # Diagnostic logging for reports of the window not reappearing in the
    # taskbar after being minimized - see the comment above where these are
    # wired up in launch_shell. Not app logic, just visibility into whether
    # the OS-level events even fired.
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    with caplog.at_level(logging.INFO, logger="windows_launcher"):
        windows_launcher.launch_shell(
            None,
            windows_launcher.ConsoleBuffer(),
            None,
            _FakeMinerThread(alive=False),
            tmp_path / "config.py",
            tmp_path / ".shell_analytics_prompt_shown",
            False,
            lambda: None,
            None,
        )
        fake_webview.window.events.minimized.set()
        fake_webview.window.events.restored.set()

    assert "minimized" in caplog.text.lower()
    assert "restored" in caplog.text.lower()


def test_launch_shell_logs_when_close_button_hides_to_tray(tmp_path, monkeypatch, caplog):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)
    fake_icon = _FakeTrayIcon()
    monkeypatch.setattr(
        windows_launcher, "_build_tray_icon", lambda on_show, on_quit: fake_icon
    )

    with caplog.at_level(logging.INFO, logger="windows_launcher"):
        windows_launcher.launch_shell(
            None,
            windows_launcher.ConsoleBuffer(),
            None,
            _FakeMinerThread(alive=True),
            tmp_path / "config.py",
            tmp_path / ".shell_analytics_prompt_shown",
            False,
            lambda: None,
            None,
        )
        fake_webview.window.events.closing.handler()

    assert "hiding the window to the tray" in caplog.text.lower()


def test_launch_shell_logs_when_tray_is_unavailable(tmp_path, monkeypatch, caplog):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    def _raise(on_show, on_quit):
        raise RuntimeError("no tray on this desktop")

    monkeypatch.setattr(windows_launcher, "_build_tray_icon", _raise)

    with caplog.at_level(logging.INFO, logger="windows_launcher"):
        windows_launcher.launch_shell(
            None,
            windows_launcher.ConsoleBuffer(),
            None,
            _FakeMinerThread(alive=False),
            tmp_path / "config.py",
            tmp_path / ".shell_analytics_prompt_shown",
            False,
            lambda: None,
            None,
        )

    assert "system tray icon unavailable" in caplog.text.lower()


def _launch_shell_with_tray(tmp_path, monkeypatch, miner_alive=True):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    captured = {}
    tray_icon = _FakeTrayIcon()

    def fake_build_tray_icon(on_show, on_quit):
        captured["on_show"] = on_show
        captured["on_quit"] = on_quit
        return tray_icon

    monkeypatch.setattr(windows_launcher, "_build_tray_icon", fake_build_tray_icon)

    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=miner_alive)
    miner = _FakeMiner()
    handle.set_miner(miner)

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        handle,
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    return fake_webview.window, captured, tray_icon, miner


def test_launch_shell_quit_from_tray_stops_miner_and_destroys_window(tmp_path, monkeypatch):
    window, captured, tray_icon, miner = _launch_shell_with_tray(tmp_path, monkeypatch)
    hide_handler = window.events.closing.handler
    dialog_calls = []

    def fake_dialog(title, message):
        # Must run only once the hide handler has been swapped out -
        # otherwise a real WinForms FormClosing veto from a leftover hide
        # handler could cancel the close before this is even reached. This
        # is what actually broke before: create_confirmation_dialog was
        # called directly from pystray's own thread, unmarshaled, and could
        # leave the app stuck after being answered - see
        # _confirm_and_quit_from_tray's docstring.
        dialog_calls.append(hide_handler not in window.events.closing._items)
        return True

    window.create_confirmation_dialog = fake_dialog

    captured["on_quit"]()

    assert dialog_calls == [True]
    assert miner.calls == [(None, None)]
    assert window.destroyed is True
    assert tray_icon.stopped is True


def test_launch_shell_quit_from_tray_shows_shutting_down_notification(tmp_path, monkeypatch):
    """The tray-Quit path is where users actually experience the multi-
    minute graceful shutdown (IRC leave, websocket teardown, watcher joins)
    freezing the window - see _stop_miner_gracefully's docstring. A tray
    notification fired before that blocking work starts is the only
    feedback the user gets that it's still working rather than stuck."""
    window, captured, tray_icon, miner = _launch_shell_with_tray(tmp_path, monkeypatch)
    window.create_confirmation_dialog = lambda title, message: True

    captured["on_quit"]()

    assert miner.calls == [(None, None)]
    assert len(tray_icon.notifications) == 1
    message, title = tray_icon.notifications[0]
    assert title == "Twitch Channel Points Miner"
    assert "shutting down" in message.lower()


def test_launch_shell_quit_from_tray_reentrant_close_is_cancelled(tmp_path, monkeypatch):
    """A second close attempt while the tray-Quit confirmation dialog is
    still open (e.g. the window's own [X] clicked again) must be cancelled
    outright, not reach create_confirmation_dialog or destroy() a second
    time. A real, unowned WinForms MessageBox doesn't block clicks from
    reaching the underlying form, so this reentrant FormClosing is a real
    scenario - letting it through used to close/dispose the window from
    inside the nested call and crash once the outer call resumed and tried
    to close it again (pywebview raising KeyError on its window-instance
    table). See _make_close_confirmation_handler's docstring."""
    window, captured, tray_icon, miner = _launch_shell_with_tray(tmp_path, monkeypatch)
    dialog_calls = []

    def fake_dialog(title, message):
        dialog_calls.append(1)
        reentrant_cancelled = window.events.closing.set()
        assert reentrant_cancelled is True
        return True

    window.create_confirmation_dialog = fake_dialog

    captured["on_quit"]()

    assert dialog_calls == [1]
    assert miner.calls == [(None, None)]
    assert window.destroyed is True
    assert tray_icon.stopped is True


def test_launch_shell_quit_from_tray_cancelled_restores_hide_to_tray(tmp_path, monkeypatch):
    window, captured, tray_icon, miner = _launch_shell_with_tray(tmp_path, monkeypatch)
    hide_handler = window.events.closing.handler
    window.create_confirmation_dialog = lambda title, message: False  # User clicks Cancel.

    captured["on_quit"]()

    assert window.destroyed is False
    assert tray_icon.stopped is False
    assert miner.calls == []
    # The window must still hide-to-tray normally afterward, not be left
    # with no closing handler at all (or the cancelled one-shot confirm
    # handler still wired in).
    assert window.events.closing.handler is hide_handler
    assert window.events.closing.handler() is False
    assert window.hidden is True


def test_launch_shell_tray_show_is_ignored_while_quit_confirmation_is_open(
    tmp_path, monkeypatch
):
    """Regression test for a real crash: pystray's Show item calls
    window.show(), which marshals onto the GUI thread via Control.Invoke -
    a call a nested modal message loop (like WinForms' MessageBox.Show,
    used for the "Stop mining?" confirmation) still pumps, since an
    unowned MessageBox doesn't stop other threads' marshaled calls from
    reaching the form. A Show landing while the window is mid-teardown
    crashed pywebview (KeyError on its window-instance table - the same
    shape of bug as the reentrant-close case above, just reached via Show
    instead of a second Close). The `on_show` passed to the tray icon must
    ignore Show while a quit is in progress - see
    _make_close_confirmation_handler's docstring on `quitting`."""
    window, captured, tray_icon, miner = _launch_shell_with_tray(tmp_path, monkeypatch)
    window.hidden = True

    def fake_dialog(title, message):
        captured["on_show"]()  # A concurrent tray "Show" click, simulated.
        return True

    window.create_confirmation_dialog = fake_dialog

    captured["on_quit"]()

    assert window.hidden is True  # Show never actually ran.
    assert window.destroyed is True
    assert tray_icon.stopped is True


def test_launch_shell_tray_show_works_again_after_quit_is_cancelled(tmp_path, monkeypatch):
    window, captured, tray_icon, miner = _launch_shell_with_tray(tmp_path, monkeypatch)
    window.hidden = True
    window.create_confirmation_dialog = lambda title, message: False  # User clicks Cancel.

    captured["on_quit"]()
    captured["on_show"]()

    assert window.hidden is False


def test_launch_shell_wires_guarded_show_into_single_instance_listener(tmp_path, monkeypatch):
    """Regression test for a coverage gap: launch_shell must hand
    _start_single_instance_listener the same quitting-guarded show as the
    tray's on_show, not a raw window.show - otherwise a second-launch ping
    landing during a quit confirmation dialog reintroduces, for that call
    site, the exact race _guarded_show exists to close. The tray path has
    test_launch_shell_tray_show_is_ignored_while_quit_confirmation_is_open;
    nothing previously exercised this one, so a future accidental revert to
    _start_single_instance_listener(window.show) would pass the whole suite."""
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    captured = {}
    monkeypatch.setattr(
        windows_launcher,
        "_start_single_instance_listener",
        lambda show: captured.__setitem__("show", show),
    )

    handle = windows_launcher._MinerThreadHandle()
    handle.thread = _FakeMinerThread(alive=True)
    handle.set_miner(_FakeMiner())

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        handle,
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    window = fake_webview.window
    window.create_confirmation_dialog = lambda title, message: True  # Confirms quit.
    window.hidden = True

    window.events.closing.set()  # The window's own [X], confirmed closed.
    captured["show"]()  # A second-instance ping arriving right after.

    assert window.hidden is True  # Ignored - not the raw, unguarded window.show.


def test_build_tray_icon_uses_bundled_icon_file(monkeypatch):
    opened = {}

    class FakeImage:
        @staticmethod
        def open(path):
            opened["path"] = path
            return "the-image"

    class FakeMenuItem:
        def __init__(self, text, action, default=False):
            self.text = text
            self.action = action
            self.default = default

    class FakeMenu:
        def __init__(self, *items):
            self.items = items

    class FakeIcon:
        def __init__(self, name, image, title, menu):
            self.name = name
            self.image = image
            self.title = title
            self.menu = menu

    fake_pystray = types.SimpleNamespace(Menu=FakeMenu, MenuItem=FakeMenuItem, Icon=FakeIcon)
    # `Image` set directly on the fake "PIL" package so `from PIL import
    # Image` resolves via a plain hasattr() check, without the real import
    # system needing to locate an actual "PIL.Image" submodule.
    monkeypatch.setitem(
        windows_launcher.sys.modules, "PIL", types.SimpleNamespace(Image=FakeImage)
    )
    monkeypatch.setitem(windows_launcher.sys.modules, "pystray", fake_pystray)

    icon = windows_launcher._build_tray_icon(lambda: None, lambda: None)

    assert icon.image == "the-image"
    assert str(opened["path"]).endswith(windows_launcher._TRAY_ICON_FILE)
    assert [item.text for item in icon.menu.items] == ["Show", "Quit"]
    # "Show" must be pystray's default item - on Windows that's what makes
    # double-clicking the tray icon itself (not just opening its menu) show
    # the window; otherwise a double-click does nothing.
    assert [item.default for item in icon.menu.items] == [True, False]


# --- Single instance --------------------------------------------------


def test_acquire_single_instance_lock_true_on_non_windows(monkeypatch):
    monkeypatch.setattr(windows_launcher.os, "name", "posix")
    # Accessing .windll at all would be a bug here - see the matching
    # comment on test_show_fatal_error_message_noop_on_other_platforms.
    monkeypatch.delattr(windows_launcher.ctypes, "windll", raising=False)

    assert windows_launcher._acquire_single_instance_lock() is True


def test_acquire_single_instance_lock_true_for_the_first_instance(monkeypatch):
    class FakeKernel32:
        def SetLastError(self, code):
            pass

        def CreateMutexW(self, security, initial_owner, name):
            self.requested_name = name
            return 1  # Any truthy handle.

        def GetLastError(self):
            return 0

    class FakeWindll:
        kernel32 = FakeKernel32()

    fake_windll = FakeWindll()
    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.ctypes, "windll", fake_windll, raising=False)

    assert windows_launcher._acquire_single_instance_lock() is True
    assert (
        fake_windll.kernel32.requested_name
        == windows_launcher._SINGLE_INSTANCE_MUTEX_NAME
    )


def test_acquire_single_instance_lock_false_when_already_running(monkeypatch):
    class FakeKernel32:
        def SetLastError(self, code):
            pass

        def CreateMutexW(self, security, initial_owner, name):
            return 1

        def GetLastError(self):
            return 183  # ERROR_ALREADY_EXISTS

    class FakeWindll:
        kernel32 = FakeKernel32()

    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.ctypes, "windll", FakeWindll(), raising=False)

    assert windows_launcher._acquire_single_instance_lock() is False


def test_acquire_single_instance_lock_fails_open_on_error(monkeypatch):
    class ExplodingWindll:
        @property
        def kernel32(self):
            raise RuntimeError("no such API")

    monkeypatch.setattr(windows_launcher.os, "name", "nt")
    monkeypatch.setattr(windows_launcher.ctypes, "windll", ExplodingWindll(), raising=False)

    # Never block launch just because the check itself broke.
    assert windows_launcher._acquire_single_instance_lock() is True


def _free_loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_single_instance_listener_shows_window_when_pinged(monkeypatch):
    # Uses a real, dynamically-probed port (not the module's fixed
    # constant) so this can't collide with anything else already using
    # that port on the machine running the test.
    monkeypatch.setattr(
        windows_launcher, "_SINGLE_INSTANCE_PORT", _free_loopback_port()
    )

    class FakeWindow:
        def __init__(self):
            self.shown = threading.Event()

        def show(self):
            self.shown.set()

    window = FakeWindow()
    windows_launcher._start_single_instance_listener(window.show)

    # The listener's background thread may not have bound/started
    # listening yet by the time this runs - retry the ping instead of
    # relying on a single attempt racing that startup.
    for _attempt in range(20):
        if window.shown.wait(timeout=0.1):
            break
        windows_launcher._notify_running_instance()

    assert window.shown.is_set() is True


def test_notify_running_instance_is_silent_with_nothing_listening(monkeypatch):
    monkeypatch.setattr(
        windows_launcher, "_SINGLE_INSTANCE_PORT", _free_loopback_port()
    )

    windows_launcher._notify_running_instance()  # must not raise


def test_main_shows_message_and_pings_existing_instance_when_already_running(
    monkeypatch,
):
    monkeypatch.setattr(windows_launcher, "_acquire_single_instance_lock", lambda: False)
    notified = []
    shown = []
    monkeypatch.setattr(
        windows_launcher, "_notify_running_instance", lambda: notified.append(True)
    )
    monkeypatch.setattr(
        windows_launcher, "_message_box", lambda text, title, flags: shown.append(text)
    )

    result = windows_launcher.main()

    assert result == 0
    assert notified == [True]
    assert len(shown) == 1


def test_main_convert_only_is_exempt_from_the_single_instance_check(
    tmp_path, monkeypatch
):
    # A scripted `--convert-only` invocation must run even while the
    # desktop app is already open - it's not "a second launch of the app".
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    monkeypatch.setattr(
        windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe", "--convert-only"]
    )
    monkeypatch.setattr(windows_launcher, "_acquire_single_instance_lock", lambda: False)
    monkeypatch.setattr(
        windows_launcher,
        "_notify_running_instance",
        lambda: (_ for _ in ()).throw(AssertionError("should not be called")),
    )

    assert windows_launcher.main() == 0


# --- Start on Windows login -----------------------------------------------


def _fake_winreg_module():
    """A minimal stand-in for the stdlib `winreg` module (Windows-only, so
    real registry access can't be exercised here) - a plain dict backs a
    single HKCU key, matching just enough of the API surface
    is_autostart_enabled/set_autostart_enabled actually use."""

    store = {}

    class FakeKeyHandle:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def OpenKey(hive, path, *args):
        if path not in store and not args:
            # QueryValueEx path: reading a key that was never created.
            raise FileNotFoundError(path)
        store.setdefault(path, {})
        return FakeKeyHandle()

    def QueryValueEx(key, name):
        for values in store.values():
            if name in values:
                return values[name], 1  # 1 == REG_SZ, not asserted on
        raise FileNotFoundError(name)

    def SetValueEx(key, name, reserved, value_type, value):
        # Only ever called against the key most recently opened for writing.
        store.setdefault(windows_launcher._AUTOSTART_KEY_PATH, {})[name] = value

    def DeleteValue(key, name):
        values = store.setdefault(windows_launcher._AUTOSTART_KEY_PATH, {})
        if name not in values:
            raise FileNotFoundError(name)
        del values[name]

    module = types.SimpleNamespace(
        HKEY_CURRENT_USER=object(),
        KEY_SET_VALUE=1,
        REG_SZ=1,
        OpenKey=OpenKey,
        QueryValueEx=QueryValueEx,
        SetValueEx=SetValueEx,
        DeleteValue=DeleteValue,
    )
    return module, store


def test_is_autostart_enabled_false_when_not_frozen(monkeypatch):
    monkeypatch.setattr(windows_launcher.sys, "frozen", False, raising=False)

    assert windows_launcher.is_autostart_enabled() is False


def test_is_autostart_enabled_false_when_registry_value_missing(monkeypatch):
    monkeypatch.setattr(windows_launcher.sys, "frozen", True, raising=False)
    fake_winreg, _store = _fake_winreg_module()
    monkeypatch.setitem(windows_launcher.sys.modules, "winreg", fake_winreg)

    assert windows_launcher.is_autostart_enabled() is False


def test_set_autostart_enabled_writes_command_and_is_then_reported_enabled(monkeypatch):
    monkeypatch.setattr(windows_launcher.sys, "frozen", True, raising=False)
    fake_winreg, store = _fake_winreg_module()
    monkeypatch.setitem(windows_launcher.sys.modules, "winreg", fake_winreg)

    success, message = windows_launcher.set_autostart_enabled(True)

    assert success is True
    assert (
        store[windows_launcher._AUTOSTART_KEY_PATH][windows_launcher._AUTOSTART_VALUE_NAME]
        == windows_launcher._autostart_command()
    )
    assert windows_launcher.is_autostart_enabled() is True


def test_set_autostart_enabled_removes_existing_value(monkeypatch):
    monkeypatch.setattr(windows_launcher.sys, "frozen", True, raising=False)
    fake_winreg, store = _fake_winreg_module()
    monkeypatch.setitem(windows_launcher.sys.modules, "winreg", fake_winreg)
    windows_launcher.set_autostart_enabled(True)

    success, _message = windows_launcher.set_autostart_enabled(False)

    assert success is True
    assert windows_launcher._AUTOSTART_VALUE_NAME not in store.get(
        windows_launcher._AUTOSTART_KEY_PATH, {}
    )
    assert windows_launcher.is_autostart_enabled() is False


def test_set_autostart_enabled_disabling_an_already_absent_value_is_a_noop(monkeypatch):
    monkeypatch.setattr(windows_launcher.sys, "frozen", True, raising=False)
    fake_winreg, _store = _fake_winreg_module()
    monkeypatch.setitem(windows_launcher.sys.modules, "winreg", fake_winreg)

    success, _message = windows_launcher.set_autostart_enabled(False)

    assert success is True


def test_set_autostart_enabled_not_available_for_a_source_checkout(monkeypatch):
    monkeypatch.setattr(windows_launcher.sys, "frozen", False, raising=False)

    success, message = windows_launcher.set_autostart_enabled(True)

    assert success is False
    assert "installed app" in message


def test_window_api_get_autostart_info_reflects_current_state(monkeypatch):
    monkeypatch.setattr(windows_launcher, "is_autostart_enabled", lambda: True)
    monkeypatch.setattr(windows_launcher.sys, "frozen", True, raising=False)
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), None, None, Path("config.py"), False, lambda: None, None
    )

    assert api.get_autostart_info() == {"available": True, "enabled": True}


def test_window_api_set_autostart_delegates_to_helper(monkeypatch):
    calls = []
    monkeypatch.setattr(
        windows_launcher,
        "set_autostart_enabled",
        lambda enabled: calls.append(enabled) or (True, "Saved."),
    )
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), None, None, Path("config.py"), False, lambda: None, None
    )

    result = api.set_autostart(True)

    assert calls == [True]
    assert result == {"success": True, "message": "Saved."}


# --- close behavior (Settings tab) ------------------------------------------


def test_read_close_behavior_defaults_to_tray_when_no_prefs_file(tmp_path):
    assert windows_launcher._read_close_behavior(tmp_path / "config.py") == "tray"


def test_write_then_read_close_behavior_round_trips(tmp_path):
    config_path = tmp_path / "config.py"

    success, message = windows_launcher._write_close_behavior(config_path, "quit")

    assert success is True
    assert message == "Saved."
    assert windows_launcher._read_close_behavior(config_path) == "quit"


def test_write_close_behavior_rejects_unknown_value(tmp_path):
    success, message = windows_launcher._write_close_behavior(tmp_path / "config.py", "bogus")

    assert success is False
    assert "Invalid" in message


def test_read_close_behavior_ignores_unrecognized_stored_value(tmp_path):
    config_path = tmp_path / "config.py"
    windows_launcher._shell_prefs_path(config_path).write_text(
        '{"close_behavior": "bogus"}', encoding="utf-8"
    )

    assert windows_launcher._read_close_behavior(config_path) == "tray"


def test_read_close_behavior_tolerates_corrupt_prefs_file(tmp_path):
    config_path = tmp_path / "config.py"
    windows_launcher._shell_prefs_path(config_path).write_text("not json", encoding="utf-8")

    assert windows_launcher._read_close_behavior(config_path) == "tray"


def test_write_close_behavior_preserves_other_keys_in_prefs_file(tmp_path):
    config_path = tmp_path / "config.py"
    windows_launcher._shell_prefs_path(config_path).write_text(
        '{"other_setting": 42}', encoding="utf-8"
    )

    windows_launcher._write_close_behavior(config_path, "quit")

    saved = json.loads(windows_launcher._shell_prefs_path(config_path).read_text(encoding="utf-8"))
    assert saved == {"other_setting": 42, "close_behavior": "quit"}


def test_make_close_dispatcher_routes_to_hide_handler_by_default():
    calls = []
    dispatcher = windows_launcher._make_close_dispatcher(
        lambda: "tray", lambda: calls.append("hide") or False, lambda: calls.append("quit") or True
    )

    result = dispatcher()

    assert calls == ["hide"]
    assert result is False


def test_make_close_dispatcher_routes_to_quit_handler_when_preference_is_quit():
    calls = []
    dispatcher = windows_launcher._make_close_dispatcher(
        lambda: "quit", lambda: calls.append("hide") or False, lambda: calls.append("quit") or True
    )

    result = dispatcher()

    assert calls == ["quit"]
    assert result is True


def test_window_api_get_close_behavior_info_reports_availability_and_value(tmp_path):
    config_path = tmp_path / "config.py"
    windows_launcher._write_close_behavior(config_path, "quit")
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), None, None, config_path, False, lambda: None, None
    )
    api._tray_available = True

    assert api.get_close_behavior_info() == {"available": True, "value": "quit"}


def test_window_api_get_close_behavior_info_defaults_unavailable(tmp_path):
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), None, None, tmp_path / "config.py", False, lambda: None, None
    )

    info = api.get_close_behavior_info()

    assert info == {"available": False, "value": "tray"}


def test_window_api_set_close_behavior_persists_value(tmp_path):
    config_path = tmp_path / "config.py"
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), None, None, config_path, False, lambda: None, None
    )

    result = api.set_close_behavior("quit")

    assert result == {"success": True, "message": "Saved."}
    assert windows_launcher._read_close_behavior(config_path) == "quit"


def test_window_api_get_about_info_reports_version_and_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(windows_launcher, "_build_commit_hash", lambda: "abc1234")
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), None, None, tmp_path / "config.py", False, lambda: None, None
    )

    info = api.get_about_info()

    assert info["version"] == windows_launcher.__version__
    assert info["commit"] == "abc1234"


def test_launch_shell_close_button_quits_immediately_when_preference_is_quit(tmp_path, monkeypatch):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)
    monkeypatch.setattr(
        windows_launcher, "_build_tray_icon", lambda on_show, on_quit: _FakeTrayIcon()
    )
    config_path = tmp_path / "config.py"
    windows_launcher._write_close_behavior(config_path, "quit")

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=False),
        config_path,
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    window = fake_webview.window
    # No miner running, so the confirm-and-quit handler allows the close
    # outright rather than hiding to the tray - proving the preference (not
    # just tray availability) decides the behavior.
    assert window.events.closing.handler() is True


def test_launch_shell_exposes_tray_availability_to_window_api(tmp_path, monkeypatch):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)
    monkeypatch.setattr(
        windows_launcher, "_build_tray_icon", lambda on_show, on_quit: _FakeTrayIcon()
    )

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=False),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    assert fake_webview.create_window_kwargs['js_api']._tray_available is True


def test_launch_shell_tray_unavailable_leaves_window_api_reporting_it(tmp_path, monkeypatch):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)
    monkeypatch.setattr(
        windows_launcher,
        "_build_tray_icon",
        lambda on_show, on_quit: (_ for _ in ()).throw(RuntimeError("no tray")),
    )

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=False),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
    )

    assert fake_webview.create_window_kwargs['js_api']._tray_available is False
    assert fake_webview.create_window_kwargs['js_api'].get_close_behavior_info() == {
        "available": False,
        "value": "tray",
    }


# --- --start-minimized -----------------------------------------------------


def test_main_strips_start_minimized_flag_before_forwarding_to_runner(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text("", encoding="utf-8")
    runner_calls = []
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(
        windows_launcher, "runner_main", lambda argv, **kwargs: runner_calls.append(argv) or 0
    )
    monkeypatch.setattr(
        windows_launcher.sys,
        "argv",
        ["TwitchChannelPointsMiner.exe", "--start-minimized", "--convert-only"],
    )

    assert windows_launcher.main() == 0
    assert runner_calls == [
        [
            "--config-dir",
            str(config_dir),
            "--legacy-runner",
            str(tmp_path / "run.py"),
            "--convert-only",
        ]
    ]


def test_main_passes_start_minimized_through_to_launch_shell(tmp_path, monkeypatch):
    _write_real_config(config_dir := tmp_path / "config")
    (config_dir / ".desktop_shell_onboarded").touch()
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    captured = {}

    def fake_launch_shell(*args, **kwargs):
        captured["start_minimized"] = kwargs.get("start_minimized")

    monkeypatch.setattr(windows_launcher, "launch_shell", fake_launch_shell)
    monkeypatch.setattr(
        windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe", "--start-minimized"]
    )

    assert windows_launcher.main() == 0
    assert captured["start_minimized"] is True


def test_main_ignores_start_minimized_while_username_setup_is_pending(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        "MINER_CONFIG = {'username': 'your-twitch-username', 'enable_analytics': False}\n"
        "STREAMERS = []\n"
        "MINE_CONFIG = {}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )
    (config_dir / ".desktop_shell_onboarded").touch()
    monkeypatch.setattr(windows_launcher, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(windows_launcher.os, "chdir", lambda _path: None)
    monkeypatch.setattr(windows_launcher, "install_console_capture", lambda _buffer: None)
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv, **kwargs: 0)
    captured = {}

    def fake_launch_shell(*args, **kwargs):
        captured["start_minimized"] = kwargs.get("start_minimized")

    monkeypatch.setattr(windows_launcher, "launch_shell", fake_launch_shell)
    monkeypatch.setattr(
        windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe", "--start-minimized"]
    )

    assert windows_launcher.main() == 0
    assert captured["start_minimized"] is False


def test_launch_shell_passes_hidden_kwarg_to_create_window(tmp_path, monkeypatch):
    fake_webview = _FakeWebview()
    _patch_fake_webview(monkeypatch, fake_webview)
    monkeypatch.setattr(windows_launcher, "_maybe_prompt_to_enable_analytics", lambda *a: None)

    windows_launcher.launch_shell(
        None,
        windows_launcher.ConsoleBuffer(),
        None,
        _FakeMinerThread(alive=False),
        tmp_path / "config.py",
        tmp_path / ".shell_analytics_prompt_shown",
        False,
        lambda: None,
        None,
        start_minimized=True,
    )

    assert fake_webview.create_window_kwargs["hidden"] is True
