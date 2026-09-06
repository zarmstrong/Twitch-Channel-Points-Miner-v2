# -*- coding: utf-8 -*-

"""Windows executable entry point for Twitch Channel Points Miner."""

import os
import secrets
import shutil
import socket
import sys
import threading
import time
import webbrowser
from collections import deque
from pathlib import Path

from TwitchChannelPointsMiner.runner import main as runner_main

DEFAULT_ANALYTICS_PORT = 5000
_ANALYTICS_DISABLED_MARKER = "'enable_analytics': False,"


def application_directory():
    """Return the user-owned directory containing the executable or script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_file(name):
    """Return a file bundled by PyInstaller or present in the source checkout."""
    bundle_directory = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_directory / name


def prepare_config(application_dir):
    """Create the external configuration template on first launch."""
    config_dir = application_dir / "config"
    config_path = config_dir / "config.py"
    if config_path.is_file():
        return config_dir, False

    config_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(bundled_file("config.example.py"), config_path)
    try:
        config_path.chmod(0o600)
    except OSError:
        # Windows and some shared filesystems do not support POSIX permissions.
        pass
    return config_dir, True


def pause_for_first_run():
    """Keep a Windows console visible after creating its initial config, or
    after the desktop shell could not be opened."""
    if os.name != "nt":
        return
    try:
        input("Press Enter to close this window...")
    except EOFError:
        # A redirected, non-interactive, or console-less (windowed build)
        # launch may not have stdin.
        pass


def ensure_windows_analytics_defaults(config_path):
    """First-run only: turn on the embedded dashboard with a generated password.

    The bundled template ships with analytics disabled, so a brand-new user
    would otherwise have nothing for the shell's Dashboard tab to show. Only
    ever called once, immediately after `prepare_config` writes a fresh
    config from the template - an existing config is never touched. Returns
    the generated password, or None if the template's shape has changed and
    the marker this looks for is no longer present.
    """
    source = config_path.read_text(encoding="utf-8")
    if _ANALYTICS_DISABLED_MARKER not in source:
        return None

    password = secrets.token_urlsafe(18)
    updated = source.replace(_ANALYTICS_DISABLED_MARKER, "'enable_analytics': True,", 1)
    updated += (
        "\n"
        "# --- Added by the Windows launcher on first run ---\n"
        "# Powers the embedded dashboard shown in the desktop window. Change\n"
        "# these values (or set enable_analytics back to False) any time.\n"
        "ANALYTICS_CONFIG = {\n"
        "    'host': '127.0.0.1',\n"
        f"    'port': {DEFAULT_ANALYTICS_PORT},\n"
        "    'refresh': 5,\n"
        "    'days_ago': 7,\n"
        f"    'password': {password!r},\n"
        "    'log_poll_interval': 5,\n"
        "}\n"
    )
    config_path.write_text(updated, encoding="utf-8")
    return password


def resolve_dashboard_info(config_path):
    """Best-effort peek at the analytics settings for the shell's Dashboard tab.

    Returns None when analytics is disabled (or the config cannot be parsed
    yet), so the shell can show a helpful message instead of a dead iframe;
    the Console tab still shows the real reason via the miner's own startup
    logging.
    """
    try:
        from TwitchChannelPointsMiner.runner import _load_config

        config = _load_config(config_path)
    except Exception:
        return None

    if config.MINER_CONFIG.get("enable_analytics") is not True:
        return None
    analytics_config = config.ANALYTICS_CONFIG
    if not isinstance(analytics_config, dict):
        return None

    host = analytics_config.get("host", "127.0.0.1")
    port = analytics_config.get("port", DEFAULT_ANALYTICS_PORT)
    # 0.0.0.0 (or similar) is a bind address, not something the embedded
    # window can connect to - view the locally-running server via loopback.
    display_host = host if host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    return {
        "host": display_host,
        "port": port,
        "url": f"http://{display_host}:{port}/",
    }


class ConsoleBuffer:
    """Bounded, thread-safe ring buffer of console output for the shell's
    Console tab.

    Captures raw stdout/stderr writes rather than hooking into `logging`
    directly, so it also shows tracebacks and any output that never goes
    through a logger - the only requirement for something to reach the
    embedded Console tab in a windowed (console-less) build where stdout
    would otherwise go nowhere.
    """

    def __init__(self, max_entries=4000):
        self._entries = deque(maxlen=max_entries)
        self._lock = threading.Lock()
        self._seq = 0

    def write(self, text):
        if not text:
            return
        with self._lock:
            self._seq += 1
            self._entries.append((self._seq, text))

    def tail(self, since_seq=0, max_entries=500):
        with self._lock:
            entries = [entry for entry in self._entries if entry[0] > since_seq]
        if len(entries) > max_entries:
            entries = entries[-max_entries:]
        next_seq = entries[-1][0] if entries else since_seq
        return [text for _seq, text in entries], next_seq


class _TeeStream:
    """Duplicates writes to a ConsoleBuffer and, if present, the real stream."""

    def __init__(self, buffer, underlying):
        self._buffer = buffer
        self._underlying = underlying

    def write(self, text):
        self._buffer.write(text)
        if self._underlying is not None:
            try:
                self._underlying.write(text)
            except (OSError, ValueError):
                self._underlying = None

    def flush(self):
        if self._underlying is not None:
            try:
                self._underlying.flush()
            except (OSError, ValueError):
                pass

    def isatty(self):
        return False


def install_console_capture(buffer):
    """Mirror stdout/stderr into `buffer` for the shell's Console tab.

    Installed before anything else so it captures startup prints, the
    logging module's console handler (configured later, once the miner
    thread reaches Settings.logger setup), and any traceback a windowed
    build (no OS console) would otherwise lose entirely.
    """
    sys.stdout = _TeeStream(buffer, sys.stdout)
    sys.stderr = _TeeStream(buffer, sys.stderr)


def _wait_until_reachable(host, port, timeout=10.0, interval=0.2):
    """Give the analytics server, started on a background thread, a moment
    to bind its port before the shell tries to load it."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=interval):
                return True
        except OSError:
            time.sleep(interval)
    return False


class WindowApi:
    """Bridge exposed to the shell's JavaScript as `window.pywebview.api`."""

    def __init__(self, console_buffer, dashboard_info, initial_tab):
        self._console_buffer = console_buffer
        self._dashboard_info = dashboard_info
        self._initial_tab = initial_tab

    def get_console_tail(self, since_seq=0):
        lines, next_seq = self._console_buffer.tail(int(since_seq or 0))
        return {"lines": lines, "next_seq": next_seq}

    def get_dashboard_info(self):
        if not self._dashboard_info:
            return {"url": None}
        _wait_until_reachable(self._dashboard_info["host"], self._dashboard_info["port"])
        return {"url": self._dashboard_info["url"], "initial_tab": self._initial_tab}

    def open_in_browser(self):
        if self._dashboard_info:
            webbrowser.open(self._dashboard_info["url"])


def launch_shell(dashboard_info, console_buffer, initial_tab):
    """Open the two-tab desktop shell (Dashboard + Console).

    Imports pywebview lazily so this module stays importable - and
    unit-testable - on platforms/environments where the Windows-only
    dependency isn't installed.
    """
    import webview

    api = WindowApi(console_buffer, dashboard_info, initial_tab)
    shell_html = bundled_file(os.path.join("assets", "windows_shell.html")).read_text(
        encoding="utf-8"
    )
    webview.create_window(
        "Twitch Channel Points Miner",
        html=shell_html,
        js_api=api,
        width=1200,
        height=800,
        min_size=(800, 600),
    )
    webview.start()


def main():
    application_dir = application_directory()
    os.chdir(application_dir)

    console_buffer = ConsoleBuffer()
    install_console_capture(console_buffer)

    config_dir, created = prepare_config(application_dir)
    config_path = config_dir / "config.py"

    argv = [
        "--config-dir",
        str(config_dir),
        "--legacy-runner",
        str(application_dir / "run.py"),
        *sys.argv[1:],
    ]
    # A scripted/automation invocation (e.g. `--convert-only`) should behave
    # exactly as before: do the work and exit, with no desktop window.
    interactive = "--convert-only" not in argv

    if created:
        print(f"Created {config_path}")
        if interactive:
            password = ensure_windows_analytics_defaults(config_path)
            if password:
                print(
                    "Enabled the embedded dashboard for this first run. If "
                    "the desktop window or your browser asks for "
                    "credentials, the username is your Twitch username and "
                    f"the password is: {password}"
                )

    if not interactive:
        return runner_main(argv)

    # Read before starting the miner thread below, which loads (and may
    # migrate/rewrite) the same file - avoids racing two concurrent writers
    # on a first run right after an upgrade.
    dashboard_info = resolve_dashboard_info(config_path)

    miner_thread = threading.Thread(
        target=runner_main, args=(argv,), name="Miner runner", daemon=True
    )
    miner_thread.start()

    # Tied to a marker file rather than `created`, so the Windows installer's
    # own pre-created config.py (see windows_installer.iss) still gets the
    # onboarding view on its actual first launch of the exe.
    onboarding_marker = config_dir / ".desktop_shell_onboarded"
    is_first_shell_launch = not onboarding_marker.is_file()
    initial_tab = "config" if is_first_shell_launch else None

    try:
        launch_shell(dashboard_info, console_buffer, initial_tab)
    except Exception as error:
        # Covers a missing pywebview install, no WebView2 runtime, or any
        # other GUI backend failure - none of which should crash the miner.
        print(
            f"Could not open the desktop window ({error}); "
            "falling back to your default browser."
        )
        if dashboard_info:
            webbrowser.open(dashboard_info["url"])
        pause_for_first_run()
    else:
        if is_first_shell_launch:
            try:
                onboarding_marker.touch()
            except OSError:
                pass
    finally:
        miner_thread.join(timeout=2)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
