![Twitch Channel Points Miner](https://raw.githubusercontent.com/zarmstrong/Twitch-Channel-Points-Miner-v3/master/assets/banner.png)
<p align="center">
<a href="https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/releases"><img alt="Latest Version" src="https://badgen.net/github/release/zarmstrong/Twitch-Channel-Points-Miner-v3?icon=github&iconColor=fff&color=white"></a>
<a href="https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/stargazers"><img alt="GitHub Repo stars" src="https://badgen.net/github/stars/zarmstrong/Twitch-Channel-Points-Miner-v3?icon=github&iconColor=fff&color=32CD32"></a>
<a href="https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/blob/master/LICENSE"><img alt="License" src="https://img.shields.io/github/license/zarmstrong/Twitch-Channel-Points-Miner-v3?style=flat&color=black&logo=unlicense&logoColor=white"></a>
<a href="https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3"><img alt="GitHub last commit" src="https://badgen.net/github/last-commit/zarmstrong/Twitch-Channel-Points-Miner-v3?icon=github&iconColor=fff&color=FFFFE0"></a>
</p>

<p align="center">
<a href="https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner"><img alt="Docker Version" src="https://img.shields.io/docker/v/zacharmstrong/twitch-channel-points-miner?style=flat&color=white&logo=docker&logoColor=white&label=release"></a>
<a href="https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner"><img alt="Docker Stars" src="https://img.shields.io/docker/stars/zacharmstrong/twitch-channel-points-miner?style=flat&color=limegreen&logo=docker&logoColor=white&label=stars"></a>
<a href="https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner"><img alt="Docker Pulls" src="https://img.shields.io/docker/pulls/zacharmstrong/twitch-channel-points-miner?style=flat&color=blue&logo=docker&logoColor=white&label=pulls"></a>
<a href="https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner"><img alt="Docker Images Size AMD64" src="https://img.shields.io/docker/image-size/zacharmstrong/twitch-channel-points-miner/latest?arch=amd64&amp;label=AMD64%20image%20size&amp;style=flat&amp;color=purple&amp;logo=amd&amp;logoColor=white"></a>
<a href="https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner"><img alt="Docker Images Size ARM64" src="https://img.shields.io/docker/image-size/zacharmstrong/twitch-channel-points-miner/latest?arch=arm64&amp;label=ARM64%20image%20size&amp;style=flat&amp;color=black&amp;logo=arm&amp;logoColor=white"></a>
</p>


<h1 align="center">https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3</h1>

**Credits**
- Main idea: https://github.com/gottagofaster236/Twitch-Channel-Points-Miner
- ~~Bet system (Selenium): https://github.com/ClementRoyer/TwitchAutoCollect-AutoBet~~
- Based on: https://github.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2
- Previous maintainer: [Roman Davydov](https://github.com/rdavydov), whose [v2 fork](https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2) this project builds upon

> A simple script that will watch a stream for you and earn the channel points.

> It can wait for a streamer to go live (+_450 points_ when the stream starts), it will automatically click the bonus button (_+50 points_), and it will follow raids (_+250 points_).

Read more about the channel points [here](https://help.twitch.tv/s/article/channel-points-guide).

# README Contents

1. 🤝 [Community](#community)
2. 🚀 [Features](#features)
3. 🧐 [How to use](#how-to-use)
    - [Install and start](#install-and-start)
        - [Docker quick start](#docker-quick-start)
            - [Docker Compose](#docker-compose)
            - [Docker CLI](#docker-cli)
        - [Windows quick start](#windows-quick-start)
        - [Source checkout quick start](#source-checkout-quick-start)
    - [Configuration file](#configuration-file)
    - [Configuration sections](#configuration-sections)
        - [MINER_CONFIG](#miner_config)
        - [STREAMERS](#streamers)
        - [MINE_CONFIG](#mine_config)
        - [Category-based Drops](#category-based-drops)
            - [How category discovery works](#how-category-discovery-works)
            - [Category configuration reference](#category-configuration-reference)
            - [Category input formats](#category-input-formats)
            - [Channel selection and sorting](#channel-selection-and-sorting)
            - [Campaign filtering and ordering](#campaign-filtering-and-ordering)
            - [Refresh behavior](#refresh-behavior)
            - [Wildcard category fallback](#wildcard-category-fallback)
            - [Category logging and analytics](#category-logging-and-analytics)
            - [Category examples](#category-examples)
            - [Category troubleshooting](#category-troubleshooting)
        - [ANALYTICS_CONFIG](#analytics_config)
    - [Runner behavior](#runner-behavior)
        - [Configuration reload limitations](#configuration-reload-limitations)
    - [Docker](#docker)
        - [Docker Hub](#docker-hub)
        - [Graceful Docker shutdowns](#graceful-docker-shutdowns)
        - [Portainer](#portainer)
    - [Replit](#replit)
    - [Limits](#limits)
4. 🔧 [Settings](#settings)
    - [LoggerSettings](#loggersettings)
        - [Color Palette](#color-palette)
        - [Notifications](#notifications)
            - [Telegram](#telegram)
            - [Discord](#discord)
            - [Generic Webhook](#generic-webhook)
            - [ntfy](#ntfy)
            - [Matrix](#matrix)
            - [Pushover](#pushover)
            - [Gotify](#gotify)
            - [Email / SMTP](#email--smtp)
        - [Events](#events)
    - [StreamerSettings](#streamersettings)
    - [BetSettings](#betsettings)
        - [Bet strategy](#bet-strategy)
        - [FilterCondition](#filtercondition)
            - [Example](#example)
        - [DelayMode](#delaymode)
5. 📈 [Analytics](#analytics)
    - [Analytics security and HTTPS reverse proxy](#analytics-security-and-https-reverse-proxy)
    - [Enabling analytics storage](#enabling-analytics-storage)
6. 🧾 [Logs](#logs)
    - [Full format](#full-format)
    - [Compact format](#compact-format)
    - [Graceful-shutdown report](#graceful-shutdown-report)
7. 🔄 [Migrating from run.py](#migrating-from-runpy)
    - [Automatic Docker migration](#automatic-docker-migration)
    - [What converts automatically](#what-converts-automatically)
8. 🍪 [Legacy cookie migration (optional)](#legacy-cookie-migration-optional)
9. 🪟 [Windows](#windows)
    - [Install the Windows executable](#install-the-windows-executable)
    - [Set up your account](#set-up-your-account)
    - [Start the miner](#start-the-miner)
    - [Update the miner](#update-the-miner)
    - [Windows troubleshooting](#windows-troubleshooting)
10. 📱 [Termux](#termux)
11. ⚠️ [Disclaimer](#disclaimer)

## Community
If you want to help with this project, please leave a star 🌟 and share it with your friends! 😎

If you want to support development, you can donate through Ko-fi or cryptocurrency:

[![Support me on Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/B0B61NBJ0)

| Currency | Wallet address |
| --- | --- |
| Bitcoin (BTC) | `bc1qdpg70u7u6fganwtddf9z3uwugkxkpltre4cva3` |
| Ethereum (ETH) | `0x926fce1Eb65e867Af751a9cB75394DF001b58DF0` |
| Dogecoin (DOGE) | `DHo26McxeVLFwQmRXdUH7LyHsdEUoVFvga` |

If you have any issues or you want to contribute, you are welcome! But please read the [CONTRIBUTING.md](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/blob/master/CONTRIBUTING.md) file. Contributors working on code can find setup, test commands, and suite conventions in the [testing guide](tests/README.md).

## Features

- Automatically watches eligible channels and claims channel-point bonuses.
- Waits for configured streamers to go live and follows eligible raids.
- Prioritizes channels by watch streaks, Drops, subscriptions, configured order,
  or channel-point balance.
- Discovers live channels from configured game categories and active Drop
  campaigns.
- Tracks Drop progress and claims available Drop rewards and Moments.
- Participates in channel-point predictions with configurable strategies,
  spending limits, filters, and timing.
- Supports per-streamer settings, follower imports, and blacklists.
- Can join IRC chat and notify you when your username is mentioned.
- Sends selected events through Telegram, Discord, Matrix, Pushover, Gotify,
  email, or a generic webhook.
- Provides colorized console logs, rotating log files, compact logging, and a
  graceful-shutdown report.
- Includes an optional analytics server for point history, Drops, and log viewing.
- Supports persistent Docker configuration with automatic legacy-runner
  conversion and limited live configuration reloads.

## How to use

Choose one of the three supported paths below. Each quick start takes you from
installation through the first launch without requiring another section.

### Install and start

#### Docker quick start

Docker is the best fit for servers, NAS devices, and unattended operation.
Choose either Docker Compose or the plain Docker CLI. Both examples persist the
configuration, authentication cookies, logs, and analytics in the current
directory.

##### Docker Compose

Create an empty working directory containing this `compose.yml`:

```yaml
services:
  miner:
    image: zacharmstrong/twitch-channel-points-miner:latest
    restart: "no"
    stop_grace_period: 60s
    stdin_open: true
    tty: true
    environment:
      - TERM=xterm-256color
      - TZ=America/Denver
    volumes:
      - ./analytics:/usr/src/app/analytics
      - ./cookies:/usr/src/app/cookies
      - ./logs:/usr/src/app/logs
      - ./config:/usr/src/app/config
    ports:
      - "5000:5000"
```

Run `docker compose up`. On the first run, the container creates
`config/config.py` and exits. Edit that file, replace the example account and
streamers, disable unused notification providers, and change `restart: "no"` to
`restart: unless-stopped`. Run `docker compose up` interactively to complete the
first Twitch sign-in, then use `docker compose up -d` for later starts.

##### Docker CLI

Create the persistent directories, then run a temporary container once to seed
the configuration:

```sh
mkdir -p analytics cookies logs config
docker run --rm -it \
    -e TERM=xterm-256color \
    -e TZ=America/Denver \
    -v "$(pwd)/analytics:/usr/src/app/analytics" \
    -v "$(pwd)/cookies:/usr/src/app/cookies" \
    -v "$(pwd)/logs:/usr/src/app/logs" \
    -v "$(pwd)/config:/usr/src/app/config" \
    -p 5000:5000 \
    zacharmstrong/twitch-channel-points-miner:latest
```

The container creates `config/config.py` and exits. Edit that file, replace the
example account and streamers, and disable unused notification providers. Then
create the persistent miner container and complete Twitch authentication in its
interactive terminal:

```sh
docker run --name twitch-miner -it \
    --restart unless-stopped \
    --stop-timeout 60 \
    -e TERM=xterm-256color \
    -e TZ=America/Denver \
    -v "$(pwd)/analytics:/usr/src/app/analytics" \
    -v "$(pwd)/cookies:/usr/src/app/cookies" \
    -v "$(pwd)/logs:/usr/src/app/logs" \
    -v "$(pwd)/config:/usr/src/app/config" \
    -p 5000:5000 \
    zacharmstrong/twitch-channel-points-miner:latest
```

After the initial login, manage the existing container with
`docker stop twitch-miner` and `docker start twitch-miner`; do not run the
creation command again. On Windows, replace each `$(pwd)` expression with an
absolute host path, such as `C:\TwitchMiner\config`.

The later [Docker reference](#docker) covers multiple accounts, Portainer, time
zones, and graceful shutdown behavior in more detail.

#### Windows quick start

The Windows executable does not require Python or Git.

1. Download `TwitchChannelPointsMiner-<version>.zip` from the official
   [Releases page](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/releases).
   You can also download `TwitchChannelPointsMiner-<version>-Setup.exe` if you
   prefer guided installation; the steps below describe the ZIP workflow.
2. Extract the whole archive to a permanent private folder; do not run it from
   inside the ZIP.
3. Run `TwitchChannelPointsMiner.exe` once. It creates `config\config.py` and
   waits for you to press Enter before closing.
4. Open `config\config.py` in a text editor, replace the example account and
   streamers, and disable unused notification providers.
5. Run the executable again and follow the Twitch sign-in instructions.

Keep the console open while mining. Stop with `Ctrl+C`. The later
[Windows reference](#windows) covers updates and troubleshooting.

#### Source checkout quick start

Use a source checkout when you want to inspect, develop, or modify the project.
Python 3.11 through 3.14 is supported.

```sh
git clone https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3
cd Twitch-Channel-Points-Miner-v3
python3 -m venv venv
source venv/bin/activate  # Windows PowerShell: .\venv\Scripts\Activate.ps1
pip install -r requirements.txt
mkdir -p config
cp config.example.py config/config.py
```

Edit `config/config.py`, then start the stable runner:

```sh
python -m TwitchChannelPointsMiner.runner --config-dir ./config
```

For every method, keep the populated configuration private: never commit or
share account names, passwords, webhook URLs, or notification credentials.
Portainer is a Docker deployment. The [Termux](#termux) instructions are
outdated and unsupported, and there is no maintained [Replit](#replit)
deployment.

### Configuration file

All run methods use `config/config.py`. Docker and source-checkout users edit the
file created or copied during their quick start; the Windows executable creates
the same file beside the executable.

A minimal configuration looks like this:

```python
from TwitchChannelPointsMiner.classes.Settings import FollowersOrder

MINER_CONFIG = {
    "username": "your-twitch-username",
    "enable_analytics": False,
}

STREAMERS = ["streamer1", "streamer2"]

MINE_CONFIG = {
    "followers": False,
    "followers_order": FollowersOrder.ASC,
    "blacklist": [],
    "categories": [],
}

# Set to a dictionary to start the analytics server, or leave as None.
ANALYTICS_CONFIG = None
```

The file is normal Python, so import any enums or settings classes used by its
values. See [config.example.py](config.example.py) for the complete annotated
configuration, including logger integrations, per-streamer settings, predictions,
categories, and analytics.

### Configuration sections

| Name | Passed to | Purpose |
|---|---|---|
| `MINER_CONFIG` | `TwitchChannelPointsMiner(...)` | Account, priority, default streamer behavior, logging, notifications, and global runtime options. |
| `STREAMERS` | First argument of `mine(...)` | Ordered streamer logins or `Streamer` objects with per-channel overrides. Use `[]` when relying only on followers or categories. |
| `MINE_CONFIG` | Keyword arguments of `mine(...)` | Followers, blacklist, category discovery, Drops diagnostics, and other mining-session options. Do not include `streamers` here. |
| `ANALYTICS_CONFIG` | `analytics(...)` | Analytics server options. Set to `None` to leave the server disabled. |

#### MINER_CONFIG

`MINER_CONFIG` contains account-wide and constructor settings. At minimum, set
`username`. This is also where you configure mining priority, analytics storage,
SSL behavior, logging and notification integrations,
the number of concurrent streams watched, and the default `StreamerSettings`
inherited by channels without overrides. `streams_watched` accepts `1` or `2`
and defaults to `2`. This option allows Twitch Turbo users to limit the miner to
one concurrent viewing session: reports in issue #787 indicate that continuously
mining two channels alongside normal Twitch viewing may contribute to Turbo users
being served ads. Invalid values are logged and replaced with `2`.

`streamer_source_priority` controls which source gets the available viewing
slots first. Its default is configured streamers, followed streamers, ordinary
category discovery, automatic badge campaigns, then the wildcard category
fallback:

```python
streamer_source_priority=[
    StreamerSource.STREAMERS,
    StreamerSource.FOLLOWERS,
    StreamerSource.CATEGORIES,
    StreamerSource.BADGES,
    StreamerSource.WILDCARD_CATEGORIES,
]
```

Move `StreamerSource.BADGES` to the front to prioritize earning badge Drops.
The `priority` rules are applied globally; source priority breaks ties within
each rule. For example, `Priority.DROPS` can reserve a slot for an active
category campaign before `Priority.ORDER` fills remaining slots with configured
streamers.

`StreamerSource.WILDCARD_CATEGORIES` is only ever populated once the
preferred `categories` pass leaves capacity idle for the cycle (see
[Wildcard category fallback](#wildcard-category-fallback)) -- reordering it
only changes how it competes for a watch slot against `CATEGORIES`/`BADGES`
*when it has something to offer*, it does not make it activate any earlier.
It defaults to last so it never silently outranks a curated `categories` list
or `auto_mine_badge_drops`; move it earlier only if you deliberately want
wildcard-discovered streams to win watch slots over those sources when both
are eligible at once.

The web config dashboard's "Stream sources" list mirrors this setting: each
row is a source with an on/off checkbox, and rows can be dragged into the
order you want `streamer_source_priority` to end up in. Saving writes both
the enabled sources and their order back to `config.py` in one step.

See [Settings](#settings) for the available priority, logger, streamer, and bet
objects. Keep credentials and integration tokens only in your private
`config/config.py`.

#### STREAMERS

`STREAMERS` is the ordered list of fixed channels. Each entry can be a lowercase
login string or a `Streamer(...)` object when that channel needs custom
`StreamerSettings`. Use an empty list when mining exclusively from followed
channels or category discovery.

List order is a real priority control, not just a display preference:
`Priority.ORDER`'s tie-break and the final watch-slot selection both key off
each streamer's position in this list, so an earlier entry wins a contested
watch slot over a later one when nothing else distinguishes them. The web
config dashboard's "Configured streamers" list can be reordered by dragging
rows (via the handle icon); that writes straight back to this list's order in
`config.py`.

Streamer settings follow this precedence: settings on an individual `Streamer`,
then the default `streamer_settings` in `MINER_CONFIG`, then project defaults.
Plain strings in `STREAMERS` use the configured defaults.

Common streamer-list patterns:

```python
# Fixed list
STREAMERS = ["streamer1", "streamer2"]
MINE_CONFIG = {"followers": False, "categories": []}

# All followed channels
STREAMERS = []
MINE_CONFIG = {"followers": True, "followers_order": FollowersOrder.ASC}

# Fixed list plus followers, excluding selected users
STREAMERS = ["streamer1"]
MINE_CONFIG = {
    "followers": True,
    "followers_order": FollowersOrder.DESC,
    "blacklist": ["user1", "user2"],
}
```

#### MINE_CONFIG

`MINE_CONFIG` contains the keyword arguments for the mining session. Use it to
combine followed channels with `STREAMERS`, apply a blacklist, configure follower
ordering, discover channels by category, and control category/Drop diagnostics.
The streamer list itself belongs in `STREAMERS`, not in this dictionary.

#### Category-based Drops

Use `categories` to mine Drops for games without maintaining a fixed list of
streamer usernames. The miner finds configured games with active, incomplete
Drop campaigns, discovers eligible live channels, and adds those channels to the
normal mining session. Categories can be used by themselves or together with
`STREAMERS` and `MINE_CONFIG["followers"] = True`.

Import the category enums (and `ChatPresence` if you want a category-specific
chat policy) before using the examples below:

```python
import logging

from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Settings import (
    CategoryCampaignOrder,
    CategorySort,
)

MINE_CONFIG = {
    "categories": [
        "pokemon-go",
        "warframe",
    ],
    "category_limit": 5,
    "category_sort": CategorySort.VIEWERS_DESC,
    "category_campaign_order": CategoryCampaignOrder.EXPIRATION,
    "category_chat": ChatPresence.NEVER,
    "category_log_level": logging.INFO,
    "category_refresh_interval_hours": 6,
}
```

##### How category discovery works

At startup, the miner:

1. Reads the configured category values and checks current Drop campaigns.
2. Keeps categories that have an active campaign with an incomplete, eligible
   time-based Drop.
3. Resolves each category to its Twitch game and searches for live channels.
4. Applies the Drops tag, campaign-channel, limit, and sorting rules described
   below.
5. Loads the discovered users as category streamers and subscribes to the same
   relevant Twitch events used for explicitly configured streamers.

If no configured category has an active incomplete campaign, category discovery
is skipped. Explicit `streamers` and downloaded followers are unaffected.

Discovered category channels use the miner's default `StreamerSettings`, except
that `category_chat` can override their chat presence. The blacklist also applies
to discovered channels. If a username is both explicitly configured and found by
category discovery, it remains explicitly configured and keeps its explicit
settings.

##### Category configuration reference

| Option | Default | Description |
|---|---:|---|
| `categories` | `[]` | Category names, slugs, URLs, or category/streamer selectors to discover. |
| `category_drops_enabled` | `True` | Require the live channel to have a `DropsEnabled` tag. This also affects campaign eligibility checks. |
| `category_limit` | `30` | Maximum channels returned by a normal category search. Values below 1 behave as 1. Restricted campaign discovery may select up to 20 live channels per campaign instead. |
| `category_sort` | `CategorySort.VIEWERS_DESC` | Determines how normal category search results are selected and ordered. Enum values and equivalent strings are accepted. |
| `category_campaign_order` | `CategoryCampaignOrder.ORDER` | Preserves configured category order or prioritizes the nearest viable campaign deadline. |
| `category_chat` | `None` | Chat policy for category-discovered streamers. `None` inherits the default streamer setting. |
| `category_log_level` | `logging.INFO` | Severity used for category discovery and refresh messages. |
| `category_refresh_interval_hours` | `6` | Hours between campaign/channel refreshes. Positive values have a 30-minute minimum; `0` disables refresh. |
| `drop_progress_stall_minutes` | `10` | Rotate to another eligible category channel when authenticated Drop progress remains unchanged for this many minutes. The minimum is `5`; `0` disables stall detection. |
| `drop_badge_catalog` | `True` | On startup, classify rewards from the shared Drops gist against the shared Twitch badge catalog and persist the result in the config directory. |
| `drop_badge_refresh_interval_hours` | `1` | Hours between checks for new campaigns. Positive values have a one-hour minimum; `0` keeps the startup check but disables periodic checks. |
| `auto_mine_badge_drops` | `False` | Automatically add live channels for active, unearned watch-time badge campaigns. |
| `badge_drop_streamer_limit` | `1` | Live candidates added for each eligible all-channel badge campaign. Accepts `1` or `2`; restricted campaigns use their listed channels. |
| `track_category_streamer_points` | `False` | Include point earn/spend events from category-only streamers in balance tracking and analytics. Explicit streamers are always tracked. |
| `drop_item_art` | `False` | Store and display Drop item artwork URLs in Drops analytics. |
| `print_open_drop_campaigns_on_load` | `False` | Log all open Drop campaigns during startup. Useful for checking category names and campaign dates. |
| `scrape_drop_progress_on_load` | `False` | Read inventory Drop progress immediately at startup. Required for accurate per-session Drop progress in the graceful-shutdown report. |
| `log_drop_checks` | `False` | Enable verbose Drop GraphQL/inventory diagnostics. Leave disabled unless troubleshooting. |

##### Category input formats

The preferred input is the lowercase slug from a Twitch directory URL:

```python
categories=[
    "pokemon-go",  # Preferred Twitch slug
    "Pokémon GO",  # Twitch display name
    "https://www.twitch.tv/directory/category/pokemon-go?filter=drops",
    "https://twitchdrops.app/game/pokemon-go",
]
```

Category matching is case- and accent-insensitive, so `Pokémon GO`, `Pokemon GO`,
and `pokemon-go` resolve to the same category. A Twitch URL is useful when the
display name is ambiguous. A twitchdrops.app game URL is useful when its slug
differs from Twitch's slug.

To require one particular live streamer, append its login after a pipe (`|`):

```python
categories=[
    "Call of Duty: Warzone|streamer-name",
    "[Pokémon GO]|[@another-streamer]",
]
```

The optional brackets and leading `@` are removed. The pipe is the separator so
punctuation in names such as `Call of Duty: Warzone` remains safe. The forced
streamer is accepted only when they are live in the requested category and, when
`category_drops_enabled=True`, have the `DropsEnabled` tag. A forced selector is
not supported on a URL; use the category name or slug form.

##### Channel selection and sorting

`category_sort` accepts a `CategorySort` member or its string value:

| Value | Selection behavior |
|---|---|
| `ORDER` | Keep Twitch's API order. |
| `VIEWERS_DESC` | Highest viewer count first. This is the default. |
| `VIEWERS_ASC` | Lowest viewer count first. |
| `STARTED_AT_DESC` | Most recently started streams first. |
| `STARTED_AT_ASC` | Longest-running streams first. |
| `RANDOM` | Shuffle the eligible candidates. |

For `ORDER` and `VIEWERS_DESC`, the miner requests enough eligible results to
fill `category_limit`. Other sorts inspect a wider candidate window—up to three
times the limit, capped at 300—before selecting the final results. An invalid
sort value falls back to `VIEWERS_DESC`.

Twitch restricts effective watching capacity; adding many discovered channels
does not mean all of them accrue progress simultaneously. Use a modest
`category_limit`, campaign ordering, and the project's normal priority settings
to keep the active set useful. See [Limits](#limits).

##### Campaign filtering and ordering

With `category_drops_enabled=True`, the miner requires a Drops-enabled live
channel and filters categories using eligible Drop campaign information. Set it
to `False` only when you intentionally want live channels from a category even
without the Drops tag.

Campaigns that Twitch reports as completed are excluded from active category
discovery. For badge rewards, the miner also checks the authenticated account's
full available badge inventory. This prevents an already-earned badge campaign
from keeping a category active when the normal Drops inventory omits that
reward.

`CategoryCampaignOrder.ORDER` preserves the order in `categories`.
`CategoryCampaignOrder.EXPIRATION` prioritizes categories whose viable campaigns
expire soonest, helping time-sensitive Drops get selected first. With `ORDER`
selected, the web config dashboard's "Configured categories" list can be
reordered by dragging rows, same as `STREAMERS`.

If Twitch's campaign sources do not list any campaign for a configured game, the
miner uses the shared `twitch-drops.json` gist as a third-priority fallback.
Restricted campaign channel lists are checked in full for live users
while the campaign is active. Shared logins eligible for multiple active
campaigns are checked first. The miner selects up to 20 live channels per
restricted campaign and checks remaining channel lists in standby batches only
when a campaign has not reached that target.

##### Refresh behavior

The miner periodically rechecks configured categories for new campaigns and live
replacement streamers. `category_refresh_interval_hours` defaults to 6 hours,
has a minimum of 30 minutes for any positive value, and adds a random delay of
20 seconds to 5 minutes. During active restricted-campaign discovery, the miner
may temporarily refresh as often as every five minutes to work through standby
channel batches. Set the value to `0` to disable all periodic category checks.

Refreshes add newly discovered streamers but do not remove channels that were
loaded earlier. Restart the miner to fully discard stale or removed category
streamers. In Docker/config mode, changing `MINE_CONFIG["categories"]` triggers
discovery after the configuration watcher detects the edit; other category
options require a restart. See [Configuration reload limitations](#configuration-reload-limitations).

The Drop badge catalog runs independently of category discovery. Its startup
check begins in a background thread after normal miner setup completes, and it
checks hourly thereafter by default. It reads normalized campaign and badge data
from the `twitch-drops.json` and `twitch-badges.json` gists. The game signature
is compared with the saved state, so only new or changed campaign data is
processed. Full Twitch badge records, game snapshots,
classifications, and historical campaign records are stored in
`drop_badge_catalog.json` beside `config.py`. Rewards confirmed by Twitch are
marked `BADGE`; unmatched rewards remain `UNKNOWN` rather than being assumed not
to be badges.

Set `auto_mine_badge_drops=True` to use that catalog to add eligible live
channels automatically. The feature only considers active watch-time campaigns,
skips badges already available to the authenticated account, honors `blacklist`,
and uses `category_chat` and `category_sort`. Subscription-only rewards are not
added because watching cannot earn them. Automatically selected streamers always
have `claim_drops=True`. This feature is disabled by default.

##### Wildcard category fallback

`wildcard_categories` is a separate opt-in fallback for when `categories`
leaves watch capacity idle this cycle -- either because `categories` is
empty, none of your configured categories currently have an active
incomplete campaign, or one does but produced zero live matching channels
right now (a real campaign with nobody streaming it isn't useful capacity
either). Once that happens, and only then, the miner discovers live channels
for *every other* category with an active incomplete Drop campaign -- filling
otherwise idle watch capacity without you having to enumerate every game
yourself. If a long `categories` list yields at least one live streamer this
cycle, wildcard stands down even if most of the list was campaign-free --
the trigger is "did the preferred pass produce anything to watch," not "was
every single configured category checked off."

Wildcard candidate discovery merges Twitch's account-scoped campaign data with
the bundled external Drops catalog because Twitch can omit public campaigns from
the account response. Twitch remains authoritative for every game it does
expose, so a completed or ineligible Twitch campaign is never resurrected by the
external catalog. Games found only in the external catalog are provisional: a
selected live channel must advertise the matching campaign through Twitch before
the miner permits it into the watch pool. The remaining candidates are ranked by
their earliest incomplete Drop deadline before the wildcard limit and pinning
rules are applied.

```python
MINE_CONFIG = {
    "categories": ["warframe", "diablo-iv"],
    "wildcard_categories": True,
    "wildcard_category_limit": 10,
    "wildcard_category_streamer_limit": 1,
    "wildcard_category_pin_active": True,
}
```

| Option | Default | Description |
|---|---:|---|
| `wildcard_categories` | `False` | Enable the fallback. Only activates once the preferred `categories` pass produces zero live streamers this cycle. |
| `wildcard_category_limit` | `10` | Maximum distinct wildcard categories added per refresh cycle. With `wildcard_category_pin_active=True`, an already-tracked wildcard category doesn't count against this cap, so the effective tracked count can exceed it -- that throttles new additions only, it does not cap the total. |
| `wildcard_category_streamer_limit` | `1` | Live channels pulled per wildcard category. Kept small by default since this is best-effort filler, not a curated list. |
| `wildcard_category_pin_active` | `True` | Keep an already-tracked wildcard category even if a re-sort by campaign expiration pushes it out of the top `wildcard_category_limit`, as long as its campaign is still active. Prevents pointlessly retiring and re-picking a streamer between refreshes. Set to `False` for a strict top-N-by-expiration set every cycle. |

This is its own `StreamerSource.WILDCARD_CATEGORIES` priority tier -- see
[MINER_CONFIG](#miner_config) -- independent from `StreamerSource.CATEGORIES`.
It defaults to the lowest priority (after `BADGES`) precisely so that
wildcard-discovered streams never compete for a watch slot ahead of your
curated `categories` list or `auto_mine_badge_drops` the moment they're
found; they only ever fill slots those sources leave unused. Reorder
`streamer_source_priority` if you want it ranked higher.

Wildcard-discovered channels use the miner's default `StreamerSettings` (no
chat by default), honor `blacklist`, and use `category_chat`/`category_sort`
like normal category discovery. Twitch only accrues Drops progress on one of
the (up to two) watched streams regardless of source, so the miner watches at
most one discovered ("not explicitly configured") Drops stream per cycle in
total across `categories` and the wildcard fallback combined -- a preferred-
category pick wins that shared slot over a wildcard one whenever both qualify
in the same cycle, freeing the wildcard candidate's slot for an explicit or
followed streamer instead of wasting it on a second stream that can't accrue
progress anyway.

##### Category logging and analytics

`category_log_level` controls category discovery and refresh records separately
from unrelated logs. For example, `logging.DEBUG` lets category diagnostics pass
an `INFO` console threshold without turning on global debug output. Use
`log_drop_checks=True` only for deeper Drop API/inventory debugging because it
can produce substantially more output.

By default, point balance events from category-only streamers are ignored to
avoid noisy or misleading analytics across transient channels. Set
`track_category_streamer_points=True` to include them. A streamer that is also in
the explicit `streamers` list is always tracked. `drop_item_art=True` enriches the
Drops analytics table with item artwork, while `scrape_drop_progress_on_load=True`
populates current inventory progress at startup.

##### Category examples

Prioritize the closest-expiring configured Drop campaigns, preferring
lower-viewer channels and never joining their chats:

```python
MINE_CONFIG = {
    "categories": ["warframe", "diablo-iv", "the-elder-scrolls-online"],
    "category_limit": 3,
    "category_sort": CategorySort.VIEWERS_ASC,
    "category_campaign_order": CategoryCampaignOrder.EXPIRATION,
    "category_chat": ChatPresence.NEVER,
    "category_drops_enabled": True,
}
```

Mix permanent streamers, followers, and category discovery:

```python
STREAMERS = ["favorite-streamer"]
MINE_CONFIG = {
    "followers": True,
    "blacklist": ["blocked-streamer"],
    "categories": ["arc-raiders", "warframe|preferred-streamer"],
    "category_limit": 5,
    "track_category_streamer_points": True,
}
```

##### Category troubleshooting

| Symptom | What to check |
|---|---|
| No category streamers are loaded | Confirm the game has an active, incomplete campaign and try its Twitch directory URL. Enable `print_open_drop_campaigns_on_load=True` to inspect detected campaigns. |
| A live channel is skipped | With `category_drops_enabled=True`, it must have the `DropsEnabled` tag. Restricted campaigns may also require the channel to be on the campaign's allowlist. |
| A forced streamer is skipped | Confirm the login is live, streaming the requested game, and has the Drops tag when required. |
| The chosen channels are unexpected | Check `category_sort`, `category_limit`, category order, and `CategoryCampaignOrder`. Restricted campaign allowlists take precedence over a normal category search. |
| A removed category still affects selection | Refresh only adds streamers; restart the miner to remove already-loaded category channels. |
| Category messages are missing | Set `category_log_level=logging.INFO` or `logging.DEBUG` and ensure the logger/console threshold permits that level. |
| Analytics omit channel point changes | Set `track_category_streamer_points=True`; it defaults to `False` for category-only channels. |
| Wildcard categories never activate | They only run once the preferred `categories` pass produces zero live streamers this cycle -- a long `categories` list with even one currently-live match will keep wildcard standing down. Confirm `wildcard_categories=True`, and check whether any configured category currently has a live matching channel, e.g. via `print_open_drop_campaigns_on_load=True` and `category_log_level=logging.DEBUG`. |
| A wildcard channel keeps getting swapped out | Set `wildcard_category_pin_active=True` (the default) so an already-tracked wildcard category isn't dropped just for falling out of the top `wildcard_category_limit` on a re-sort. |

A channel eligible for both `auto_mine_badge_drops` and
`wildcard_categories` at once is claimed by whichever source
`streamer_source()` checks first -- badges, then wildcard, then ordinary
category discovery -- independent of `streamer_source_priority`, which only
governs watch-slot arbitration once a streamer already has a source.

#### ANALYTICS_CONFIG

Set `ANALYTICS_CONFIG` to `None` when the web server is not needed. Otherwise,
provide the keyword arguments for `analytics(...)`, such as `host`, `port`,
`refresh`, `days_ago`, `password`, and `log_poll_interval`. Analytics storage
also requires `MINER_CONFIG["enable_analytics"] = True`. See [Analytics](#analytics)
for configuration and security guidance.

### Runner behavior

Docker, Windows, and source checkouts all use the stable application runner. It
loads `config/config.py`, starts analytics when configured, applies supported
configuration migrations, watches for supported live changes, and starts
mining. Directly constructing `TwitchChannelPointsMiner` and calling `.mine(...)`
is the legacy `run.py` workflow; existing installations can
[migrate from run.py](#migrating-from-runpy).

The runner checks `config/config.py` every five seconds. Set
`TCPM_CONFIG_RELOAD_SECONDS` to change the interval; the minimum is one second.

At startup, the runner checks `CONFIG_VERSION`. Unversioned configurations are
backed up as `config.py.v0.bak` and upgraded in place. The migration adds missing
global `StreamerSettings` fields with their defaults and appends newly introduced
priority values to the existing priority list without reordering it. A config
created by a newer unsupported release is rejected rather than rewritten. The
obsolete Twitch `MINER_CONFIG["password"]` field is removed from both the active
configuration and any migration backup; Twitch authentication uses the TV
activation flow instead.
Generated Python is parsed before replacement. If an earlier migration left an
invalid file and a versioned backup exists, startup rebuilds the config from that
backup without overwriting it.

#### Configuration reload limitations

- New entries in `STREAMERS` are applied without a restart. Removing an
  explicitly configured streamer also removes its loaded state, chat connection,
  and channel PubSub subscriptions. A channel discovered from another enabled
  source remains loaded under that source.
- Changes to `MINE_CONFIG["categories"]` trigger discovery after the watcher
  detects the edit. Previously discovered channels remain loaded until restart.
- Changes to existing streamer settings, `MINER_CONFIG`, `ANALYTICS_CONFIG`, and
  non-category `MINE_CONFIG` options require a restart.
- Invalid configuration is logged and ignored. The watcher retries after the
  file changes again.

### Docker

#### Docker Hub
Official Docker images are on https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner for `linux/amd64` and `linux/arm64`. The same tags are also published to the legacy https://hub.docker.com/r/zacharmstrong/twitch-channel-points-miner-v2 repository so existing installations continue to receive updates.

The ARM64 image can run in compatible 64-bit Android Linux environments where
the device reports `aarch64` or `arm64-v8a`. Android itself is not a supported
Docker host, and users are responsible for providing a suitable container,
chroot, or Linux compatibility layer. Older 32-bit ARMv7 devices are not
supported.

Maintainers building or publishing images from source should follow the
[Docker image build guide](BUILD.md#docker-images).

The image reads `/usr/src/app/config/config.py`. Create and mount a host `config`
directory. When that directory is empty on first launch, the container copies
the bundled [config.example.py](config.example.py) to `config/config.py` and
exits. Customize the new file, then start the container again. If you normally
use restart policies like `restart: unless-stopped` or `--restart unless-stopped`,
run the initial seed launch with restarts disabled (`restart: "no"` or
`--restart no`) so the container stays stopped while you edit `config/config.py`.
After editing, restore your preferred restart policy before launching the
container again. The container entrypoint starts the stable runner on
subsequent launches.

Persist these directories on the host:

- `analytics`: analytics history and Drop data
- `cookies`: saved Twitch authentication
- `logs`: miner log files
- `config`: the user-owned configuration

**Example using docker-compose:**

```yml
version: "3.9"

services:
  miner:
    image: zacharmstrong/twitch-channel-points-miner
    restart: unless-stopped
    stop_grace_period: 60s
    stdin_open: true
    tty: true
    environment:
      - TERM=xterm-256color
      - TZ=America/Denver
    volumes:
      - ./analytics:/usr/src/app/analytics
      - ./cookies:/usr/src/app/cookies
      - ./logs:/usr/src/app/logs
      - ./config:/usr/src/app/config
    ports:
      - "5000:5000"
```

**Example with docker run:**
```sh
docker run \
    --restart unless-stopped \
    --stop-timeout 60 \
    -e TZ=America/Denver \
    -v $(pwd)/analytics:/usr/src/app/analytics \
    -v $(pwd)/cookies:/usr/src/app/cookies \
    -v $(pwd)/logs:/usr/src/app/logs \
    -v $(pwd)/config:/usr/src/app/config \
    -p 5000:5000 \
    zacharmstrong/twitch-channel-points-miner
```

Set `TZ` to your [IANA time zone](https://en.wikipedia.org/wiki/List_of_tz_database_time_zones),
for example `America/New_York`, `Europe/London`, or `Asia/Tokyo`. This controls
the local time used in container logs and timestamps. If `TZ` is omitted, the
container uses UTC.

`$(pwd)` does not work in Windows Command Prompt. Use absolute host paths there,
such as `/path/to/cookies:/usr/src/app/cookies`.

On Windows, use absolute host paths. For example, mount
`C:\Absolute\Path\config` at `/usr/src/app/config`.

#### Graceful Docker shutdowns

The miner handles Docker's normal `SIGTERM` stop signal and writes its
graceful-shutdown report after stopping its worker threads. This applies to
`docker stop`, `docker restart`, and normal Docker Compose stop, restart, down,
and recreate operations.

Docker's default stop grace period is commonly only 10 seconds. Shutdown can
take longer while chat, WebSocket, campaign-sync, and badge-catalog workers
finish. Configure at least 60 seconds so Docker does not send `SIGKILL` before
the report is written:

```yaml
services:
  miner:
    stop_grace_period: 60s
    restart: unless-stopped
```

For `docker run`, use the equivalent `--stop-timeout 60` and
`--restart unless-stopped` options. The restart policy controls recovery after
process or host restarts; it does not replace the stop grace period.

An immediate `docker kill`, an out-of-memory kill, a host crash, or power loss
cannot run shutdown code and therefore cannot produce an end report. Reports
are stored in the mounted `logs` directory, so keep that directory persistent.

For a non-default container path, set `TCPM_CONFIG_DIR` and mount the host
directory at the same location. Users upgrading from the legacy runner should
follow [Migrating from run.py](#migrating-from-runpy) once; new installations do
not need a legacy file.

Directories that are not mounted are created inside the container and their data
is lost when the container is removed. On first authentication, run the container
interactively with `-it` so you can complete the Twitch login.

For multiple accounts, give each container separate config, cookie, log, and
analytics directories, and use a different host port for each analytics server:

```sh
docker run --name user1 -it \
    -v $(pwd)/user1/config:/usr/src/app/config \
    -v $(pwd)/user1/cookies:/usr/src/app/cookies \
    -v $(pwd)/user1/logs:/usr/src/app/logs \
    -v $(pwd)/user1/analytics:/usr/src/app/analytics \
    -p 5001:5000 \
    zacharmstrong/twitch-channel-points-miner
```

```sh
docker run --name user2 -it \
    -v $(pwd)/user2/config:/usr/src/app/config \
    -v $(pwd)/user2/cookies:/usr/src/app/cookies \
    -v $(pwd)/user2/logs:/usr/src/app/logs \
    -v $(pwd)/user2/analytics:/usr/src/app/analytics \
    -p 5002:5000 \
    zacharmstrong/twitch-channel-points-miner
```

#### Portainer

See the [Portainer deployment guide](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/wiki/Deploy-with-Portainer)
for persistent storage, container settings, first-time authentication, analytics,
and updates.

### Replit

This repository does not currently maintain an official Replit deployment.

### Limits
_**Twitch has a limit - you can't watch more than two channels at one time. Set
`streams_watched` to `1` or `2` in `MINER_CONFIG` to control how many are watched
concurrently; the default is `2`. The highest-priority streamers are selected.**_

The `streams_watched` option exists because each mined channel creates viewing
activity on the account. Twitch Turbo users have reported that running two mined
streams continuously, especially while also watching Twitch normally, may result
in ads being served ([upstream issue #787](https://github.com/rdavydov/Twitch-Channel-Points-Miner-v2/issues/787)).
Setting the option to `1` reduces the miner's concurrent viewing activity. It is
a mitigation rather than a guarantee, because Twitch does not document this
behavior.

Make sure to write the streamers array in order of priority from left to right. If you use `followers=True` you can choose to download the followers sorted by follow date (ASC or DESC).

## Settings
Most settings are documented inline in [config.example.py](config.example.py).
The `priority` option controls which eligible streamers receive the available
watch slots. It accepts one `Priority` value or an ordered list. Include at least
one fallback such as `ORDER`, `POINTS_ASCENDING`, or `POINTS_DESCENDING`; using
only `STREAK`, for example, leaves no selection rule after available streaks have
been handled.

Available values:
 - `STREAK` - Catch the watch streak from all streamers
 - `FAVORITE` - Prioritize streamers with `StreamerSettings(favorite=True)`
 - `DROPS` - Claim all drops from streamers with drops tags enabled
 - `SUBSCRIBED` - Prioritize streamers you're subscribed to (higher subscription tiers are mined first)
 - `ORDER` - Following the order of the list
 - `POINTS_ASCENDING` - On top the streamers with the lowest points
 - `POINTS_DESCENDING` - On top the streamers with the highest points

Priorities can be combined in order. Avoid contradictory fallback rules such as
`ORDER` and `POINTS_ASCENDING` in the same list.

Set `points_limit` in `StreamerSettings` to stop watching a streamer once its
channel-points balance reaches that value. A pending watch streak can temporarily
bypass the limit so its reward is not missed. Set `favorite=True` per streamer
and include `Priority.FAVORITE` to reserve watch slots for favorites first.

### LoggerSettings

| Key | Type | Default | Description |
|---|---|---|---|
| `save` | bool | `True` | Save logs to rotating files. |
| `less` | bool | `False` | Use compact log formatting and shorter messages. |
| `console_level` | logging level | `logging.INFO` | Minimum level written to the console. |
| `console_username` | bool | `False` | Include the account username in console and notification messages. |
| `time_zone` | str or None | `None` | Log time zone, such as `America/Denver`. |
| `date_format` | str | `"dd/mm/yy"` | Date format used in logs and analytics. Use `dd`, `mm`, and `yy` or `yyyy`, separated by `/`, `-`, or `.`. |
| `file_level` | logging level | `logging.DEBUG` | Minimum level written to log files. |
| `emoji` | bool | Platform-dependent | Enable emoji; defaults to disabled on Windows and enabled elsewhere. |
| `colored` | bool | `False` | Enable colored console output. |
| `auto_clear` | bool | `True` | Rotate logs daily and retain seven backups. |
| `color_palette` | ColorPalette | Default palette | Customize colors by event. |
| `telegram` | Telegram or None | `None` | Send selected events through Telegram. |
| `discord` | Discord or None | `None` | Send selected events through Discord. |
| `webhook` | Webhook or None | `None` | Send selected events to a generic HTTP endpoint. |
| `matrix` | Matrix or None | `None` | Send selected events to a Matrix room. |
| `pushover` | Pushover or None | `None` | Send selected events through Pushover. |
| `gotify` | Gotify or None | `None` | Send selected events to a Gotify server. |
| `email` | Email or None | `None` | Send selected events through an SMTP server. |
| `ntfy` | Ntfy or None | `None` | Publish selected events to an ntfy topic. |
| `daily_report` | bool | `False` | Generate a daily channel-points and Drop activity report. |
| `daily_report_time` | str | `"00:00"` | Local delivery time for the daily report, in 24-hour `HH:MM` format. |

#### Color Palette
`ColorPalette` customizes console colors by event name. Unspecified events use
`Fore.RESET`, while prediction wins and losses default to green and red. Values
may be Colorama constants or case-insensitive color names: `BLACK`, `RED`,
`GREEN`, `YELLOW`, `BLUE`, `MAGENTA`, `CYAN`, `WHITE`, or `RESET`.
```python
from colorama import Fore
ColorPalette(
    STREAMER_ONLINE = Fore.GREEN,
    STREAMER_OFFLINE = Fore.RED,
    GAIN_FOR_RAID = Fore.YELLOW,
    GAIN_FOR_CLAIM = Fore.YELLOW,
    GAIN_FOR_WATCH = Fore.YELLOW,
    GAIN_FOR_WATCH_STREAK = Fore.YELLOW,
    BET_WIN = Fore.GREEN,
    BET_LOSE = Fore.RED,
    BET_REFUND = Fore.RESET,
    BET_FILTERS = Fore.MAGENTA,
    BET_GENERAL = Fore.BLUE,
    BET_FAILED = Fore.RED,
)
```

#### Notifications

Notifications are event-filtered copies of selected log messages. Add provider
objects to `LoggerSettings`; leave unused providers as `None`. Every provider
requires an `events` list, and the same event can be sent through several
providers.

```python
from TwitchChannelPointsMiner.classes.Settings import Events
from TwitchChannelPointsMiner.classes.Telegram import Telegram
from TwitchChannelPointsMiner.logger import LoggerSettings

logger_settings = LoggerSettings(
    save=True,
    auto_clear=True,
    log_retention_days=7,
    telegram=Telegram(
        chat_id=123456789,
        token="YOUR_BOT_TOKEN",
        events=[Events.STREAMER_OFFLINE, Events.DROP_CLAIM],
        message_thread_id=987,
    ),
)
```

With `auto_clear=True` (the default), the active `logs/<username>.log` file is
rolled at local midnight on Windows and Linux. Daily archives use a date suffix,
and `log_retention_days` controls how many are retained (7 by default). Set
`auto_clear=False` to preserve the existing timestamped-file-per-start behavior.

Assign this object to `MINER_CONFIG["logger_settings"]`. The complete
[configuration template](config.example.py) shows all providers together.
Placeholder values are ignored; use real credentials and never commit them.

| Provider | Destination | Class |
|---|---|---|
| Telegram | Bot chat | `Telegram` |
| Discord | Channel webhook | `Discord` |
| Generic webhook | HTTP endpoint | `Webhook` |
| Matrix | Matrix room | `Matrix` |
| Pushover | Pushover account or group | `Pushover` |
| Gotify | Self-hosted Gotify application | `Gotify` |
| Email | Addresses reached through SMTP | `Email` |
| ntfy | ntfy topic (hosted or self-hosted) | `Ntfy` |

Delivery failures are logged and do not stop mining. Enable
`console_username` when several accounts share a destination. The analytics
dashboard can send a test message for each enabled provider.

##### Telegram

Create a bot with [@BotFather](https://t.me/botfather), then obtain the numeric
destination `chat_id` (for example with [@getmyid_bot](https://t.me/getmyid_bot)).

| Key | Type | Default | Description |
|---|---|---|---|
| `chat_id` | int | Required | Numeric Telegram chat ID. |
| `token` | str | Required | Bot token issued by BotFather. |
| `events` | list | Required | Events to send. |
| `disable_notification` | bool | `False` | Send without sound or vibration. |
| `message_thread_id` | int or None | `None` | Optional Telegram forum topic ID for supergroup topics. |

```python
Telegram(
    chat_id=123456789,
    token="YOUR_BOT_TOKEN",
    events=[Events.STREAMER_ONLINE, Events.CHAT_MENTION],
    disable_notification=True,
    message_thread_id=987,
)
```

##### Discord

In the destination channel, open **Edit Channel > Integrations > Webhooks**,
create a webhook, and copy its URL.

| Key | Type | Default | Description |
|---|---|---|---|
| `webhook_api` | str | Required | Discord webhook URL. |
| `events` | list | Required | Events to send. |

```python
Discord(
    webhook_api="https://discord.com/api/webhooks/ID/TOKEN",
    events=[Events.STREAMER_OFFLINE, Events.BET_LOSE],
)
```

##### Generic Webhook

`Webhook` sends `event_name` and `message` as URL query parameters for `GET`, or
as form fields in the request body for `POST`.

| Key | Type | Default | Description |
|---|---|---|---|
| `endpoint` | str | Required | Destination URL. |
| `method` | str | Required | `POST` or `GET`. |
| `events` | list | Required | Events to send. |
| `timeout` | float | `10` | Request timeout in seconds. |

```python
Webhook(
    endpoint="https://example.com/webhook",
    method="POST",
    events=[Events.DROP_CLAIM, Events.CONFIGURATION],
    timeout=5,
)
```

##### ntfy

`Ntfy` publishes plain-text notifications to [ntfy](https://ntfy.sh/). The
default server is `https://ntfy.sh`; set `server_url` for a self-hosted server.
Topic names act like passwords when access control is not enabled, so use a
hard-to-guess topic and do not commit it. Protected topics can use an access
token.

| Key | Type | Default | Description |
|---|---|---|---|
| `topic` | str | Required | Destination topic name. |
| `events` | list | Required | Events to send. |
| `server_url` | str | `"https://ntfy.sh"` | Hosted or self-hosted ntfy server URL. |
| `token` | str or None | `None` | Optional ntfy access token. |
| `priority` | int or None | `None` | Optional ntfy priority from 1 through 5. |
| `tags` | list, tuple, str, or None | `None` | Optional ntfy tags. |
| `timeout` | float | `10` | Request timeout in seconds. |

```python
from TwitchChannelPointsMiner.classes.Ntfy import Ntfy

Ntfy(
    topic="YOUR_NTFY_TOPIC",
    token="YOUR_ACCESS_TOKEN",
    priority=3,
    tags=["twitch"],
    events=[Events.STREAMER_OFFLINE, Events.DROP_CLAIM],
)
```

##### Matrix

Matrix logs in when configured, then sends plain-text messages to the room.

| Key | Type | Default | Description |
|---|---|---|---|
| `username` | str | Required | Username accepted by the homeserver. |
| `password` | str | Required | Matrix account password. |
| `homeserver` | str | Required | Hostname without `https://`, such as `matrix.org`. |
| `room_id` | str | Required | Destination room ID, such as `!roomid:matrix.org`. |
| `events` | list | Required | Events to send. |

```python
Matrix(
    username="twitch_miner",
    password="YOUR_MATRIX_PASSWORD",
    homeserver="matrix.org",
    room_id="!roomid:matrix.org",
    events=[Events.STREAMER_OFFLINE, Events.DROP_CLAIM],
)
```

The account must already be able to post in the room. Authentication failure is
logged at startup and disables Matrix delivery for that run.

##### Pushover

Create an application at [Pushover](https://pushover.net/), then use the account
user key and application API token.

| Key | Type | Default | Description |
|---|---|---|---|
| `userkey` | str | Required | Pushover user or group key. |
| `token` | str | Required | Application API token. |
| `priority` | int | Required | [Pushover message priority](https://pushover.net/api#priority). |
| `sound` | str | Required | [Pushover sound name](https://pushover.net/api#sounds). |
| `events` | list | Required | Events to send. |

```python
Pushover(
    userkey="YOUR_USER_KEY",
    token="YOUR_APPLICATION_TOKEN",
    priority=0,
    sound="pushover",
    events=[Events.CHAT_MENTION, Events.DROP_CLAIM],
)
```

##### Gotify

Create an application in Gotify and use its message endpoint, including the
application token.

| Key | Type | Default | Description |
|---|---|---|---|
| `endpoint` | str | Required | Message URL, such as `https://gotify.example/message?token=TOKEN`. |
| `priority` | int | Required | Priority passed to Gotify. |
| `events` | list | Required | Events to send. |

```python
Gotify(
    endpoint="https://gotify.example/message?token=YOUR_TOKEN",
    priority=8,
    events=[Events.STREAMER_OFFLINE, Events.BET_LOSE],
)
```

##### Email / SMTP

Email uses Python's built-in SMTP support and accepts one recipient or a list or
tuple of recipients.

| Key | Type | Default | Description |
|---|---|---|---|
| `host` | str | Required | SMTP server hostname. |
| `port` | int | Required | SMTP server port. |
| `sender` | str | Required | Address placed in the `From` header. |
| `recipients` | str, list, or tuple | Required | One recipient or a list/tuple of recipients. |
| `events` | list | Required | Events to send. |
| `username` | str or None | `None` | SMTP login username. |
| `password` | str or None | `None` | SMTP login password. |
| `use_ssl` | bool | `False` | Use implicit TLS from connection start. |
| `starttls` | bool | `True` | Upgrade a plain connection with STARTTLS. |
| `timeout` | float | `15` | Connection timeout in seconds. |

```python
Email(
    host="smtp.example.com",
    port=587,
    username="miner@example.com",
    password="YOUR_SMTP_PASSWORD",
    sender="miner@example.com",
    recipients=["you@example.com"],
    events=[Events.DAILY_REPORT, Events.CONFIGURATION],
    starttls=True,
)
```

`use_ssl` and `starttls` cannot both be enabled. Use `use_ssl=True,
starttls=False` for implicit TLS, commonly on port 465. For scheduled summaries,
subscribe to `DAILY_REPORT` and set `daily_report=True`. Report progress is saved
per account under `logs/.state/`; an overdue report is sent after startup
finishes refreshing streamer balances.

#### Events

Import `Events` from `TwitchChannelPointsMiner.classes.Settings` and select any
of these values in each provider's `events` list.

| Event | Sent when |
|---|---|
| `UPDATE_AVAILABLE` | The update checker finds a newer release. This alert is forced through every configured provider when update checking is enabled. |
| `DAILY_REPORT` | The scheduled point and Drop activity summary is generated. Requires `daily_report=True`. |
| `STREAMER_ONLINE` | A tracked streamer goes live. |
| `STREAMER_OFFLINE` | A tracked streamer goes offline. |
| `GAIN_FOR_RAID` | Channel points are earned for joining a raid. |
| `GAIN_FOR_CLAIM` | Channel points are earned from a claimed bonus. |
| `GAIN_FOR_WATCH` | Channel points are earned for watch time. |
| `GAIN_FOR_WATCH_STREAK` | Channel points are earned from a watch-streak reward. |
| `BET_WIN` | A prediction wins and its result is credited. |
| `BET_LOSE` | A prediction loses. |
| `BET_REFUND` | A prediction is canceled or refunded. |
| `BET_FILTERS` | A prediction is skipped because a configured filter does not match. |
| `BET_GENERAL` | General prediction decisions or status messages are produced. |
| `BET_FAILED` | A prediction cannot be placed. |
| `BET_START` | A prediction window opens. |
| `BONUS_CLAIM` | The periodic channel-points bonus is claimed. |
| `MOMENT_CLAIM` | A Twitch Moment is claimed. |
| `JOIN_RAID` | The miner follows a raid to another channel. |
| `DROP_CLAIM` | A completed Drop reward is claimed. |
| `DROP_STATUS` | Drop discovery, eligibility, or progress status is reported. This can be verbose. |
| `CHAT_MENTION` | The configured username is mentioned in joined chat. Chat presence must be enabled. |
| `CONFIGURATION` | Startup, reload, authentication, or other configuration information requires attention. Critical authentication alerts may be forced through configured providers. |

### StreamerSettings

| Key | Type | Default | Description |
|---|---|---|---|
| `make_predictions` | bool | `True` | Participate in channel-point predictions. |
| `follow_raid` | bool | `True` | Follow eligible raids. |
| `claim_drops` | bool | `True` | Accumulate Drop watch progress and claim available Drops. |
| `claim_moments` | bool | `True` | Claim available [Moments](https://help.twitch.tv/s/article/moments). |
| `watch_streak` | bool | `True` | Prioritize available watch-streak rewards. |
| `favorite` | bool | `False` | Prioritize this streamer when `Priority.FAVORITE` is configured. |
| `points_limit` | int or None | `None` | Stop watching at this balance, except for an eligible pending watch streak. |
| `community_goals` | bool | `False` | Contribute the maximum allowed points to community goals. |
| `bet` | BetSettings | Default settings | Configure prediction strategy, limits, filters, and timing. |
| `chat` | ChatPresence | `ONLINE` | Control when the miner joins IRC chat. |

Allowed values for `chat` are:
- `ALWAYS` Join in IRC chat and never leave
- `NEVER` Never join IRC chat
- `ONLINE` Participate in IRC chat while the streamer is online (leave when offline)
- `OFFLINE` Participate in IRC chat while the streamer is offline (leave when online)

### BetSettings

| Key | Type | Default | Description |
|---|---|---|---|
| `strategy` | Strategy | `SMART` | Prediction outcome-selection strategy. |
| `percentage` | int | `5` | Percentage of the current balance to place. |
| `percentage_gap` | int | `20` | Percentage-point gap used by the SMART strategy. |
| `max_points` | int | `50000` | Maximum calculated prediction amount. |
| `minimum_points` | int | `0` | Balance that must be exceeded before placing a prediction. |
| `stealth_mode` | bool | `False` | Keep a large prediction just below the current highest prediction. |
| `filter_condition` | FilterCondition or None | `None` | Optional condition that must pass before placing a prediction. |
| `delay_mode` | DelayMode | `FROM_END` | How prediction timing is calculated. |
| `delay` | float | `6` | Timing value interpreted according to `delay_mode`. |

#### Bet strategy

- **MOST_VOTED**: Select the option most voted based on users count
- **HIGH_ODDS**: Select the option with the highest odds
- **PERCENTAGE**: Select the option with the highest percentage based on odds (It's the same that show Twitch) - Should be the same as select LOWEST_ODDS
- **SMART_MONEY**: Select the option with the highest points placed.
- **SMART**: If the majority in percent chose an option, then follow the other users, otherwise select the option with the highest odds
- **NUMBER_1**: Always select the 1st option, BLUE side if there are only two options
- **NUMBER_2**: Always select the 2nd option, PINK side if there are only two options
- **NUMBER_3**: Always select the 3rd option
- **NUMBER_4**: Always select the 4th option
- **NUMBER_5**: Always select the 5th option
- **NUMBER_6**: Always select the 6th option
- **NUMBER_7**: Always select the 7th option
- **NUMBER_8**: Always select the 8th option

![Screenshot](https://raw.githubusercontent.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2/master/assets/prediction.png)

Here is a concrete example:

- **MOST_VOTED**: 21 users selected **“over 7.5”**, compared with 9 for “under 7.5.”
- **HIGH_ODDS**: The highest odd is 2.27 on **'over 7.5'** vs 1.79 on 'under 7.5'
- **PERCENTAGE**: The highest percentage is 56% for **'under 7.5'**
- **SMART**: Calculate the percentage based on the users. The percentages are: 'over 7.5': 70% and 'under 7.5': 30%. If the difference between the two percentages is higher than `percentage_gap` select the highest percentage, else the highest odds.

With `percentage_gap=20`, the 40-point difference exceeds the threshold, so the
miner selects “over 7.5.”
#### FilterCondition

| Key | Type | Default | Description |
|---|---|---|---|
| `by` | OutcomeKeys | `None` | Prediction value to evaluate. |
| `where` | Condition | `None` | Comparison operator. |
| `value` | number | `None` | Comparison target. |

Allowed values for `by` are:
- `PERCENTAGE_USERS` (no sum) [Would never want a sum as it'd always be 100%]
- `ODDS_PERCENTAGE` (no sum) [Doesn't make sense to sum odds]
- `ODDS` (no sum) [Doesn't make sense to sum odds]
- `DECISION_USERS` (no sum)
- `DECISION_POINTS` (no sum)
- `TOP_POINTS` (no sum) [Doesn't make sense to the top points of both sides]
- `TOTAL_USERS` (sum)
- `TOTAL_POINTS` (sum)

Allowed values for `where` are: `GT, LT, GTE, LTE`

##### Example

Require more than 200 total participants:

```python
FilterCondition(by=OutcomeKeys.TOTAL_USERS, where=Condition.GT, value=200)
```

Require the selected outcome's odds to be at least 1.3:

```python
FilterCondition(by=OutcomeKeys.ODDS, where=Condition.GTE, value=1.3)
```

Require the largest prediction to be below 2,000 points:

```python
FilterCondition(by=OutcomeKeys.TOP_POINTS, where=Condition.LT, value=2000)
```

#### DelayMode

- **FROM_START**: Will wait `delay` seconds from when the bet was opened
- **FROM_END**: Wait until `delay` seconds remain in the prediction window
- **PERCENTAGE**: Will place the bet when `delay` percent of the set timer is elapsed

Here's a concrete example. Let's suppose we have a bet that is opened with a timer of 10 minutes:

- **FROM_START** with `delay=20`: The bet will be placed 20s after the bet is opened
- **FROM_END** with `delay=20`: The bet will be placed 20s before the end of the bet (so 9mins 40s after the bet is opened)
- **PERCENTAGE** with `delay=0.2`: The bet will be placed when the timer went down by 20% (so 2mins after the bet is opened)

## Analytics

The optional analytics server provides a browser-based view of channel-point
history, Drops, and miner logs. The points chart includes tooltips with the
balance, timestamp, and gain or spend reason. Annotations highlight predictions,
watch streaks, and other significant changes, and can be disabled in the page.
The interface also includes light and dark themes.

The **Config** tab manages explicit streamers, categories and their order,
per-streamer behavior, category discovery, stream sources, logging, daily
reports, and notification providers. Streamer additions/removals and category
list changes are detected by the running miner; controls marked in the page as
restart-required take effect on the next start.

Dashboard changes are written directly to their canonical `STREAMERS`,
`MINER_CONFIG`, and `MINE_CONFIG` values in `config.py`; unrelated source and
comments are preserved. An existing `web-config.json` is imported automatically
and renamed to `.web-config.json.migrated.bak`. Notification
credentials are write-only in the browser: existing values are reported only
as configured, never returned by the API, and blank credential fields retain
the saved value. Protect the Config tab with the same analytics authentication
and HTTPS guidance described below.

| Light theme | Dark theme |
| ----------- | ---------- |
| ![Light theme](https://raw.githubusercontent.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2/master/assets/chart-analytics-light.png) | ![Dark theme](https://raw.githubusercontent.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2/master/assets/chart-analytics-dark.png) |

Enable storage with `MINER_CONFIG["enable_analytics"] = True` and configure the
server through `ANALYTICS_CONFIG`. The chart refreshes every `refresh` minutes.
The log viewer polls every `log_poll_interval` seconds (default `5`; accepted
range `1` to `180`), and `days_ago` controls the chart's initial time range.

For access from another machine, bind to `0.0.0.0` and provide a strong
`password`; remote binds without a password are rejected. Sign in with the
configured Twitch username and analytics password. The built-in server uses
plain HTTP, so expose it only on a trusted network or behind an HTTPS reverse
proxy. Background and design context are available in [issue #96](https://github.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2/issues/96).
```python
MINER_CONFIG = {
    "username": "your-twitch-username",
    "enable_analytics": True,
}

ANALYTICS_CONFIG = {
    "host": "127.0.0.1",
    "port": 5000,
    "refresh": 5,
    "days_ago": 7,
    "log_poll_interval": 5,
}
```

### Analytics security and HTTPS reverse proxy

The analytics server contains account activity and miner logs. Its built-in authentication uses HTTP Basic authentication, which does not encrypt credentials or response data. Binding it to `0.0.0.0` exposes it to every reachable network interface; use a strong, unique analytics password and do not expose port 5000 directly to the internet.

For remote access, keep the miner bound to loopback and terminate HTTPS in a reverse proxy. A minimal nginx location looks like this:

```nginx
server {
    listen 443 ssl;
    server_name analytics.example.com;

    ssl_certificate /etc/letsencrypt/live/analytics.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/analytics.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
    }
}
```

Set `ANALYTICS_CONFIG={"host": "127.0.0.1", "password": "a-strong-password"}`
behind that proxy. Restrict the proxy further with a firewall, VPN, or IP
allowlist where possible. nginx and certificate provisioning must be configured
for your operating system and domain; the example only shows the relevant proxy
boundary.

### Enabling analytics storage

Disabling analytics reduces memory and disk use because the miner does not create
or update analytics JSON files.

Set `MINER_CONFIG["enable_analytics"]` to `True` when using the analytics server.
Otherwise leave it `False` (the default).

## Logs

Logging is controlled by `LoggerSettings` in `MINER_CONFIG`. The default full
format includes timestamps, levels, function names, streamer state, point gains,
Drop activity, and prediction decisions. Set `less=True` for shorter console
messages while retaining the important event details.

### Full format

```text
14/07/26 12:00:00 - INFO - [run]: 💣 Start session: 'session-id'
14/07/26 12:00:02 - INFO - [set_online]: 🥳 Streamer(username=streamer-name, channel_id=0000000, channel_points=12000) is Online!
14/07/26 12:05:00 - INFO - [on_message]: 🚀 +10 → Streamer(username=streamer-name, channel_id=0000000, channel_points=12010) - Reason: WATCH.
14/07/26 12:06:00 - INFO - [claim_bonus]: 🎁 Claiming the bonus for Streamer(username=streamer-name, channel_id=0000000, channel_points=12010)!
```

### Compact format

```text
14/07 12:00:00 - 💣 Start session: 'session-id'
14/07 12:00:02 - 🥳 streamer-name (12k points) is Online!
14/07 12:05:00 - 🚀 +10 → streamer-name (12k points) - Reason: WATCH.
14/07 12:06:00 - 🎁 Claiming the bonus for streamer-name (12k points)!
```

### Graceful-shutdown report

During a controlled shutdown—such as `Ctrl+C` or Docker's normal stop signal—the
miner summarizes session duration, the log path, prediction results, and points
gained or spent per streamer and event type. It also reports Drop rewards whose
progress changed during the session, including watched minutes, minutes gained,
required minutes, and final status. Enable `scrape_drop_progress_on_load` to
capture the startup baseline required for this part of the report. When it is
disabled, the miner does not make an implicit startup inventory request or
estimate Drop gains from an incomplete baseline. Docker users should configure
the [recommended stop grace period](#graceful-docker-shutdowns). A hard kill,
host crash, or power loss cannot produce this report.

```text
14/07/26 18:00:00 - 🛑 Ending session: 'session-id'
14/07/26 18:00:00 - 📄 Logs file: /path/to/logs/username.timestamp.log
14/07/26 18:00:00 - ⌛ Duration 6:00:00
14/07/26 18:00:00 - 🤖 streamer-name (15k points), Total points gained: 3k
14/07/26 18:00:00 - 💰 CLAIM(4 times, 200 gained), WATCH(24 times, 240 gained)
14/07/26 18:00:00 - 🎁 Drop progress gained this session (1 reward):
14/07/26 18:00:00 - ⏳ Game - Campaign - Reward: 25/60m (+19m), in progress
```

## Migrating from run.py

Existing Docker users can let the image convert a conventional legacy runner on
first startup. The conversion preserves imports and expressions while splitting
the old calls into the four current configuration values.

### Automatic Docker migration

1. Back up the existing `run.py` and container configuration.
2. Create an empty, persistent host directory named `config`.
3. During the first upgraded start, mount both the directory and the old runner:

   ```yaml
   volumes:
     - ./config:/usr/src/app/config
     - ./run.py:/usr/src/app/run.py
   ```

4. Recreate the container. When `/usr/src/app/config/config.py` is absent, the
   entrypoint converts `/usr/src/app/run.py`, validates the result, and starts
   with the new configuration.
5. Check the startup output, inspect `config/config.py`, and confirm the account,
   streamers, mining options, notifications, and analytics server behave as
   expected.
6. Remove the `run.py` mount and recreate the container again. Keep only the
   persistent config directory for future upgrades.

After a successful conversion, the converter renames the legacy runner to
`run.py.bak` so it cannot be mistaken for the active configuration. It refuses
to overwrite either an existing `config/config.py` or `run.py.bak`. The new
configuration has owner-only permissions, and `config/.converted-from-run-py`
records the source path and SHA-256 hash.

### What converts automatically

The legacy file must contain exactly one `TwitchChannelPointsMiner(...)` call,
exactly one `.mine(...)` call, and no more than one `.analytics(...)` call. The
converter maps them as follows:

| Legacy expression | New configuration value |
|---|---|
| Arguments to `TwitchChannelPointsMiner(...)` | `MINER_CONFIG` |
| First positional argument to `.mine(...)` | `STREAMERS` |
| Keyword arguments to `.mine(...)` | `MINE_CONFIG` |
| Arguments to `.analytics(...)` | `ANALYTICS_CONFIG`, or `None` when absent |

Imports needed by enums and settings objects are retained. Expanded `**kwargs`,
extra positional arguments, multiple miner/mine calls, or syntax errors cannot
be converted safely.

### Persisted data versions

User-owned formats are checked when their corresponding feature starts:

- `config/config.py` uses `CONFIG_VERSION` and is migrated before execution.
- Streamer analytics JSON and `drops_by_category.json` use a root `version` and
  are migrated when analytics is enabled. Pre-migration files are retained as
  `<filename>.v0.bak`.
- Watch-streak state and `drop_badge_catalog.json` already carry independent
  version fields checked by their loaders.
- Cookie JSON keeps Twitch's cookie-dictionary shape and is not wrapped in a
  project schema. Authentication remains responsible for validating it.

Migrations are idempotent and refuse to overwrite an existing backup or consume
a format version newer than the running release.

If conversion fails, any incomplete output is removed, a migration warning is
printed, and the original runner executes unchanged. Correct the legacy file and
remove any partial config before retrying, or create `config/config.py` manually
from [config.example.py](config.example.py). Set
`TCPM_DISABLE_AUTO_CONVERSION=1` only when you intentionally need to postpone
conversion; the container will continue using the mounted legacy runner.

To convert without starting the miner, run the image's runner with
`--convert-only` while mounting both paths. This writes the converted config but
does not launch mining. Conversion still refuses to replace an existing config.

```sh
docker run --rm \
    -v $(pwd)/config:/usr/src/app/config \
    -v $(pwd)/run.py:/usr/src/app/run.py \
    zacharmstrong/twitch-channel-points-miner --convert-only
```

## Legacy cookie migration (optional)

This section applies only if you are upgrading from an older version or the
original miner and already have a working `twitch-cookies.pkl`. New users can
skip it—the miner creates the account cookie file after normal authentication.

The current account file is `cookies/<twitch-username>.pkl`, where the filename
must match `MINER_CONFIG["username"]`. Despite the historical `.pkl` extension,
new files contain validated JSON rather than Python pickle data.

To reuse an old cookie:

1. Stop the miner.
2. Back up the old cookie file. Treat it like a password: it contains an
   authentication token and must not be committed, shared, or included in logs.
3. Create the persistent `cookies` directory if it does not exist.
4. Copy and rename the old file to match the configured Twitch login:

   ```sh
   mkdir -p cookies
   cp /path/to/twitch-cookies.pkl cookies/your-twitch-username.pkl
   ```

5. Start the miner normally. If the file contains the supported legacy
   data-only cookie list, the miner loads it with a restricted parser, validates
   its structure, rewrites it as JSON, and limits its permissions to the owner.

For Docker, the host directory must be mounted at `/usr/src/app/cookies` as shown
in [Docker](#docker). The same per-account filename rule applies inside the
mounted directory. Use a separate cookie and config directory for each account.

The legacy cookie may no longer be accepted by Twitch. If the miner reports an
invalid or unsafe file, remove it and authenticate again. If Twitch rejects a
previously valid saved token, the miner clears the cached login and restarts so
that reauthentication can occur; follow the instructions printed in the logs.

## Windows
The Windows executable does not require Python, Git, or Command Prompt. Keep its
folder private because it will contain your Twitch login and saved session.

### Install the Windows executable

1. Open the project's
   [Releases page](https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3/releases)
   and select the newest release.
2. Under **Assets**, download the file named
   `TwitchChannelPointsMiner-<version>.zip`. Do not download **Source code**.
   If you prefer an installer, download
   `TwitchChannelPointsMiner-<version>-Setup.exe` and follow its prompts instead
   of the ZIP extraction steps below.
3. Open your **Downloads** folder, right-click the downloaded ZIP file, and
   select **Extract All...**.
4. Choose a permanent location that you can easily find, such as
   `Documents\TwitchChannelPointsMiner`, and select **Extract**. Do not run the
   program from inside the ZIP file.
5. Open the extracted folder and double-click `TwitchChannelPointsMiner.exe`.
   The first run creates the configuration file and opens a desktop window
   directly on the **Config** tab, ready for you to fill in.

Only download the executable from the project's official Releases page. If
Microsoft Defender SmartScreen appears, check that the publisher warning names
`TwitchChannelPointsMiner.exe` and that you used the link above. Then select
**More info** followed by **Run anyway**. If the file came from anywhere else,
delete it instead.

### Set up your account

The desktop window has two tabs: **Dashboard** (the analytics dashboard,
embedded) and **Console** (the miner's live log output). On first run it
opens straight to the Dashboard tab's **Config** view.

1. If the window asks for a username and password, use your Twitch username
   and the password printed on the **Console** tab (also saved in
   `config\config.py`, under `ANALYTICS_CONFIG`).
2. Under **Configured streamers**, add the channels you want to watch.
3. Review the other Config tab sections - categories, sources, logging, and
   notifications. The [configuration guide](#configuration-file) explains
   every section in more detail; anything not covered by the Config tab can
   still be edited directly in `config\config.py` with Notepad.

Changes made in the Config tab are written to `config\config.py`
automatically; streamer and category changes take effect immediately, and
other settings after a restart. You can return to this tab any time to enable
followed channels, Drops categories, predictions, notifications, and other
settings.

### Start the miner

The miner starts automatically as soon as the desktop window opens - there is
no separate step. Watch its progress on the **Dashboard** tab, and follow any
Twitch sign-in instructions on the **Console** tab (this is also where
startup errors appear if the dashboard itself fails to load). Use **Open in
browser** on either tab to view the dashboard in your default browser
instead.

Closing the window stops the miner. The miner saves its settings, login
session, and logs in folders beside the executable. Do not move the
executable by itself after setup; move the entire `TwitchChannelPointsMiner`
folder if you want it in a different location.

### Update the miner

1. Stop the running miner.
2. Download and extract the newest release as described above.
3. Copy the new `TwitchChannelPointsMiner.exe` into your existing miner folder.
4. When Windows asks, choose **Replace the file in the destination**.
5. Start the miner normally. Your existing configuration and login data remain
   in place.

### Windows troubleshooting

- **The desktop window never appears, or a console window flashes and
  closes:** The executable needs the Microsoft Edge WebView2 runtime, which
  ships with Windows 11 and most up-to-date Windows 10 installs. If it's
  missing, install the
  [Evergreen WebView2 Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
  and run the executable again.
- **The Dashboard tab shows "The dashboard isn't available yet":** Check the
  Console tab for the reason. Analytics may still be starting up, or
  `enable_analytics` may be set to `False` in `config\config.py`.
- **Windows says it cannot find the configuration:** Right-click the downloaded
  ZIP and use **Extract All...** before running the executable.
- **Emoji or symbols look broken:** In `config.py`, use
  `LoggerSettings(emoji=False)` in `MINER_CONFIG["logger_settings"]`. See
  [LoggerSettings](#loggersettings) for the complete example.
- **The miner closes or reports an error after setup:** Check the Console tab,
  or open the `logs` folder beside the executable and check the newest log
  file. Remove account names, cookies, tokens, and passwords before sharing a
  log in a bug report.

Developers who want to create the executable from source can use the
[Windows build guide](BUILD.md#windows-executable).

## Termux
> [!WARNING]
> These instructions are outdated and have not been tested with the v3 miner.
> They may not work and could cause unexpected problems. Use them at your own
> risk.

**1. Upgrade packages**
```
pkg upgrade
```

**2. Install packages to Termux**
```
pkg install python git libcrypt ndk-sysroot clang zlib binutils
LDFLAGS="-L${PREFIX}/lib/" CFLAGS="-I${PREFIX}/include/" pip install --upgrade wheel
```

**3. Clone this repository**

`git clone https://github.com/zarmstrong/Twitch-Channel-Points-Miner-v3`

**4. Go to the miner's directory**

`cd Twitch-Channel-Points-Miner-v3`

**5. Create and edit the configuration**

```sh
mkdir -p config
cp config.example.py config/config.py
nano config/config.py
```

**6. Install packages**
```
pip install -r requirements.txt
pip install Twitch-Channel-Points-Miner-v2
```

**7. Run the miner!**

`python -m TwitchChannelPointsMiner.runner --config-dir ./config`

Read more at [#92](https://github.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2/issues/92) [#76](https://github.com/Tkd-Alex/Twitch-Channel-Points-Miner-v2/issues/76)

## Disclaimer
This project comes with no guarantee or warranty. You are responsible for whatever happens from using this project. It is possible to get soft or hard banned by using this project if you are not careful. This is a personal project and is in no way affiliated with Twitch.
