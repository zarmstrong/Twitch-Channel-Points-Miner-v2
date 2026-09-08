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
import urllib.error
import urllib.request
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


from TwitchChannelPointsMiner import __version__  # noqa: E402
from TwitchChannelPointsMiner.classes.AnalyticsServer import (  # noqa: E402
    SHELL_BYPASS_TOKEN_ENV_VAR,
)
from TwitchChannelPointsMiner.config_editor import _assignment, _dict_item, _simple_value
from TwitchChannelPointsMiner.runner import main as runner_main  # noqa: E402

DEFAULT_ANALYTICS_PORT = 54455
# Deliberately not 5000: that port is commonly already taken (Hyper-V's
# reserved dynamic port ranges on Windows, other dev tools, or a leftover
# copy of this app itself), which surfaces as a confusing bind failure or,
# worse, the shell silently talking to whatever else is already there. This
# high, uncommon port is only the default for a *newly created* config -
# users can still change it in ANALYTICS_CONFIG at any time, and an existing
# config that already has a 'port' value keeps it untouched.
# Used only for the literal text edit in ensure_windows_analytics_defaults()
# below, never as a safety gate - see that function's docstring for why.
_ANALYTICS_DISABLED_MARKER = "'enable_analytics': False,"
_PLACEHOLDER_USERNAME = "your-twitch-username"
_TRAY_ICON_FILE = "twitch-miner.ico"
_AUTOSTART_VALUE_NAME = "TwitchChannelPointsMiner"
_AUTOSTART_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"
_START_MINIMIZED_FLAG = "--start-minimized"
# Session-local (not "Global\\") - matches the rest of this launcher's
# per-user assumptions (e.g. the autostart registry key lives under HKCU),
# and avoids needing any elevated privilege to create.
_SINGLE_INSTANCE_MUTEX_NAME = "Local\\TwitchChannelPointsMinerSingleInstance"
# Loopback-only "please show yourself" signal for a second launch to send
# the first. Distinct from the analytics dashboard's own (configurable,
# default 54455) port.
_SINGLE_INSTANCE_HOST = "127.0.0.1"
_SINGLE_INSTANCE_PORT = 54876


def application_directory():
    """Return the user-owned directory containing the executable or script."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_file(name):
    """Return a file bundled by PyInstaller or present in the source checkout."""
    bundle_directory = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_directory / name


def _build_install_mode():
    """Returns "standard" or "portable", baked into the bundle at build time
    by build_windows.bat's two separate invocations - not inferred from the
    exe's own filename (trivially renamed by the end user, which would
    otherwise silently flip behavior) or anything else decided after the
    fact. Missing or unreadable (always true for a source checkout, which
    has no bundle at all) defaults to "portable": the long-standing, safe
    behavior of keeping everything beside the script/exe.
    """
    try:
        return bundled_file("install_mode.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "portable"


def _is_standard_build():
    return _build_install_mode() == "standard"


def _standard_application_directory():
    """Per-user Windows data directory for a "standard" build - deliberately
    separate from wherever the exe binary itself lives, so installing,
    upgrading, or uninstalling the app never touches configuration, cookies,
    analytics, or logs."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "TwitchChannelPointsMiner"


def config_root_directory(exe_dir):
    """Where config/cookies/analytics/logs live for this run."""
    if _is_standard_build():
        return _standard_application_directory()
    return exe_dir


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


def _message_box(text, title, icon_flag):
    """Best-effort native message box for when nothing else can reach the
    user - a --windowed build has no console, and this may run before the
    desktop shell window exists (or after it failed to open) either way.
    """
    if os.name != "nt":
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, text, title, icon_flag)
    except Exception:
        pass


def _show_fatal_error_message(text):
    _message_box(text, "Twitch Channel Points Miner - Error", 0x10)  # MB_ICONERROR


def _show_migration_notice(text):
    _message_box(text, "Twitch Channel Points Miner", 0x40)  # MB_ICONINFORMATION


# Carried forward into the new standard location - the app needs these to
# keep working exactly where the user left off (same account, cookies,
# watch/points history).
_LEGACY_DIRS_CARRY_FORWARD = ("config", "cookies", "analytics")
# Archived only, never copied forward: old logs aren't part of continuing to
# run and are never rotated/cleaned by anything once they're just sitting in
# a copy - carrying them into the live logs folder would mean an old,
# unbounded pile the app's normal log management never touches.
_LEGACY_DIRS_ARCHIVE_ONLY = ("logs",)


def _migrate_legacy_windows_data(exe_dir, standard_dir):
    """One-time migration for a "standard" build that finds data from before
    this feature existed, back when everything lived beside the exe (the
    only behavior a portable build, or any older version, has ever had).

    Moves each old data folder into <exe_dir>/config-legacy/<name> (so
    nothing is lost regardless of what happens next), then copies the ones
    the app actually needs going forward into the new standard location -
    old logs are archived but deliberately not carried into the live logs
    folder (see _LEGACY_DIRS_ARCHIVE_ONLY).

    Gated on a marker *inside* the archive folder (config-legacy/.migrated),
    not on whether a legacy config.py still exists - once archived,
    config.py no longer exists at its old path at all, so re-checking that
    on a later run would look identical to "nothing to migrate", and this
    must not run twice regardless (a second run would archive the fresh
    standard-location data instead of the real legacy data). For the same
    reason, "is there anything to do" also can't be answered by checking for
    just config.py: an earlier attempt can fail partway through (e.g.
    "config" moved, then "cookies" hits a locked file) without ever writing
    the marker, at which point config.py is already gone from its old path
    even though cookies/analytics/logs are still stranded there - so this
    also treats a config-legacy folder that exists without a .migrated
    marker as an interrupted migration to resume, regardless of what's left
    at the old path.

    Whether the copy-forward step for a given item has already run is
    tracked with its own marker inside legacy_root (config-legacy/.<name>
    -copied), not by checking whether `destination` exists: prepare_config()
    can create <standard_dir>/config/config.py on an earlier, ordinary
    launch that happens to run before this legacy data was ever discovered
    (e.g. the user installed the "standard" build and ran it once before
    copying their old portable folder's contents alongside the exe) -
    "destination already exists" is then true for a reason that has nothing
    to do with this migration ever having copied anything, and skipping the
    copy on that basis would silently strand the user's real config behind
    a bootstrap template that looks, from here, indistinguishable from a
    completed migration. The copy itself uses dirs_exist_ok=True so it
    still succeeds (overwriting any such stale content with the real
    archived data) regardless of what, if anything, already sits at
    `destination`.

    Returns None if there was nothing to do, or a message describing what
    happened (success or partial failure) for a one-time notice to the user.
    """
    legacy_root = exe_dir / "config-legacy"
    if (legacy_root / ".migrated").is_file():
        return None
    has_legacy_source = any(
        (exe_dir / name).is_dir()
        for name in _LEGACY_DIRS_CARRY_FORWARD + _LEGACY_DIRS_ARCHIVE_ONLY
    )
    if not has_legacy_source and not legacy_root.exists():
        return None  # nothing to migrate - a fresh standard install

    try:
        legacy_root.mkdir(parents=True, exist_ok=True)
        for name in _LEGACY_DIRS_CARRY_FORWARD + _LEGACY_DIRS_ARCHIVE_ONLY:
            source = exe_dir / name
            archived = legacy_root / name
            # `source` may already be gone even on a first pass through this
            # item within a resumed migration (see the docstring above) - the
            # carry-forward copy below must still run off `archived` in that
            # case, not be skipped just because there's nothing left to move.
            if source.is_dir() and not archived.exists():
                shutil.move(str(source), str(archived))
            if name in _LEGACY_DIRS_CARRY_FORWARD:
                destination = standard_dir / name
                copied_marker = legacy_root / f".{name}-copied"
                if archived.is_dir() and not copied_marker.is_file():
                    shutil.copytree(archived, destination, dirs_exist_ok=True)
                    copied_marker.touch()
        (legacy_root / ".migrated").touch()
    except OSError as error:
        return (
            "Could not fully move your existing configuration to its new "
            f"location ({error}).\n\nYour original files are safe at:\n"
            f"{legacy_root}"
        )

    return (
        "Your configuration, cookies, and analytics were moved to:\n"
        f"{standard_dir}\n\n"
        "Your original files, including past logs, were kept, untouched, "
        f"at:\n{legacy_root}"
    )


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


def _needs_username(config_path):
    """True if MINER_CONFIG['username'] is still the bundled template's
    placeholder, or blank.

    Safe to check by content, unlike the analytics gating above: a Twitch
    username can't contain a hyphen, so "your-twitch-username" can never be
    a value a real user deliberately chose - there is no legitimate history
    that looks identical to this one, the way enable_analytics=False can
    hide either an untouched template or a deliberate choice.
    """
    try:
        tree = ast.parse(config_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return False
    username = _simple_value(_dict_item(_assignment(tree, "MINER_CONFIG"), "username"))
    return (
        not isinstance(username, str)
        or not username.strip()
        or username == _PLACEHOLDER_USERNAME
    )


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


def _wait_until_dashboard_ready(url, timeout=10.0, interval=0.2):
    """Give the analytics server, started on a background thread, a moment
    to bind its port before the shell tries to load it - and confirm it's
    actually *this run's* server answering, not merely something reachable
    on that port.

    A bare TCP connect can't tell those apart: if a previous copy of this
    app is still running in the background and still holding the port, it
    would accept the connection just fine, but it has its own per-launch
    auth-bypass token (see main()) and would reject `url`'s token, leaving
    the shell to embed a confusing "Authentication required" page instead
    of the dashboard. Requiring an actual HTTP 200 for `url` (which already
    carries this run's token, if any) treats that case the same as nothing
    being there at all - both are "not ready" - rather than as a success.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=interval) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
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


def _dashboard_url_with_bypass(url):
    """Append the per-launch shell auth-bypass token (see main()) to a
    dashboard URL, if one was set, so the embedded iframe (or the "Open in
    browser" button) never has to prompt for Basic Auth credentials the
    user just generated for themselves and has no reason to type back in.

    A plain iframe/browser navigation can't attach a custom header, so this
    rides along as a query parameter instead; AnalyticsServer's
    require_authentication() persists it into a cookie on first use so the
    dashboard's own later same-origin fetch() calls keep authenticating
    without needing to repeat it.
    """
    token = os.environ.get(SHELL_BYPASS_TOKEN_ENV_VAR)
    if not token:
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}shell_token={token}"


def _acquire_single_instance_lock():
    """True if this process is the only running instance.

    Backed by a named kernel mutex rather than anything file- or
    port-based: creation is atomic, so two processes racing to start at
    the same moment (e.g. a login-triggered autostart alongside a manual
    double-click) can never both see "I'm first". The handle is
    deliberately never closed - Windows releases it automatically when
    this process exits, and holding it open for the process lifetime is
    exactly the intended "is the app still running" signal.

    Always True on non-Windows (nothing here is meaningful without the
    Windows-only ctypes.windll), so this stays a no-op for tests/dev.
    """
    if os.name != "nt":
        return True
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.SetLastError(0)
        handle = kernel32.CreateMutexW(None, False, _SINGLE_INSTANCE_MUTEX_NAME)
        if not handle:
            return True  # Couldn't even create it - fail open, don't block launch.
        ERROR_ALREADY_EXISTS = 183
        return kernel32.GetLastError() != ERROR_ALREADY_EXISTS
    except Exception:
        return True


def _notify_running_instance():
    """Best-effort: ask the already-running instance (see
    _start_single_instance_listener) to bring itself to the foreground.

    Silently gives up on any failure - e.g. an older already-running build
    without this listener, or the port being unexpectedly taken by
    something else. Either way the user still gets the "already running"
    message box from main(); this is a nicety on top of that, not the
    only feedback they get.
    """
    try:
        with socket.create_connection(
            (_SINGLE_INSTANCE_HOST, _SINGLE_INSTANCE_PORT), timeout=1
        ):
            pass
    except OSError:
        pass


def _start_single_instance_listener(window):
    """Runs for the app's lifetime: any connection on this loopback port
    means a second launch wants us to come to the foreground (see
    _notify_running_instance, the client side of this).

    A closed connection carries no payload - just connecting is the whole
    signal - so there's nothing to parse and nothing a local unprivileged
    process could send to make this do anything other than show the
    window it could already show itself via the tray.
    """

    def _serve():
        try:
            server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((_SINGLE_INSTANCE_HOST, _SINGLE_INSTANCE_PORT))
            server.listen(1)
        except OSError:
            return  # Port unavailable - not fatal, just no remote-show support.
        while True:
            try:
                conn, _address = server.accept()
            except OSError:
                return
            conn.close()
            window.show()

    threading.Thread(
        target=_serve, name="Single instance listener", daemon=True
    ).start()


def _build_tray_icon(on_show, on_quit):
    """Build a (not-yet-running) system tray icon with Show/Quit actions.

    Imports pystray/Pillow lazily, the same way launch_shell() imports
    webview lazily - so this module stays importable on platforms/test
    environments where the Windows-only tray dependency isn't installed.
    Any failure here (missing dependency, unreadable icon, no tray support
    on this desktop) is the caller's signal to fall back to the older
    confirm-on-close behavior rather than leave the app unclosable.
    """
    import pystray
    from PIL import Image

    image = Image.open(bundled_file(os.path.join("assets", _TRAY_ICON_FILE)))
    menu = pystray.Menu(
        pystray.MenuItem("Show", lambda icon, item: on_show()),
        pystray.MenuItem("Quit", lambda icon, item: on_quit()),
    )
    return pystray.Icon("TwitchChannelPointsMiner", image, "Twitch Channel Points Miner", menu)


def _make_hide_to_tray_handler(window, tray_icon):
    """Build a `window.events.closing` handler that hides the window to the
    tray instead of closing it - mining is unaffected, so unlike the older
    confirm-on-close handler this never needs to ask anything. Quitting for
    real now only ever happens via the tray icon's own Quit action (see
    launch_shell), which is where that confirmation moved to.

    Fires a tray balloon/toast notification each time, since hiding the
    window gives no other feedback that the app is still running rather
    than having just closed - easy to mistake for a quit, especially the
    first time. Best-effort: pystray's HAS_NOTIFICATION is False on some
    platforms/backends, and a notification failing is never worth blocking
    the hide itself over.
    """

    def on_closing():
        window.hide()
        try:
            tray_icon.notify(
                "Still running and mining in the background. "
                "Use the tray icon's Quit to stop it.",
                "Twitch Channel Points Miner",
            )
        except Exception:
            pass
        return False

    return on_closing


def _autostart_command():
    """Command line to register for "start on Windows login" - only
    meaningful for a frozen (PyInstaller) build; a source checkout has no
    stable double-clickable entry point to relaunch, so callers gate the
    whole feature on this.
    """
    return f'"{sys.executable}" {_START_MINIMIZED_FLAG}'


def is_autostart_enabled():
    """Whether this exe is currently registered to launch at Windows login,
    via the per-user Run key (no admin rights required, unlike the
    all-users equivalent)."""
    if not getattr(sys, "frozen", False):
        return False
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY_PATH) as key:
            value, _type = winreg.QueryValueEx(key, _AUTOSTART_VALUE_NAME)
    except OSError:
        return False
    return value == _autostart_command()


def set_autostart_enabled(enabled):
    """Add or remove the per-user Run key entry that launches this exe
    (minimized, straight to the tray - see _START_MINIMIZED_FLAG) at
    Windows login.

    Returns (success, message) - meant to be shown to the user as-is, the
    same contract as _enable_dashboard_from_shell.
    """
    if not getattr(sys, "frozen", False):
        return False, "Starting on login is only available in the installed app."
    try:
        import winreg
    except ImportError:
        return False, "Windows registry access is unavailable."

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _AUTOSTART_KEY_PATH, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(
                    key, _AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, _autostart_command()
                )
            else:
                try:
                    winreg.DeleteValue(key, _AUTOSTART_VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as error:
        return False, f"Could not update Windows startup settings: {error}"
    return True, "Saved."


class WindowApi:
    """Bridge exposed to the shell's JavaScript as `window.pywebview.api`."""

    def __init__(
        self,
        console_buffer,
        dashboard_info,
        initial_tab,
        config_path,
        needs_username,
        start_mining,
        logs_dir,
    ):
        self._console_buffer = console_buffer
        self._dashboard_info = dashboard_info
        self._initial_tab = initial_tab
        self._config_path = config_path
        self._needs_username = needs_username
        self._start_mining = start_mining
        self._logs_dir = logs_dir

    def get_console_tail(self, since_seq=0):
        lines, next_seq = self._console_buffer.tail(int(since_seq or 0))
        return {"lines": lines, "next_seq": next_seq}

    def get_setup_info(self):
        """Whether the shell must collect a Twitch username - blocking the
        normal Dashboard/Console tabs, and mining itself - before anything
        else happens. See `submit_username`."""
        return {"needs_username": self._needs_username}

    def submit_username(self, username):
        """Save the username the user just entered and, only now, start
        mining - deliberately deferred until this point rather than started
        with the bundled template's placeholder, which would just fail
        Twitch login immediately on every first run.
        """
        try:
            from TwitchChannelPointsMiner.config_editor import (
                ConfigEditError,
                set_miner_username,
            )

            set_miner_username(self._config_path, username)
        except (ConfigEditError, OSError) as error:
            return {"success": False, "message": str(error)}
        self._needs_username = False
        self._start_mining()
        return {"success": True, "message": None}

    def get_dashboard_info(self):
        if not self._dashboard_info:
            return {"url": None, "enabled": False}
        url = _dashboard_url_with_bypass(self._dashboard_info["url"])
        if not _wait_until_dashboard_ready(url):
            return {
                "url": None,
                "enabled": True,
                "message": (
                    "The dashboard didn't respond as expected. Another "
                    "program - or a previous copy of this app still running "
                    "in the background - may already be using its port. "
                    "Check the Console tab for details."
                ),
            }
        return {
            "url": url,
            "initial_tab": self._initial_tab,
            "enabled": True,
        }

    def open_in_browser(self):
        if self._dashboard_info:
            webbrowser.open(_dashboard_url_with_bypass(self._dashboard_info["url"]))

    def open_twitch_activate(self):
        webbrowser.open("https://www.twitch.tv/activate")

    def open_external_url(self, url):
        """Open an arbitrary http(s) link from the embedded dashboard iframe
        in the user's real default browser.

        The dashboard runs inside a same-origin-with-itself but
        cross-origin-with-the-shell iframe, so a plain `target="_blank"`
        link there tries to open a new pywebview-native popup window
        instead of a normal browser tab - which has no default browser
        association and just shows blank. The dashboard's own JS posts
        these clicks up to the shell (see windows_shell.html's `message`
        listener), which calls this instead.

        Restricted to http(s) so this can't be used to launch arbitrary
        local files or other URI schemes - not a strong security boundary
        (the dashboard content is our own template, not attacker-supplied),
        just a sensible guard against a malformed or unexpected URL.
        """
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            webbrowser.open(url)

    def enable_dashboard(self):
        success, message = _enable_dashboard_from_shell(self._config_path)
        return {"success": success, "message": message}

    def open_config_folder(self):
        _open_folder(self._config_path.parent)

    def open_logs_folder(self):
        _open_folder(self._logs_dir)

    def get_autostart_info(self):
        return {
            "available": bool(getattr(sys, "frozen", False)),
            "enabled": is_autostart_enabled(),
        }

    def set_autostart(self, enabled):
        success, message = set_autostart_enabled(bool(enabled))
        return {"success": success, "message": message}


def _open_folder(path):
    """Best-effort: open `path` in the OS file manager, creating it first if
    it doesn't exist yet (e.g. the logs folder before the miner has written
    anything). Silently does nothing if that fails - not worth surfacing an
    error dialog over.

    Gated on hasattr(os, "startfile") rather than os.name == "nt": the
    attribute genuinely only exists on a Windows CPython build, so it's
    equivalent in production, but doesn't require flipping the real
    (process-wide) os.name to test - which pathlib itself also reads to
    pick Path's concrete subclass, and does not uniformly tolerate being
    told to build the "wrong" one for the actual OS across Python versions.
    """
    try:
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        if hasattr(os, "startfile"):
            os.startfile(path)  # noqa: S606 - Windows-only, opens Explorer
    except OSError:
        pass


def _run_miner_thread(argv, on_miner_ready):
    """Thread target wrapping runner_main() the way runner.cli() wraps it for
    the normal synchronous entry point - without this, a RuntimeError raised
    before mining starts (e.g. failure to create the default config) would
    propagate unhandled off the main thread and never reach the user: the
    shell window would just sit there with mining silently never started.
    """
    try:
        runner_main(argv, on_miner_ready=on_miner_ready)
    except RuntimeError as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        _show_fatal_error_message(
            f"Twitch Channel Points Miner failed to start:\n\n{error}"
        )


class _MinerThreadHandle:
    """Shares a reference to the miner thread between the close-confirmation
    handler below and the first-run setup flow, which defers actually
    creating and starting that thread until a real username is entered (see
    `_needs_username` and `WindowApi.submit_username`) - so at the time the
    handler is wired up, there may not be a thread yet.
    """

    def __init__(self):
        self.thread = None
        self.miner = None

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def set_miner(self, miner):
        self.miner = miner


def _make_close_confirmation_handler(window, miner_thread):
    """Build a `window.events.closing` handler that blocks the close unless
    the user confirms, whenever the miner is still running.

    Before this shell existed, stopping the miner required an explicit
    Ctrl+C; a bare click on the window's close button must not silently end
    an unattended, hours-long mining session. Returning False from a
    pywebview `closing` handler cancels the close.

    `miner_thread` only needs an `is_alive()` method - a plain Thread, or a
    _MinerThreadHandle for a thread that may not exist yet, both work.
    """

    def on_closing():
        if not miner_thread.is_alive():
            return True
        try:
            confirmed = window.create_confirmation_dialog(
                "Stop mining?",
                "Closing this window will stop mining. Are you sure?",
            )
        except Exception:
            # If the dialog itself can't be shown, err on the side of NOT
            # silently stopping an unattended miner.
            return False
        if confirmed:
            _stop_miner_gracefully(miner_thread)
        return confirmed

    return on_closing


def _stop_miner_gracefully(miner_thread_handle):
    """Runs the same clean shutdown a SIGINT/SIGTERM would have triggered
    (IRC chat leave, websocket pool teardown, watcher-thread joins, final
    report) before the window is allowed to close.

    Needed because the miner runs on a background daemon thread here, and
    TwitchChannelPointsMiner._register_signal_handlers() is a deliberate
    no-op off the main thread - so nothing else ever calls end() for this
    shell. Runs on the GUI thread while the miner thread is mid-loop; end()
    already guards its state (streamer mutex checks, running flags) for
    exactly this kind of concurrent call. Its trailing sys.exit(0) is caught
    below since it would only ever unwind this thread's dialog handler, not
    the process - the process exits normally once the window finishes
    closing.
    """
    miner = getattr(miner_thread_handle, "miner", None)
    if miner is None:
        return
    try:
        miner.end(None, None)
    except SystemExit:
        pass
    except Exception as error:
        print(f"Error while stopping the miner gracefully: {error}", file=sys.stderr)


def launch_shell(
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
    """Open the two-tab desktop shell (Dashboard + Console) - or, on a first
    run with no configured Twitch username yet, a setup screen that
    collects one via `start_mining` before either tab (and mining itself)
    starts. See `_needs_username` and `WindowApi.submit_username`.

    Imports pywebview lazily so this module stays importable - and
    unit-testable - on platforms/environments where the Windows-only
    dependency isn't installed.
    """
    import webview

    api = WindowApi(
        console_buffer,
        dashboard_info,
        initial_tab,
        config_path,
        needs_username,
        start_mining,
        logs_dir,
    )
    shell_html = bundled_file(os.path.join("assets", "windows_shell.html")).read_text(
        encoding="utf-8"
    )
    window = webview.create_window(
        f"Twitch Channel Points Miner - {__version__}",
        html=shell_html,
        js_api=api,
        width=1200,
        height=800,
        min_size=(800, 600),
        # pywebview disables in-page text selection by default; the Console
        # tab exists specifically so logs can be read (and copied) here
        # instead of a separate window, so that default would defeat its
        # purpose.
        text_select=True,
        # Start-on-login launches straight to the tray rather than popping a
        # window on every boot - but never while first-run setup still needs
        # to collect a username (see main()'s start_minimized gating).
        hidden=start_minimized,
    )
    _start_single_instance_listener(window)

    tray_icon = None

    def _quit_from_tray():
        # Reuses the same confirm-then-stop-mining logic the close button
        # used to run directly - it now only runs from the tray's Quit
        # action, since closing the window itself just hides it (see below).
        if _make_close_confirmation_handler(window, miner_thread)():
            if tray_icon is not None:
                tray_icon.stop()
            window.destroy()

    try:
        tray_icon = _build_tray_icon(window.show, _quit_from_tray)
    except Exception:
        # No tray support available (dependency missing, no desktop tray,
        # bad icon, ...) - fall back to the older confirm-on-close behavior
        # rather than leave the app with no way to quit at all.
        tray_icon = None

    if tray_icon is not None:
        threading.Thread(target=tray_icon.run, name="Tray icon", daemon=True).start()
        window.events.closing += _make_hide_to_tray_handler(window, tray_icon)
    else:
        window.events.closing += _make_close_confirmation_handler(window, miner_thread)

    def _on_started():
        # Dialogs (unlike event handlers) require the GUI loop that
        # webview.start() begins - pywebview's own examples run them via
        # this callback, not before start() is called.
        if not needs_username:
            # Asking about the dashboard before the user has even entered a
            # username would be premature - it can still appear on a later,
            # normal launch once one is set.
            _maybe_prompt_to_enable_analytics(window, dashboard_info, config_path, prompt_marker)

    # Without this, pywebview falls back to extracting whatever icon
    # Windows associates with sys.executable at runtime - fragile for a
    # frozen onefile build and simply wrong (python.exe's icon) when run
    # unfrozen from source. Passing it explicitly is the documented,
    # reliable way to get the app's own icon on the actual running window
    # (taskbar/Alt-Tab), separate from the exe *file's* icon in Explorer,
    # which PyInstaller's --icon flag in build_windows.bat already covers.
    webview.start(_on_started, icon=str(bundled_file(os.path.join("assets", _TRAY_ICON_FILE))))


def self_test():
    """Verify the frozen exe actually has its GUI dependency bundled.

    Used only as `--self-test`, wired up as a CI smoke-test step right
    after build_windows.bat: it isolates the one import launch_shell()
    needs (pywebview) without touching config, the miner, or any window,
    so a PyInstaller bundle missing it - e.g. because a build step
    installed requirements.txt instead of requirements-windows.txt -
    fails the build immediately instead of only surfacing for a user at
    runtime. Must never open a window or message box: nothing would be
    there to dismiss it on a CI runner, and the job would hang forever.
    """
    try:
        import webview  # noqa: F401
    except Exception as error:
        print(f"self-test FAILED: could not import webview: {error}")
        return 1
    print("self-test OK: webview is importable")
    return 0


def main():
    if "--self-test" in sys.argv[1:]:
        return self_test()

    # A scripted/automation invocation (e.g. `--convert-only`) never opens a
    # window and does its work and exits - it's fine, and expected (e.g.
    # from an installer step or a script), for that to run alongside an
    # already-running desktop instance, so it's exempted from this check
    # entirely rather than treated as a second launch of the app itself.
    if "--convert-only" not in sys.argv[1:] and not _acquire_single_instance_lock():
        _notify_running_instance()
        _message_box(
            "Twitch Channel Points Miner is already running.\n\n"
            "Bringing the existing window to the foreground - check your "
            "taskbar or system tray if it doesn't appear.",
            "Twitch Channel Points Miner",
            0x40,  # MB_ICONINFORMATION
        )
        return 0

    exe_dir = application_directory()
    application_dir = config_root_directory(exe_dir)
    # For a standard build this may be a brand-new per-user AppData folder
    # that has never existed before (nothing else creates it up front, as
    # exe_dir always trivially exists already) - os.chdir() below requires
    # it to exist first.
    application_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(application_dir)

    console_buffer = ConsoleBuffer()
    install_console_capture(console_buffer)

    # Always attempted for a standard build (never for portable, which has
    # nothing to migrate to), regardless of whether this turns out to be an
    # interactive launch - see the notice-display gating below.
    migration_notice = (
        _migrate_legacy_windows_data(exe_dir, application_dir)
        if _is_standard_build()
        else None
    )

    config_dir, created = prepare_config(application_dir)
    config_path = config_dir / "config.py"

    # A launcher-only flag (set by the "start on Windows login" registry
    # entry / installer shortcut - see _autostart_command) - runner.py's own
    # argument parser knows nothing about it, so it must never reach
    # runner_main().
    start_minimized = _START_MINIMIZED_FLAG in sys.argv[1:]
    forwarded_args = [arg for arg in sys.argv[1:] if arg != _START_MINIMIZED_FLAG]

    argv = [
        "--config-dir",
        str(config_dir),
        "--legacy-runner",
        str(exe_dir / "run.py"),
        *forwarded_args,
    ]
    # A scripted/automation invocation (e.g. `--convert-only`) should behave
    # exactly as before: do the work and exit, with no desktop window - so
    # the migration notice below (a blocking modal) must never show for one.
    interactive = "--convert-only" not in argv

    if migration_notice and interactive:
        _show_migration_notice(migration_notice)

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

    # A fresh, process-only secret (never written to config.py or disk) so
    # this run's own embedded dashboard can skip the Basic Auth prompt -
    # see _dashboard_url_with_bypass() and AnalyticsServer's
    # require_authentication(). Set before the miner thread starts (whenever
    # that ends up being - see the username-setup deferral below) so it's
    # already there by the time AnalyticsServer reads it.
    os.environ[SHELL_BYPASS_TOKEN_ENV_VAR] = secrets.token_urlsafe(32)

    # Read before starting the miner thread below, which loads (and may
    # migrate/rewrite) the same file - avoids racing two concurrent writers
    # on a first run right after an upgrade.
    dashboard_info = resolve_dashboard_info(config_path)

    miner_thread_handle = _MinerThreadHandle()

    def start_mining():
        thread = threading.Thread(
            target=_run_miner_thread,
            args=(argv, miner_thread_handle.set_miner),
            name="Miner runner",
            daemon=True,
        )
        miner_thread_handle.thread = thread
        thread.start()

    # A config still carrying the bundled template's placeholder username
    # would just fail Twitch login immediately - collect a real one through
    # the shell's setup panel first, and defer starting the miner until
    # then, instead of starting it now knowing it can only fail.
    needs_username = _needs_username(config_path)
    if not needs_username:
        start_mining()

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
            miner_thread_handle,
            config_path,
            analytics_prompt_marker,
            needs_username,
            start_mining,
            application_dir / "logs",
            # First-run setup must still show the window regardless of how
            # it was launched - there would be nothing to interact with.
            start_minimized=start_minimized and not needs_username,
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
        if needs_username:
            # No dashboard exists to fall back to, and mining never started -
            # the only way forward left is editing the file directly.
            print(
                "No Twitch username is configured yet. Set 'username' under "
                f"MINER_CONFIG in {config_path} and restart."
            )
        elif dashboard_info:
            webbrowser.open(_dashboard_url_with_bypass(dashboard_info["url"]))
        pause_for_first_run()
    else:
        if is_first_shell_launch:
            try:
                onboarding_marker.touch()
            except OSError:
                pass
    finally:
        if miner_thread_handle.thread is not None:
            miner_thread_handle.thread.join(timeout=2)

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
