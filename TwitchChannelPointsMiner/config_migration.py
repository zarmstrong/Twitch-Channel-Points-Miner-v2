# -*- coding: utf-8 -*-

"""Convert a conventional user-owned run.py into a declarative config.py."""

import ast
import builtins
import errno
import hashlib
import os
import shutil
import stat
import tempfile
from pathlib import Path

CONFIG_VERSION = 8
STREAMER_SETTINGS_DEFAULTS = (
    ("make_predictions", "True"),
    ("follow_raid", "True"),
    ("claim_drops", "True"),
    ("claim_moments", "True"),
    ("watch_streak", "True"),
    ("favorite", "False"),
    ("points_limit", "None"),
    ("community_goals", "False"),
    ("bet", "None"),
    ("chat", "None"),
)
LOGGER_SETTINGS_DEFAULTS = (
    ("save", "True"),
    ("less", "False"),
    ("console_level", "logging.INFO"),
    ("console_username", "False"),
    ("time_zone", "None"),
    ("date_format", '"dd/mm/yy"'),
    ("file_level", "logging.DEBUG"),
    ("emoji", "True"),
    ("colored", "False"),
    ("color_palette", "ColorPalette()"),
    ("auto_clear", "True"),
    ("log_retention_days", "7"),
    ("daily_report", "False"),
    ("daily_report_time", '"00:00"'),
    ("username", "None"),
)
LOGGER_NOTIFICATION_SETTINGS = (
    "telegram",
    "discord",
    "webhook",
    "matrix",
    "pushover",
    "gotify",
    "email",
    "ntfy",
)
LOGGER_NOTIFICATION_TEMPLATES = {
    "telegram": 'Telegram(chat_id=123456789, token="TOKEN", events=[])',
    "discord": 'Discord(webhook_api="https://discord.com/api/webhooks/...", events=[])',
    "webhook": 'Webhook(endpoint="https://example.com/webhook", method="POST", events=[])',
    "matrix": 'Matrix(username="USER", password="PASSWORD", homeserver="matrix.org", room_id="ROOM", events=[])',
    "pushover": 'Pushover(userkey="USER_KEY", token="TOKEN", priority=0, sound="pushover", events=[])',
    "gotify": 'Gotify(endpoint="https://example.com/message?token=TOKEN", priority=0, events=[])',
    "email": 'Email(host="smtp.example.com", port=587, sender="miner@example.com", recipients=["you@example.com"], events=[])',
    "ntfy": 'Ntfy(topic="YOUR_NTFY_TOPIC", events=[])',
}
BET_SETTINGS_DEFAULTS = (
    ("strategy", "None"),
    ("percentage", "None"),
    ("percentage_gap", "None"),
    ("max_points", "None"),
    ("minimum_points", "None"),
    ("stealth_mode", "None"),
    ("filter_condition", "None"),
    ("delay", "None"),
    ("delay_mode", "None"),
)
CONFIG_PRIORITY_ADDITIONS = ("Priority.FAVORITE",)
CONFIG_STREAMER_SOURCE_ADDITIONS = (
    "StreamerSource.STREAMERS",
    "StreamerSource.FOLLOWERS",
    "StreamerSource.CATEGORIES",
    "StreamerSource.BADGES",
    "StreamerSource.WILDCARD_CATEGORIES",
)
MINER_CONFIG_DEFAULTS = (
    ("claim_drops_startup", "False"),
    ("enable_analytics", "False"),
    ("disable_ssl_cert_verification", "False"),
    ("disable_at_in_nickname", "False"),
    ("update_check", "True"),
    ("update_check_interval_hours", "24"),
    ("streams_watched", "2"),
)
MINE_CONFIG_DEFAULTS = (
    ("blacklist", "[]"),
    ("followers", "False"),
    ("followers_order", '"ASC"'),
    ("categories", "[]"),
    ("category_drops_enabled", "True"),
    ("category_limit", "30"),
    ("category_sort", '"VIEWERS_DESC"'),
    ("category_campaign_order", '"ORDER"'),
    ("category_chat", "None"),
    ("category_log_level", "logging.INFO"),
    ("drop_item_art", "False"),
    ("print_open_drop_campaigns_on_load", "False"),
    ("scrape_drop_progress_on_load", "False"),
    ("log_drop_checks", "False"),
    ("track_category_streamer_points", "False"),
    ("category_refresh_interval_hours", "6"),
    ("drop_progress_stall_minutes", "10"),
    ("drop_pick_stickiness_minutes", "15"),
    ("drop_badge_catalog", "True"),
    ("drop_badge_refresh_interval_hours", "1"),
    ("auto_mine_badge_drops", "False"),
    ("badge_drop_streamer_limit", "1"),
    ("wildcard_categories", "False"),
    ("wildcard_category_limit", "10"),
    ("wildcard_category_streamer_limit", "1"),
    ("wildcard_category_pin_active", "True"),
)
ANALYTICS_CONFIG_DEFAULTS = (
    ("host", '"127.0.0.1"'),
    ("port", "5000"),
    ("refresh", "5"),
    ("days_ago", "7"),
    ("password", "None"),
    ("log_poll_interval", "5"),
)


class ConfigMigrationError(ValueError):
    pass


def _assignment_value(tree, name):
    for node in tree.body:
        targets = node.targets if isinstance(node, ast.Assign) else []
        if isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(
            isinstance(target, ast.Name) and target.id == name for target in targets
        ):
            return node.value
    return None


def _config_dict(tree):
    value = _resolve_value(tree, _assignment_value(tree, "MINER_CONFIG"))
    return value if isinstance(value, ast.Dict) else None


def _dict_value(node, name):
    if not isinstance(node, ast.Dict):
        return None
    for key, value in zip(node.keys, node.values):
        if isinstance(key, ast.Constant) and key.value == name:
            return value
    return None


def _dict_names(node):
    return {
        key.value
        for key in node.keys
        if isinstance(key, ast.Constant) and isinstance(key.value, str)
    }


def _dict_key_value(tree, key):
    resolved = _resolve_value(tree, key)
    if isinstance(resolved, ast.Constant) and isinstance(resolved.value, str):
        return resolved.value
    return None


def _dict_has_literal_string_keys(node):
    return isinstance(node, ast.Dict) and all(
        isinstance(key, ast.Constant) and isinstance(key.value, str)
        for key in node.keys
    )


def _call_keyword(node, name):
    if not isinstance(node, ast.Call):
        return None
    for keyword in node.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _resolve_value(tree, node):
    seen = set()
    while isinstance(node, ast.Name) and node.id not in seen:
        seen.add(node.id)
        resolved = _assignment_value(tree, node.id)
        if resolved is None:
            break
        node = resolved
    return node


def _missing_call_defaults(call, call_name, defaults):
    if (
        not isinstance(call, ast.Call)
        or _call_name(call.func) != call_name
        or any(keyword.arg is None for keyword in call.keywords)
    ):
        return []
    existing = {keyword.arg for keyword in call.keywords}
    return [f"{name}={value}" for name, value in defaults if name not in existing]


def _commented_call_defaults(source, line_offsets, call, names):
    if not isinstance(call, ast.Call) or any(
        keyword.arg is None for keyword in call.keywords
    ):
        return []
    existing = {keyword.arg for keyword in call.keywords}
    missing = [name for name in names if name not in existing]
    if not missing:
        return []

    closing_offset = _source_offset(
        line_offsets, call.end_lineno, call.end_col_offset - 1
    )
    closing_line_start = line_offsets[call.end_lineno - 1]
    closing_prefix = source[closing_line_start:closing_offset]
    if closing_prefix.strip():
        # Keep compact calls compact. Adding line comments before their closing
        # delimiter can comment out surrounding syntax, especially when the
        # call is nested in a single-line dictionary.
        return []

    candidates = [*call.args, *call.keywords]
    if candidates:
        first = candidates[0]
        indent = source[
            line_offsets[first.lineno - 1] : _source_offset(
                line_offsets, first.lineno, first.col_offset
            )
        ]
    else:
        call_line_start = line_offsets[call.lineno - 1]
        indent = source[call_line_start : call_line_start + call.col_offset] + "    "
    return [
        (
            closing_line_start,
            "".join(
                f"{indent}# {name}={LOGGER_NOTIFICATION_TEMPLATES[name]},\n"
                for name in missing
            ),
        )
    ]


def _name_is_defined(tree, name):
    for node in tree.body:
        if isinstance(node, ast.Import):
            if any(
                (alias.asname or alias.name.split(".")[0]) == name
                for alias in node.names
            ):
                return True
        elif isinstance(node, ast.ImportFrom):
            if any((alias.asname or alias.name) == name for alias in node.names):
                return True
    return False


def _import_offset(source, line_offsets, tree):
    imports = [
        node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    if imports:
        node = imports[-1]
        return _source_offset(line_offsets, node.end_lineno, node.end_col_offset)
    version = _assignment_value(tree, "CONFIG_VERSION")
    if version is not None:
        return line_offsets[version.end_lineno]
    first_line_end = source.find("\n") + 1
    return first_line_end if source.startswith(("# -*- coding:", "# coding:")) else 0


def _enum_list_additions(
    source,
    tree,
    node,
    additions,
    enum_name,
    import_statement,
    required_imports,
):
    existing_members = {
        item.attr
        for item in node.elts
        if isinstance(item, ast.Attribute) and isinstance(item.attr, str)
    }
    prefix = next(
        (
            _source(source, item.value)
            for item in node.elts
            if isinstance(item, ast.Attribute)
        ),
        None,
    )
    if prefix is None:
        prefix = enum_name
        if (
            not _name_is_defined(tree, enum_name)
            and import_statement not in required_imports
        ):
            required_imports.append(import_statement)
    return [
        f"{prefix}.{addition.rsplit('.', 1)[-1]}"
        for addition in additions
        if addition.rsplit(".", 1)[-1] not in existing_members
    ]


def _line_start_offsets(source):
    offsets = []
    offset = 0
    for line in source.splitlines(keepends=True):
        offsets.append(offset)
        offset += len(line)
    if not offsets or offset == len(source):
        offsets.append(offset)
    return offsets


def _source_offset(line_offsets, lineno, col_offset):
    return line_offsets[lineno - 1] + col_offset


def _dict_entry_removal(source, line_offsets, tree, node, name):
    """Return a source edit removing a statically resolvable dictionary entry."""
    index = next(
        (
            index
            for index, key in enumerate(node.keys)
            if _dict_key_value(tree, key) == name
        ),
        None,
    )
    if index is None:
        return None

    key = node.keys[index]
    value = node.values[index]
    start = _source_offset(line_offsets, key.lineno, key.col_offset)
    if index + 1 < len(node.keys):
        next_key = node.keys[index + 1]
        end = _source_offset(line_offsets, next_key.lineno, next_key.col_offset)
        return start, end, ""

    end = _source_offset(line_offsets, value.end_lineno, value.end_col_offset)
    closing = _source_offset(line_offsets, node.end_lineno, node.end_col_offset - 1)
    trailing = source[end:closing]
    comma = trailing.find(",")
    if comma >= 0:
        return start, end + comma + 1, ""
    if index:
        previous = node.values[index - 1]
        previous_end = _source_offset(
            line_offsets, previous.end_lineno, previous.end_col_offset
        )
        separator = source[previous_end:start].rfind(",")
        if separator >= 0:
            start = previous_end + separator
    return start, end, ""


def _apply_edits(source, edits):
    migrated = source
    normalized = [
        edit if len(edit) == 3 else (edit[0], edit[0], edit[1]) for edit in edits
    ]
    for start, end, replacement in sorted(normalized, reverse=True):
        migrated = migrated[:start] + replacement + migrated[end:]
    return migrated


def _without_miner_password(source, source_name="config.py"):
    tree = ast.parse(source, filename=source_name)
    config = _config_dict(tree)
    if config is None:
        return source
    edit = _dict_entry_removal(
        source, _line_start_offsets(source), tree, config, "password"
    )
    return _apply_edits(source, [edit]) if edit else source


def _closing_line_insertion(source, line_offsets, node, lines):
    closing_offset = _source_offset(
        line_offsets, node.end_lineno, node.end_col_offset - 1
    )
    closing_line_start = line_offsets[node.end_lineno - 1]
    closing_prefix = source[closing_line_start:closing_offset]
    if isinstance(node, ast.Dict):
        candidates = node.keys
        final_candidates = node.values
    else:
        candidates = [
            *getattr(node, "args", []),
            *getattr(node, "keywords", []),
            *getattr(node, "elts", []),
        ]
        final_candidates = candidates
    if closing_prefix.strip():
        trimmed = closing_prefix.rstrip()
        if candidates and trimmed.endswith(","):
            separator = "" if closing_prefix.endswith((" ", "\t")) else " "
        else:
            separator = ", " if candidates else ""
        return [(closing_offset, separator + ", ".join(lines))]

    if candidates:
        indent = source[
            line_offsets[candidates[0].lineno - 1] : _source_offset(
                line_offsets, candidates[0].lineno, candidates[0].col_offset
            )
        ]
    else:
        indent = closing_prefix + "    "
    insertions = [(closing_line_start, "".join(f"{indent}{line},\n" for line in lines))]
    if final_candidates:
        last = final_candidates[-1]
        last_end = _source_offset(line_offsets, last.end_lineno, last.end_col_offset)
        if not source[last_end:closing_offset].lstrip().startswith(","):
            insertions.append((last_end, ","))
    return insertions


def _config_version(tree):
    value = _assignment_value(tree, "CONFIG_VERSION")
    if value is None:
        return 0
    if not isinstance(value, ast.Constant) or not isinstance(value.value, int):
        raise ConfigMigrationError("CONFIG_VERSION must be an integer")
    return value.value


def migrate_config_source(source, source_name="config.py"):
    try:
        tree = ast.parse(source, filename=source_name)
    except SyntaxError as error:
        raise ConfigMigrationError(f"Cannot parse {source_name}: {error}") from error

    version = _config_version(tree)
    if version > CONFIG_VERSION:
        raise ConfigMigrationError(
            f"{source_name} uses unsupported CONFIG_VERSION {version}; "
            f"this release supports up to {CONFIG_VERSION}"
        )
    config = _config_dict(tree)
    if config is None:
        raise ConfigMigrationError("MINER_CONFIG must be a dictionary")

    line_offsets = _line_start_offsets(source)
    edits = []
    password_removal = _dict_entry_removal(
        source, line_offsets, tree, config, "password"
    )
    if password_removal:
        edits.append(password_removal)
    if version == CONFIG_VERSION:
        migrated = _apply_edits(source, edits)
        return migrated, version, version
    config_names = _dict_names(config)
    safe_config = _dict_has_literal_string_keys(config)
    missing_miner_options = (
        [
            f"{name!r}: {value}"
            for name, value in MINER_CONFIG_DEFAULTS
            if name not in config_names
        ]
        if safe_config
        else []
    )
    required_imports = []
    if safe_config and "priority" not in config_names:
        missing_miner_options.append(
            "'priority': [Priority.STREAK, Priority.DROPS, Priority.ORDER, "
            "Priority.FAVORITE]"
        )
        if not _name_is_defined(tree, "Priority"):
            required_imports.append(
                "from TwitchChannelPointsMiner.classes.Settings import Priority"
            )
    if safe_config and "streamer_source_priority" not in config_names:
        missing_miner_options.append(
            "'streamer_source_priority': [StreamerSource.STREAMERS, "
            "StreamerSource.FOLLOWERS, StreamerSource.CATEGORIES, "
            "StreamerSource.BADGES, StreamerSource.WILDCARD_CATEGORIES]"
        )
        if not _name_is_defined(tree, "StreamerSource"):
            required_imports.append(
                "from TwitchChannelPointsMiner.classes.Settings import StreamerSource"
            )
    if missing_miner_options:
        edits.extend(
            _closing_line_insertion(source, line_offsets, config, missing_miner_options)
        )
    streamer_settings = _resolve_value(tree, _dict_value(config, "streamer_settings"))
    missing = _missing_call_defaults(
        streamer_settings, "StreamerSettings", STREAMER_SETTINGS_DEFAULTS
    )
    if missing:
        edits.extend(
            _closing_line_insertion(source, line_offsets, streamer_settings, missing)
        )

    bet_settings = _resolve_value(tree, _call_keyword(streamer_settings, "bet"))
    if bet_settings is not None:
        missing = _missing_call_defaults(
            bet_settings, "BetSettings", BET_SETTINGS_DEFAULTS
        )
        if missing:
            edits.extend(
                _closing_line_insertion(source, line_offsets, bet_settings, missing)
            )

    for logger_settings in _find_calls(tree, "LoggerSettings"):
        logger_names = {
            keyword.arg
            for keyword in logger_settings.keywords
            if keyword.arg is not None
        }
        email_import = "from TwitchChannelPointsMiner.classes.Email import Email"
        if (
            "email" not in logger_names
            and not _name_is_defined(tree, "Email")
            and email_import not in required_imports
        ):
            required_imports.append(email_import)
        missing = _missing_call_defaults(
            logger_settings, "LoggerSettings", LOGGER_SETTINGS_DEFAULTS
        )
        if missing:
            if (
                not _name_is_defined(tree, "logging")
                and "import logging" not in required_imports
            ):
                required_imports.append("import logging")
            color_palette_import = (
                "from TwitchChannelPointsMiner.logger import ColorPalette"
            )
            if (
                not _name_is_defined(tree, "ColorPalette")
                and color_palette_import not in required_imports
            ):
                required_imports.append(color_palette_import)
            edits.extend(
                _closing_line_insertion(source, line_offsets, logger_settings, missing)
            )
        edits.extend(
            _commented_call_defaults(
                source,
                line_offsets,
                logger_settings,
                LOGGER_NOTIFICATION_SETTINGS,
            )
        )

    priority = _resolve_value(tree, _dict_value(config, "priority"))
    if isinstance(priority, (ast.List, ast.Tuple)):
        missing = _enum_list_additions(
            source,
            tree,
            priority,
            CONFIG_PRIORITY_ADDITIONS,
            "Priority",
            "from TwitchChannelPointsMiner.classes.Settings import Priority",
            required_imports,
        )
        if missing:
            edits.extend(
                _closing_line_insertion(source, line_offsets, priority, missing)
            )

    source_priority = _resolve_value(
        tree, _dict_value(config, "streamer_source_priority")
    )
    if isinstance(source_priority, (ast.List, ast.Tuple)):
        missing = _enum_list_additions(
            source,
            tree,
            source_priority,
            CONFIG_STREAMER_SOURCE_ADDITIONS,
            "StreamerSource",
            "from TwitchChannelPointsMiner.classes.Settings import StreamerSource",
            required_imports,
        )
        if missing:
            edits.extend(
                _closing_line_insertion(source, line_offsets, source_priority, missing)
            )

    mine_config = _resolve_value(tree, _assignment_value(tree, "MINE_CONFIG"))
    if isinstance(mine_config, ast.Dict):
        mine_names = _dict_names(mine_config)
        missing_mine_options = (
            [
                f"{name!r}: {value}"
                for name, value in MINE_CONFIG_DEFAULTS
                if name not in mine_names
            ]
            if _dict_has_literal_string_keys(mine_config)
            else []
        )
        if missing_mine_options:
            if (
                "category_log_level" not in mine_names
                and not _name_is_defined(tree, "logging")
                and "import logging" not in required_imports
            ):
                required_imports.append("import logging")
            edits.extend(
                _closing_line_insertion(
                    source, line_offsets, mine_config, missing_mine_options
                )
            )

    analytics_config = _resolve_value(tree, _assignment_value(tree, "ANALYTICS_CONFIG"))
    if _dict_has_literal_string_keys(analytics_config):
        missing_analytics_options = [
            f"{name!r}: {value}"
            for name, value in ANALYTICS_CONFIG_DEFAULTS
            if name not in _dict_names(analytics_config)
        ]
        if missing_analytics_options:
            edits.extend(
                _closing_line_insertion(
                    source,
                    line_offsets,
                    analytics_config,
                    missing_analytics_options,
                )
            )

    if required_imports:
        import_offset = _import_offset(source, line_offsets, tree)
        prefix = "\n" if import_offset and source[import_offset - 1] != "\n" else ""
        edits.append((import_offset, prefix + "\n".join(required_imports) + "\n"))

    version_node = _assignment_value(tree, "CONFIG_VERSION")
    if version_node is None:
        first_line_end = source.find("\n") + 1
        version_offset = (
            first_line_end if source.startswith(("# -*- coding:", "# coding:")) else 0
        )
        edits.append(
            (version_offset, version_offset, f"CONFIG_VERSION = {CONFIG_VERSION}\n")
        )
    else:
        start = _source_offset(
            line_offsets, version_node.lineno, version_node.col_offset
        )
        end = _source_offset(
            line_offsets, version_node.end_lineno, version_node.end_col_offset
        )
        edits.append((start, end, str(CONFIG_VERSION)))

    migrated = _apply_edits(source, edits)
    try:
        ast.parse(migrated, filename=source_name)
    except SyntaxError as error:
        raise ConfigMigrationError(
            f"Migration generated invalid {source_name}: {error}"
        ) from error
    return migrated, version, CONFIG_VERSION


def migrate_config(config_path):
    path = Path(config_path)
    if path.is_symlink():
        raise ConfigMigrationError(
            f"Refusing to migrate symlinked configuration {path}"
        )
    current_source = path.read_text(encoding="utf-8")
    source = current_source
    recovery_backup = None
    try:
        migrated, old_version, new_version = migrate_config_source(source, path.name)
    except ConfigMigrationError as error:
        if not isinstance(error.__cause__, SyntaxError):
            raise
        backups = [
            backup
            for backup in sorted(path.parent.glob(f"{path.name}.v*.bak"))
            if not backup.is_symlink() and backup.is_file()
        ]
        if not backups:
            raise
        recovery_backup = backups[-1]
        source = recovery_backup.read_text(encoding="utf-8")
        migrated, old_version, new_version = migrate_config_source(source, path.name)
    if migrated == current_source:
        return False

    backup = path.with_name(f"{path.name}.v{old_version}.bak")
    reuse_backup = False
    if recovery_backup is None:
        if backup.is_symlink():
            raise ConfigMigrationError(f"Refusing to use symlinked backup {backup}")
        if backup.exists():
            if not backup.is_file():
                raise ConfigMigrationError(f"Backup path is not a file: {backup}")
            sanitized_source = _without_miner_password(current_source, path.name)
            backup_source = backup.read_text(encoding="utf-8")
            if backup_source == current_source and backup_source != sanitized_source:
                backup.write_text(sanitized_source, encoding="utf-8")
            elif backup_source != sanitized_source:
                raise ConfigMigrationError(
                    f"Refusing to overwrite existing {backup}: its contents do "
                    "not match the current configuration"
                )
            reuse_backup = True

    backup_created = False
    if recovery_backup is None and not reuse_backup:
        sanitized_source = _without_miner_password(current_source, path.name)
        backup.write_text(sanitized_source, encoding="utf-8")
        shutil.copystat(path, backup)
        backup_created = True
    mode = stat.S_IMODE(path.stat().st_mode)
    descriptor = None
    temporary = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".migrating", dir=path.parent
        )
        temporary = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(migrated)
        os.chmod(temporary, mode)
        try:
            os.replace(temporary, path)
        except OSError as error:
            # Docker and Podman reject replacing a directly bind-mounted file
            # with EBUSY. The file itself can still be writable, so fall back
            # to updating that mount in place. Directory mounts continue to
            # use the atomic replacement above.
            if error.errno != errno.EBUSY:
                raise
            with path.open("r+", encoding="utf-8") as handle:
                handle.seek(0)
                handle.write(migrated)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            temporary.unlink()
            temporary = None
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        if backup_created:
            backup.unlink(missing_ok=True)
        raise
    return True


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _find_calls(tree, name):
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node.func) == name
    ]


def _source(source, node):
    value = ast.get_source_segment(source, node)
    if value is None:
        raise ConfigMigrationError("Unable to preserve a configuration expression")
    return value


def _is_runner_import(node):
    return (
        isinstance(node, ast.ImportFrom)
        and node.module == "TwitchChannelPointsMiner"
        and any(alias.name == "TwitchChannelPointsMiner" for alias in node.names)
    )


def _render_dict(
    source, call, positional_names=(), ignore_args=False, default_entries=()
):
    if not ignore_args and len(call.args) > len(positional_names):
        raise ConfigMigrationError(
            f"Too many positional arguments in {_call_name(call.func)}()"
        )
    entries = (
        []
        if ignore_args
        else [
            (name, _source(source, value))
            for name, value in zip(positional_names, call.args)
        ]
    )
    for keyword in call.keywords:
        if keyword.arg is None:
            raise ConfigMigrationError("Expanded **kwargs cannot be converted safely")
        entries.append((keyword.arg, _source(source, keyword.value)))
    existing_names = {name for name, _ in entries}
    entries.extend(
        (name, value) for name, value in default_entries if name not in existing_names
    )
    if not entries:
        return "{}"
    body = "\n".join(f"    {name!r}: {value}," for name, value in entries)
    return "{\n" + body + "\n}"


def _undefined_names(source, source_name):
    tree = ast.parse(source, filename=source_name)
    loaded = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load)
    }
    defined = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del))
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            defined.update(
                alias.asname or alias.name.split(".")[0] for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            defined.update(alias.asname or alias.name for alias in node.names)
    return sorted(loaded - defined - set(dir(builtins)))


def convert_runner_source(source, source_name="run.py"):
    try:
        tree = ast.parse(source, filename=source_name)
    except SyntaxError as error:
        raise ConfigMigrationError(f"Cannot parse {source_name}: {error}") from error

    constructors = _find_calls(tree, "TwitchChannelPointsMiner")
    mines = _find_calls(tree, "mine")
    analytics = _find_calls(tree, "analytics")
    if len(constructors) != 1 or len(mines) != 1 or len(analytics) > 1:
        raise ConfigMigrationError(
            "Expected exactly one TwitchChannelPointsMiner() and mine() call, "
            "and at most one analytics() call"
        )

    mine = mines[0]
    if len(mine.args) > 1:
        raise ConfigMigrationError("mine() has more than one positional argument")
    streamers = _source(source, mine.args[0]) if mine.args else "[]"
    imports = [
        _source(source, node)
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        and not _is_runner_import(node)
    ]
    has_streamer_source_import = any(
        isinstance(node, ast.ImportFrom)
        and node.module == "TwitchChannelPointsMiner.classes.Settings"
        and any(
            alias.name == "StreamerSource" and alias.asname is None
            for alias in node.names
        )
        for node in tree.body
    )
    if not has_streamer_source_import:
        imports.append(
            "from TwitchChannelPointsMiner.classes.Settings import StreamerSource"
        )
    output = [
        "# -*- coding: utf-8 -*-",
        f"# Automatically converted from {source_name}; review before editing.",
        "",
        *imports,
        "",
        "MINER_CONFIG = "
        + _render_dict(
            source,
            constructors[0],
            ("username",),
            default_entries=(
                (
                    "streamer_source_priority",
                    "[StreamerSource.STREAMERS, StreamerSource.FOLLOWERS, "
                    "StreamerSource.CATEGORIES, StreamerSource.BADGES, "
                    "StreamerSource.WILDCARD_CATEGORIES]",
                ),
            ),
        ),
        "",
        f"STREAMERS = {streamers}",
        "",
        f"MINE_CONFIG = {_render_dict(source, mine, ignore_args=True)}",
        "",
        "ANALYTICS_CONFIG = "
        + (_render_dict(source, analytics[0]) if analytics else "None"),
        "",
    ]
    converted = "\n".join(output)
    undefined = _undefined_names(converted, source_name)
    if undefined:
        names = ", ".join(undefined)
        raise ConfigMigrationError(
            f"Cannot convert {source_name}: configuration references names that "
            f"are not imported: {names}"
        )
    return converted


def convert_runner(runner_path, config_path):
    runner = Path(runner_path)
    config = Path(config_path)
    backup = runner.with_name(runner.name + ".bak")
    if config.exists():
        raise ConfigMigrationError(f"Refusing to overwrite existing {config}")
    if backup.exists():
        raise ConfigMigrationError(f"Refusing to overwrite existing {backup}")
    converted = convert_runner_source(
        runner.read_text(encoding="utf-8"), source_name=runner.name
    )
    config.parent.mkdir(parents=True, exist_ok=True)
    temporary = config.with_name(config.name + ".migrating")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
            config_file.write(converted)
        os.replace(temporary, config)
        os.chmod(config, 0o600)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    marker = config.with_name(".converted-from-run-py")
    try:
        digest = hashlib.sha256(runner.read_bytes()).hexdigest()
        marker.write_text(
            f"source={runner.resolve()}\nsha256={digest}\n", encoding="utf-8"
        )
        runner.rename(backup)
    except Exception:
        marker.unlink(missing_ok=True)
        config.unlink(missing_ok=True)
        raise
    return config
