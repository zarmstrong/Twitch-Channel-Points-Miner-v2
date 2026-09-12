# -*- coding: utf-8 -*-

"""Source-preserving edits for dashboard-managed Python configuration."""

import ast
import errno
import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path

CONFIG_FILE_MUTEX = threading.Lock()
STREAMER_RE = re.compile(r"^[A-Za-z0-9_]{1,25}$")
WEB_CONFIG_FILENAME = "web-config.json"
STREAMER_SETTING_DEFAULTS = {
    "favorite": False,
    "make_predictions": True,
    "follow_raid": True,
    "claim_drops": True,
    "claim_moments": True,
    "chat": "ONLINE",
    "points_limit": None,
}
STREAMER_SETTING_NAMES = set(STREAMER_SETTING_DEFAULTS)
CATEGORY_SORTS = {
    "ORDER",
    "VIEWERS_DESC",
    "VIEWERS_ASC",
    "STARTED_AT_DESC",
    "STARTED_AT_ASC",
    "RANDOM",
}
CHAT_PRESENCES = {"ALWAYS", "NEVER", "ONLINE", "OFFLINE"}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
SOURCE_NAMES = {"streamers", "followers", "categories", "badges", "wildcard_categories"}
# Ordered UI-name -> StreamerSource enum-name mapping, shared between the
# read side (building source_order) and the write side (update_sources) so
# the two can't silently drift out of sync.
SOURCE_ENUM_BY_NAME = {
    "streamers": "STREAMERS",
    "followers": "FOLLOWERS",
    "categories": "CATEGORIES",
    "badges": "BADGES",
    "wildcard_categories": "WILDCARD_CATEGORIES",
}
DEFAULT_SOURCE_ORDER = tuple(SOURCE_ENUM_BY_NAME)
NOTIFICATION_SCHEMAS = {
    "telegram": {
        "fields": ("chat_id", "message_thread_id", "disable_notification", "events"),
        "secrets": ("token",),
    },
    "discord": {"fields": ("events",), "secrets": ("webhook_api",)},
    "webhook": {"fields": ("method", "events"), "secrets": ("endpoint",)},
    "email": {
        "fields": (
            "host",
            "port",
            "username",
            "sender",
            "recipients",
            "use_ssl",
            "starttls",
            "events",
        ),
        "secrets": ("password",),
    },
    "matrix": {
        "fields": ("username", "homeserver", "room_id", "events"),
        "secrets": ("password",),
    },
    "pushover": {
        "fields": ("priority", "sound", "events"),
        "secrets": ("userkey", "token"),
    },
    "gotify": {"fields": ("priority", "events"), "secrets": ("endpoint",)},
    "ntfy": {
        "fields": ("server_url", "priority", "tags", "events"),
        "secrets": ("topic", "token"),
    },
}
NOTIFICATION_REQUIRED = {
    "telegram": {"chat_id", "token"},
    "discord": {"webhook_api"},
    "webhook": {"endpoint"},
    "email": {"host", "port", "sender", "recipients"},
    "matrix": {"username", "password", "homeserver", "room_id"},
    "pushover": {"userkey", "token"},
    "gotify": {"endpoint"},
    "ntfy": {"topic"},
}
NOTIFICATION_POSITIONAL_FIELDS = {
    "telegram": (
        "chat_id",
        "token",
        "events",
        "disable_notification",
        "message_thread_id",
    ),
    "discord": ("webhook_api", "events"),
    "webhook": ("endpoint", "method", "events", "timeout"),
    "email": (
        "host",
        "port",
        "sender",
        "recipients",
        "events",
        "username",
        "password",
        "use_ssl",
        "starttls",
        "timeout",
    ),
    "matrix": ("username", "password", "homeserver", "room_id", "events"),
    "pushover": ("userkey", "token", "priority", "sound", "events"),
    "gotify": ("endpoint", "priority", "events"),
    "ntfy": (
        "topic",
        "events",
        "server_url",
        "token",
        "priority",
        "tags",
        "timeout",
    ),
}


class ConfigEditError(ValueError):
    pass


def _assignment(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name
            for target in node.targets
        ):
            return node.value
    return None


def _dict_item(node, key_name):
    if not isinstance(node, ast.Dict):
        return None
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == key_name:
            return value
    return None


def _config_lists(source):
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        raise ConfigEditError(f"Configuration cannot be parsed: {error.msg}") from error
    streamers = _assignment(tree, "STREAMERS")
    categories = _dict_item(_assignment(tree, "MINE_CONFIG"), "categories")
    if not isinstance(streamers, ast.List):
        raise ConfigEditError(
            "STREAMERS must be a literal list to edit it in the web UI."
        )
    if not isinstance(categories, ast.List):
        raise ConfigEditError(
            "MINE_CONFIG['categories'] must be a literal list to edit it in the web UI."
        )
    return tree, streamers, categories


def _streamer_value(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "Streamer"
    ):
        if (
            node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            return node.args[0].value
        for keyword in node.keywords:
            if (
                keyword.arg == "username"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ):
                return keyword.value.value
    return None


def _simple_value(node):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_simple_value(item) for item in node.elts]
    if isinstance(node, ast.Dict):
        return {
            _simple_value(key): _simple_value(value)
            for key, value in zip(node.keys, node.values)
        }
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return {
            "__call__": getattr(node.func, "id", getattr(node.func, "attr", "")),
            "__args__": [_simple_value(item) for item in node.args],
            **{
                keyword.arg: _simple_value(keyword.value)
                for keyword in node.keywords
                if keyword.arg is not None
            },
        }
    return None


def _base_web_config(config_path):
    from TwitchChannelPointsMiner.classes.Settings import Events

    source = Path(config_path).read_text(encoding="utf-8")
    tree, streamer_nodes, category_nodes = _config_lists(source)
    miner = _simple_value(_assignment(tree, "MINER_CONFIG")) or {}
    mine = _simple_value(_assignment(tree, "MINE_CONFIG")) or {}
    global_streamer_settings = miner.get("streamer_settings") or {}
    effective_streamer_defaults = dict(STREAMER_SETTING_DEFAULTS)
    effective_streamer_defaults.update(
        {
            name: global_streamer_settings[name]
            for name in STREAMER_SETTING_NAMES
            if name in global_streamer_settings
            and global_streamer_settings[name] is not None
        }
    )
    logger_settings = miner.get("logger_settings") or {}
    update_interval = miner.get("update_check_interval_hours", 24)
    startup_only = update_interval == "inf" or (
        isinstance(update_interval, dict)
        and update_interval.get("__call__") == "float"
        and update_interval.get("__args__") == ["inf"]
    )

    streamers = []
    for node in streamer_nodes.elts:
        username = _streamer_value(node)
        if username is None:
            continue
        settings = dict(effective_streamer_defaults)
        parsed = _simple_value(node) if isinstance(node, ast.Call) else {}
        explicit_settings = parsed.get("settings") or (
            parsed.get("__args__", [None, {}])[1]
            if len(parsed.get("__args__", [])) > 1
            else {}
        )
        settings.update(
            {
                name: explicit_settings[name]
                for name in STREAMER_SETTING_NAMES
                if name in explicit_settings and explicit_settings[name] is not None
            }
        )
        streamers.append({"username": username, "settings": settings})

    categories = [
        node.value
        for node in category_nodes.elts
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    source_priority = miner.get("streamer_source_priority") or list(
        ("STREAMERS", "FOLLOWERS", "CATEGORIES", "BADGES", "WILDCARD_CATEGORIES")
    )
    sources = {
        "streamers": "STREAMERS" in source_priority,
        "followers": "FOLLOWERS" in source_priority
        and bool(mine.get("followers", False)),
        "categories": "CATEGORIES" in source_priority and bool(categories),
        "badges": "BADGES" in source_priority
        and bool(mine.get("auto_mine_badge_drops", False)),
        "wildcard_categories": "WILDCARD_CATEGORIES" in source_priority
        and bool(mine.get("wildcard_categories", False)),
    }
    ui_name_by_enum = {enum: name for name, enum in SOURCE_ENUM_BY_NAME.items()}
    source_order = [
        ui_name_by_enum[member]
        for member in source_priority
        if member in ui_name_by_enum
    ]
    source_order += [name for name in DEFAULT_SOURCE_ORDER if name not in source_order]
    notifications = {}
    for provider, schema in NOTIFICATION_SCHEMAS.items():
        configured = logger_settings.get(provider)
        if not isinstance(configured, dict):
            configured = {}
        else:
            configured = dict(configured)
            for name, value in zip(
                NOTIFICATION_POSITIONAL_FIELDS[provider],
                configured.get("__args__", []),
            ):
                configured.setdefault(name, value)
        fields = {
            name: configured.get(name)
            for name in schema["fields"]
            if name in configured
        }
        notifications[provider] = {
            "enabled": bool(configured),
            "fields": fields,
            "secrets": {name: bool(configured.get(name)) for name in schema["secrets"]},
        }
        available = {
            name for name, value in fields.items() if value not in (None, "", [])
        }
        available.update(
            name
            for name, present in notifications[provider]["secrets"].items()
            if present
        )
        notifications[provider]["test_available"] = bool(configured) and (
            NOTIFICATION_REQUIRED[provider].issubset(available)
        )

    return {
        "streamers": streamers,
        "streamer_defaults": effective_streamer_defaults,
        "categories": categories,
        "category": {
            "limit": mine.get("category_limit", 30),
            "sort": mine.get("category_sort", "VIEWERS_DESC"),
            "refresh_interval_hours": mine.get("category_refresh_interval_hours", 6),
            "drops_enabled": mine.get("category_drops_enabled", True),
        },
        "sources": sources,
        "source_order": source_order,
        "logging": {
            "console_level": logger_settings.get("console_level", "INFO"),
            "file_level": logger_settings.get("file_level", "DEBUG"),
            "daily_report": logger_settings.get("daily_report", False),
            "daily_report_time": logger_settings.get("daily_report_time", "00:00"),
        },
        "updates": {
            "enabled": miner.get("update_check", True),
            "interval_hours": 24 if startup_only else update_interval,
            "startup_only": startup_only,
        },
        "notifications": notifications,
        "notification_schemas": NOTIFICATION_SCHEMAS,
        "notification_event_options": [event.name for event in Events],
    }


def _overrides_path(config_path):
    return Path(config_path).with_name(WEB_CONFIG_FILENAME)


def load_web_overrides(config_path):
    path = _overrides_path(config_path)
    if not path.is_file():
        return {}
    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        raise
    try:
        data = json.loads(source)
    except json.JSONDecodeError as error:
        raise ConfigEditError(
            f"{WEB_CONFIG_FILENAME} contains invalid JSON."
        ) from error
    if not isinstance(data, dict):
        raise ConfigEditError(f"{WEB_CONFIG_FILENAME} must contain a JSON object.")
    return data


def read_managed_web_config(config_path):
    return _base_web_config(config_path)


def migrate_web_config(config_path):
    path = _overrides_path(config_path)
    if not path.is_file():
        return False
    overrides = load_web_overrides(config_path)
    if "streamers" in overrides:
        records = overrides["streamers"]
        if not isinstance(records, list):
            raise ConfigEditError("Managed streamers must be a list.")
        for record in records:
            if not isinstance(record, dict):
                raise ConfigEditError("Each managed streamer must be an object.")
            username = record.get("username")
            if not isinstance(username, str) or STREAMER_RE.fullmatch(username) is None:
                raise ConfigEditError("Each managed streamer needs a valid username.")
            _validate_streamer_settings(record.get("settings", {}))
        _write_streamers(
            config_path,
            records,
            {str(record.get("username", "")).lower() for record in records},
        )
    if "categories" in overrides:
        categories = overrides["categories"]
        if not isinstance(categories, list) or any(
            not _valid_managed_category(category) for category in categories
        ):
            raise ConfigEditError("Managed categories must be a list of valid values.")
        _write_config_categories(config_path, categories)
    actions = {
        "category": "update_category",
        "sources": "update_sources",
        "logging": "update_logging",
        "updates": "update_updates",
    }
    for name, action in actions.items():
        if name in overrides:
            if not isinstance(overrides[name], dict):
                messages = {
                    "category": "Invalid managed category settings.",
                    "sources": "Managed stream sources must be Boolean values.",
                    "logging": "Invalid managed logging settings.",
                    "updates": "Invalid managed update settings.",
                }
                raise ConfigEditError(messages[name])
            _update_managed_web_config(
                config_path, {"action": action, "values": overrides[name]}
            )
    notifications = overrides.get("notifications", {})
    if not isinstance(notifications, dict):
        raise ConfigEditError("Managed notifications must be an object.")
    for provider, update in notifications.items():
        if provider not in NOTIFICATION_SCHEMAS or not isinstance(update, dict):
            raise ConfigEditError("Invalid managed notification provider.")
        if not isinstance(update.get("fields", {}), dict) or not isinstance(
            update.get("secrets", {}), dict
        ):
            raise ConfigEditError(
                "Managed notification fields and secrets must be objects."
            )
        values = dict(update.get("fields", {}))
        values.update(
            {
                name: value
                for name, value in update.get("secrets", {}).items()
                if name in NOTIFICATION_SCHEMAS.get(provider, {}).get("secrets", ())
            }
        )
        if "enabled" in update:
            values["enabled"] = update["enabled"]
        _update_managed_web_config(
            config_path,
            {
                "action": "update_notification",
                "provider": provider,
                "values": values,
            },
        )
    os.replace(path, path.with_name(f".{WEB_CONFIG_FILENAME}.migrated.bak"))
    return True


def _write_web_overrides(config_path, data):
    path = _overrides_path(config_path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as temporary:
            json.dump(data, temporary, indent=2, sort_keys=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.chmod(temporary_name, 0o600)
        except OSError:
            # Some mounted and Windows filesystems do not expose POSIX modes.
            pass
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _write_config_categories(config_path, categories):
    path = Path(config_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    source = path.read_text(encoding="utf-8")
    _tree, _streamers, category_node = _config_lists(source)
    lines = source.splitlines(keepends=True)
    line_start = sum(
        len(line.encode("utf-8")) for line in lines[: category_node.lineno - 1]
    )
    end_line_start = sum(
        len(line.encode("utf-8")) for line in lines[: category_node.end_lineno - 1]
    )
    start = line_start + category_node.col_offset
    end = end_line_start + category_node.end_col_offset
    indentation = lines[category_node.lineno - 1][
        : len(lines[category_node.lineno - 1])
        - len(lines[category_node.lineno - 1].lstrip())
    ]
    if categories:
        rendered = (
            "[\n"
            + "".join(f"{indentation}    {category!r},\n" for category in categories)
            + f"{indentation}]"
        )
    else:
        rendered = "[]"
    encoded = source.encode("utf-8")
    updated = encoded[:start] + rendered.encode("utf-8") + encoded[end:]

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=False
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.chmod(temporary_name, mode)
        except OSError:
            # Some mounted and Windows filesystems do not expose POSIX modes.
            pass
        try:
            os.replace(temporary_name, path)
        except OSError as error:
            # Docker and Podman reject replacing a directly bind-mounted file
            # with EBUSY. The file itself can still be writable, so fall back
            # to updating that mount in place. Directory mounts continue to
            # use the atomic replacement above.
            if error.errno != errno.EBUSY:
                raise
            with path.open("r+b") as handle:
                handle.seek(0)
                handle.write(updated)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _replace_config_node(config_path, node, rendered):
    path = Path(config_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    start = sum(len(line.encode("utf-8")) for line in lines[: node.lineno - 1])
    start += node.col_offset
    end = sum(len(line.encode("utf-8")) for line in lines[: node.end_lineno - 1])
    end += node.end_col_offset
    encoded = source.encode("utf-8")
    updated = encoded[:start] + rendered.encode("utf-8") + encoded[end:]
    _replace_config_bytes(path, updated, mode)


def _replace_config_bytes(path, updated, mode):
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=str(path.parent), text=False
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            temporary.write(updated)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            os.chmod(temporary_name, mode)
        except OSError:
            pass
        try:
            os.replace(temporary_name, path)
        except OSError as error:
            if error.errno != errno.EBUSY:
                raise
            with path.open("r+b") as handle:
                handle.seek(0)
                handle.write(updated)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _expression(source):
    return ast.parse(source, mode="eval").body


def _set_dict_items(config_path, assignment_name, values):
    for name, rendered in values.items():
        source = Path(config_path).read_text(encoding="utf-8")
        dictionary = _assignment(ast.parse(source), assignment_name)
        if not isinstance(dictionary, ast.Dict):
            raise ConfigEditError(f"{assignment_name} must be a literal dictionary.")
        existing = {
            key.value: index
            for index, key in enumerate(dictionary.keys)
            if isinstance(key, ast.Constant) and isinstance(key.value, str)
        }
        if name in existing:
            _replace_config_node(
                config_path, dictionary.values[existing[name]], rendered
            )
        else:
            _insert_config_item(config_path, dictionary, f"{name!r}: {rendered}")


def _insert_config_item(config_path, container, rendered):
    path = Path(config_path)
    source = path.read_text(encoding="utf-8")
    lines = source.splitlines(keepends=True)
    end = (
        sum(len(line.encode("utf-8")) for line in lines[: container.end_lineno - 1])
        + container.end_col_offset
    )
    encoded = source.encode("utf-8")
    closing = end - 1
    body = encoded[:closing].rstrip()
    comma = b"" if body.endswith((b"{", b"(", b"[", b",")) else b","
    base_indent = lines[container.lineno - 1][
        : len(lines[container.lineno - 1]) - len(lines[container.lineno - 1].lstrip())
    ]
    indentation = base_indent + "    "
    insertion = comma + f"\n{indentation}{rendered},\n{base_indent}".encode("utf-8")
    updated = encoded[:closing] + insertion + encoded[closing:]
    _replace_config_bytes(path, updated, stat.S_IMODE(path.stat().st_mode))


def _call_keyword(call, name):
    return next((item for item in call.keywords if item.arg == name), None)


def _set_call_keywords(call, values):
    for name, rendered in values.items():
        keyword = _call_keyword(call, name)
        if rendered is None:
            if keyword is not None:
                call.keywords.remove(keyword)
            continue
        value = _expression(rendered)
        if keyword is None:
            call.keywords.append(ast.keyword(arg=name, value=value))
        else:
            keyword.value = value


def _write_call_keywords(config_path, find_call, values):
    for name, rendered in values.items():
        source = Path(config_path).read_text(encoding="utf-8")
        call = find_call(ast.parse(source))
        if not isinstance(call, ast.Call):
            raise ConfigEditError("Expected a constructor call in config.py.")
        keyword = _call_keyword(call, name)
        if keyword is not None:
            _replace_config_node(config_path, keyword.value, rendered)
        else:
            _insert_config_item(config_path, call, f"{name}={rendered}")


def _write_streamers(config_path, records, updated_usernames=None):
    if isinstance(updated_usernames, str):
        updated_usernames = {updated_usernames}
    else:
        updated_usernames = set(updated_usernames or ())
    if updated_usernames:
        _ensure_config_import(
            config_path,
            "from TwitchChannelPointsMiner.classes.Chat import ChatPresence",
        )
        _ensure_config_import(
            config_path,
            "from TwitchChannelPointsMiner.classes.entities.Streamer import "
            "Streamer, StreamerSettings",
        )
    source = Path(config_path).read_text(encoding="utf-8")
    tree, streamers, _categories = _config_lists(source)
    existing = {
        _streamer_value(node).lower(): node
        for node in streamers.elts
        if _streamer_value(node) is not None
    }
    rendered_nodes = []
    for record in records:
        username = record["username"]
        node = existing.get(username.lower(), ast.Constant(username))
        if username.lower() in updated_usernames:
            if isinstance(node, ast.Constant):
                node = _expression(f"Streamer({username!r})")
            settings_keyword = _call_keyword(node, "settings")
            if settings_keyword is None or not isinstance(
                settings_keyword.value, ast.Call
            ):
                settings = _expression("StreamerSettings()")
                if settings_keyword is None:
                    node.keywords.append(ast.keyword(arg="settings", value=settings))
                else:
                    settings_keyword.value = settings
            else:
                settings = settings_keyword.value
            _set_call_keywords(
                settings,
                {
                    name: (f"ChatPresence.{value}" if name == "chat" else repr(value))
                    for name, value in record["settings"].items()
                },
            )
        rendered_nodes.append(node)
    streamers.elts = rendered_nodes
    ast.fix_missing_locations(streamers)
    _replace_config_node(
        config_path, _assignment(ast.parse(source), "STREAMERS"), ast.unparse(streamers)
    )


def enable_analytics_dashboard(
    config_path,
    password,
    host="127.0.0.1",
    port=54455,
    refresh=5,
    days_ago=7,
    log_poll_interval=5,
):
    """Turn on the analytics dashboard for an *existing* configuration.

    Used for an explicit, in-the-moment user action - the Windows desktop
    shell's one-time "enable the dashboard?" prompt, or its on-demand
    "Enable dashboard" button, both in windows_launcher.py - rather than the
    silent first-run bootstrap in that module's
    ensure_windows_analytics_defaults(), which only ever touches a config it
    just created from the template. Because this path is a deliberate user
    choice, it goes through the same source-preserving AST edit machinery as
    every other dashboard-driven config change, keeping the rest of the
    user's file - formatting, comments, unrelated settings - untouched.

    Leaves ANALYTICS_CONFIG alone if it is already a literal dict (the user
    has existing analytics settings, just switched off - respect them
    rather than overwriting host/port/password with fresh defaults); only
    replaces it when it is still unset (e.g. `ANALYTICS_CONFIG = None`).
    """
    _set_dict_items(config_path, "MINER_CONFIG", {"enable_analytics": "True"})

    source = Path(config_path).read_text(encoding="utf-8")
    analytics_config_node = _assignment(ast.parse(source), "ANALYTICS_CONFIG")
    if analytics_config_node is None:
        raise ConfigEditError("ANALYTICS_CONFIG assignment not found in config.py.")
    if isinstance(analytics_config_node, ast.Dict):
        return

    rendered = (
        "{\n"
        f"    'host': {host!r},\n"
        f"    'port': {port},\n"
        f"    'refresh': {refresh},\n"
        f"    'days_ago': {days_ago},\n"
        f"    'password': {password!r},\n"
        f"    'log_poll_interval': {log_poll_interval},\n"
        "}"
    )
    _replace_config_node(config_path, analytics_config_node, rendered)


def set_miner_username(config_path, username):
    """Set MINER_CONFIG['username'] - the Twitch account the miner logs in
    as - on an existing configuration.

    Used by the Windows desktop shell's first-run setup prompt (see
    windows_launcher.py), shown when a config's username is still the
    bundled template's placeholder. Twitch account usernames follow the
    same character rules as a streamer username, so STREAMER_RE is reused
    for validation. Writes through the normal AST edit path, preserving
    the rest of the file untouched.
    """
    username = username.strip()
    if not STREAMER_RE.fullmatch(username):
        raise ConfigEditError("Enter a valid Twitch username.")
    _set_dict_items(config_path, "MINER_CONFIG", {"username": repr(username)})
    return username


def _write_logger_settings(config_path, values):
    rendered = {
        name: (
            f"logging.{value}"
            if name in {"console_level", "file_level"}
            else repr(value)
        )
        for name, value in values.items()
    }
    _write_call_keywords(
        config_path,
        lambda tree: _dict_item(_assignment(tree, "MINER_CONFIG"), "logger_settings"),
        rendered,
    )


def _notification_expression(provider, values, existing):
    constructors = {
        "telegram": "Telegram",
        "discord": "Discord",
        "webhook": "Webhook",
        "email": "Email",
        "matrix": "Matrix",
        "pushover": "Pushover",
        "gotify": "Gotify",
        "ntfy": "Ntfy",
    }
    call = (
        existing
        if isinstance(existing, ast.Call)
        else _expression(f"{constructors[provider]}()")
    )
    rendered = {}
    for name, value in values.items():
        if name == "enabled" or value == "":
            continue
        if name == "events":
            rendered[name] = repr(_runtime_notification_events(value))
        else:
            rendered[name] = repr(value)
    _set_call_keywords(call, rendered)
    return call


def _write_notification(config_path, provider, values):
    source = Path(config_path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    logger_settings = _dict_item(_assignment(tree, "MINER_CONFIG"), "logger_settings")
    if not isinstance(logger_settings, ast.Call):
        raise ConfigEditError(
            "MINER_CONFIG['logger_settings'] must be LoggerSettings(...)."
        )
    keyword = _call_keyword(logger_settings, provider)
    existing = keyword.value if keyword is not None else None
    if not isinstance(existing, ast.Call) and values.get("enabled") is not True:
        return
    if values.get("enabled") is False:
        _write_call_keywords(
            config_path,
            lambda tree: _dict_item(
                _assignment(tree, "MINER_CONFIG"), "logger_settings"
            ),
            {provider: "None"},
        )
        return
    else:
        if not isinstance(existing, ast.Call):
            constructor = provider.title()
            _ensure_config_import(
                config_path,
                f"from TwitchChannelPointsMiner.classes.{constructor} import {constructor}",
            )
            rendered = ast.unparse(_notification_expression(provider, values, None))
            _write_call_keywords(
                config_path,
                lambda tree: _dict_item(
                    _assignment(tree, "MINER_CONFIG"), "logger_settings"
                ),
                {provider: rendered},
            )
            return
    rendered_values = {}
    for name, value in values.items():
        if name == "enabled" or value == "":
            continue
        rendered_values[name] = (
            repr(_runtime_notification_events(value))
            if name == "events"
            else repr(value)
        )
    _write_call_keywords(
        config_path,
        lambda tree: (
            _call_keyword(
                _dict_item(_assignment(tree, "MINER_CONFIG"), "logger_settings"),
                provider,
            ).value
        ),
        rendered_values,
    )


def _ensure_config_import(config_path, statement):
    path = Path(config_path)
    source = path.read_text(encoding="utf-8")
    if statement in source:
        return
    lines = source.splitlines(keepends=True)
    insertion = 1 if lines and "coding" in lines[0] else 0
    lines.insert(insertion, statement + "\n")
    _replace_config_bytes(
        path,
        "".join(lines).encode("utf-8"),
        stat.S_IMODE(path.stat().st_mode),
    )


def _validate_streamer_settings(settings):
    if not isinstance(settings, dict) or set(settings) - STREAMER_SETTING_NAMES:
        raise ConfigEditError("Unsupported per-streamer setting.")
    for name in STREAMER_SETTING_NAMES - {"chat", "points_limit"}:
        if name in settings and not isinstance(settings[name], bool):
            raise ConfigEditError(f"{name} must be true or false.")
    if "chat" in settings and settings["chat"] not in CHAT_PRESENCES:
        raise ConfigEditError("Invalid chat presence.")
    points_limit = settings.get("points_limit")
    if points_limit is not None and (
        not isinstance(points_limit, int)
        or isinstance(points_limit, bool)
        or points_limit < 0
    ):
        raise ConfigEditError("Points limit must be a non-negative integer or null.")


def _valid_managed_category(value):
    return (
        isinstance(value, str)
        and 1 <= len(value.strip()) <= 300
        and not any(character in value for character in "\r\n\x00")
    )


def _require_reorder_is_permutation(new_order, current_names, item_label):
    if sorted(map(str.lower, new_order)) != sorted(map(str.lower, current_names)):
        raise ConfigEditError(
            f"{item_label} order must contain every configured {item_label.lower()}."
        )


def _runtime_notification_events(values):
    from TwitchChannelPointsMiner.classes.Settings import Events

    if not isinstance(values, (list, tuple)):
        raise ConfigEditError("events must be a list of valid event names.")
    normalized = []
    for value in values:
        if isinstance(value, Events):
            normalized.append(str(value))
            continue
        if not isinstance(value, str):
            raise ConfigEditError("events must be a list of valid event names.")
        name = value.removeprefix("Events.")
        try:
            normalized.append(str(Events[name]))
        except KeyError as error:
            raise ConfigEditError(f"Unknown notification event: {value}.") from error
    return normalized


def update_managed_web_config(config_path, payload):
    with CONFIG_FILE_MUTEX:
        migrate_web_config(config_path)
        return _update_managed_web_config(config_path, payload)


def _update_managed_web_config(config_path, payload):
    if not isinstance(payload, dict):
        raise ConfigEditError("The configuration update must be a JSON object.")
    action = payload.get("action")
    current = read_managed_web_config(config_path)

    if action in {"add", "remove"}:
        kind = payload.get("kind")
        raw_value = payload.get("value")
        value = raw_value.strip() if isinstance(raw_value, str) else ""
        if action == "add":
            valid = (
                STREAMER_RE.fullmatch(value) is not None
                if kind == "streamers"
                else _valid_managed_category(value)
                if kind == "categories"
                else False
            )
        else:
            valid = bool(value) and kind in {"streamers", "categories"}
        if not valid:
            raise ConfigEditError("Invalid streamer username or category value.")
        items = list(current[kind])
        names = [item["username"] if kind == "streamers" else item for item in items]
        matching = {name.lower() for name in names}
        if action == "add":
            if value.lower() in matching:
                raise ConfigEditError(f"{value} is already configured.")
            items.append(
                {
                    "username": value,
                    "settings": dict(current["streamer_defaults"]),
                }
                if kind == "streamers"
                else value
            )
        else:
            if value.lower() not in matching:
                raise ConfigEditError(f"{value} is not configured.")
            items = [
                item
                for item in items
                if (item["username"] if kind == "streamers" else item).lower()
                != value.lower()
            ]
        if kind == "categories":
            _write_config_categories(config_path, items)
        else:
            _write_streamers(config_path, items)
    elif action == "reorder_categories":
        categories = payload.get("categories")
        if not isinstance(categories, list) or any(
            not _valid_managed_category(item) for item in categories
        ):
            raise ConfigEditError("Invalid category order.")
        _require_reorder_is_permutation(categories, current["categories"], "Category")
        _write_config_categories(config_path, categories)
    elif action == "reorder_streamers":
        usernames = payload.get("usernames")
        if not isinstance(usernames, list) or any(
            not isinstance(item, str) for item in usernames
        ):
            raise ConfigEditError("Invalid streamer order.")
        current_usernames = [item["username"] for item in current["streamers"]]
        _require_reorder_is_permutation(usernames, current_usernames, "Streamer")
        by_username = {
            item["username"].lower(): item["username"] for item in current["streamers"]
        }
        reordered = [
            {"username": by_username[username.lower()]} for username in usernames
        ]
        _write_streamers(config_path, reordered)
    elif action == "update_streamer":
        username = str(payload.get("username", "")).lower().strip()
        settings = payload.get("settings")
        _validate_streamer_settings(settings)
        streamers = list(current["streamers"])
        for streamer in streamers:
            if streamer["username"].lower() == username:
                streamer["settings"].update(settings)
                break
        else:
            raise ConfigEditError("Streamer is not configured.")
        _write_streamers(config_path, streamers, username)
    elif action == "update_category":
        values = payload.get("values") or {}
        allowed = {"limit", "sort", "refresh_interval_hours", "drops_enabled"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ConfigEditError("Unsupported category setting.")
        if "limit" in values and (
            not isinstance(values["limit"], int)
            or isinstance(values["limit"], bool)
            or not 1 <= values["limit"] <= 100
        ):
            raise ConfigEditError("Category limit must be between 1 and 100.")
        if "sort" in values and values["sort"] not in CATEGORY_SORTS:
            raise ConfigEditError("Invalid category sort.")
        if "refresh_interval_hours" in values and (
            not isinstance(values["refresh_interval_hours"], (int, float))
            or isinstance(values["refresh_interval_hours"], bool)
            or not 0 <= values["refresh_interval_hours"] <= 168
        ):
            raise ConfigEditError("Refresh interval must be between 0 and 168 hours.")
        if "drops_enabled" in values and not isinstance(values["drops_enabled"], bool):
            raise ConfigEditError("Drops-only behavior must be true or false.")
        mapping = {
            "limit": "category_limit",
            "sort": "category_sort",
            "refresh_interval_hours": "category_refresh_interval_hours",
            "drops_enabled": "category_drops_enabled",
        }
        rendered = {
            mapping[name]: (f"CategorySort.{value}" if name == "sort" else repr(value))
            for name, value in values.items()
        }
        if "sort" in values:
            _ensure_config_import(
                config_path,
                "from TwitchChannelPointsMiner.classes.Settings import CategorySort",
            )
        _set_dict_items(config_path, "MINE_CONFIG", rendered)
    elif action == "update_sources":
        values = payload.get("values") or {}
        if (
            not isinstance(values, dict)
            or set(values) - SOURCE_NAMES
            or any(not isinstance(value, bool) for value in values.values())
        ):
            raise ConfigEditError("Invalid stream source controls.")
        order = payload.get("order")
        if order is not None and (
            not isinstance(order, list) or sorted(order) != sorted(SOURCE_NAMES)
        ):
            raise ConfigEditError("Invalid stream source order.")
        source_names = SOURCE_ENUM_BY_NAME
        source = Path(config_path).read_text(encoding="utf-8")
        miner = _simple_value(_assignment(ast.parse(source), "MINER_CONFIG")) or {}
        priority = list(
            miner.get("streamer_source_priority")
            or ("STREAMERS", "FOLLOWERS", "CATEGORIES", "BADGES", "WILDCARD_CATEGORIES")
        )
        if order is not None:
            # A single payload carries both enablement and position: rebuild
            # the priority list from the posted order, falling back to each
            # source's currently-saved enabled state for any name this
            # particular payload didn't include in `values` (mirroring how a
            # `values`-only payload is already allowed to be partial).
            currently_enabled = {
                name: source_names[name] in priority for name in SOURCE_NAMES
            }
            priority = [
                source_names[name]
                for name in order
                if values.get(name, currently_enabled[name]) is True
            ]
        else:
            for name, enabled in values.items():
                member = source_names[name]
                if enabled and member not in priority:
                    priority.append(member)
                elif not enabled and member in priority:
                    priority.remove(member)
        _ensure_config_import(
            config_path,
            "from TwitchChannelPointsMiner.classes.Settings import StreamerSource",
        )
        _set_dict_items(
            config_path,
            "MINER_CONFIG",
            {
                "streamer_source_priority": "["
                + ", ".join(f"StreamerSource.{name}" for name in priority)
                + "]"
            },
        )
        mine_values = {}
        if "followers" in values:
            mine_values["followers"] = repr(values["followers"])
        if "badges" in values:
            mine_values["auto_mine_badge_drops"] = repr(values["badges"])
        if "wildcard_categories" in values:
            mine_values["wildcard_categories"] = repr(values["wildcard_categories"])
        if mine_values:
            _set_dict_items(config_path, "MINE_CONFIG", mine_values)
    elif action == "update_logging":
        values = payload.get("values") or {}
        allowed = {"console_level", "file_level", "daily_report", "daily_report_time"}
        if not isinstance(values, dict) or set(values) - allowed:
            raise ConfigEditError("Unsupported logging setting.")
        for name in ("console_level", "file_level"):
            if name in values and values[name] not in LOG_LEVELS:
                raise ConfigEditError("Invalid logging level.")
        if "daily_report" in values and not isinstance(values["daily_report"], bool):
            raise ConfigEditError("Daily report must be true or false.")
        if "daily_report_time" in values and not re.fullmatch(
            r"(?:[01]\d|2[0-3]):[0-5]\d", str(values["daily_report_time"])
        ):
            raise ConfigEditError("Daily report time must use HH:MM.")
        _write_logger_settings(config_path, values)
    elif action == "update_updates":
        values = payload.get("values") or {}
        _validate_update_settings(values)
        miner_values = {}
        if "enabled" in values:
            miner_values["update_check"] = repr(values["enabled"])
        if values.get("startup_only") is True:
            miner_values["update_check_interval_hours"] = 'float("inf")'
        elif "interval_hours" in values:
            miner_values["update_check_interval_hours"] = repr(values["interval_hours"])
        _set_dict_items(config_path, "MINER_CONFIG", miner_values)
    elif action == "update_notification":
        provider = payload.get("provider")
        schema = NOTIFICATION_SCHEMAS.get(provider)
        values = payload.get("values") or {}
        if schema is None or not isinstance(values, dict):
            raise ConfigEditError("Invalid notification provider.")
        values = dict(values)
        allowed = {"enabled", *schema["fields"], *schema["secrets"]}
        if set(values) - allowed or (
            "enabled" in values and not isinstance(values["enabled"], bool)
        ):
            raise ConfigEditError("Unsupported notification setting.")
        for list_name in ("events", "recipients", "tags"):
            if list_name in values and (
                not isinstance(values[list_name], list)
                or any(not isinstance(item, str) for item in values[list_name])
            ):
                raise ConfigEditError(f"{list_name} must be a list of strings.")
        if "events" in values:
            _runtime_notification_events(values["events"])
        for number_name in ("chat_id", "message_thread_id", "port", "priority"):
            if values.get(number_name) == "":
                if number_name == "message_thread_id":
                    values[number_name] = None
                else:
                    values.pop(number_name)
                continue
            if number_name in values:
                if number_name == "message_thread_id" and values[number_name] is None:
                    continue
                if not isinstance(values[number_name], int) or isinstance(
                    values[number_name], bool
                ):
                    raise ConfigEditError(f"{number_name} must be an integer.")
        for bool_name in ("disable_notification", "use_ssl", "starttls"):
            if bool_name in values and not isinstance(values[bool_name], bool):
                raise ConfigEditError(f"{bool_name} must be true or false.")
        list_fields = {"events", "recipients", "tags"}
        number_fields = {"chat_id", "message_thread_id", "port", "priority"}
        bool_fields = {"disable_notification", "use_ssl", "starttls"}
        text_fields = set(schema["fields"]) - list_fields - number_fields - bool_fields
        for text_name in text_fields | set(schema["secrets"]):
            if text_name in values and not isinstance(values[text_name], str):
                raise ConfigEditError(f"{text_name} must be a string.")
        for text_name in text_fields:
            if values.get(text_name) == "":
                values.pop(text_name)
        if "method" in values and values["method"].upper() not in {"GET", "POST"}:
            raise ConfigEditError("Webhook method must be GET or POST.")
        if values.get("use_ssl") is True and values.get("starttls") is True:
            raise ConfigEditError("Email SSL and STARTTLS cannot both be enabled.")
        if values.get("enabled") is True:
            state = current["notifications"][provider]
            available = {
                name
                for name, value in {**state["fields"], **values}.items()
                if value not in (None, "", [])
            }
            available.update(
                name for name, configured in state["secrets"].items() if configured
            )
            available.update(
                name
                for name in schema["secrets"]
                if isinstance(values.get(name), str) and values[name]
            )
            missing = sorted(NOTIFICATION_REQUIRED[provider] - available)
            if missing:
                raise ConfigEditError(f"{provider} requires: {', '.join(missing)}.")
        _write_notification(config_path, provider, values)
    else:
        raise ConfigEditError("Unsupported configuration action.")

    return read_managed_web_config(config_path)


def _validate_update_settings(values):
    allowed = {"enabled", "interval_hours", "startup_only"}
    if not isinstance(values, dict) or set(values) - allowed:
        raise ConfigEditError("Unsupported update-check setting.")
    for name in ("enabled", "startup_only"):
        if name in values and not isinstance(values[name], bool):
            raise ConfigEditError(f"{name} must be true or false.")
    if (
        values.get("startup_only") is not True
        and "interval_hours" in values
        and (
            not isinstance(values["interval_hours"], int)
            or isinstance(values["interval_hours"], bool)
            or values["interval_hours"] < 3
        )
    ):
        raise ConfigEditError(
            "Update-check interval must be a whole number of at least 3 hours."
        )
