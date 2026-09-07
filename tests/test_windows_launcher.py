import builtins
import os
import socket
import stat
import threading
import types
from pathlib import Path

import pytest

import windows_launcher


@pytest.fixture(autouse=True)
def _isolate_shell_bypass_token_env(monkeypatch):
    # main() sets this real env var directly (not via monkeypatch, since
    # it must actually reach the AnalyticsServer thread it starts) so it
    # survives past any one test's teardown; clearing it before every test
    # here stops one test's main() call from leaking a token into another
    # test's assertions about dashboard URLs.
    monkeypatch.delenv(windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR, raising=False)


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
    ):
        calls.append(extract(dashboard_info, initial_tab))

    return fake


_REAL_USERNAME = "a_real_twitch_user"


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
        windows_launcher, "runner_main", lambda argv: runner_calls.append(argv) or 0
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)

    def fake_launch_shell(*args, **kwargs):
        tokens_seen.append(os.environ.get(windows_launcher.SHELL_BYPASS_TOKEN_ENV_VAR))

    monkeypatch.setattr(windows_launcher, "launch_shell", fake_launch_shell)
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert len(tokens_seen) == 1
    assert tokens_seen[0]  # non-empty: a real per-launch secret was set


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
        windows_launcher, "runner_main", lambda argv: runner_calls.append(argv) or 0
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
        windows_launcher, "runner_main", lambda argv: runner_calls.append(argv) or 0
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)
    monkeypatch.setattr(
        windows_launcher,
        "launch_shell",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no WebView2 runtime")),
    )
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    monkeypatch.setattr(windows_launcher, "pause_for_first_run", lambda: paused.append(True))
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0

    assert opened == ["http://127.0.0.1:5000/"]
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


def test_wait_until_reachable_detects_open_port():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    host, port = server.getsockname()
    try:
        assert (
            windows_launcher._wait_until_reachable(host, port, timeout=1, interval=0.05)
            is True
        )
    finally:
        server.close()


def test_wait_until_reachable_times_out_when_nothing_listening():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        _host, closed_port = probe.getsockname()

    assert (
        windows_launcher._wait_until_reachable(
            "127.0.0.1", closed_port, timeout=0.2, interval=0.05
        )
        is False
    )


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
        "_wait_until_reachable",
        lambda host, port: waited.append((host, port)) or True,
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

    assert waited == [("127.0.0.1", 5000)]
    assert result == {
        "url": "http://127.0.0.1:5000/",
        "initial_tab": "config",
        "enabled": True,
    }


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


class _FakeEventSlot:
    def __iadd__(self, handler):
        self.handler = handler
        return self


class _FakeEvents:
    def __init__(self):
        self.closing = _FakeEventSlot()


class _FakeWindow:
    def __init__(self):
        self.events = _FakeEvents()


class _FakeWebview:
    """A `webview` module stand-in whose start() actually invokes the
    callback launch_shell() passes it, matching pywebview's real contract
    (dialogs, unlike event handlers, only work once that callback runs)."""

    def __init__(self):
        self.window = _FakeWindow()
        self.create_window_args = None
        self.create_window_kwargs = None

    def create_window(self, *args, **kwargs):
        self.create_window_args = args
        self.create_window_kwargs = kwargs
        return self.window

    def start(self, func=None):
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
    monkeypatch.setattr(windows_launcher, "runner_main", lambda argv: 0)

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
