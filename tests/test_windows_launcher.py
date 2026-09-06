import builtins
import os
import socket
import stat
from pathlib import Path

import pytest

import windows_launcher


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


def test_main_starts_miner_thread_and_launches_shell_when_interactive(
    tmp_path, monkeypatch
):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        "MINER_CONFIG = {'enable_analytics': False}\n"
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
        lambda dashboard_info, console_buffer, initial_tab: shell_calls.append(
            (dashboard_info, initial_tab)
        ),
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
        lambda dashboard_info, console_buffer, initial_tab: shell_calls.append(
            (dashboard_info, initial_tab)
        ),
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
        "MINER_CONFIG = {'enable_analytics': True}\n"
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
        lambda dashboard_info, console_buffer, initial_tab: shell_calls.append(
            initial_tab
        ),
    )
    monkeypatch.setattr(windows_launcher.sys, "argv", ["TwitchChannelPointsMiner.exe"])

    assert windows_launcher.main() == 0
    assert shell_calls == ["config"]
    assert (config_dir / ".desktop_shell_onboarded").is_file()

    shell_calls.clear()
    assert windows_launcher.main() == 0
    assert shell_calls == [None]


def test_main_falls_back_to_browser_when_shell_launch_fails(tmp_path, monkeypatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "config.py").write_text(
        "MINER_CONFIG = {'enable_analytics': True}\n"
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


def test_ensure_windows_analytics_defaults_enables_dashboard(tmp_path):
    config_path = tmp_path / "config.py"
    config_path.write_text(
        "MINER_CONFIG = {\n"
        "    'username': 'someone',\n"
        "    'enable_analytics': False,\n"
        "}\n"
        "ANALYTICS_CONFIG = None\n",
        encoding="utf-8",
    )

    password = windows_launcher.ensure_windows_analytics_defaults(config_path)

    assert password
    updated = config_path.read_text(encoding="utf-8")
    assert "'enable_analytics': True," in updated
    assert "'enable_analytics': False," not in updated
    assert f"'password': {password!r}" in updated
    # The original (now-shadowed) None assignment is left in place; only a
    # second, later assignment is appended - simpler and more robust than
    # rewriting the commented-out example block in place.
    assert updated.count("ANALYTICS_CONFIG") == 2


def test_ensure_windows_analytics_defaults_leaves_unrecognized_template_alone(tmp_path):
    config_path = tmp_path / "config.py"
    original = "MINER_CONFIG = {'enable_analytics': True}\n"
    config_path.write_text(original, encoding="utf-8")

    result = windows_launcher.ensure_windows_analytics_defaults(config_path)

    assert result is None
    assert config_path.read_text(encoding="utf-8") == original


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
    api = windows_launcher.WindowApi(buffer, dashboard_info=None, initial_tab=None)

    result = api.get_console_tail(0)

    assert result == {"lines": ["hello\n"], "next_seq": 1}


def test_window_api_get_dashboard_info_returns_null_url_when_disabled():
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), dashboard_info=None, initial_tab=None
    )

    assert api.get_dashboard_info() == {"url": None}


def test_window_api_get_dashboard_info_waits_for_port_then_returns_url(monkeypatch):
    waited = []
    monkeypatch.setattr(
        windows_launcher,
        "_wait_until_reachable",
        lambda host, port: waited.append((host, port)) or True,
    )
    dashboard_info = {"host": "127.0.0.1", "port": 5000, "url": "http://127.0.0.1:5000/"}
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), dashboard_info, initial_tab="config"
    )

    result = api.get_dashboard_info()

    assert waited == [("127.0.0.1", 5000)]
    assert result == {"url": "http://127.0.0.1:5000/", "initial_tab": "config"}


def test_window_api_open_in_browser_opens_dashboard_url(monkeypatch):
    opened = []
    monkeypatch.setattr(windows_launcher.webbrowser, "open", lambda url: opened.append(url))
    dashboard_info = {"host": "127.0.0.1", "port": 5000, "url": "http://127.0.0.1:5000/"}
    api = windows_launcher.WindowApi(
        windows_launcher.ConsoleBuffer(), dashboard_info, initial_tab=None
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
        windows_launcher.ConsoleBuffer(), dashboard_info=None, initial_tab=None
    )

    api.open_in_browser()


def test_launch_shell_surfaces_missing_pywebview(monkeypatch):
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "webview":
            raise ModuleNotFoundError("No module named 'webview'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ModuleNotFoundError):
        windows_launcher.launch_shell(None, windows_launcher.ConsoleBuffer(), None)
