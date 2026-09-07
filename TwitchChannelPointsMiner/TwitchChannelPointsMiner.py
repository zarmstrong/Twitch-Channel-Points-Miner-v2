# -*- coding: utf-8 -*-

import json
import logging
import math
import os
import random
import re
import signal
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from pathlib import Path

from TwitchChannelPointsMiner import __version__
from TwitchChannelPointsMiner.classes.Chat import ChatPresence, ThreadChat
from TwitchChannelPointsMiner.classes.DropBadgeCatalog import DropBadgeCatalog
from TwitchChannelPointsMiner.classes.entities.PubsubTopic import PubsubTopic
from TwitchChannelPointsMiner.classes.entities.Streamer import (
    Streamer,
    StreamerSettings,
)
from TwitchChannelPointsMiner.classes.Exceptions import StreamerDoesNotExistException
from TwitchChannelPointsMiner.classes.gql.Errors import RetryError
from TwitchChannelPointsMiner.classes.gql.Integration import GQLFactory
from TwitchChannelPointsMiner.classes.Settings import (
    CategoryCampaignOrder,
    Events,
    FollowersOrder,
    Priority,
    Settings,
    StreamerSource,
)
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.WebSocketsPool import WebSocketsPool
from TwitchChannelPointsMiner.constants import GITHUB_REPOSITORY_URL
from TwitchChannelPointsMiner.data_migration import (
    DataMigrationError,
    migrate_analytics_directory,
)
from TwitchChannelPointsMiner.logger import LoggerSettings, configure_loggers
from TwitchChannelPointsMiner.utils import (
    AttemptStrategy,
    _millify,
    at_least_one_value_in_settings_is,
    check_versions,
    get_user_agent,
    internet_connection_available,
    is_newer_version,
    set_default_settings,
)
from TwitchChannelPointsMiner.WatchStreakCache import WatchStreakCache

# Suppress:
#   - chardet.charsetprober - [feed]
#   - chardet.charsetprober - [get_confidence]
#   - requests - [Starting new HTTPS connection (1)]
#   - Flask (werkzeug) logs
#   - irc.client - [process_data]
#   - irc.client - [_dispatcher]
#   - irc.client - [_handle_message]
logging.getLogger("chardet.charsetprober").setLevel(logging.ERROR)
logging.getLogger("requests").setLevel(logging.ERROR)
logging.getLogger("werkzeug").setLevel(logging.ERROR)
logging.getLogger("irc.client").setLevel(logging.ERROR)
logging.getLogger("websocket").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)


def _migrate_analytics_data(path):
    try:
        return migrate_analytics_directory(path)
    except (DataMigrationError, OSError) as error:
        raise RuntimeError(
            f"Unable to migrate analytics data in {path}: {error}"
        ) from error


def _normalize_streams_watched(streams_watched):
    if type(streams_watched) is int and streams_watched in (1, 2):
        return streams_watched

    logger.error("streams_watched must be either 1 or 2; using the default value 2")
    return 2


def _normalize_streamer_source_priority(source_priority):
    defaults = [
        StreamerSource.STREAMERS,
        StreamerSource.FOLLOWERS,
        StreamerSource.CATEGORIES,
        StreamerSource.BADGES,
        StreamerSource.WILDCARD_CATEGORIES,
    ]
    if not isinstance(source_priority, (list, tuple)):
        logger.error(
            "streamer_source_priority must be a list of StreamerSource values; "
            "using the default order"
        )
        return defaults

    normalized = []
    for source in source_priority:
        if isinstance(source, StreamerSource) and source not in normalized:
            normalized.append(source)
    for source in defaults:
        if source not in normalized:
            normalized.append(source)
    return normalized


def _normalize_badge_drop_streamer_limit(limit):
    if type(limit) is int and limit in (1, 2):
        return limit
    logger.error(
        "badge_drop_streamer_limit must be either 1 or 2; using the default value 1"
    )
    return 1


def _normalize_drop_progress_stall_minutes(minutes):
    if (
        isinstance(minutes, (int, float))
        and not isinstance(minutes, bool)
        and math.isfinite(minutes)
        and (minutes == 0 or minutes >= 5)
    ):
        return float(minutes)
    logger.error(
        "drop_progress_stall_minutes must be 0 or at least 5; "
        "using the default value 10"
    )
    return 10.0


def _normalize_wildcard_category_positive_int(value, name, default):
    if type(value) is int and not isinstance(value, bool) and value > 0:
        return value
    logger.error(
        f"{name} must be a positive integer; using the default value {default}"
    )
    return default


def _normalize_update_check_interval(interval):
    if isinstance(interval, bool) or not isinstance(interval, (int, float)):
        raise TypeError("update_check_interval_hours must be a whole number of hours")
    if math.isinf(interval) and interval > 0:
        return math.inf
    if not math.isfinite(interval) or interval < 3 or int(interval) != interval:
        raise ValueError(
            "update_check_interval_hours must be a whole number from 3 to infinity"
        )
    return int(interval)


def _is_running_in_container():
    return Path("/.dockerenv").exists() or Path("/run/.containerenv").exists()


def _unique_streamer_names(streamer_names):
    return list(dict.fromkeys(streamer_names))


def _drop_progress_report_entries(original, current):
    if original is None:
        return []

    entries = []
    for tracking_key, payload in current.items():
        previous = original.get(tracking_key, {})
        current_minutes = payload.get("current_minutes_watched", 0) or 0
        previous_minutes = previous.get("current_minutes_watched", 0) or 0
        current_status = payload.get("status") or "in_progress"
        previous_status = previous.get("status") or "in_progress"
        if current_minutes == previous_minutes and current_status == previous_status:
            continue
        if (
            current_status == "captured"
            and current_minutes == 0
            and previous_minutes == 0
        ):
            continue

        entry = payload.copy()
        entry["minutes_gained"] = max(current_minutes - previous_minutes, 0)
        entries.append(entry)

    return sorted(
        entries,
        key=lambda entry: (
            str(entry.get("category") or ""),
            str(entry.get("campaign") or ""),
            str(entry.get("item_name") or ""),
        ),
    )


def _capture_drop_progress_baseline(twitch, progress_scraped=False):
    if progress_scraped is False:
        return None
    return twitch.drop_report_snapshot()


DAILY_REPORT_STATE_VERSION = 1


def _load_daily_report_state(path):
    try:
        with open(path, "r", encoding="utf-8") as state_file:
            payload = json.load(state_file)
        if (
            not isinstance(payload, dict)
            or payload.get("version") != DAILY_REPORT_STATE_VERSION
            or not isinstance(payload.get("streamers"), dict)
            or (
                payload.get("drop_progress") is not None
                and not isinstance(payload.get("drop_progress"), dict)
            )
        ):
            raise ValueError("unsupported or malformed state")
        last_report = payload.get("last_report_date")
        return {
            "date": date.fromisoformat(last_report) if last_report else None,
            "streamers": {
                str(username): int(points)
                for username, points in payload["streamers"].items()
            },
            "drop_progress": payload.get("drop_progress"),
        }
    except FileNotFoundError:
        return None
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as error:
        logger.warning(
            f"Unable to read daily-report state from {path}; starting fresh: {error}"
        )
        return None


def _save_daily_report_state(path, report_date, streamers, drop_progress):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": DAILY_REPORT_STATE_VERSION,
        "last_report_date": report_date.isoformat() if report_date else None,
        "streamers": streamers,
        "drop_progress": drop_progress,
    }
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=target.parent,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as state_file:
            temporary = state_file.name
            json.dump(payload, state_file, indent=2, sort_keys=True)
            state_file.flush()
            os.fsync(state_file.fileno())
        os.replace(temporary, target)
    except (OSError, TypeError, ValueError) as error:
        logger.warning(f"Unable to save daily-report state to {target}: {error}")
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


class TwitchChannelPointsMiner:
    __slots__ = [
        "username",
        "twitch",
        "claim_drops_startup",
        "enable_analytics",
        "disable_ssl_cert_verification",
        "disable_at_in_nickname",
        "streams_watched",
        "streamer_source_priority",
        "priority",
        "streamers",
        "events_predictions",
        "minute_watcher_thread",
        "sync_campaigns_thread",
        "ws_pool",
        "session_id",
        "running",
        "start_datetime",
        "original_streamers",
        "original_drop_progress",
        "logs_file",
        "queue_listener",
        "config_reload_lock",
        "drop_badge_catalog",
        "drop_badge_catalog_thread",
        "drop_badge_catalog_stop_event",
        "auto_mine_badge_drops",
        "badge_drop_streamer_limit",
        "badge_drop_category_chat",
        "badge_drop_category_sort",
        "badge_drop_blacklist",
        "wildcard_categories",
        "wildcard_category_limit",
        "wildcard_category_streamer_limit",
        "wildcard_category_pin_active",
        "watch_streak_cache",
        "daily_report_date",
        "daily_report_streamers",
        "daily_report_drop_progress",
        "daily_report_state_path",
        "update_check_enabled",
        "update_check_interval_seconds",
        "next_update_check_at",
    ]

    def __init__(
        self,
        username: str,
        claim_drops_startup: bool = False,
        enable_analytics: bool = False,
        disable_ssl_cert_verification: bool = False,
        disable_at_in_nickname: bool = False,
        # Global logging settings.
        priority: list = [Priority.STREAK, Priority.DROPS, Priority.ORDER],
        # This settings will be global shared trought Settings class
        logger_settings: LoggerSettings = LoggerSettings(),
        # Default values for all streamers
        streamer_settings: StreamerSettings = StreamerSettings(),
        streams_watched: int = 2,
        gql: AttemptStrategy | GQLFactory | None = None,
        streamer_source_priority: list
        | tuple = (
            StreamerSource.STREAMERS,
            StreamerSource.FOLLOWERS,
            StreamerSource.CATEGORIES,
            StreamerSource.BADGES,
            StreamerSource.WILDCARD_CATEGORIES,
        ),
        update_check: bool = True,
        update_check_interval_hours: int | float = 24,
    ):

        # Fixes TypeError: 'NoneType' object is not subscriptable
        if not username or username == "your-twitch-username":
            logger.error("Please edit your runner file (usually run.py) and try again.")
            logger.error("No username, exiting...")
            sys.exit(0)
        if (
            not isinstance(username, str)
            or re.fullmatch(r"[A-Za-z0-9_]{1,25}", username) is None
        ):
            raise ValueError(
                "username must contain only letters, numbers, or underscores "
                "and be between 1 and 25 characters"
            )

        # This disables certificate verification and allows the connection to proceed, but also makes it vulnerable to man-in-the-middle (MITM) attacks.
        Settings.disable_ssl_cert_verification = disable_ssl_cert_verification

        Settings.disable_at_in_nickname = disable_at_in_nickname

        self.streams_watched = _normalize_streams_watched(streams_watched)
        self.streamer_source_priority = _normalize_streamer_source_priority(
            streamer_source_priority
        )
        if not isinstance(update_check, bool):
            raise TypeError("update_check must be a boolean")
        update_check_interval_hours = _normalize_update_check_interval(
            update_check_interval_hours
        )
        self.update_check_enabled = update_check
        self.update_check_interval_seconds = update_check_interval_hours * 60 * 60
        self.next_update_check_at = None
        Settings.latest_release_version = None
        Settings.update_instructions = None

        import socket

        def is_connected():
            try:
                # resolve the IP address of the Twitch.tv domain name
                socket.gethostbyname("twitch.tv")
                return True
            except OSError:
                pass
            return False

        # check for Twitch.tv connectivity every 5 seconds
        error_printed = False
        while not is_connected():
            if not error_printed:
                logger.error("Waiting for Twitch.tv connectivity...")
                error_printed = True
            time.sleep(5)

        # Analytics switch
        Settings.enable_analytics = enable_analytics

        if enable_analytics is True:
            Settings.analytics_path = os.path.join(
                Path().absolute(), "analytics", username
            )
            Path(Settings.analytics_path).mkdir(parents=True, exist_ok=True)
            _migrate_analytics_data(Settings.analytics_path)

        self.username = username

        # Set as global config
        Settings.logger = logger_settings

        # Init as default all the missing values
        streamer_settings.default()
        streamer_settings.bet.default()
        Settings.streamer_settings = streamer_settings

        # user_agent = get_user_agent("FIREFOX")
        user_agent = get_user_agent("CHROME")
        if gql is None:
            gql_factory = GQLFactory()
        elif isinstance(gql, AttemptStrategy):
            gql_factory = GQLFactory(attempt_strategy=gql)
        elif isinstance(gql, GQLFactory):
            gql_factory = gql
        else:
            raise ValueError("gql must be None, AttemptStrategy, or GQLFactory")
        self.twitch = Twitch(self.username, user_agent, gql_factory=gql_factory)

        self.claim_drops_startup = claim_drops_startup
        self.priority = priority if isinstance(priority, list) else [priority]

        self.streamers: list[Streamer] = []
        self.events_predictions = {}
        self.minute_watcher_thread = None
        self.sync_campaigns_thread = None
        self.ws_pool = None

        self.session_id = str(uuid.uuid4())
        self.running = False
        self.start_datetime = None
        self.original_streamers = []
        self.original_drop_progress = None
        self.config_reload_lock = threading.Lock()
        self.drop_badge_catalog = None
        self.drop_badge_catalog_thread = None
        self.drop_badge_catalog_stop_event = threading.Event()
        self.auto_mine_badge_drops = False
        self.badge_drop_streamer_limit = 1
        self.badge_drop_category_chat = None
        self.badge_drop_category_sort = "VIEWERS_DESC"
        self.badge_drop_blacklist = set()
        self.wildcard_categories = False
        self.wildcard_category_limit = 10
        self.wildcard_category_streamer_limit = 1
        self.wildcard_category_pin_active = True

        if not hasattr(Settings, "config_path"):
            Settings.config_path = os.path.abspath(
                os.environ.get(
                    "TCPM_CONFIG_DIR", os.path.join(Path().absolute(), "config")
                )
            )

        self.logs_file, self.queue_listener = configure_loggers(
            self.username, logger_settings
        )
        watch_streak_cache_path = os.path.join(
            Path().absolute(),
            "logs",
            ".state",
            f"watch_streak_cache.{self.username.lower()}.json",
        )
        self.watch_streak_cache = WatchStreakCache.load(
            watch_streak_cache_path, self.username
        )
        self.daily_report_state_path = os.path.join(
            Path().absolute(),
            "logs",
            ".state",
            f"daily_report.{self.username.lower()}.json",
        )
        daily_report_state = _load_daily_report_state(self.daily_report_state_path)
        self.daily_report_date = (
            daily_report_state["date"] if daily_report_state is not None else None
        )
        self.daily_report_streamers = (
            daily_report_state["streamers"] if daily_report_state is not None else {}
        )
        self.daily_report_drop_progress = (
            daily_report_state["drop_progress"]
            if daily_report_state is not None
            else None
        )

        if os.environ.pop("TCPM_LEGACY_CONFIG_NOTICE", None):
            logger.warning(
                "Docker configuration update required: mount "
                "/usr/src/app/config to enable automatic conversion from run.py. "
                "The existing run.py is still running and has not been modified.",
                extra={
                    "emoji": ":warning:",
                    "event": Events.CONFIGURATION,
                    "force_alert": True,
                },
            )

        logger.info(f"Twitch Channel Points Miner - {__version__}")
        logger.info(GITHUB_REPOSITORY_URL)
        self.__check_for_update()

        self._register_signal_handlers()

    def _register_signal_handlers(self):
        """Install SIGINT/SIGSEGV/SIGTERM handlers so Ctrl+C (or an external
        termination signal) triggers a graceful shutdown via self.end().

        signal.signal() raises ValueError outside the main thread of the
        main interpreter - e.g. the Windows desktop shell runs the miner on
        a background thread so pywebview's blocking loop can own the main
        thread. There's simply no signal to catch in that case (window-close
        handling there is done separately, via pywebview's own close event),
        so this is a no-op instead of crashing.
        """
        if threading.current_thread() is not threading.main_thread():
            return
        for sign in [signal.SIGINT, signal.SIGSEGV, signal.SIGTERM]:
            signal.signal(sign, self.end)

    def analytics(
        self,
        host: str = "127.0.0.1",
        port: int = 5000,
        refresh: int = 5,
        days_ago: int = 7,
        password: str = None,
        log_poll_interval: int = 5,
    ):
        # Analytics switch
        if Settings.enable_analytics is True:
            from TwitchChannelPointsMiner.classes.AnalyticsServer import AnalyticsServer

            days_ago = days_ago if days_ago <= 365 * 15 else 365 * 15
            http_server = AnalyticsServer(
                host=host,
                port=port,
                refresh=refresh,
                days_ago=days_ago,
                username=self.username,
                password=password,
                log_poll_interval=log_poll_interval,
            )
            http_server.daemon = True
            http_server.name = "Analytics Thread"
            http_server.start()
        else:
            logger.error("Can't start analytics(), please set enable_analytics=True")

    def mine(
        self,
        streamers: list = [],
        blacklist: list = [],
        followers: bool = False,
        followers_order: FollowersOrder = FollowersOrder.ASC,
        categories: list = [],
        category_drops_enabled: bool = True,
        category_limit: int = 30,
        category_sort="VIEWERS_DESC",
        category_campaign_order: CategoryCampaignOrder = (CategoryCampaignOrder.ORDER),
        category_chat=None,
        category_log_level: int = logging.INFO,
        drop_item_art: bool = False,
        print_open_drop_campaigns_on_load: bool = False,
        scrape_drop_progress_on_load: bool = False,
        log_drop_checks: bool = False,
        track_category_streamer_points: bool = False,
        category_refresh_interval_hours: float = 6,
        drop_progress_stall_minutes: float = 10,
        drop_badge_catalog: bool = True,
        drop_badge_refresh_interval_hours: float = 1,
        auto_mine_badge_drops: bool = False,
        badge_drop_streamer_limit: int = 1,
        wildcard_categories: bool = False,
        wildcard_category_limit: int = 10,
        wildcard_category_streamer_limit: int = 1,
        wildcard_category_pin_active: bool = True,
    ):
        self.run(
            streamers=streamers,
            blacklist=blacklist,
            followers=followers,
            followers_order=followers_order,
            categories=categories,
            category_drops_enabled=category_drops_enabled,
            category_limit=category_limit,
            category_sort=category_sort,
            category_campaign_order=category_campaign_order,
            category_chat=category_chat,
            category_log_level=category_log_level,
            drop_item_art=drop_item_art,
            print_open_drop_campaigns_on_load=print_open_drop_campaigns_on_load,
            scrape_drop_progress_on_load=scrape_drop_progress_on_load,
            log_drop_checks=log_drop_checks,
            track_category_streamer_points=track_category_streamer_points,
            category_refresh_interval_hours=category_refresh_interval_hours,
            drop_progress_stall_minutes=drop_progress_stall_minutes,
            drop_badge_catalog=drop_badge_catalog,
            drop_badge_refresh_interval_hours=drop_badge_refresh_interval_hours,
            auto_mine_badge_drops=auto_mine_badge_drops,
            badge_drop_streamer_limit=badge_drop_streamer_limit,
            wildcard_categories=wildcard_categories,
            wildcard_category_limit=wildcard_category_limit,
            wildcard_category_streamer_limit=wildcard_category_streamer_limit,
            wildcard_category_pin_active=wildcard_category_pin_active,
        )

    def run(
        self,
        streamers: list = [],
        blacklist: list = [],
        followers: bool = False,
        followers_order: FollowersOrder = FollowersOrder.ASC,
        categories: list = [],
        category_drops_enabled: bool = True,
        category_limit: int = 30,
        category_sort="VIEWERS_DESC",
        category_campaign_order: CategoryCampaignOrder = (CategoryCampaignOrder.ORDER),
        category_chat=None,
        category_log_level: int = logging.INFO,
        drop_item_art: bool = False,
        print_open_drop_campaigns_on_load: bool = False,
        scrape_drop_progress_on_load: bool = False,
        log_drop_checks: bool = False,
        track_category_streamer_points: bool = False,
        category_refresh_interval_hours: float = 6,
        drop_progress_stall_minutes: float = 10,
        drop_badge_catalog: bool = True,
        drop_badge_refresh_interval_hours: float = 1,
        auto_mine_badge_drops: bool = False,
        badge_drop_streamer_limit: int = 1,
        wildcard_categories: bool = False,
        wildcard_category_limit: int = 10,
        wildcard_category_streamer_limit: int = 1,
        wildcard_category_pin_active: bool = True,
    ):
        if self.running:
            logger.error("You can't start multiple sessions of this instance!")
        else:
            logger.info(
                f"Start session: '{self.session_id}'", extra={"emoji": ":bomb:"}
            )
            self.running = True
            self.start_datetime = datetime.now()

            self.twitch.login()
            self.twitch.track_drop_item_art = drop_item_art
            self.twitch.scrape_drop_progress_on_load = scrape_drop_progress_on_load
            self.twitch.log_drop_checks = log_drop_checks
            self.twitch.category_log_level = category_log_level
            drop_progress_stall_minutes = _normalize_drop_progress_stall_minutes(
                drop_progress_stall_minutes
            )
            Settings.track_category_streamer_points = track_category_streamer_points
            self.auto_mine_badge_drops = auto_mine_badge_drops is True
            self.badge_drop_streamer_limit = _normalize_badge_drop_streamer_limit(
                badge_drop_streamer_limit
            )
            self.badge_drop_category_chat = category_chat
            self.badge_drop_category_sort = category_sort
            self.badge_drop_blacklist = {
                str(username).lower().strip() for username in blacklist
            }
            self.wildcard_categories = wildcard_categories is True
            self.wildcard_category_limit = _normalize_wildcard_category_positive_int(
                wildcard_category_limit, "wildcard_category_limit", 10
            )
            self.wildcard_category_streamer_limit = (
                _normalize_wildcard_category_positive_int(
                    wildcard_category_streamer_limit,
                    "wildcard_category_streamer_limit",
                    1,
                )
            )
            self.wildcard_category_pin_active = wildcard_category_pin_active is True

            drop_badge_refresh_seconds = 0
            if drop_badge_catalog is True:
                self.drop_badge_catalog = DropBadgeCatalog(
                    self.twitch.twitch_login, Settings.config_path
                )
                self.twitch.drop_badge_rewards = (
                    self.drop_badge_catalog.confirmed_badge_rewards()
                )
                drop_badge_refresh_seconds = (
                    max(float(drop_badge_refresh_interval_hours), 1) * 60 * 60
                    if drop_badge_refresh_interval_hours > 0
                    else 0
                )
                self.drop_badge_catalog_thread = threading.Thread(
                    target=self.__drop_badge_catalog_loop,
                    args=(drop_badge_refresh_seconds,),
                    name="Drop badge catalog",
                    daemon=True,
                )

            if print_open_drop_campaigns_on_load is True:
                self.twitch.log_open_drop_campaigns()

            if self.twitch.scrape_drop_progress_on_load is True:
                self.twitch.scrape_drop_progress_from_inventory(reason="run_load")

            if self.claim_drops_startup is True:
                self.twitch.claim_all_drops_from_inventory()

            self.original_drop_progress = _capture_drop_progress_baseline(
                self.twitch,
                progress_scraped=self.twitch.scrape_drop_progress_on_load is True,
            )

            streamers_name: list = []
            streamers_dict: dict = {}
            category_usernames = set()
            wildcard_category_usernames = set()
            follower_usernames = set()
            explicitly_configured_usernames = set()

            for streamer in streamers:
                username = (
                    streamer.username
                    if isinstance(streamer, Streamer)
                    else streamer.lower().strip()
                )
                if username not in blacklist:
                    streamers_name.append(username)
                    streamers_dict[username] = streamer
                    explicitly_configured_usernames.add(username)

            if followers is True:
                followers_array = self.twitch.get_followers(order=followers_order)
                logger.info(
                    f"Load {len(followers_array)} followers from your profile!",
                    extra={"emoji": ":clipboard:"},
                )
                for username in followers_array:
                    if username not in streamers_dict and username not in blacklist:
                        streamers_name.append(username)
                        streamers_dict[username] = username.lower().strip()
                        follower_usernames.add(username)

            # Fetch the drops inventory once and share it between the
            # preferred and wildcard passes below (both would otherwise
            # independently fetch it via GraphQL when wildcard triggers this
            # cycle). Matches each function's own early return when
            # category_drops_enabled is False, so this never fetches
            # something neither pass would have needed.
            discovery_inventory = None
            if category_drops_enabled and (categories or self.wildcard_categories):
                discovery_inventory = self.twitch.get_drops_inventory()

            eligible_categories = []
            all_category_usernames = []
            if categories:
                eligible_categories = self.twitch.filter_categories_with_active_drops(
                    categories,
                    order=category_campaign_order,
                    drops_enabled=category_drops_enabled,
                    inventory=discovery_inventory,
                )

                if categories and eligible_categories == []:
                    logger.log(
                        category_log_level,
                        "Skipping category stream discovery: no configured categories have active incomplete campaigns",
                        extra={"emoji": ":sleeping:", "category_log": True},
                    )

                for category in eligible_categories:
                    all_category_usernames.extend(
                        self.twitch.get_live_streamers_for_category(
                            category,
                            drops_enabled=category_drops_enabled,
                            limit=category_limit,
                            sort_by=category_sort,
                        )
                    )

                for username in all_category_usernames:
                    category_usernames.add(username)
                    if username not in streamers_dict and username not in blacklist:
                        streamers_name.append(username)
                        streamers_dict[username] = username.lower().strip()

            # Fall back to every other category with an active incomplete
            # drop campaign once the preferred pass leaves capacity idle --
            # not only when no preferred category has an active campaign at
            # all, but also when one does but yielded no live channel this
            # cycle (e.g. the only eligible category has zero live streamers
            # right now). This is its own opt-in, lowest-default-priority
            # StreamerSource tier -- see streamer_source_priority -- not a
            # silent extension of CATEGORIES.
            if self.wildcard_categories and not all_category_usernames:
                wildcard_eligible_categories = (
                    self.twitch.get_wildcard_categories_with_active_drops(
                        exclude_category_slugs=self.twitch.get_category_slugs(
                            categories
                        ),
                        drops_enabled=category_drops_enabled,
                        limit=self.wildcard_category_limit,
                        pinned_category_slugs=set(),
                        pin_active=self.wildcard_category_pin_active,
                        inventory=discovery_inventory,
                        refresh_external_catalog=True,
                    )
                )
                all_wildcard_category_usernames = []
                for category in wildcard_eligible_categories:
                    all_wildcard_category_usernames.extend(
                        self.twitch.get_live_streamers_for_category(
                            category,
                            drops_enabled=category_drops_enabled,
                            limit=self.wildcard_category_streamer_limit,
                            sort_by=category_sort,
                        )
                    )

                for username in all_wildcard_category_usernames:
                    wildcard_category_usernames.add(username)
                    if username not in streamers_dict and username not in blacklist:
                        streamers_name.append(username)
                        streamers_dict[username] = username.lower().strip()

            streamers_name = _unique_streamer_names(streamers_name)
            logger.info(
                f"Loading data for {len(streamers_name)} streamers. Please wait...",
                extra={"emoji": ":nerd_face:"},
            )

            def build_streamer(username):
                time.sleep(random.uniform(0.15, 0.35))
                is_follower_streamer = username in follower_usernames
                is_wildcard_category_streamer = (
                    username in wildcard_category_usernames
                    and username not in explicitly_configured_usernames
                    and is_follower_streamer is False
                )
                is_category_streamer = (
                    username in category_usernames
                    and username not in explicitly_configured_usernames
                    and is_follower_streamer is False
                ) or is_wildcard_category_streamer
                streamer = (
                    streamers_dict[username]
                    if isinstance(streamers_dict[username], Streamer) is True
                    else Streamer(
                        username,
                        settings=(
                            StreamerSettings(chat=category_chat)
                            if is_category_streamer is True
                            and category_chat is not None
                            else None
                        ),
                        from_followers=is_follower_streamer,
                        from_category=is_category_streamer,
                        from_wildcard_category=is_wildcard_category_streamer,
                        explicitly_configured=(
                            username in explicitly_configured_usernames
                        ),
                    )
                )
                streamer.explicitly_configured = (
                    username in explicitly_configured_usernames
                )
                streamer.watch_streak_cache = self.watch_streak_cache
                streamer.channel_id = self.twitch.get_channel_id(username)
                streamer.settings = set_default_settings(
                    streamer.settings, Settings.streamer_settings
                )
                streamer.settings.bet = set_default_settings(
                    streamer.settings.bet, Settings.streamer_settings.bet
                )
                if streamer.settings.chat != ChatPresence.NEVER:
                    streamer.irc_chat = ThreadChat(
                        self.username,
                        self.twitch.twitch_login.get_auth_token(),
                        streamer.username,
                    )
                return streamer

            loaded_streamers = [None] * len(streamers_name)
            workers = min(10, len(streamers_name))
            if workers:
                with ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="Streamer bootstrap"
                ) as executor:
                    futures = {
                        executor.submit(build_streamer, username): (index, username)
                        for index, username in enumerate(streamers_name)
                    }
                    for future in as_completed(futures):
                        index, username = futures[future]
                        try:
                            loaded_streamers[index] = future.result()
                        except StreamerDoesNotExistException:
                            logger.info(
                                f"Streamer {username} does not exist",
                                extra={"emoji": ":cry:"},
                            )
                        except RetryError as error:
                            logger.warning(
                                f"Failed to load streamer {username}, retrying once: {error}"
                            )
                            try:
                                loaded_streamers[index] = build_streamer(username)
                            except StreamerDoesNotExistException:
                                logger.info(
                                    f"Streamer {username} does not exist",
                                    extra={"emoji": ":cry:"},
                                )
                            except RetryError as retry_error:
                                logger.error(
                                    f"Failed to load streamer {username} after "
                                    f"retry: {retry_error}"
                                )
                            except Exception:
                                logger.error(
                                    f"Failed to load streamer {username}",
                                    exc_info=True,
                                )
                        except Exception:
                            logger.error(
                                f"Failed to load streamer {username}", exc_info=True
                            )

            self.streamers = [
                streamer for streamer in loaded_streamers if streamer is not None
            ]

            # Populate the streamers with default values.
            # 1. Load channel points and auto-claim bonus
            # 2. Check if streamers are online
            # 3. DEACTIVATED: Check if the user is a moderator. (was used before the 5th of April 2021 to deactivate predictions)
            invalid_streamers = self.twitch.initialize_streamers_context(self.streamers)
            if invalid_streamers:
                self.streamers = [
                    streamer
                    for streamer in self.streamers
                    if streamer.username not in invalid_streamers
                ]

            self.original_streamers = [
                streamer.channel_points for streamer in self.streamers
            ]
            if Settings.logger.daily_report is True and self.daily_report_date is None:
                self.daily_report_streamers = {
                    streamer.username: streamer.channel_points
                    for streamer in self.streamers
                }
                self.daily_report_drop_progress = self.twitch.drop_report_snapshot()
                report_now = datetime.now()
                report_time = datetime.strptime(
                    Settings.logger.daily_report_time, "%H:%M"
                ).time()
                self.daily_report_date = (
                    report_now.date()
                    if report_now.time() >= report_time
                    else report_now.date() - timedelta(days=1)
                )
                self.__save_daily_report_state()
            elif Settings.logger.daily_report is True:
                for streamer in self.streamers:
                    self.daily_report_streamers.setdefault(
                        streamer.username, streamer.channel_points
                    )
                self.__save_daily_report_state()

            # If we have at least one streamer with settings = make_predictions True
            make_predictions = at_least_one_value_in_settings_is(
                self.streamers, "make_predictions", True
            )

            # If we have at least one streamer with settings = claim_drops True
            # Spawn a thread for sync inventory and dashboard
            if (
                at_least_one_value_in_settings_is(self.streamers, "claim_drops", True)
                is True
            ):
                self.sync_campaigns_thread = threading.Thread(
                    target=self.twitch.sync_campaigns,
                    args=(self.streamers,),
                )
                self.sync_campaigns_thread.name = "Sync campaigns/inventory"
                self.sync_campaigns_thread.start()
                time.sleep(30)

            self.minute_watcher_thread = threading.Thread(
                target=self.twitch.send_minute_watched_events,
                args=(self.streamers, self.priority),
                kwargs={
                    "streams_watched": self.streams_watched,
                    "source_priority": self.streamer_source_priority,
                    "drop_progress_stall_minutes": drop_progress_stall_minutes,
                },
            )
            self.minute_watcher_thread.name = "Minute watcher"
            self.minute_watcher_thread.start()

            self.ws_pool = WebSocketsPool(
                twitch=self.twitch,
                streamers=self.streamers,
                events_predictions=self.events_predictions,
            )

            # Subscribe to community-points-user. Get update for points spent or gains
            user_id = self.twitch.twitch_login.get_user_id()
            # print(f"!!!!!!!!!!!!!! USER_ID: {user_id}")

            # Fixes 'ERR_BADAUTH'
            if not user_id:
                logger.error("No user_id, exiting...")
                self.end(0, 0)

            self.ws_pool.submit(
                PubsubTopic(
                    "community-points-user-v1",
                    user_id=user_id,
                )
            )

            # Going to subscribe to predictions-user-v1. Get update when we place a new prediction (confirm)
            if make_predictions is True:
                self.ws_pool.submit(
                    PubsubTopic(
                        "predictions-user-v1",
                        user_id=user_id,
                    )
                )

            for streamer in self.streamers:
                self.ws_pool.submit(
                    PubsubTopic("video-playback-by-id", streamer=streamer)
                )

                if streamer.settings.follow_raid is True:
                    self.ws_pool.submit(PubsubTopic("raid", streamer=streamer))

                if streamer.settings.make_predictions is True:
                    self.ws_pool.submit(
                        PubsubTopic("predictions-channel-v1", streamer=streamer)
                    )

                if streamer.settings.claim_moments is True:
                    self.ws_pool.submit(
                        PubsubTopic("community-moments-channel-v1", streamer=streamer)
                    )

                if streamer.settings.community_goals is True:
                    self.ws_pool.submit(
                        PubsubTopic("community-points-channel-v1", streamer=streamer)
                    )

            if self.drop_badge_catalog_thread is not None:
                self.drop_badge_catalog_thread.start()

            refresh_context = time.time()
            category_refresh_interval_seconds = (
                max(category_refresh_interval_hours, 0.5) * 60 * 60
                if category_refresh_interval_hours > 0
                else 0
            )
            effective_category_refresh_seconds = (
                min(category_refresh_interval_seconds, 5 * 60)
                if self.twitch.twitchdrops_app_campaigns
                and category_refresh_interval_seconds > 0
                else category_refresh_interval_seconds
            )
            next_category_refresh_at = (
                time.time()
                + effective_category_refresh_seconds
                + random.randint(20, 5 * 60)
                if effective_category_refresh_seconds > 0
                else None
            )
            upcoming_drop_start = self.twitch.next_upcoming_drop_start()
            if upcoming_drop_start is not None and next_category_refresh_at is not None:
                next_category_refresh_at = min(
                    next_category_refresh_at,
                    time.time()
                    + max((upcoming_drop_start - datetime.utcnow()).total_seconds(), 0)
                    + 5,
                )
            while self.running and self.twitch.running:
                if self.twitch.restart_requested.wait(random.uniform(20, 60)):
                    break
                self.__send_daily_report_if_due()
                self.__check_for_update()
                # Do an external control for WebSocket. Check if the thread is running
                # Check if is not None because maybe we have already created a new connection on array+1 and now index is None
                for index in range(0, len(self.ws_pool.ws)):
                    if (
                        self.ws_pool.ws[index].is_reconnecting is False
                        and self.ws_pool.ws[index].elapsed_last_ping() > 10
                        and internet_connection_available() is True
                    ):
                        logger.info(
                            f"#{index} - The last PING was sent more than 10 minutes ago. Reconnecting to the WebSocket..."
                        )
                        WebSocketsPool.handle_reconnection(self.ws_pool.ws[index])

                if ((time.time() - refresh_context) // 60) >= 30:
                    refresh_context = time.time()
                    for index in range(0, len(self.streamers)):
                        if self.streamers[index].is_online:
                            self.twitch.load_channel_points_context(
                                self.streamers[index]
                            )

                if (
                    (categories or self.wildcard_categories)
                    and next_category_refresh_at is not None
                    and time.time() >= next_category_refresh_at
                ):
                    with self.config_reload_lock:
                        self.__refresh_category_streamers(
                            categories=categories,
                            blacklist=blacklist,
                            drops_enabled=category_drops_enabled,
                            limit=category_limit,
                            sort_by=category_sort,
                            campaign_order=category_campaign_order,
                            category_chat=category_chat,
                            category_log_level=category_log_level,
                            wildcard_categories=self.wildcard_categories,
                            wildcard_category_limit=self.wildcard_category_limit,
                            wildcard_category_streamer_limit=(
                                self.wildcard_category_streamer_limit
                            ),
                            wildcard_category_pin_active=(
                                self.wildcard_category_pin_active
                            ),
                        )
                    effective_category_refresh_seconds = (
                        min(category_refresh_interval_seconds, 5 * 60)
                        if self.twitch.twitchdrops_app_campaigns
                        else category_refresh_interval_seconds
                    )
                    next_category_refresh_at = (
                        time.time()
                        + effective_category_refresh_seconds
                        + random.randint(20, 5 * 60)
                    )
                    upcoming_drop_start = self.twitch.next_upcoming_drop_start()
                    if upcoming_drop_start is not None:
                        next_category_refresh_at = min(
                            next_category_refresh_at,
                            time.time()
                            + max(
                                (
                                    upcoming_drop_start - datetime.utcnow()
                                ).total_seconds(),
                                0,
                            )
                            + 5,
                        )

    def __check_for_update(self, now=None):
        if not self.update_check_enabled:
            return False
        now = time.time() if now is None else now
        if self.next_update_check_at is not None and now < self.next_update_check_at:
            return False

        _, github_version = check_versions()
        current_version = __version__
        update_available = is_newer_version(github_version, current_version)
        interval = self.update_check_interval_seconds
        if update_available:
            interval = max(interval, 24 * 60 * 60)
            if _is_running_in_container():
                update_instructions = (
                    "Docker deployment detected. On the Docker host, pull "
                    "zacharmstrong/twitch-channel-points-miner:latest and recreate "
                    "the container. Docker Compose: docker compose pull && "
                    "docker compose up -d"
                )
            else:
                update_instructions = (
                    f"Download it from {GITHUB_REPOSITORY_URL}/releases/latest"
                )
            Settings.latest_release_version = github_version
            Settings.update_instructions = update_instructions
            logger.info(
                f"Update available: Twitch Channel Points Miner {github_version} "
                f"(running {current_version}). {update_instructions}",
                extra={
                    "emoji": ":arrow_up:",
                    "event": Events.UPDATE_AVAILABLE,
                    "force_alert": True,
                },
            )
        elif github_version != "0.0.0":
            Settings.latest_release_version = None
            Settings.update_instructions = None
        elif github_version == "0.0.0":
            logger.warning("Unable to check GitHub for a newer release")

        self.next_update_check_at = now + interval
        return update_available

    def __send_daily_report_if_due(self, now=None):
        if Settings.logger.daily_report is not True or not self.streamers:
            return
        now = now or datetime.now()
        scheduled = datetime.strptime(Settings.logger.daily_report_time, "%H:%M").time()
        if now.time() < scheduled or self.daily_report_date == now.date():
            return

        activity_lines = []
        for streamer in self.streamers:
            baseline = self.daily_report_streamers.get(
                streamer.username, streamer.channel_points
            )
            gained = streamer.channel_points - baseline
            if gained != 0:
                activity_lines.append(
                    f"{streamer.username}: {gained:+,} channel points"
                )

        drop_progress = self.twitch.drop_report_snapshot()
        drop_entries = _drop_progress_report_entries(
            self.daily_report_drop_progress,
            drop_progress,
        )
        if drop_entries:
            activity_lines.append(
                f"Drop progress updated for {len(drop_entries)} reward(s):"
            )
            for entry in drop_entries:
                category = entry.get("category") or "Unknown game"
                campaign = entry.get("campaign") or "Unknown campaign"
                minutes_required = entry.get("minutes_required", 0) or 0
                activity_lines.append(
                    f"- {category} — {campaign} — "
                    f"{entry.get('item_name') or 'Unknown reward'}: "
                    f"+{entry.get('minutes_gained', 0) or 0}m"
                    f"{f' of {minutes_required}m' if minutes_required > 0 else ''}, "
                    f"{str(entry.get('status') or 'in_progress').replace('_', ' ')}"
                )

        if activity_lines:
            lines = [f"Daily report for {now.date().isoformat()}", *activity_lines]
            logger.info(
                "\n".join(lines),
                extra={"emoji": ":bar_chart:", "event": Events.DAILY_REPORT},
            )
        self.daily_report_date = now.date()
        self.daily_report_streamers = {
            streamer.username: streamer.channel_points for streamer in self.streamers
        }
        self.daily_report_drop_progress = drop_progress
        self.__save_daily_report_state()

    def __save_daily_report_state(self):
        _save_daily_report_state(
            self.daily_report_state_path,
            self.daily_report_date,
            self.daily_report_streamers,
            self.daily_report_drop_progress,
        )

    def __drop_badge_catalog_loop(self, refresh_seconds):
        logger.info(
            "Starting Drop badge catalog check in the background: "
            f"{self.drop_badge_catalog.path}",
            extra={"emoji": ":card_index_dividers:", "category_log": True},
        )
        self.__sync_drop_badge_catalog(initial=True)
        if self.auto_mine_badge_drops:
            self.__auto_mine_badge_campaigns()
        if refresh_seconds <= 0:
            return
        ownership_refresh_seconds = min(refresh_seconds, 5 * 60)
        next_catalog_refresh = time.monotonic() + refresh_seconds
        while self.running and self.twitch.running:
            if self.drop_badge_catalog_stop_event.wait(ownership_refresh_seconds):
                return
            if self.running and self.twitch.running:
                if time.monotonic() >= next_catalog_refresh:
                    self.__sync_drop_badge_catalog()
                    next_catalog_refresh = time.monotonic() + refresh_seconds
                if self.auto_mine_badge_drops:
                    self.__auto_mine_badge_campaigns()

    def __sync_drop_badge_catalog(self, initial=False):
        if self.drop_badge_catalog is None:
            return
        try:
            result = self.drop_badge_catalog.sync()
            self.twitch.drop_badge_rewards = (
                self.drop_badge_catalog.confirmed_badge_rewards()
            )
        except Exception as error:
            logger.warning(
                f"Unable to refresh Drop badge catalog: {error}",
                extra={"emoji": ":warning:", "category_log": True},
            )
            return

        new_campaigns = result["new_campaigns"]
        new_badges = sum(
            drop.get("badge_classification", {}).get("status") == "BADGE"
            for record in new_campaigns
            for drop in record.get("campaign", {}).get("drops", []) or []
        )
        if initial:
            logger.info(
                "Drop badge catalog loaded: "
                f"{result['stored_campaigns']} campaigns, "
                f"{result['confirmed_badge_rewards']} confirmed badge rewards",
                extra={"emoji": ":card_index_dividers:", "category_log": True},
            )
        elif new_campaigns:
            logger.info(
                f"Drop badge catalog found {len(new_campaigns)} new campaigns "
                f"with {new_badges} confirmed badges",
                extra={"emoji": ":gift:", "category_log": True},
            )
        else:
            logger.info(
                "Drop badge catalog hourly check found no new campaigns",
                extra={"emoji": ":white_check_mark:", "category_log": True},
            )

    def __auto_mine_badge_campaigns(self):
        if self.drop_badge_catalog is None or self.ws_pool is None:
            return

        try:
            previous_owned_badges = getattr(self.twitch, "available_badge_names", None)
            owned_badges = self.twitch.get_earned_badge_names(refresh=True)
            if owned_badges is None:
                self.twitch.available_badge_names = previous_owned_badges
                logger.warning(
                    "Skipping automatic badge Drop discovery because Twitch did "
                    "not return the account's earned badge inventory",
                    extra={"emoji": ":warning:", "category_log": True},
                )
                return

            completed_campaign_signatures = set()
            try:
                inventory = self.twitch.get_drops_inventory()
                completed_campaign_signatures = (
                    self.twitch.completed_badge_campaign_signatures(inventory)
                )
            except Exception as error:
                logger.warning(
                    "Unable to fetch the account's Drops inventory for badge "
                    f"campaign completion checks: {error}",
                    extra={"emoji": ":warning:", "category_log": True},
                )

            campaigns = self.drop_badge_catalog.eligible_badge_campaigns(
                owned_badges,
                completed_campaign_signatures=completed_campaign_signatures,
            )
        except Exception as error:
            logger.warning(
                f"Unable to determine eligible badge Drop campaigns: {error}",
                extra={"emoji": ":warning:", "category_log": True},
            )
            return

        unrestricted_games = []
        restricted_campaigns_by_game = {}
        for record in campaigns:
            game_slug = str(record.get("game_slug") or "").strip()
            campaign = record.get("campaign") or {}
            if not game_slug:
                continue
            channels = [
                channel.lower().strip()
                for channel in campaign.get("channels", []) or []
                if isinstance(channel, str) and channel.strip()
            ]
            if campaign.get("all_channels") is True:
                unrestricted_games.append(game_slug)
            elif channels:
                restricted_campaigns_by_game.setdefault(game_slug, []).append(
                    {**campaign, "channels": channels}
                )

        discovered_usernames = []
        for game_slug in dict.fromkeys(unrestricted_games):
            try:
                discovered_usernames.extend(
                    self.twitch.get_live_streamers_for_category(
                        game_slug,
                        drops_enabled=True,
                        limit=self.badge_drop_streamer_limit,
                        sort_by=self.badge_drop_category_sort,
                        respect_campaign_restrictions=False,
                    )
                )
            except Exception as error:
                logger.warning(
                    f"Unable to find a live channel for badge Drop campaign "
                    f"'{game_slug}': {error}",
                    extra={"emoji": ":warning:", "category_log": True},
                )

        for game_slug, restricted_campaigns in restricted_campaigns_by_game.items():
            try:
                discovered_usernames.extend(
                    self.twitch.get_live_streamers_for_category(
                        game_slug,
                        drops_enabled=True,
                        limit=30,
                        sort_by=self.badge_drop_category_sort,
                        restricted_campaigns=restricted_campaigns,
                    )
                )
            except Exception as error:
                logger.warning(
                    f"Unable to find restricted live channels for badge Drop "
                    f"campaign '{game_slug}': {error}",
                    extra={"emoji": ":warning:", "category_log": True},
                )

        added = 0
        earned_badge_changed = (
            previous_owned_badges is not None
            and owned_badges.issuperset(previous_owned_badges)
            and owned_badges != previous_owned_badges
        )
        with self.config_reload_lock:
            if earned_badge_changed:
                self.__reconcile_badge_campaign_streamers(discovered_usernames)
            existing_usernames = {streamer.username for streamer in self.streamers}
            for username in dict.fromkeys(discovered_usernames):
                username = str(username).lower().strip()
                if (
                    not username
                    or username in existing_usernames
                    or username in self.badge_drop_blacklist
                ):
                    continue

                try:
                    streamer = Streamer(
                        username,
                        settings=StreamerSettings(
                            claim_drops=True,
                            chat=self.badge_drop_category_chat,
                        ),
                        from_category=True,
                        from_badge_campaign=True,
                    )
                    streamer.channel_id = self.twitch.get_channel_id(username)
                    streamer.settings = set_default_settings(
                        streamer.settings, Settings.streamer_settings
                    )
                    streamer.settings.bet = set_default_settings(
                        streamer.settings.bet, Settings.streamer_settings.bet
                    )
                    if streamer.settings.chat != ChatPresence.NEVER:
                        streamer.irc_chat = ThreadChat(
                            self.username,
                            self.twitch.twitch_login.get_auth_token(),
                            streamer.username,
                        )

                    self.twitch.load_channel_points_context(streamer)
                    self.twitch.check_streamer_online(streamer)
                    self.streamers.append(streamer)
                    self.original_streamers.append(streamer.channel_points)
                    existing_usernames.add(username)
                    added += 1

                    self.ws_pool.submit(
                        PubsubTopic("video-playback-by-id", streamer=streamer)
                    )
                    if streamer.settings.follow_raid is True:
                        self.ws_pool.submit(PubsubTopic("raid", streamer=streamer))
                    if streamer.settings.make_predictions is True:
                        self.ws_pool.submit(
                            PubsubTopic("predictions-channel-v1", streamer=streamer)
                        )
                    if streamer.settings.claim_moments is True:
                        self.ws_pool.submit(
                            PubsubTopic(
                                "community-moments-channel-v1", streamer=streamer
                            )
                        )
                    if streamer.settings.community_goals is True:
                        self.ws_pool.submit(
                            PubsubTopic(
                                "community-points-channel-v1", streamer=streamer
                            )
                        )
                except StreamerDoesNotExistException:
                    logger.info(
                        f"Streamer {username} does not exist",
                        extra={"emoji": ":cry:"},
                    )
                except Exception as error:
                    logger.warning(
                        f"Unable to add badge Drop streamer {username}: {error}",
                        extra={"emoji": ":warning:", "category_log": True},
                    )

            if added > 0 and self.sync_campaigns_thread is None:
                self.sync_campaigns_thread = threading.Thread(
                    target=self.twitch.sync_campaigns,
                    args=(self.streamers,),
                    name="Sync campaigns/inventory",
                )
                self.sync_campaigns_thread.start()

        badge_count = sum(len(record.get("eligible_drops", [])) for record in campaigns)
        logger.info(
            "Automatic badge Drop check complete: "
            f"{len(campaigns)} eligible campaigns, {badge_count} unearned badges, "
            f"{added} new live streamers",
            extra={"emoji": ":gift:", "category_log": True},
        )

    def __reconcile_badge_campaign_streamers(self, discovered_usernames):
        """Retire badge-only channels after Twitch reports an ownership change."""
        discovered = {
            str(username).lower().strip()
            for username in discovered_usernames
            if str(username).strip()
        }
        retained = []
        retained_baselines = []
        removed = []
        for index, streamer in enumerate(self.streamers):
            baseline = (
                self.original_streamers[index]
                if index < len(self.original_streamers)
                else streamer.channel_points
            )
            if (
                streamer.from_badge_campaign is not True
                or streamer.username in discovered
            ):
                retained.append(streamer)
                retained_baselines.append(baseline)
                continue

            streamer.from_badge_campaign = False
            if streamer.explicitly_configured or streamer.from_followers:
                retained.append(streamer)
                retained_baselines.append(baseline)
                continue

            self.ws_pool.remove_streamer_topics(streamer)
            if streamer.irc_chat is not None and streamer.irc_chat.is_alive():
                streamer.irc_chat.stop()
                streamer.irc_chat.join(timeout=5)
            removed.append(streamer.username)

        self.streamers[:] = retained
        self.original_streamers[:] = retained_baselines
        if removed:
            logger.info(
                "Earned badge inventory changed; retired badge Drop streamers: "
                + ", ".join(removed),
                extra={"emoji": ":next_track_button:", "category_log": True},
            )

    def __reconcile_category_streamers(self, discovered_usernames, wildcard=False):
        """Retire category-only channels absent from the latest discovery.

        Scoped by `wildcard` so a preferred-category refresh only retires
        preferred-category streamers, and a wildcard refresh only retires
        wildcard-discovered ones -- the two discovery passes run independently
        and must not evict each other's streamers just for being absent from
        the other pass's discovery list.
        """
        discovered = {
            str(username).lower().strip()
            for username in discovered_usernames
            if str(username).strip()
        }
        retained = []
        retained_baselines = []
        removed = []
        for index, streamer in enumerate(self.streamers):
            baseline = (
                self.original_streamers[index]
                if index < len(self.original_streamers)
                else streamer.channel_points
            )
            in_scope = (
                streamer.from_category is True
                and streamer.from_wildcard_category is wildcard
            )
            if not in_scope or streamer.username in discovered:
                retained.append(streamer)
                retained_baselines.append(baseline)
                continue

            streamer.from_category = False
            streamer.from_wildcard_category = False
            if (
                streamer.explicitly_configured
                or streamer.from_followers
                or streamer.from_badge_campaign
            ):
                retained.append(streamer)
                retained_baselines.append(baseline)
                continue

            self.ws_pool.remove_streamer_topics(streamer)
            if streamer.irc_chat is not None and streamer.irc_chat.is_alive():
                streamer.irc_chat.stop()
                streamer.irc_chat.join(timeout=5)
            removed.append(streamer.username)

        self.streamers[:] = retained
        self.original_streamers[:] = retained_baselines
        if removed:
            label = "Wildcard category" if wildcard else "Category"
            logger.info(
                f"{label} refresh retired stale Drop streamers: " + ", ".join(removed),
                extra={"emoji": ":next_track_button:", "category_log": True},
            )

    def __order_category_streamers(self, discovered_usernames, wildcard=False):
        """Match category streamer priority to the latest discovery order."""
        if len(self.original_streamers) < len(self.streamers):
            self.original_streamers.extend(
                streamer.channel_points
                for streamer in self.streamers[len(self.original_streamers) :]
            )
        priority = {
            username: index
            for index, username in enumerate(
                dict.fromkeys(
                    str(username).lower().strip()
                    for username in discovered_usernames
                    if str(username).strip()
                )
            )
        }
        category_indexes = [
            index
            for index, streamer in enumerate(self.streamers)
            if streamer.from_category is True
            and streamer.from_wildcard_category is wildcard
            and streamer.username in priority
        ]
        ordered = sorted(
            (
                (self.streamers[index], self.original_streamers[index])
                for index in category_indexes
            ),
            key=lambda item: priority[item[0].username],
        )
        for index, (streamer, baseline) in zip(category_indexes, ordered):
            self.streamers[index] = streamer
            self.original_streamers[index] = baseline

    def __add_discovered_category_streamers(
        self, discovered_usernames, blacklist, category_chat, wildcard=False
    ):
        existing_usernames = {streamer.username for streamer in self.streamers}
        blacklist_usernames = {str(username).lower().strip() for username in blacklist}
        added = 0
        for username in dict.fromkeys(discovered_usernames):
            username = username.lower().strip()
            if username in existing_usernames or username in blacklist_usernames:
                continue

            try:
                streamer = Streamer(
                    username,
                    settings=(
                        StreamerSettings(chat=category_chat)
                        if category_chat is not None
                        else None
                    ),
                    from_category=True,
                    from_wildcard_category=wildcard,
                )
                streamer.channel_id = self.twitch.get_channel_id(username)
                streamer.settings = set_default_settings(
                    streamer.settings, Settings.streamer_settings
                )
                streamer.settings.bet = set_default_settings(
                    streamer.settings.bet, Settings.streamer_settings.bet
                )
                if streamer.settings.chat != ChatPresence.NEVER:
                    streamer.irc_chat = ThreadChat(
                        self.username,
                        self.twitch.twitch_login.get_auth_token(),
                        streamer.username,
                    )

                self.twitch.load_channel_points_context(streamer)
                self.twitch.check_streamer_online(streamer)
                self.streamers.append(streamer)
                self.original_streamers.append(streamer.channel_points)
                existing_usernames.add(username)
                added += 1

                self.ws_pool.submit(
                    PubsubTopic("video-playback-by-id", streamer=streamer)
                )
                if streamer.settings.follow_raid is True:
                    self.ws_pool.submit(PubsubTopic("raid", streamer=streamer))
                if streamer.settings.make_predictions is True:
                    self.ws_pool.submit(
                        PubsubTopic("predictions-channel-v1", streamer=streamer)
                    )
                if streamer.settings.claim_moments is True:
                    self.ws_pool.submit(
                        PubsubTopic("community-moments-channel-v1", streamer=streamer)
                    )
                if streamer.settings.community_goals is True:
                    self.ws_pool.submit(
                        PubsubTopic("community-points-channel-v1", streamer=streamer)
                    )
            except StreamerDoesNotExistException:
                logger.info(
                    f"Streamer {username} does not exist",
                    extra={"emoji": ":cry:"},
                )
        return added

    def __refresh_category_streamers(
        self,
        categories,
        blacklist,
        drops_enabled,
        limit,
        sort_by,
        campaign_order,
        category_chat,
        category_log_level,
        wildcard_categories=False,
        wildcard_category_limit=10,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    ):
        logger.log(
            category_log_level,
            "Refreshing configured categories and drop campaigns",
            extra={
                "emoji": ":arrows_counterclockwise:",
                "category_log": True,
            },
        )

        # Force live campaign discovery to run again instead of reusing startup data.
        self.twitch.discovered_open_drop_campaigns = None
        # Fetch the drops inventory once and share it between the preferred
        # and wildcard passes below, instead of each independently fetching
        # it via GraphQL when wildcard triggers this cycle. Matches each
        # function's own early return when drops_enabled is False, so this
        # never fetches something neither pass would have needed.
        discovery_inventory = None
        if drops_enabled and (categories or wildcard_categories):
            discovery_inventory = self.twitch.get_drops_inventory()
        eligible_categories = self.twitch.filter_categories_with_active_drops(
            categories,
            order=campaign_order,
            drops_enabled=drops_enabled,
            inventory=discovery_inventory,
        )
        discovered_usernames = []
        for category in eligible_categories:
            discovered_usernames.extend(
                self.twitch.get_live_streamers_for_category(
                    category,
                    drops_enabled=drops_enabled,
                    limit=limit,
                    sort_by=sort_by,
                )
            )

        added = self.__add_discovered_category_streamers(
            discovered_usernames, blacklist, category_chat, wildcard=False
        )

        # Wildcard discovery only runs once the preferred pass leaves
        # capacity idle this cycle -- not only when no preferred category has
        # an active campaign at all, but also when one does but produced no
        # live streamer (e.g. the only eligible category has zero live
        # channels right now). If the preferred pass DID produce at least one
        # streamer, the wildcard pass below deliberately stands down (this is
        # the existing traffic safeguard and is unchanged in spirit, just
        # retargeted at actual idle capacity instead of category eligibility
        # alone). Standing down is transient: it means "not evaluated this
        # cycle", not "gone". The wildcard-scoped reconcile/order calls below
        # are skipped for that specific case, so already-tracked wildcard
        # streamers are left completely alone (same connections, same
        # tracking) rather than retired on the basis of an empty discovery
        # list that was never actually populated. Watch-slot priority
        # arbitration already ranks CATEGORIES above WILDCARD_CATEGORIES, so
        # a still-tracked wildcard stream simply loses contested slots to a
        # preferred one without needing to be torn down. They're only retired
        # by a genuine discovery pass that re-evaluates the full eligible set
        # and finds them actually gone -- or immediately, below, if
        # wildcard_categories itself has been turned off (that reconcile
        # still runs unconditionally, same "off means off" semantics as
        # disabling `categories`).
        wildcard_discovery_ran = wildcard_categories and not discovered_usernames
        wildcard_standing_down = wildcard_categories and not wildcard_discovery_ran
        wildcard_discovered_usernames = []
        if wildcard_discovery_ran:
            pinned_category_slugs = {
                self.twitch.get_game_name_slug(streamer.stream.game_name())
                for streamer in self.streamers
                if streamer.from_wildcard_category is True
                and streamer.is_online
                and streamer.stream.game_name()
            }
            wildcard_eligible_categories = (
                self.twitch.get_wildcard_categories_with_active_drops(
                    exclude_category_slugs=self.twitch.get_category_slugs(categories),
                    drops_enabled=drops_enabled,
                    limit=wildcard_category_limit,
                    pinned_category_slugs=pinned_category_slugs,
                    pin_active=wildcard_category_pin_active,
                    inventory=discovery_inventory,
                    refresh_external_catalog=True,
                )
            )
            for category in wildcard_eligible_categories:
                wildcard_discovered_usernames.extend(
                    self.twitch.get_live_streamers_for_category(
                        category,
                        drops_enabled=drops_enabled,
                        limit=wildcard_category_streamer_limit,
                        sort_by=sort_by,
                    )
                )

        added += self.__add_discovered_category_streamers(
            wildcard_discovered_usernames, blacklist, category_chat, wildcard=True
        )

        # Keep the previous category set available while replacement channels
        # are initialized so the minute watcher cannot observe a partial refresh.
        self.__reconcile_category_streamers(discovered_usernames, wildcard=False)
        self.__order_category_streamers(discovered_usernames, wildcard=False)
        if not wildcard_standing_down:
            self.__reconcile_category_streamers(
                wildcard_discovered_usernames, wildcard=True
            )
            self.__order_category_streamers(
                wildcard_discovered_usernames, wildcard=True
            )

        if added > 0 and self.sync_campaigns_thread is None:
            self.sync_campaigns_thread = threading.Thread(
                target=self.twitch.sync_campaigns,
                args=(self.streamers,),
            )
            self.sync_campaigns_thread.name = "Sync campaigns/inventory"
            self.sync_campaigns_thread.start()

        unique_wildcard_discovered = len(dict.fromkeys(wildcard_discovered_usernames))
        logger.log(
            category_log_level,
            f"Category refresh complete: {len(eligible_categories)} active categories, "
            f"{unique_wildcard_discovered} wildcard streamer(s) discovered, "
            f"{added} new streamers",
            extra={"emoji": ":white_check_mark:", "category_log": True},
        )

    def add_streamers(self, streamers):
        """Add explicitly configured streamers to a running miner."""
        if not self.running or self.ws_pool is None:
            raise RuntimeError("The miner is not ready for live configuration changes")
        with self.config_reload_lock:
            self._add_streamers(streamers)

    def remove_streamers(self, usernames):
        """Remove explicit streamers from a running miner safely."""
        if not self.running or self.ws_pool is None:
            raise RuntimeError("The miner is not ready for live configuration changes")
        requested = {str(username).lower().strip() for username in usernames}
        with self.config_reload_lock:
            retained = []
            retained_baselines = []
            for index, streamer in enumerate(self.streamers):
                baseline = (
                    self.original_streamers[index]
                    if index < len(self.original_streamers)
                    else streamer.channel_points
                )
                if streamer.username not in requested:
                    retained.append(streamer)
                    retained_baselines.append(baseline)
                    continue
                streamer.explicitly_configured = False
                if (
                    streamer.from_followers
                    or streamer.from_category
                    or streamer.from_badge_campaign
                ):
                    retained.append(streamer)
                    retained_baselines.append(baseline)
                    continue
                self.ws_pool.remove_streamer_topics(streamer)
                if streamer.irc_chat is not None and streamer.irc_chat.is_alive():
                    streamer.irc_chat.stop()
                    streamer.irc_chat.join(timeout=5)
                logger.info(
                    f"Removed {streamer.username} from the reloaded configuration",
                    extra={"emoji": ":heavy_minus_sign:"},
                )
            self.streamers[:] = retained
            self.original_streamers[:] = retained_baselines

    def _add_streamers(self, streamers):
        existing = {streamer.username for streamer in self.streamers}
        for configured in streamers:
            username = (
                configured.username
                if isinstance(configured, Streamer)
                else str(configured).lower().strip()
            )
            if username in existing:
                continue
            try:
                streamer = (
                    configured
                    if isinstance(configured, Streamer)
                    else Streamer(username)
                )
                streamer.explicitly_configured = True
                streamer.channel_id = self.twitch.get_channel_id(username)
                streamer.settings = set_default_settings(
                    streamer.settings, Settings.streamer_settings
                )
                streamer.settings.bet = set_default_settings(
                    streamer.settings.bet, Settings.streamer_settings.bet
                )
                if streamer.settings.chat != ChatPresence.NEVER:
                    streamer.irc_chat = ThreadChat(
                        self.username,
                        self.twitch.twitch_login.get_auth_token(),
                        streamer.username,
                    )
                self.twitch.load_channel_points_context(streamer)
                self.twitch.check_streamer_online(streamer)
                self.streamers.append(streamer)
                self.original_streamers.append(streamer.channel_points)
                existing.add(username)
                self.ws_pool.submit(
                    PubsubTopic("video-playback-by-id", streamer=streamer)
                )
                if streamer.settings.follow_raid is True:
                    self.ws_pool.submit(PubsubTopic("raid", streamer=streamer))
                if streamer.settings.make_predictions is True:
                    self.ws_pool.submit(
                        PubsubTopic("predictions-channel-v1", streamer=streamer)
                    )
                if streamer.settings.claim_moments is True:
                    self.ws_pool.submit(
                        PubsubTopic("community-moments-channel-v1", streamer=streamer)
                    )
                if streamer.settings.community_goals is True:
                    self.ws_pool.submit(
                        PubsubTopic("community-points-channel-v1", streamer=streamer)
                    )
                logger.info(
                    f"Added {streamer.username} from the reloaded configuration",
                    extra={"emoji": ":heavy_plus_sign:"},
                )
            except StreamerDoesNotExistException:
                logger.info(
                    f"Streamer {username} does not exist",
                    extra={"emoji": ":cry:"},
                )

    def refresh_categories(self, mine_config):
        """Apply the current configured category list to a running miner."""
        with self.config_reload_lock:
            self.wildcard_categories = (
                mine_config.get("wildcard_categories", False) is True
            )
            self.wildcard_category_limit = _normalize_wildcard_category_positive_int(
                mine_config.get("wildcard_category_limit", 10),
                "wildcard_category_limit",
                10,
            )
            self.wildcard_category_streamer_limit = (
                _normalize_wildcard_category_positive_int(
                    mine_config.get("wildcard_category_streamer_limit", 1),
                    "wildcard_category_streamer_limit",
                    1,
                )
            )
            self.wildcard_category_pin_active = (
                mine_config.get("wildcard_category_pin_active", True) is True
            )
            self.__refresh_category_streamers(
                categories=mine_config.get("categories", []),
                blacklist=mine_config.get("blacklist", []),
                drops_enabled=mine_config.get("category_drops_enabled", True),
                limit=mine_config.get("category_limit", 30),
                sort_by=mine_config.get("category_sort", "VIEWERS_DESC"),
                campaign_order=mine_config.get(
                    "category_campaign_order", CategoryCampaignOrder.ORDER
                ),
                category_chat=mine_config.get("category_chat"),
                category_log_level=mine_config.get("category_log_level", logging.INFO),
                wildcard_categories=self.wildcard_categories,
                wildcard_category_limit=self.wildcard_category_limit,
                wildcard_category_streamer_limit=(
                    self.wildcard_category_streamer_limit
                ),
                wildcard_category_pin_active=self.wildcard_category_pin_active,
            )

    def end(self, signum, frame):
        if not self.running:
            return

        logger.info("CTRL+C Detected! Please wait just a moment!")

        for streamer in self.streamers:
            if (
                streamer.irc_chat is not None
                and streamer.settings.chat != ChatPresence.NEVER
            ):
                streamer.leave_chat()
                if streamer.irc_chat.is_alive() is True:
                    streamer.irc_chat.join()

        self.running = self.twitch.running = False
        self.drop_badge_catalog_stop_event.set()
        if self.ws_pool is not None:
            self.ws_pool.end()

        if self.minute_watcher_thread is not None:
            self.minute_watcher_thread.join(timeout=60)
            if self.minute_watcher_thread.is_alive():
                logger.warning(
                    "Minute watcher thread did not stop in time, continuing shutdown anyway"
                )

        if self.sync_campaigns_thread is not None:
            self.sync_campaigns_thread.join(timeout=60)
            if self.sync_campaigns_thread.is_alive():
                logger.warning(
                    "Sync campaigns thread did not stop in time, continuing shutdown anyway"
                )

        if self.drop_badge_catalog_thread is not None:
            self.drop_badge_catalog_thread.join(timeout=30)

        # Check if all the mutex are unlocked.
        # Prevent breaks of .json file
        for streamer in self.streamers:
            if streamer.mutex.locked():
                streamer.mutex.acquire()
                streamer.mutex.release()

        self.__print_report()

        # Stop the queue listener to make sure all messages have been logged
        self.queue_listener.stop()

        sys.exit(0)

    def __print_report(self):
        print("\n")
        logger.info(
            f"Ending session: '{self.session_id}'", extra={"emoji": ":stop_sign:"}
        )
        if self.logs_file is not None:
            logger.info(
                f"Logs file: {self.logs_file}", extra={"emoji": ":page_facing_up:"}
            )
        logger.info(
            f"Duration {datetime.now() - self.start_datetime}",
            extra={"emoji": ":hourglass:"},
        )

        if not Settings.logger.less and self.events_predictions != {}:
            print("")
            for event_id in self.events_predictions:
                event = self.events_predictions[event_id]
                if (
                    event.bet_confirmed is True
                    and event.streamer.settings.make_predictions is True
                ):
                    logger.info(
                        f"{event.streamer.settings.bet}",
                        extra={"emoji": ":wrench:"},
                    )
                    if event.streamer.settings.bet.filter_condition is not None:
                        logger.info(
                            f"{event.streamer.settings.bet.filter_condition}",
                            extra={"emoji": ":pushpin:"},
                        )
                    logger.info(
                        f"{event.print_recap()}",
                        extra={"emoji": ":bar_chart:"},
                    )

        print("")
        for streamer_index in range(0, len(self.streamers)):
            if self.streamers[streamer_index].history != {}:
                gained = (
                    self.streamers[streamer_index].channel_points
                    - self.original_streamers[streamer_index]
                )

                from colorama import Fore

                streamer_highlight = Fore.YELLOW

                streamer_gain = (
                    f"{streamer_highlight}{self.streamers[streamer_index]}{Fore.RESET}, Total Points Gained: {_millify(gained)}"
                    if Settings.logger.less
                    else f"{streamer_highlight}{repr(self.streamers[streamer_index])}{Fore.RESET}, Total Points Gained (after farming - before farming): {_millify(gained)}"
                )

                indent = " " * 25
                streamer_history = "\n".join(
                    f"{indent}{history}"
                    for history in self.streamers[streamer_index]
                    .print_history()
                    .split("; ")
                )

                logger.info(
                    f"{streamer_gain}\n{streamer_history}",
                    extra={"emoji": ":moneybag:"},
                )

        drop_entries = _drop_progress_report_entries(
            self.original_drop_progress,
            self.twitch.drop_report_snapshot(),
        )
        if drop_entries:
            print("")
            reward_label = "reward" if len(drop_entries) == 1 else "rewards"
            logger.info(
                f"Drop progress gained this session "
                f"({len(drop_entries)} {reward_label}):",
                extra={"emoji": ":gift:"},
            )
            for entry in drop_entries:
                category = entry.get("category") or "Unknown category"
                campaign = entry.get("campaign") or "Unknown campaign"
                item_name = entry.get("item_name") or "Unknown reward"
                watched = entry.get("current_minutes_watched", 0) or 0
                required = entry.get("minutes_required", 0) or 0
                gained = entry.get("minutes_gained", 0) or 0
                status = str(entry.get("status") or "in_progress").replace("_", " ")
                logger.info(
                    f"{category} - {campaign} - {item_name}: "
                    f"{watched}/{required}m (+{gained}m), {status}",
                    extra={"emoji": ":hourglass_flowing_sand:"},
                )
