# -*- coding: utf-8 -*-

"""Windows executable entry point for Twitch Channel Points Miner."""

import ast
import ctypes  # cross-platform stdlib module; only .windll is Windows-only (guarded below)
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


class _NullStream:
    """Absorbs writes harmlessly.

    A --windowed PyInstaller build starts with sys.stdout/sys.stderr set to
    None (no OS console attached) until something replaces them. Without
    this, the first print() or log call anywhere in this process - including
    ones triggered merely by importing TwitchChannelPointsMiner below, before
    main() ever runs - would crash with an AttributeError on a None stream.
    main() replaces this with the real Console-tab capture almost
    immediately; anything written before then is lost, not shown in the
    Console tab, but the process no longer crashes over it.
    """

    def write(self, _text):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False


if sys.stdout is None:
    sys.stdout = _NullStream()
if sys.stderr is None:
    sys.stderr = _NullStream()


from TwitchChannelPointsMiner.config_editor import _assignment, _dict_item, _simple_value
from TwitchChannelPointsMiner.runner import main as runner_main  # noqa: E402

DEFAULT_ANALYTICS_PORT = 5000
# Used only for the literal text edit in ensure_windows_analytics_defaults()
# below, never as a safety gate - see that function's docstring for why.
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
    if sys.stdin is None:
        # A --windowed/--noconsole build has no console and thus no stdin
        # at all; input() raises RuntimeError("lost sys.stdin") immediately
        # rather than EOFError in this case, so it must be checked first.
        return
    try:
        input("Press Enter to close this window...")
    except EOFError:
        # A redirected or otherwise non-interactive launch may still have
        # a stdin object that simply has nothing to read.
        pass


def _show_fatal_error_message(text):
    """Best-effort native fallback for when nothing else can reach the user.

    A --windowed build has no console, and if the desktop shell itself never
    opened, there is no window either - a native message box is the only
    remaining way to avoid failing completely silently.
    """
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(
            0, text, "Twitch Channel Points Miner - Error", 0x10  # MB_ICONERROR
        )
    except Exception:
        pass


def _matches_template_defaults(source):
    """True only if this config's CURRENT VALUES for enable_analytics and
    ANALYTICS_CONFIG equal the bundled template's defaults (False and None).

    This confirms the config currently matches those defaults - it does NOT
    confirm the file was never intentionally set that way; those are
    different guarantees. A user who deliberately chose
    enable_analytics=False and left ANALYTICS_CONFIG unset produces content
    indistinguishable from an untouched template. This is purely a
    defense-in-depth check (for a bundled template whose shape has changed
    unexpectedly), layered on top of the provenance check in
    ensure_windows_analytics_defaults() - it is not, by itself, a safe
    substitute for that check. Parsed with config_editor.py's existing
    AST helpers rather than a second, parallel implementation.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False

    enable_analytics_node = _dict_item(
        _assignment(tree, "MINER_CONFIG"), "enable_analytics"
    )
    if enable_analytics_node is None:
        return False
    if _simple_value(enable_analytics_node) is not False:
        return False

    analytics_config_node = _assignment(tree, "ANALYTICS_CONFIG")
    if analytics_config_node is None:
        return False
    return _simple_value(analytics_config_node) is None


def ensure_windows_analytics_defaults(config_path, just_created):
    """Turn on the embedded dashboard with a generated password.

    The bundled template ships with analytics disabled, so a brand-new user
    would otherwise have nothing for the shell's Dashboard tab to show.

    `just_created` must be True only when the caller's own copy of the
    bundled template to `config_path` happened in *this* run (see
    `prepare_config`'s return value). This function deliberately does not,
    and cannot safely, infer freshness by reopening and inspecting the
    file's current content: a user who deliberately set
    enable_analytics=False and left ANALYTICS_CONFIG unset - a legitimate,
    intentional choice - produces content byte-for-byte indistinguishable
    from an untouched template. Only provenance (did *this* call just create
    the file?) can tell those two histories apart; a content check cannot,
    no matter how it is implemented. Making the caller pass this explicitly
    turns "don't call this on an existing config" into an API contract a
    future refactor can see and violate visibly, instead of a content
    heuristic it could silently defeat.

    A secondary, defense-in-depth content check (`_matches_template_defaults`)
    still runs after the provenance check passes, in case the bundled
    template's own shape has changed unexpectedly - see its docstring for
    why it is not a substitute for the provenance check above.

    windows_installer.iss's CustomizeStarterConfig is a Pascal port of this
    for the installer's own pre-created config.py, correctly gated instead
    by Inno Setup's onlyifdoesntexist/AfterInstall provenance (a file-copy
    that is skipped never runs AfterInstall) - keep the two in sync if the
    written defaults change.
    """
    if not just_created:
        return None

    source = config_path.read_text(encoding="utf-8")
    if not _matches_template_defaults(source):
        return None

    password = secrets.token_urlsafe(18)
    updated = source.replace(_ANALYTICS_DISABLED_MARKER, "'enable_analytics': True,", 1)
    if updated == source:
        # _matches_template_defaults confirmed enable_analytics is False,
        # but this exact literal text wasn't found to replace (e.g. a
        # different quote or spacing style) - bail out rather than append an
        # ANALYTICS_CONFIG block that nothing would actually turn on.
        return None
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

    Installed as early as possible in main() so it captures startup prints,
    the logging module's console handler (configured later, once the miner
    thread reaches Settings.logger setup), and any traceback a windowed
    build (no OS console) would otherwise lose entirely. Whatever is
    currently installed - a real stream, or the module-level _NullStream
    fallback above - becomes this tee's underlying stream, so nothing
    printed before this call is duplicated once it runs.
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


def _enable_dashboard_from_shell(config_path):
    """Shared by the one-time consent prompt and the Dashboard tab's
    on-demand "Enable dashboard" button: writes enable_analytics=True (and
    a generated password) through config_editor.py's normal AST-based edit
    path - an explicit, in-the-moment user action, unlike the silent
    first-run bootstrap in ensure_windows_analytics_defaults().

    Does not attempt to start AnalyticsServer in this already-running
    process. Settings.analytics_path is only ever set by
    TwitchChannelPointsMiner.__init__ when analytics was enabled *at
    construction time*; since it was off this run, that path never ran, and
    safely reproducing it here would mean re-implementing that setup
    (including its data-migration step) against a different, unrelated
    module's private internals. A restart re-runs that setup correctly
    instead, so this only ever saves the config and reports that a restart
    is needed.

    Returns (success, message) - message is meant to be shown to the user
    as-is, in either a dialog or the Dashboard tab's status line.
    """
    try:
        from TwitchChannelPointsMiner.config_editor import (
            ConfigEditError,
            enable_analytics_dashboard,
        )

        enable_analytics_dashboard(config_path, secrets.token_urlsafe(18))
    except (ConfigEditError, OSError) as error:
        return False, f"Could not enable the dashboard: {error}"
    return True, "Saved. Restart the app for the dashboard to start."


def _maybe_prompt_to_enable_analytics(window, dashboard_info, config_path, prompt_marker):
    """One-time consent prompt for an *existing* user whose config already
    has analytics off - shown at most once ever, regardless of the answer,
    tracked by `prompt_marker` (separate from the onboarding marker, which
    tracks something else: whether the Dashboard tab has been opened to the
    Config view before).

    Separate code path from ensure_windows_analytics_defaults(), which only
    ever runs on a config this run just created; this runs for a config
    that already existed with analytics off, for any reason.
    """
    if dashboard_info is not None:
        return
    if prompt_marker.is_file():
        return

    try:
        wants_enable = window.create_confirmation_dialog(
            "Enable the dashboard?",
            "The dashboard is currently turned off. Enable it now?",
        )
    except Exception:
        # The dialog itself couldn't be shown - don't mark this as
        # "answered" so a future launch (e.g. once WebView2 is fixed) can
        # still offer it.
        return

    try:
        prompt_marker.touch()
    except OSError:
        pass

    if not wants_enable:
        return

    _success, message = _enable_dashboard_from_shell(config_path)
    try:
        window.create_confirmation_dialog("Dashboard", message)
    except Exception:
        pass


class WindowApi:
    """Bridge exposed to the shell's JavaScript as `window.pywebview.api`."""

    def __init__(self, console_buffer, dashboard_info, initial_tab, config_path):
        self._console_buffer = console_buffer
        self._dashboard_info = dashboard_info
        self._initial_tab = initial_tab
        self._config_path = config_path

    def get_console_tail(self, since_seq=0):
        lines, next_seq = self._console_buffer.tail(int(since_seq or 0))
        return {"lines": lines, "next_seq": next_seq}

    def get_dashboard_info(self):
        if not self._dashboard_info:
            return {"url": None, "enabled": False}
        _wait_until_reachable(self._dashboard_info["host"], self._dashboard_info["port"])
        return {
            "url": self._dashboard_info["url"],
            "initial_tab": self._initial_tab,
            "enabled": True,
        }

    def open_in_browser(self):
        if self._dashboard_info:
            webbrowser.open(self._dashboard_info["url"])

    def enable_dashboard(self):
        success, message = _enable_dashboard_from_shell(self._config_path)
        return {"success": success, "message": message}


def _make_close_confirmation_handler(window, miner_thread):
    """Build a `window.events.closing` handler that blocks the close unless
    the user confirms, whenever the miner is still running.

    Before this shell existed, stopping the miner required an explicit
    Ctrl+C; a bare click on the window's close button must not silently end
    an unattended, hours-long mining session. Returning False from a
    pywebview `closing` handler cancels the close.
    """

    def on_closing():
        if not miner_thread.is_alive():
            return True
        try:
            return window.create_confirmation_dialog(
                "Stop mining?",
                "Closing this window will stop mining. Are you sure?",
            )
        except Exception:
            # If the dialog itself can't be shown, err on the side of NOT
            # silently stopping an unattended miner.
            return False

    return on_closing


def launch_shell(
    dashboard_info, console_buffer, initial_tab, miner_thread, config_path, prompt_marker
):
    """Open the two-tab desktop shell (Dashboard + Console).

    Imports pywebview lazily so this module stays importable - and
    unit-testable - on platforms/environments where the Windows-only
    dependency isn't installed.
    """
    import webview

    api = WindowApi(console_buffer, dashboard_info, initial_tab, config_path)
    shell_html = bundled_file(os.path.join("assets", "windows_shell.html")).read_text(
        encoding="utf-8"
    )
    window = webview.create_window(
        "Twitch Channel Points Miner",
        html=shell_html,
        js_api=api,
        width=1200,
        height=800,
        min_size=(800, 600),
    )
    window.events.closing += _make_close_confirmation_handler(window, miner_thread)

    def _on_started():
        # Dialogs (unlike event handlers) require the GUI loop that
        # webview.start() begins - pywebview's own examples run them via
        # this callback, not before start() is called.
        _maybe_prompt_to_enable_analytics(window, dashboard_info, config_path, prompt_marker)

    webview.start(_on_started)


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
            password = ensure_windows_analytics_defaults(config_path, just_created=created)
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
    # Separate marker: tracks the one-time "enable the dashboard?" consent
    # prompt for an existing config with analytics off, independent of the
    # onboarding view above (see _maybe_prompt_to_enable_analytics).
    analytics_prompt_marker = config_dir / ".shell_analytics_prompt_shown"

    try:
        launch_shell(
            dashboard_info,
            console_buffer,
            initial_tab,
            miner_thread,
            config_path,
            analytics_prompt_marker,
        )
    except Exception as error:
        # Covers a missing pywebview install, no WebView2 runtime, or any
        # other GUI backend failure - none of which should crash the miner.
        message = (
            f"Could not open the desktop window ({error}); "
            "falling back to your default browser."
        )
        print(message)
        _show_fatal_error_message(message)
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


def run():
    """Entry point wrapper: on a genuinely uncaught failure, show a native
    message box before re-raising.

    A --windowed build has no console to print a traceback to, so without
    this an unexpected crash here would fail completely silently - no
    window, no console, no error, nothing.
    """
    try:
        return main()
    except Exception as error:
        _show_fatal_error_message(
            "Twitch Channel Points Miner failed to start:\n\n"
            f"{error}\n\n"
            "Check the logs folder beside the executable for details."
        )
        raise


if __name__ == "__main__":
    raise SystemExit(run())
