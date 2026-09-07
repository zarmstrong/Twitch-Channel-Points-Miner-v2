import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from threading import Lock, Thread

from flask import Flask, Response, cli, g, render_template, request

from TwitchChannelPointsMiner import __version__
from TwitchChannelPointsMiner.classes.Settings import ANALYTICS_FILE_MUTEX, Settings
from TwitchChannelPointsMiner.config_editor import (
    NOTIFICATION_SCHEMAS,
    ConfigEditError,
    read_managed_web_config,
    update_managed_web_config,
)
from TwitchChannelPointsMiner.constants import GITHUB_REPOSITORY_URL

cli.show_server_banner = lambda *_: None
logger = logging.getLogger(__name__)

MAX_LOG_TAIL_BYTES = 1024 * 1024
UPDATE_DISMISSAL_COOKIE = "tcpm_update_dismissed_version"
RESPONSE_CACHE_TTL_SECONDS = 10.0

# Set by windows_launcher.py, which runs the AnalyticsServer thread inside
# the same process as its own trusted desktop shell - never by Docker or a
# plain source checkout. Lets the shell's own embedded dashboard skip the
# Basic Auth prompt without weakening it for anyone else: a browser opened
# by anything other than that same process's launcher never has this token.
SHELL_BYPASS_TOKEN_ENV_VAR = "TCPM_SHELL_ANALYTICS_TOKEN"
SHELL_BYPASS_COOKIE = "tcpm_shell_token"


class TTLResponseCache:
    """Thread-safe TTL cache for expensive dashboard JSON responses.

    The analytics dashboard polls /streamers and /json_all every few seconds;
    both re-read and re-parse every (ever-growing) streamer analytics file on
    each request. Behind a reverse proxy that burst of work competes with the
    miner's own threads and can stall responses until the proxy times out.
    Serving a cached response for a short window keeps polling cheap without
    any user-visible staleness beyond a few seconds.
    """

    def __init__(self, ttl_seconds=RESPONSE_CACHE_TTL_SECONDS):
        self.ttl_seconds = ttl_seconds
        self._entries = {}
        self._mutex = Lock()

    def get(self, key):
        if self.ttl_seconds <= 0:
            return None
        with self._mutex:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, payload = entry
            if expires_at <= time.monotonic():
                self._entries.pop(key, None)
                return None
            return payload

    def set(self, key, payload):
        if self.ttl_seconds <= 0:
            return
        with self._mutex:
            # Callers that vary their key per-request (e.g. keying on a
            # file's mtime so a change is visible immediately instead of
            # waiting out the TTL) would otherwise accumulate one entry per
            # write forever, since an expired key is only ever swept when
            # that exact key is re-fetched. Sweep expired entries here too
            # so such callers stay bounded.
            now = time.monotonic()
            expired = [
                existing_key
                for existing_key, (expires_at, _payload) in self._entries.items()
                if expires_at <= now
            ]
            for existing_key in expired:
                self._entries.pop(existing_key, None)

            self._entries[key] = (now + self.ttl_seconds, payload)

    def clear(self):
        with self._mutex:
            self._entries.clear()


def _is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _number_or_zero(value):
    return value if _is_number(value) else 0


def bounded_log_start(file_size, last_received_index, tail_bytes=None):
    """Return a safe absolute offset for an analytics log response.

    Older cached dashboard clients may omit ``tailBytes`` and repeatedly ask for
    the log from byte zero.  Never let one request read an unbounded log into
    memory and monopolize the miner process.
    """
    position = last_received_index if last_received_index <= file_size else 0
    response_limit = min(tail_bytes or MAX_LOG_TAIL_BYTES, MAX_LOG_TAIL_BYTES)
    return max(position, file_size - response_limit)


def seek_log_start(log_file, position, discard_partial_line=False):
    """Seek to a log offset, skipping only an incomplete leading line."""
    log_file.seek(position)
    if discard_partial_line is False or position == 0:
        return

    log_file.seek(position - 1)
    if log_file.read(1) != b"\n":
        log_file.seek(position)
        log_file.readline()
    else:
        log_file.seek(position)


def get_assets_folder():
    repository_assets = Path(__file__).resolve().parents[2] / "assets"
    if repository_assets.is_dir():
        return str(repository_assets)

    return str(Path().absolute() / "assets")


def streamers_available():
    path = Settings.analytics_path
    excluded_files = {"drops_by_category.json", "now_watching.json"}
    available = [
        f
        for f in os.listdir(path)
        if os.path.isfile(os.path.join(path, f))
        and f.endswith(".json")
        and f not in excluded_files
    ]
    logger.debug("Analytics points scan path='%s' files=%s", path, sorted(available))
    return available


def filter_datas(start_date, end_date, datas):
    # Note: https://stackoverflow.com/questions/4676195/why-do-i-need-to-multiply-unix-timestamps-by-1000-in-javascript
    if not isinstance(datas, dict):
        datas = {}

    start_date = (
        datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000
        if start_date is not None
        else 0
    )
    end_date = (
        datetime.strptime(end_date, "%Y-%m-%d")
        if end_date is not None
        else datetime.now()
    ).replace(hour=23, minute=59, second=59).timestamp() * 1000

    raw_series = datas.get("series", [])
    original_series = raw_series if isinstance(raw_series, list) else []
    if not isinstance(datas.get("annotations", []), list):
        datas["annotations"] = []

    if original_series:
        datas["series"] = sorted(
            (
                entry
                for entry in original_series
                if isinstance(entry, dict)
                and _is_number(entry.get("x"))
                and start_date <= entry.get("x", -1) <= end_date
            ),
            key=lambda entry: (entry["x"], _number_or_zero(entry.get("y"))),
        )
    else:
        datas["series"] = []

    # If no data is found within the timeframe, that usually means the streamer hasn't streamed within that timeframe
    # We create a series that shows up as a straight line on the dashboard, with 'No Stream' as labels
    if len(datas["series"]) == 0:
        if len(original_series) == 0:
            datas["series"] = []
            if "annotations" not in datas:
                datas["annotations"] = []
            return datas

        # Attempt to get the last known balance from before the provided timeframe
        earlier_entries = [
            entry
            for entry in original_series
            if isinstance(entry, dict)
            and _is_number(entry.get("x"))
            and 0 <= entry["x"] <= start_date
        ]
        if earlier_entries == []:
            datas["series"] = []
            datas["annotations"] = []
            return datas
        last_balance = max(
            earlier_entries,
            key=lambda entry: (entry["x"], _number_or_zero(entry.get("y"))),
        )
        last_balance = _number_or_zero(last_balance.get("y"))

        datas["series"] = [
            {"x": start_date, "y": last_balance, "z": "No Stream"},
            {"x": end_date, "y": last_balance, "z": "No Stream"},
        ]

    raw_annotations = datas.get("annotations", [])
    if isinstance(raw_annotations, list) and raw_annotations:
        datas["annotations"] = sorted(
            (
                annotation
                for annotation in raw_annotations
                if isinstance(annotation, dict)
                and _is_number(annotation.get("x"))
                and start_date <= annotation.get("x", -1) <= end_date
            ),
            key=lambda annotation: annotation.get("x", 0),
        )
    else:
        datas["annotations"] = []

    return datas


def read_json(streamer, return_response=True):
    start_date = request.args.get("startDate", type=str)
    end_date = request.args.get("endDate", type=str)

    path = Settings.analytics_path
    streamer = streamer if streamer.endswith(".json") else f"{streamer}.json"

    # Check if the file exists before attempting to read it
    if not os.path.exists(os.path.join(path, streamer)):
        error_message = f"File '{streamer}' not found."
        logger.error(error_message)
        if return_response:
            return Response(
                json.dumps({"error": error_message}),
                status=404,
                mimetype="application/json",
            )
        else:
            return {"error": error_message}

    try:
        with open(os.path.join(path, streamer), "r") as file:
            data = json.load(file)
    except json.JSONDecodeError as e:
        error_message = f"Error decoding JSON in file '{streamer}': {str(e)}"
        logger.error(error_message)
        if return_response:
            return Response(
                json.dumps({"error": error_message}),
                status=500,
                mimetype="application/json",
            )
        else:
            return {"error": error_message}

    # Handle filtering data, if applicable
    filtered_data = filter_datas(start_date, end_date, data)
    if return_response:
        return Response(
            json.dumps(filtered_data), status=200, mimetype="application/json"
        )
    else:
        return filtered_data


def get_streamer_summary(streamer):
    """Read the latest points record without running the chart-data pipeline."""
    filename = streamer if streamer.endswith(".json") else f"{streamer}.json"
    file_path = os.path.join(Settings.analytics_path, filename)
    try:
        with open(file_path, "r", encoding="utf-8") as file:
            series = json.load(file).get("series", []) or []
    except (json.JSONDecodeError, OSError, AttributeError) as error:
        logger.error("Unable to read analytics summary '%s': %s", file_path, error)
        return {"points": 0, "last_activity": 0}

    if not isinstance(series, list):
        series = []

    latest = max(
        (
            entry
            for entry in series
            if isinstance(entry, dict) and _is_number(entry.get("x"))
        ),
        key=lambda entry: entry["x"],
        default=None,
    )
    if latest is None:
        return {"points": 0, "last_activity": 0}
    return {
        "points": _number_or_zero(latest.get("y")),
        "last_activity": latest["x"],
    }


def json_all():
    cached = response_cache.get("json_all")
    if cached is not None:
        return Response(cached, status=200, mimetype="application/json")

    payload = json.dumps(
        [
            {
                "name": streamer.strip(".json"),
                "data": read_json(streamer, return_response=False),
            }
            for streamer in streamers_available()
        ]
    )
    response_cache.set("json_all", payload)
    return Response(payload, status=200, mimetype="application/json")


def drops_by_category():
    drops_file = os.path.join(Settings.analytics_path, "drops_by_category.json")
    if os.path.isfile(drops_file) is False:
        logger.warning("Analytics Drops file not found: '%s'", drops_file)
        return Response(
            json.dumps({"categories": {}, "drops": []}),
            status=200,
            mimetype="application/json",
        )

    try:
        with open(drops_file, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError) as error:
        logger.error("Unable to read analytics Drops file '%s': %s", drops_file, error)
        return Response(
            json.dumps({"categories": {}, "drops": []}),
            status=200,
            mimetype="application/json",
        )

    drops = data.get("drops", [])
    grouped = {}
    for drop in drops:
        category = drop.get("category", "Unknown")
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(drop)

    logger.debug(
        "Analytics Drops response path='%s' drops=%d categories=%d",
        drops_file,
        len(drops),
        len(grouped),
    )

    return Response(
        json.dumps({"categories": grouped, "drops": drops}),
        status=200,
        mimetype="application/json",
    )


def now_watching():
    now_watching_file = os.path.join(Settings.analytics_path, "now_watching.json")
    try:
        mtime = os.path.getmtime(now_watching_file)
    except OSError:
        mtime = None

    # Key the cache by the file's mtime (rather than a fixed key) so a
    # write from the miner is visible on the very next poll instead of
    # waiting out the shared cache's TTL - this endpoint is polled on the
    # fast log-tail cadence specifically to reflect live state.
    cache_key = f"now_watching:{mtime}"
    cached = response_cache.get(cache_key)
    if cached is not None:
        return Response(cached, status=200, mimetype="application/json")

    entries = []
    if mtime is not None:
        try:
            with open(now_watching_file, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, list):
                entries = data
        except (json.JSONDecodeError, OSError) as error:
            logger.error(
                "Unable to read analytics Now Watching file '%s': %s",
                now_watching_file,
                error,
            )
            entries = []

    payload = json.dumps(entries)
    response_cache.set(cache_key, payload)
    return Response(payload, status=200, mimetype="application/json")


def index(refresh=5, days_ago=7, log_poll_interval=5):
    assets_folder = get_assets_folder()
    asset_version = max(
        os.path.getmtime(os.path.join(assets_folder, filename))
        for filename in ("script.js", "style.css", "dark-theme.css")
    )
    latest_version = getattr(Settings, "latest_release_version", None)
    dismissed_version = request.cookies.get(UPDATE_DISMISSAL_COOKIE)
    return render_template(
        "charts.html",
        refresh=(refresh * 60 * 1000),
        daysAgo=days_ago,
        logPollInterval=(log_poll_interval * 1000),
        dateFormat=Settings.logger.date_format,
        assetVersion=int(asset_version),
        currentVersion=__version__,
        latestVersion=latest_version,
        updateInstructions=getattr(Settings, "update_instructions", None),
        updateReleaseUrl=f"{GITHUB_REPOSITORY_URL}/releases/latest",
        showUpdateBanner=(
            latest_version is not None and dismissed_version != latest_version
        ),
        updateDismissalCookie=UPDATE_DISMISSAL_COOKIE,
    )


def streamers():
    cached = response_cache.get("streamers")
    if cached is not None:
        return Response(cached, status=200, mimetype="application/json")

    available = sorted(streamers_available())
    response = []
    for streamer in available:
        summary = get_streamer_summary(streamer)
        response.append({"name": streamer, **summary})
    logger.debug(
        "Analytics points response path='%s' streamers=%d",
        Settings.analytics_path,
        len(response),
    )
    payload = json.dumps(response)
    response_cache.set("streamers", payload)
    return Response(payload, status=200, mimetype="application/json")


def delete_streamer_analytics(streamer):
    filename = streamer if streamer.endswith(".json") else f"{streamer}.json"

    # Only files returned by streamers_available() may be deleted. Besides
    # preventing path traversal, this protects non-streamer analytics files.
    if filename not in streamers_available():
        return Response(
            json.dumps({"error": f"Analytics data for '{streamer}' not found."}),
            status=404,
            mimetype="application/json",
        )

    with ANALYTICS_FILE_MUTEX:
        try:
            os.remove(os.path.join(Settings.analytics_path, filename))
        except FileNotFoundError:
            return Response(
                json.dumps({"error": f"Analytics data for '{streamer}' not found."}),
                status=404,
                mimetype="application/json",
            )
        except OSError as error:
            logger.error(f"Unable to delete analytics data in '{filename}': {error}")
            return Response(
                json.dumps({"error": "Unable to delete streamer analytics data."}),
                status=500,
                mimetype="application/json",
            )

    logger.info(f"Deleted analytics data in '{filename}'")
    response_cache.clear()
    return Response(status=204)


def web_config():
    config_file = Path(Settings.config_path) / "config.py"
    payload = {}
    try:
        if request.method == "GET":
            data = read_managed_web_config(config_file)
        else:
            payload = request.get_json(silent=True) or {}
            if "action" not in payload and {"kind", "value"} <= set(payload):
                payload["action"] = "add"
            data = update_managed_web_config(config_file, payload)
    except ConfigEditError as error:
        logger.warning("Unable to update configuration from dashboard: %s", error)
        return Response(
            json.dumps({"error": str(error)}), status=400, mimetype="application/json"
        )
    except (OSError, TypeError, AttributeError):
        logger.exception("Unable to access dashboard-managed configuration")
        return Response(
            json.dumps({"error": "Unable to access configuration."}),
            status=500,
            mimetype="application/json",
        )
    if request.method == "POST":
        logger.info(
            "Applied %s configuration action from dashboard",
            payload.get("action"),
        )
    return Response(json.dumps(data), status=200, mimetype="application/json")


def test_web_notification(provider):
    if provider not in NOTIFICATION_SCHEMAS:
        return Response(
            json.dumps({"error": "Unknown notification provider."}),
            status=404,
            mimetype="application/json",
        )
    try:
        from TwitchChannelPointsMiner.classes.Settings import Events
        from TwitchChannelPointsMiner.runner import _load_config

        config_file = Path(Settings.config_path) / "config.py"
        config = _load_config(config_file)
        logger_settings = config.MINER_CONFIG.get("logger_settings")
        notification = (
            getattr(logger_settings, provider, None)
            if logger_settings is not None
            else None
        )
        managed = read_managed_web_config(config_file)["notifications"][provider]
        if notification is None or managed["test_available"] is not True:
            return Response(
                json.dumps({"error": "Configure and enable this notification first."}),
                status=409,
                mimetype="application/json",
            )
        event_name = str(Events.CONFIGURATION)
        if event_name not in notification.events:
            notification.events.append(event_name)
        result = notification.send(
            "This is a test notification from Twitch Channel Points Miner.",
            Events.CONFIGURATION,
        )
        if isinstance(result, tuple) and result[0] is False:
            return Response(
                json.dumps({"error": result[1]}),
                status=502,
                mimetype="application/json",
            )
        if result is False:
            return Response(
                json.dumps({"error": "The notification service rejected the test."}),
                status=502,
                mimetype="application/json",
            )
    except (
        ConfigEditError,
        OSError,
        RuntimeError,
        TypeError,
        AttributeError,
        ValueError,
    ):
        logger.exception("Unable to send %s test notification", provider)
        return Response(
            json.dumps({"error": "Unable to send test notification."}),
            status=500,
            mimetype="application/json",
        )

    logger.info("Sent %s test notification from dashboard", provider)
    return Response(
        json.dumps({"message": "Test notification sent."}),
        status=200,
        mimetype="application/json",
    )


def check_assets():
    required_files = [
        "banner.png",
        "charts.html",
        "script.js",
        "style.css",
        "dark-theme.css",
    ]
    assets_folder = get_assets_folder()
    missing_files = [
        f for f in required_files if not os.path.isfile(os.path.join(assets_folder, f))
    ]
    if missing_files:
        raise FileNotFoundError(
            f"Missing analytics assets in {assets_folder}: {', '.join(missing_files)}"
        )


response_cache = TTLResponseCache()


class AnalyticsServer(Thread):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        refresh: int = 5,
        days_ago: int = 7,
        username: str = None,
        password: str = None,
        log_poll_interval: int = 5,
    ):
        super(AnalyticsServer, self).__init__()

        check_assets()

        if not isinstance(log_poll_interval, int) or isinstance(
            log_poll_interval, bool
        ):
            raise TypeError("Log polling interval must be an integer number of seconds")
        if not 1 <= log_poll_interval <= 180:
            raise ValueError("Log polling interval must be between 1 and 180 seconds")

        self.host = host
        self.port = port
        self.refresh = refresh
        self.days_ago = days_ago
        self.username = username
        self.password = password
        self.shell_bypass_token = os.environ.get(SHELL_BYPASS_TOKEN_ENV_VAR)

        if host not in {"127.0.0.1", "localhost", "::1"} and not password:
            raise ValueError("Analytics exposed beyond localhost requires a password")

        def generate_log():
            raw_position = request.args.get("lastIndex", "0")
            raw_tail_bytes = request.args.get("tailBytes")
            try:
                last_received_index = int(raw_position)
            except (TypeError, ValueError):
                return Response("Invalid log position.", status=400)
            if last_received_index < 0:
                return Response("Invalid log position.", status=400)

            tail_bytes = None
            if raw_tail_bytes is not None:
                try:
                    tail_bytes = int(raw_tail_bytes)
                except (TypeError, ValueError):
                    return Response("Invalid log tail size.", status=400)
                if tail_bytes <= 0 or tail_bytes > MAX_LOG_TAIL_BYTES:
                    return Response("Invalid log tail size.", status=400)

            logs_path = os.path.join(Path().absolute(), "logs")
            log_file_path = os.path.join(logs_path, f"{username}.log")
            try:
                file_size = os.path.getsize(log_file_path)
                requested_position = (
                    last_received_index if last_received_index <= file_size else 0
                )
                position = bounded_log_start(
                    file_size, requested_position, tail_bytes=tail_bytes
                )
                with open(log_file_path, "rb") as log_file:
                    seek_log_start(
                        log_file,
                        position,
                        discard_partial_line=position > requested_position,
                    )
                    new_log_entries = log_file.read()
                    next_position = log_file.tell()

                return Response(
                    new_log_entries,
                    status=200,
                    mimetype="text/plain",
                    headers={"X-Log-Position": str(next_position)},
                )

            except (FileNotFoundError, OSError):
                return Response(
                    "Log file not found.", status=404, mimetype="text/plain"
                )

        self.app = Flask(
            __name__,
            template_folder=get_assets_folder(),
            static_folder=get_assets_folder(),
        )

        @self.app.before_request
        def require_authentication():
            if self.password is None:
                if request.path.startswith("/config"):
                    return Response(
                        json.dumps(
                            {
                                "error": (
                                    "Configure an analytics username and password "
                                    "before accessing configuration."
                                )
                            }
                        ),
                        status=403,
                        mimetype="application/json",
                    )
                return None
            if self.shell_bypass_token:
                cookie_token = request.cookies.get(SHELL_BYPASS_COOKIE)
                if cookie_token and secrets.compare_digest(
                    cookie_token, self.shell_bypass_token
                ):
                    return None
                query_token = request.args.get("shell_token")
                if query_token and secrets.compare_digest(
                    query_token, self.shell_bypass_token
                ):
                    # The cookie above is what authenticates every later
                    # same-origin request the dashboard's own JS makes (it
                    # has no way to repeat this query param) - persisted via
                    # the after_request hook below, once, right here.
                    g.tcpm_set_shell_bypass_cookie = True
                    return None
            authorization = request.authorization
            valid_username = authorization is not None and secrets.compare_digest(
                authorization.username or "", self.username or ""
            )
            valid_password = authorization is not None and secrets.compare_digest(
                authorization.password or "", self.password
            )
            if valid_username and valid_password:
                return None
            return Response(
                "Authentication required.",
                status=401,
                headers={"WWW-Authenticate": 'Basic realm="Twitch analytics"'},
            )

        @self.app.after_request
        def persist_shell_bypass_cookie(response):
            if getattr(g, "tcpm_set_shell_bypass_cookie", False):
                response.set_cookie(
                    SHELL_BYPASS_COOKIE,
                    self.shell_bypass_token,
                    httponly=True,
                    samesite="Lax",
                )
            return response

        self.app.add_url_rule(
            "/",
            "index",
            index,
            defaults={
                "refresh": refresh,
                "days_ago": days_ago,
                "log_poll_interval": log_poll_interval,
            },
            methods=["GET"],
        )
        self.app.add_url_rule("/streamers", "streamers", streamers, methods=["GET"])
        self.app.add_url_rule(
            "/streamers/<string:streamer>",
            "delete_streamer_analytics",
            delete_streamer_analytics,
            methods=["DELETE"],
        )
        self.app.add_url_rule(
            "/json/<string:streamer>", "json", read_json, methods=["GET"]
        )
        self.app.add_url_rule("/json_all", "json_all", json_all, methods=["GET"])
        self.app.add_url_rule(
            "/drops_by_category",
            "drops_by_category",
            drops_by_category,
            methods=["GET"],
        )
        self.app.add_url_rule(
            "/now_watching", "now_watching", now_watching, methods=["GET"]
        )
        self.app.add_url_rule("/log", "log", generate_log, methods=["GET"])
        self.app.add_url_rule(
            "/config", "web_config", web_config, methods=["GET", "POST"]
        )
        self.app.add_url_rule(
            "/config/notifications/<string:provider>/test",
            "test_web_notification",
            test_web_notification,
            methods=["POST"],
        )

    def run(self):
        logger.info(
            f"Analytics running on http://{self.host}:{self.port}/",
            extra={"emoji": ":globe_with_meridians:"},
        )
        # Production WSGI server instead of Flask's development server.
        from waitress import serve

        serve(
            self.app,
            host=self.host,
            port=self.port,
            threads=8,
            ident=None,
        )
