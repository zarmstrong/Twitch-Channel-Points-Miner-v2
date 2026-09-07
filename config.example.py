# -*- coding: utf-8 -*-
# Copy this template to config/config.py and review each setting before use.

CONFIG_VERSION = 8

import logging
from colorama import Fore
from TwitchChannelPointsMiner.logger import LoggerSettings, ColorPalette
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Email import Email
from TwitchChannelPointsMiner.classes.Webhook import Webhook
from TwitchChannelPointsMiner.classes.Telegram import Telegram
from TwitchChannelPointsMiner.classes.Matrix import Matrix
from TwitchChannelPointsMiner.classes.Pushover import Pushover
from TwitchChannelPointsMiner.classes.Gotify import Gotify
from TwitchChannelPointsMiner.classes.Ntfy import Ntfy
from TwitchChannelPointsMiner.classes.Settings import (
    Priority,
    Events,
    FollowersOrder,
    CategorySort,
    CategoryCampaignOrder,
    StreamerSource,
)
from TwitchChannelPointsMiner.classes.entities.Bet import Strategy, BetSettings, Condition, OutcomeKeys, FilterCondition, DelayMode
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings

MINER_CONFIG = {
    'username': "your-twitch-username",
    'claim_drops_startup': False,
    'priority': [                               # Custom priority in this case for example:
        Priority.STREAK,                        # - We want first of all to catch all watch streak from all streamers
        Priority.DROPS,                         # - When we don't have anymore watch streak to catch, wait until all drops are collected over the streamers
        Priority.ORDER,                         # - When we have all of the drops claimed and no watch-streak available, use the order priority (POINTS_ASCENDING, POINTS_DESCENDING)
        Priority.FAVORITE                       # - New priority options are appended last during config migration; move this earlier to favor marked streamers
    ],
    'enable_analytics': False,
    'disable_ssl_cert_verification': False,
    'disable_at_in_nickname': False,
    'update_check': True,                       # Check GitHub Releases for newer versions and alert through all configured notifiers
    'update_check_interval_hours': 24,          # Whole hours from 3 upward; use float("inf") to check only at startup
    'streams_watched': 2,                       # Watch 1 stream to reduce concurrent sessions (which may help Twitch Turbo users avoid ads), or 2 for the default maximum
    'streamer_source_priority': [
        StreamerSource.STREAMERS,
        StreamerSource.FOLLOWERS,
        StreamerSource.CATEGORIES,
        StreamerSource.BADGES,
        StreamerSource.WILDCARD_CATEGORIES,       # Fallback tier: only used once the 'categories' pass leaves capacity idle this cycle. Reorder to change its priority (e.g. move above BADGES).
    ],
    'logger_settings': LoggerSettings(
        save=True,                              # If you want to save logs in a file (suggested)
        console_level=logging.INFO,             # Level of logs - use logging.DEBUG for more info
        console_username=False,                 # Adds a username to every console log line if True. Also adds it to Telegram, Discord, etc. Useful when you have several accounts
        auto_clear=True,                        # Rotate the active log file every night at local midnight (default)
        log_retention_days=7,                   # Number of daily log archives to keep before deleting the oldest
        time_zone="",                           # Set a specific time zone for console and file loggers. Use tz database names. Example: "America/Denver"
        date_format="dd/mm/yy",                 # Date format in logs and analytics. Supported tokens: dd, mm, yy, yyyy
        file_level=logging.INFO,                # Level of logs - If you think the log file it's too big, use logging.INFO
        emoji=True,                             # On Windows, we have a problem printing emoji. Set to false if you have a problem
        less=False,                             # If you think that the logs are too verbose, set this to True
        colored=True,                           # If you want to print colored text
        daily_report=True,                      # Send a daily point/Drop activity summary through notifiers subscribed to DAILY_REPORT
        daily_report_time="00:00",              # Local time in 24-hour HH:MM format
        color_palette=ColorPalette(             # You can also create a custom palette color (for the common message).
            STREAMER_online="GREEN",            # Don't worry about lower/upper case. The script will parse all the values.
            streamer_offline="red",             # Read more in README.md
            BET_WIN=Fore.MAGENTA                # Color allowed are: [BLACK, RED, GREEN, YELLOW, BLUE, MAGENTA, CYAN, WHITE, RESET].
        ),
        telegram=Telegram(                                                          # You can omit or set to None if you don't want to receive updates on Telegram
            chat_id=123456789,                                                      # Chat ID to send messages @getmyid_bot
            token="123456789:shfuihreuifheuifhiu34578347",                          # Telegram API token @BotFather
            events=[Events.STREAMER_ONLINE, Events.STREAMER_OFFLINE,
                    Events.BET_LOSE, Events.CHAT_MENTION],                          # Only these events will be sent to the chat
            disable_notification=True,                                              # Revoke the notification (sound/vibration)
            message_thread_id=None,                                                 # Optional forum topic ID for supergroup topics
        ),
        discord=Discord(
            webhook_api="https://discord.com/api/webhooks/0123456789/0a1B2c3D4e5F6g7H8i9J",  # Discord Webhook URL
            events=[Events.STREAMER_ONLINE, Events.STREAMER_OFFLINE,
                    Events.BET_LOSE, Events.CHAT_MENTION],                                  # Only these events will be sent to the chat
        ),
        webhook=Webhook(
            endpoint="https://example.com/webhook",                                                                    # Webhook URL
            method="GET",                                                                   # GET or POST
            events=[Events.STREAMER_ONLINE, Events.STREAMER_OFFLINE,
                    Events.BET_LOSE, Events.CHAT_MENTION],                                  # Only these events will be sent to the endpoint
        ),
        email=Email(                                                                    # SMTP email alerts; omit or set to None to disable
            host="smtp.example.com",
            port=587,
            username="miner@example.com",
            password="YOUR_SMTP_PASSWORD",
            sender="miner@example.com",
            recipients=["you@example.com"],
            events=[Events.DAILY_REPORT, Events.CONFIGURATION],
            starttls=True,                                                              # Use use_ssl=True and starttls=False for implicit TLS (usually port 465)
        ),
        matrix=Matrix(
            username="twitch_miner",                                                   # Matrix username (without homeserver)
            password="...",                                                            # Matrix password
            homeserver="matrix.org",                                                   # Matrix homeserver
            room_id="...",                                                             # Room ID
            events=[Events.STREAMER_ONLINE, Events.STREAMER_OFFLINE, Events.BET_LOSE], # Only these events will be sent
        ),
        pushover=Pushover(
            userkey="YOUR-ACCOUNT-TOKEN",                                             # Login to https://pushover.net/, the user token is on the main page
            token="YOUR-APPLICATION-TOKEN",                                           # Create a application on the website, and use the token shown in your application
            priority=0,                                                               # Read more about priority here: https://pushover.net/api#priority
            sound="pushover",                                                         # A list of sounds can be found here: https://pushover.net/api#sounds
            events=[Events.CHAT_MENTION, Events.DROP_CLAIM],                          # Only these events will be sent
        ),
        gotify=Gotify(
            endpoint="https://example.com/message?token=TOKEN",
            priority=8,
            events=[Events.STREAMER_ONLINE, Events.STREAMER_OFFLINE,
                    Events.BET_LOSE, Events.CHAT_MENTION],
        ),
        ntfy=Ntfy(
            topic="YOUR_NTFY_TOPIC",                                                # Treat an unprotected topic name like a password
            server_url="https://ntfy.sh",                                           # Replace with your self-hosted ntfy server if applicable
            token=None,                                                              # Optional access token for protected topics
            priority=3,                                                              # Optional ntfy priority from 1 (min) to 5 (max)
            tags=["twitch"],                                                         # Optional ntfy tags or emoji short names
            events=[Events.STREAMER_ONLINE, Events.DROP_CLAIM],
        )
    ),
    'streamer_settings': StreamerSettings(
        make_predictions=True,                  # If you want to Bet / Make prediction
        follow_raid=True,                       # Follow raid to obtain more points
        claim_drops=True,                       # We can't filter rewards base on stream. Set to False for skip viewing counter increase and you will never obtain a drop reward from this script. Issue #21
        claim_moments=True,                     # If set to True, https://help.twitch.tv/s/article/moments will be claimed when available
        watch_streak=True,                      # If a streamer go online change the priority of streamers array and catch the watch screak. Issue #11
        favorite=False,                         # Prioritize this streamer when Priority.FAVORITE is configured
        points_limit=None,                      # Stop watching after reaching this balance; pending watch streaks still take priority
        community_goals=False,                  # If True, contributes the max channel points per stream to the streamers' community challenge goals
        chat=ChatPresence.ONLINE,               # Join irc chat to increase watch-time [ALWAYS, NEVER, ONLINE, OFFLINE]
        bet=BetSettings(
            strategy=Strategy.SMART,            # Choose you strategy!
            percentage=5,                       # Place the x% of your channel points
            percentage_gap=20,                  # Gap difference between outcomesA and outcomesB (for SMART strategy)
            max_points=50000,                   # If the x percentage of your channel points is gt bet_max_points set this value
            stealth_mode=True,                  # If the calculated amount of channel points is GT the highest bet, place the highest value minus 1-2 points Issue #33
            delay_mode=DelayMode.FROM_END,      # When placing a bet, we will wait until `delay` seconds before the end of the timer
            delay=6,
            minimum_points=20000,               # Place the bet only if we have at least 20k points. Issue #113
            filter_condition=FilterCondition(
                by=OutcomeKeys.TOTAL_USERS,     # Where apply the filter. Allowed [PERCENTAGE_USERS, ODDS_PERCENTAGE, ODDS, TOP_POINTS, TOTAL_USERS, TOTAL_POINTS]
                where=Condition.LTE,            # 'by' must be [GT, LT, GTE, LTE] than value
                value=800
            )
        )
    ),
}

STREAMERS = [
        Streamer(
            "masterslate",  # Watching my channel through the miner isn't needed, but is greatly appreciated if you'd like to support me.
            settings=StreamerSettings(
                make_predictions=False,
                follow_raid=False,
                claim_drops=True,
                watch_streak=True,
                community_goals=False,
                bet=None,
            ),
        ),
        Streamer("streamer-username01", settings=StreamerSettings(make_predictions=True  , follow_raid=False , claim_drops=True  , watch_streak=True , community_goals=False , bet=BetSettings(strategy=Strategy.SMART      , percentage=5 , stealth_mode=True,  percentage_gap=20 , max_points=234   , filter_condition=FilterCondition(by=OutcomeKeys.TOTAL_USERS,      where=Condition.LTE, value=800 ) ) )),
        Streamer("streamer-username02", settings=StreamerSettings(make_predictions=False , follow_raid=True  , claim_drops=False ,                                             bet=BetSettings(strategy=Strategy.PERCENTAGE , percentage=5 , stealth_mode=False, percentage_gap=20 , max_points=1234  , filter_condition=FilterCondition(by=OutcomeKeys.TOTAL_POINTS,     where=Condition.GTE, value=250 ) ) )),
        Streamer("streamer-username03", settings=StreamerSettings(make_predictions=True  , follow_raid=False ,                     watch_streak=True , community_goals=True  , bet=BetSettings(strategy=Strategy.SMART      , percentage=5 , stealth_mode=False, percentage_gap=30 , max_points=50000 , filter_condition=FilterCondition(by=OutcomeKeys.ODDS,             where=Condition.LT,  value=300 ) ) )),
        Streamer("streamer-username04", settings=StreamerSettings(make_predictions=False , follow_raid=True  ,                     watch_streak=True ,                                                                                                                                                                                                                                                       )),
        Streamer("streamer-username05", settings=StreamerSettings(make_predictions=True  , follow_raid=True  , claim_drops=True ,  watch_streak=True , community_goals=True  , bet=BetSettings(strategy=Strategy.HIGH_ODDS  , percentage=7 , stealth_mode=True,  percentage_gap=20 , max_points=90    , filter_condition=FilterCondition(by=OutcomeKeys.PERCENTAGE_USERS, where=Condition.GTE, value=300 ) ) )),
        Streamer("streamer-username06"),
        Streamer("streamer-username07"),
        Streamer("streamer-username08"),
        "streamer-username09",
        "streamer-username10",
        "streamer-username11"
    ]

MINE_CONFIG = {
    'followers': False,
    'followers_order': FollowersOrder.ASC,
    'categories': [
        "rust",
        "gray-zone-warfare",
        "diablo-iv",
        "arc-raiders",
        "the-elder-scrolls-online",
        "hitman-world-of-assassination",
        "palworld",
        "warframe",
        # Force a specific live streamer for a category (also accepts "[category]|[streamer]"):
        # "gray-zone-warfare|streamer-username01",
        # You can also pass the full Twitch URL:
        # "https://www.twitch.tv/directory/category/gray-zone-warfare?filter=drops"
    ],
    'category_drops_enabled': True,
    'category_limit': 5,
    'category_sort': CategorySort.VIEWERS_DESC,
    'category_campaign_order': CategoryCampaignOrder.EXPIRATION,
    'category_chat': ChatPresence.NEVER,
    'category_log_level': logging.INFO,
    'drop_item_art': True,
    'print_open_drop_campaigns_on_load': True,
    'scrape_drop_progress_on_load': True,
    'log_drop_checks': True,
    'category_refresh_interval_hours': 3,
    'drop_progress_stall_minutes': 10,
    'drop_pick_stickiness_minutes': 15,
    'drop_badge_catalog': True,
    'drop_badge_refresh_interval_hours': 1,
    'auto_mine_badge_drops': False,
    'badge_drop_streamer_limit': 1,
    'wildcard_categories': False,               # Once the 'categories' pass leaves capacity idle this cycle (no eligible category, or an eligible one with no live channel), fall back to every other category with an active incomplete drop campaign. Twitch campaign data is supplemented by the external Drops catalog; external-only candidates must still be confirmed on a live Twitch channel. Opt-in; see StreamerSource.WILDCARD_CATEGORIES in streamer_source_priority above for where it ranks (lowest by default).
    'wildcard_category_limit': 10,              # Cap on distinct wildcard categories added per refresh cycle. With pinning on (below), already-tracked wildcard categories don't count against this cap, so the effective tracked total can exceed it -- that's expected, not a bug.
    'wildcard_category_streamer_limit': 1,      # Streamers pulled per wildcard category (kept small: this is best-effort filler, not a curated list).
    'wildcard_category_pin_active': True,       # Keep an already-tracked wildcard category even if it falls out of the top wildcard_category_limit on a re-sort, as long as its campaign is still active. Prevents needless streamer churn between refresh cycles.
}

# Leave disabled unless MINER_CONFIG['enable_analytics'] is also set to True.
ANALYTICS_CONFIG = None

# ANALYTICS_CONFIG = {
#     'host': "127.0.0.1",                     # Use 0.0.0.0 only on a trusted network and set a strong password
#     'port': 5000,
#     'refresh': 5,                             # Chart refresh interval in minutes
#     'days_ago': 7,                            # Initial chart history range
#     'password': None,                         # Required when binding to a non-loopback host
#     'log_poll_interval': 5,                   # Log viewer polling interval in seconds (1-180)
# }
