import errno
import json
import math
import os
import stat
import threading
from types import SimpleNamespace

import pytest
from flask import Flask

import TwitchChannelPointsMiner.config_editor as config_editor
from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Matrix import Matrix
from TwitchChannelPointsMiner.classes.AnalyticsServer import (
    test_web_notification as send_web_notification_test,
    web_config,
)
from TwitchChannelPointsMiner.classes.Settings import Settings, StreamerSource
from TwitchChannelPointsMiner.classes.WebSocketsPool import WebSocketsPool
from TwitchChannelPointsMiner.classes.entities.PubsubTopic import PubsubTopic
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer
from TwitchChannelPointsMiner.config_editor import (
    ConfigEditError,
    enable_analytics_dashboard,
    load_web_overrides,
    migrate_web_config,
    read_managed_web_config,
    set_miner_username,
    update_managed_web_config,
)
from TwitchChannelPointsMiner.logger import LoggerSettings
from TwitchChannelPointsMiner.runner import _config_digest, _load_config


def write_config(path):
    path.write_text(
        """\
import logging
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Discord import Discord
from TwitchChannelPointsMiner.classes.Settings import CategorySort, StreamerSource
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings
from TwitchChannelPointsMiner.logger import LoggerSettings

MINER_CONFIG = {
    "username": "example",
    "streamer_source_priority": [StreamerSource.STREAMERS, StreamerSource.CATEGORIES],
    "streamer_settings": StreamerSettings(favorite=False, chat=ChatPresence.ONLINE),
    "logger_settings": LoggerSettings(
        console_level=logging.INFO,
        file_level=logging.DEBUG,
        daily_report=False,
        discord=Discord(webhook_api="https://secret.example/hook", events=[]),
    ),
}
STREAMERS = [Streamer("one", settings=StreamerSettings(favorite=True)), "two"]
MINE_CONFIG = {
    "followers": False,
    "categories": ["alpha", "beta"],
    "category_limit": 5,
    "category_sort": CategorySort.VIEWERS_DESC,
    "category_refresh_interval_hours": 3,
    "category_drops_enabled": True,
    "auto_mine_badge_drops": False,
}
ANALYTICS_CONFIG = None
""",
        encoding="utf-8",
    )


def test_managed_web_config_masks_credentials_and_reads_effective_settings(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = read_managed_web_config(config)

    assert [item["username"] for item in result["streamers"]] == ["one", "two"]
    assert result["streamers"][0]["settings"]["favorite"] is True
    assert result["streamers"][1]["settings"]["chat"] == "ONLINE"
    assert result["notifications"]["discord"]["enabled"] is True
    assert result["notifications"]["discord"]["fields"] == {"events": []}
    assert result["notifications"]["discord"]["secrets"] == {"webhook_api": True}
    assert result["notifications"]["discord"]["test_available"] is True
    assert "DROP_CLAIM" in result["notification_event_options"]
    assert "DAILY_REPORT" in result["notification_event_options"]
    assert "secret.example" not in json.dumps(result)
    assert result["updates"] == {
        "enabled": True,
        "interval_hours": 24,
        "startup_only": False,
    }


def test_managed_web_config_updates_release_check_settings(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {
            "action": "update_updates",
            "values": {
                "enabled": False,
                "interval_hours": 12,
                "startup_only": False,
            },
        },
    )

    assert result["updates"] == {
        "enabled": False,
        "interval_hours": 12,
        "startup_only": False,
    }

    loaded = _load_config(config)
    assert loaded.MINER_CONFIG["update_check"] is False
    assert loaded.MINER_CONFIG["update_check_interval_hours"] == 12

    update_managed_web_config(
        config,
        {
            "action": "update_updates",
            "values": {
                "enabled": True,
                "interval_hours": 24,
                "startup_only": True,
            },
        },
    )

    loaded = _load_config(config)
    assert loaded.MINER_CONFIG["update_check"] is True
    assert loaded.MINER_CONFIG["update_check_interval_hours"] == math.inf


def test_managed_web_config_updates_wildcard_category_source(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {"action": "update_sources", "values": {"wildcard_categories": True}},
    )

    assert result["sources"]["wildcard_categories"] is True
    loaded = _load_config(config)
    assert (
        StreamerSource.WILDCARD_CATEGORIES
        in loaded.MINER_CONFIG["streamer_source_priority"]
    )
    assert loaded.MINE_CONFIG["wildcard_categories"] is True

    result = update_managed_web_config(
        config,
        {"action": "update_sources", "values": {"wildcard_categories": False}},
    )

    assert result["sources"]["wildcard_categories"] is False
    loaded = _load_config(config)
    assert (
        StreamerSource.WILDCARD_CATEGORIES
        not in loaded.MINER_CONFIG["streamer_source_priority"]
    )
    assert loaded.MINE_CONFIG["wildcard_categories"] is False


def test_managed_web_config_reorders_streamers_without_touching_settings(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {"action": "reorder_streamers", "usernames": ["two", "one"]},
    )

    assert [item["username"] for item in result["streamers"]] == ["two", "one"]
    one_settings = next(
        item["settings"]
        for item in result["streamers"]
        if item["username"] == "one"
    )
    assert one_settings["favorite"] is True
    loaded = _load_config(config)
    assert [
        getattr(streamer, "username", streamer) for streamer in loaded.STREAMERS
    ] == ["two", "one"]


def test_managed_web_config_rejects_incomplete_streamer_order(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="must contain every configured streamer"):
        update_managed_web_config(
            config,
            {"action": "reorder_streamers", "usernames": ["one"]},
        )


def test_managed_web_config_rejects_streamer_order_with_unknown_username(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="must contain every configured streamer"):
        update_managed_web_config(
            config,
            {"action": "reorder_streamers", "usernames": ["one", "two", "extra"]},
        )


def test_managed_web_config_update_sources_enables_and_reorders_in_one_call(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {
            "action": "update_sources",
            "values": {"wildcard_categories": True},
            "order": [
                "wildcard_categories",
                "streamers",
                "followers",
                "categories",
                "badges",
            ],
        },
    )

    assert result["sources"]["wildcard_categories"] is True
    assert result["source_order"][0] == "wildcard_categories"
    loaded = _load_config(config)
    priority = loaded.MINER_CONFIG["streamer_source_priority"]
    assert priority[0] == StreamerSource.WILDCARD_CATEGORIES
    assert loaded.MINE_CONFIG["wildcard_categories"] is True


def test_managed_web_config_update_sources_without_order_keeps_legacy_behavior(
    tmp_path,
):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {"action": "update_sources", "values": {"followers": True}},
    )

    # Baseline priority is [STREAMERS, CATEGORIES]; the legacy no-order path
    # appends the newly-enabled source at the end rather than rebuilding
    # from a posted order.
    assert result["sources"]["followers"] is True
    assert result["source_order"].index("followers") == 2


def test_managed_web_config_rejects_incomplete_source_order(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="Invalid stream source order"):
        update_managed_web_config(
            config,
            {
                "action": "update_sources",
                "values": {},
                "order": ["streamers", "followers"],
            },
        )


def test_managed_web_config_reads_math_inf_as_startup_only(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    source = config.read_text(encoding="utf-8")
    source = source.replace("import logging\n", "import logging\nimport math\n", 1)
    source = source.replace(
        '    "username": "example",\n',
        '    "username": "example",\n' '    "update_check_interval_hours": math.inf,\n',
        1,
    )
    config.write_text(source, encoding="utf-8")

    result = read_managed_web_config(config)

    assert result["updates"] == {
        "enabled": True,
        "interval_hours": 24,
        "startup_only": True,
    }


@pytest.mark.parametrize(
    "values",
    [
        {"enabled": "yes"},
        {"startup_only": "yes"},
        {"interval_hours": 2},
        {"interval_hours": 3.5},
        {"unknown": True},
    ],
)
def test_managed_web_config_rejects_invalid_release_check_settings(tmp_path, values):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError):
        update_managed_web_config(
            config,
            {"action": "update_updates", "values": values},
        )


def test_managed_web_config_ignores_interval_when_checking_only_at_startup(
    tmp_path,
):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {
            "action": "update_updates",
            "values": {"interval_hours": 0, "startup_only": True},
        },
    )

    assert result["updates"]["startup_only"] is True
    loaded = _load_config(config)
    assert loaded.MINER_CONFIG["update_check_interval_hours"] == math.inf


def test_managed_web_config_updates_lists_settings_and_permissions(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    config.chmod(0o640)
    original_source = config.read_text(encoding="utf-8")

    update_managed_web_config(
        config, {"action": "remove", "kind": "streamers", "value": "two"}
    )
    update_managed_web_config(
        config,
        {"action": "reorder_categories", "categories": ["beta", "alpha"]},
    )
    result = update_managed_web_config(
        config,
        {
            "action": "update_streamer",
            "username": "one",
            "settings": {
                "favorite": False,
                "make_predictions": False,
                "follow_raid": False,
                "claim_drops": True,
                "claim_moments": True,
                "chat": "NEVER",
                "points_limit": 25000,
            },
        },
    )

    assert [item["username"] for item in result["streamers"]] == ["one"]
    assert result["categories"] == ["beta", "alpha"]
    assert result["streamers"][0]["settings"]["points_limit"] == 25000
    assert not (tmp_path / "web-config.json").exists()
    assert config.read_text(encoding="utf-8") != original_source
    loaded = _load_config(config)
    assert loaded.MINE_CONFIG["categories"] == ["beta", "alpha"]
    assert stat.S_IMODE(config.stat().st_mode) == 0o640


def test_managed_web_config_updates_categories_when_replace_is_ebusy(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.py"
    write_config(config)

    real_replace = os.replace

    def maybe_busy(source, destination):
        if destination == config:
            raise OSError(errno.EBUSY, "Device or resource busy")
        return real_replace(source, destination)

    monkeypatch.setattr("TwitchChannelPointsMiner.config_editor.os.replace", maybe_busy)

    result = update_managed_web_config(
        config,
        {"action": "reorder_categories", "categories": ["beta", "alpha"]},
    )

    assert result["categories"] == ["beta", "alpha"]
    monkeypatch.undo()
    loaded = _load_config(config)
    assert loaded.MINE_CONFIG["categories"] == ["beta", "alpha"]


def test_managed_web_config_updates_categories_after_non_ascii_source(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    source = config.read_text(encoding="utf-8").replace(
        "MINE_CONFIG = {", "# Pokémon\nMINE_CONFIG = {"
    )
    config.write_text(source, encoding="utf-8")

    update_managed_web_config(
        config,
        {"action": "reorder_categories", "categories": ["beta", "alpha"]},
    )

    assert "# Pokémon" in config.read_text(encoding="utf-8")
    loaded = _load_config(config)
    assert loaded.MINE_CONFIG["categories"] == ["beta", "alpha"]


def test_managed_web_config_updates_categories_when_chmod_is_unsupported(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.py"
    write_config(config)
    real_chmod = os.chmod

    def unsupported_chmod(path, *args, **kwargs):
        if str(path).startswith(str(tmp_path / ".config.py.")):
            raise OSError("unsupported")
        return real_chmod(path, *args, **kwargs)

    monkeypatch.setattr(
        "TwitchChannelPointsMiner.config_editor.os.chmod", unsupported_chmod
    )

    result = update_managed_web_config(
        config,
        {"action": "reorder_categories", "categories": ["beta", "alpha"]},
    )

    assert result["categories"] == ["beta", "alpha"]
    monkeypatch.undo()
    loaded = _load_config(config)
    assert loaded.MINE_CONFIG["categories"] == ["beta", "alpha"]


def test_managed_categories_support_display_names_urls_and_forced_streamers(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    categories = [
        "Call of Duty: Warzone|streamer_name",
        "https://www.twitch.tv/directory/category/pokemon-go?filter=drops",
        "beta",
        "alpha",
    ]
    update_managed_web_config(
        config,
        {
            "action": "add",
            "kind": "categories",
            "value": categories[0],
        },
    )
    update_managed_web_config(
        config,
        {
            "action": "add",
            "kind": "categories",
            "value": categories[1],
        },
    )

    result = update_managed_web_config(
        config, {"action": "reorder_categories", "categories": categories}
    )

    assert result["categories"] == categories


def test_invalid_category_error_uses_supported_value_terminology(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="category value"):
        update_managed_web_config(
            config,
            {"action": "add", "kind": "categories", "value": "bad\ncategory"},
        )


@pytest.mark.parametrize("kind", ["streamers", "categories"])
@pytest.mark.parametrize("action", ["add", "remove"])
@pytest.mark.parametrize("value", [None, 123, True, [], {}])
def test_add_remove_rejects_non_string_values(tmp_path, action, kind, value):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(
        ConfigEditError, match="Invalid streamer username or category value"
    ):
        update_managed_web_config(
            config,
            {"action": action, "kind": kind, "value": value},
        )


def test_notification_secrets_are_write_only_and_blank_values_are_not_saved(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "discord",
            "values": {"enabled": True, "events": ["DROP_CLAIM"], "webhook_api": ""},
        },
    )

    assert config.read_text(encoding="utf-8").count("https://secret.example/hook") == 1
    assert result["notifications"]["discord"]["secrets"]["webhook_api"] is True
    assert "secret.example" not in json.dumps(result)


def test_blank_notification_text_fields_preserve_existing_values(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "matrix",
            "values": {
                "enabled": False,
                "username": "miner",
                "homeserver": "matrix.example",
                "room_id": "!room:matrix.example",
            },
        },
    )

    update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "matrix",
            "values": {"username": "", "homeserver": "", "room_id": ""},
        },
    )

    assert read_managed_web_config(config)["notifications"]["matrix"]["fields"] == {}


def test_enabling_new_notification_requires_credentials(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="telegram requires: token"):
        update_managed_web_config(
            config,
            {
                "action": "update_notification",
                "provider": "telegram",
                "values": {"enabled": True, "chat_id": 123, "events": []},
            },
        )


def test_ntfy_dashboard_accepts_tags_and_uses_default_server(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "ntfy",
            "values": {
                "enabled": True,
                "topic": "private-topic",
                "tags": ["twitch", "gift"],
                "events": ["DROP_CLAIM"],
            },
        },
    )
    loaded = _load_config(config)
    ntfy = loaded.MINER_CONFIG["logger_settings"].ntfy

    assert result["notifications"]["ntfy"]["test_available"] is True
    assert result["notifications"]["ntfy"]["secrets"] == {
        "topic": True,
        "token": False,
    }
    assert ntfy.server_url == "https://ntfy.sh"
    assert ntfy.tags == ["twitch", "gift"]
    assert ntfy.events == ["DROP_CLAIM"]


def test_notification_events_are_validated_and_normalized_for_runtime(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "discord",
            "values": {"enabled": True, "events": ["DROP_CLAIM"]},
        },
    )

    loaded = _load_config(config)

    assert loaded.MINER_CONFIG["logger_settings"].discord.events == ["DROP_CLAIM"]
    with pytest.raises(ConfigEditError, match="Unknown notification event"):
        update_managed_web_config(
            config,
            {
                "action": "update_notification",
                "provider": "discord",
                "values": {"events": ["NOT_A_REAL_EVENT"]},
            },
        )


def test_new_notification_secret_is_applied_but_never_returned(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    result = update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "telegram",
            "values": {
                "enabled": True,
                "chat_id": 123,
                "token": "super-secret-token",
                "message_thread_id": 987,
                "disable_notification": True,
                "events": ["DROP_CLAIM"],
            },
        },
    )
    loaded = _load_config(config)
    assert (
        "super-secret-token"
        in loaded.MINER_CONFIG["logger_settings"].telegram.telegram_api
    )
    assert result["notifications"]["telegram"]["fields"]["message_thread_id"] == 987
    assert result["notifications"]["telegram"]["secrets"] == {"token": True}
    assert "super-secret-token" not in json.dumps(result)


def test_telegram_message_thread_id_must_be_integer(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="message_thread_id must be an integer"):
        update_managed_web_config(
            config,
            {
                "action": "update_notification",
                "provider": "telegram",
                "values": {"message_thread_id": "topic"},
            },
        )


def test_telegram_message_thread_id_can_be_cleared(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    result = update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "telegram",
            "values": {
                "enabled": True,
                "chat_id": 123,
                "token": "super-secret-token",
                "message_thread_id": None,
                "events": ["DROP_CLAIM"],
            },
        },
    )

    assert result["notifications"]["telegram"]["fields"]["message_thread_id"] is None


def test_other_notification_numeric_fields_cannot_be_none(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match="port must be an integer"):
        update_managed_web_config(
            config,
            {
                "action": "update_notification",
                "provider": "email",
                "values": {"port": None},
            },
        )


def test_secret_only_update_does_not_enable_disabled_notification(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "gotify",
            "values": {"endpoint": "https://gotify.example/message?token=secret"},
        },
    )

    loaded = _load_config(config)

    assert loaded.MINER_CONFIG["logger_settings"].gotify is None


def test_secret_only_update_keeps_enabled_notification_enabled(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "discord",
            "values": {"webhook_api": "https://new.example/hook"},
        },
    )

    loaded = _load_config(config)

    assert loaded.MINER_CONFIG["logger_settings"].discord.webhook_api == (
        "https://new.example/hook"
    )


def test_canonical_config_edits_build_effective_runtime_config(tmp_path):
    config_path = tmp_path / "config.py"
    write_config(config_path)
    update_managed_web_config(
        config_path,
        {"action": "remove", "kind": "streamers", "value": "two"},
    )
    update_managed_web_config(
        config_path,
        {
            "action": "update_streamer",
            "username": "one",
            "settings": {"favorite": False, "chat": "NEVER"},
        },
    )
    update_managed_web_config(
        config_path,
        {
            "action": "update_category",
            "values": {"limit": 9, "sort": "RANDOM", "drops_enabled": False},
        },
    )
    module = _load_config(config_path)

    assert [streamer.username for streamer in module.STREAMERS] == ["one"]
    assert module.STREAMERS[0].settings.favorite is False
    assert module.STREAMERS[0].settings.chat is ChatPresence.NEVER
    assert module.MINE_CONFIG["category_limit"] == 9
    assert module.MINE_CONFIG["category_sort"] == "RANDOM"
    assert module.MINE_CONFIG["category_drops_enabled"] is False


def test_canonical_config_edits_add_required_symbol_imports(tmp_path):
    config = tmp_path / "config.py"
    config.write_text(
        """\
MINER_CONFIG = {"streamer_settings": {}}
STREAMERS = ["one"]
MINE_CONFIG = {"categories": [], "category_sort": "ORDER"}
ANALYTICS_CONFIG = None
""",
        encoding="utf-8",
    )

    update_managed_web_config(
        config,
        {
            "action": "update_streamer",
            "username": "one",
            "settings": {"favorite": True, "chat": "NEVER"},
        },
    )
    update_managed_web_config(
        config,
        {"action": "update_category", "values": {"sort": "RANDOM"}},
    )
    update_managed_web_config(
        config,
        {"action": "update_sources", "values": {"followers": True}},
    )

    source = config.read_text(encoding="utf-8")
    assert "from TwitchChannelPointsMiner.classes.Chat import ChatPresence" in source
    assert (
        "from TwitchChannelPointsMiner.classes.entities.Streamer import "
        "Streamer, StreamerSettings"
    ) in source
    assert (
        "from TwitchChannelPointsMiner.classes.Settings import CategorySort" in source
    )
    assert (
        "from TwitchChannelPointsMiner.classes.Settings import StreamerSource" in source
    )
    namespace = {}
    exec(compile(source, config.name, "exec"), namespace)
    assert namespace["STREAMERS"][0].settings.favorite is True
    assert namespace["STREAMERS"][0].settings.chat is ChatPresence.NEVER
    assert namespace["MINE_CONFIG"]["category_sort"] == "RANDOM"
    assert any(
        source.name == "FOLLOWERS"
        for source in namespace["MINER_CONFIG"]["streamer_source_priority"]
    )


def test_enable_analytics_dashboard_preserves_formatting_and_comments(tmp_path):
    # Used by the Windows desktop shell's one-time "enable the dashboard?"
    # consent prompt and its on-demand "Enable dashboard" button - a
    # deliberate, in-the-moment user action, so (unlike the shell's silent
    # first-run bootstrap) it must go through this source-preserving path
    # and leave the rest of the file - comments included - untouched.
    config = tmp_path / "config.py"
    config.write_text(
        """\
# my custom header comment
MINER_CONFIG = {
    "username": "someone",  # inline comment
    "enable_analytics": False,
}
STREAMERS = []
MINE_CONFIG = {}
ANALYTICS_CONFIG = None
""",
        encoding="utf-8",
    )

    enable_analytics_dashboard(config, password="generated-secret")

    source = config.read_text(encoding="utf-8")
    assert "# my custom header comment" in source
    assert '"username": "someone",  # inline comment' in source

    module = _load_config(config)
    assert module.MINER_CONFIG["enable_analytics"] is True
    assert module.ANALYTICS_CONFIG == {
        "host": "127.0.0.1",
        "port": 54455,
        "refresh": 5,
        "days_ago": 7,
        "password": "generated-secret",
        "log_poll_interval": 5,
    }


def test_enable_analytics_dashboard_respects_existing_analytics_config(tmp_path):
    # A user may have pre-configured analytics settings while leaving it
    # switched off; enabling it must not clobber those with fresh defaults
    # and an unrelated generated password.
    from TwitchChannelPointsMiner.config_migration import CONFIG_VERSION

    config = tmp_path / "config.py"
    # CONFIG_VERSION matches the current schema so _load_config's own,
    # unrelated schema migration (which legitimately backfills missing
    # ANALYTICS_CONFIG keys like refresh/days_ago on an *old*-schema config)
    # is a verified no-op - isolating enable_analytics_dashboard's own
    # behavior, which is what this test is about.
    config.write_text(
        f"""\
CONFIG_VERSION = {CONFIG_VERSION}
MINER_CONFIG = {{
    "enable_analytics": False,
}}
STREAMERS = []
MINE_CONFIG = {{}}
ANALYTICS_CONFIG = {{
    "host": "127.0.0.1",
    "port": 9999,
    "password": "already-set-by-user",
}}
""",
        encoding="utf-8",
    )

    enable_analytics_dashboard(config, password="should-not-be-used")

    module = _load_config(config)
    assert module.MINER_CONFIG["enable_analytics"] is True
    assert module.ANALYTICS_CONFIG == {
        "host": "127.0.0.1",
        "port": 9999,
        "password": "already-set-by-user",
    }


def test_enable_analytics_dashboard_requires_analytics_config_assignment(tmp_path):
    config = tmp_path / "config.py"
    config.write_text(
        'MINER_CONFIG = {"enable_analytics": False}\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigEditError, match="ANALYTICS_CONFIG"):
        enable_analytics_dashboard(config, password="secret")


def test_set_miner_username_preserves_formatting_and_comments(tmp_path):
    # Used by the Windows desktop shell's first-run setup panel, which
    # collects a real Twitch username before mining starts (see
    # windows_launcher.py's _needs_username/WindowApi.submit_username) - a
    # deliberate, in-the-moment user action, so this must preserve the rest
    # of the file, comments included, like every other dashboard-driven edit.
    config = tmp_path / "config.py"
    config.write_text(
        """\
# my custom header comment
MINER_CONFIG = {
    "username": "your-twitch-username",
    "enable_analytics": False,  # inline comment
}
STREAMERS = []
MINE_CONFIG = {}
ANALYTICS_CONFIG = None
""",
        encoding="utf-8",
    )

    result = set_miner_username(config, "  real_twitch_user  ")

    assert result == "real_twitch_user"
    source = config.read_text(encoding="utf-8")
    assert "# my custom header comment" in source
    assert '"enable_analytics": False,  # inline comment' in source

    module = _load_config(config)
    assert module.MINER_CONFIG["username"] == "real_twitch_user"


def test_set_miner_username_rejects_invalid_characters(tmp_path):
    config = tmp_path / "config.py"
    config.write_text('MINER_CONFIG = {"username": "your-twitch-username"}\n', encoding="utf-8")

    with pytest.raises(ConfigEditError, match="valid Twitch username"):
        set_miner_username(config, "not a valid username!!!")

    assert (
        config.read_text(encoding="utf-8")
        == 'MINER_CONFIG = {"username": "your-twitch-username"}\n'
    )


def test_legacy_streamers_migrate_in_one_source_rewrite(tmp_path, monkeypatch):
    config = tmp_path / "config.py"
    write_config(config)
    records = [
        {"username": "one", "settings": {"favorite": False, "chat": "NEVER"}},
        {"username": "two", "settings": {"favorite": True, "chat": "ALWAYS"}},
    ]
    (tmp_path / "web-config.json").write_text(
        json.dumps({"streamers": records}), encoding="utf-8"
    )
    calls = []
    write_streamers = config_editor._write_streamers

    def record_write(config_path, migrated_records, updated_usernames=None):
        calls.append((migrated_records, updated_usernames))
        return write_streamers(config_path, migrated_records, updated_usernames)

    monkeypatch.setattr(config_editor, "_write_streamers", record_write)

    migrate_web_config(config)

    assert len(calls) == 1
    assert calls[0][1] == {"one", "two"}
    loaded = _load_config(config)
    assert loaded.STREAMERS[0].settings.chat is ChatPresence.NEVER
    assert loaded.STREAMERS[1].settings.favorite is True
    assert loaded.STREAMERS[1].settings.chat is ChatPresence.ALWAYS


def test_remove_streamers_unsubscribes_only_purely_explicit_streamers():
    explicit = Streamer("explicit", explicitly_configured=True)
    category = Streamer("category", from_category=True, explicitly_configured=True)
    websocket_pool = SimpleNamespace(removed=[])
    websocket_pool.remove_streamer_topics = websocket_pool.removed.append
    miner = SimpleNamespace(
        running=True,
        ws_pool=websocket_pool,
        config_reload_lock=threading.Lock(),
        streamers=[explicit, category],
        original_streamers=[100, 200],
    )

    TwitchChannelPointsMiner.remove_streamers(miner, ["explicit", "category"])

    assert miner.streamers == [category]
    assert miner.original_streamers == [200]
    assert category.explicitly_configured is False
    assert websocket_pool.removed == [explicit]


def test_websocket_pool_removes_and_unlistens_streamer_topics():
    target = Streamer("target")
    retained = Streamer("retained")
    target_topic = SimpleNamespace(streamer=target)
    retained_topic = SimpleNamespace(streamer=retained)

    class RecordingLock:
        active = False

        def __enter__(self):
            self.active = True

        def __exit__(self, *_args):
            self.active = False

    topic_lock = RecordingLock()
    removed = []

    def unlisten(topic, auth_token):
        assert topic_lock.active is False
        removed.append((topic, auth_token))

    websocket = SimpleNamespace(
        topics=[target_topic, retained_topic],
        pending_topics=[target_topic],
        is_opened=True,
        removed=removed,
    )
    websocket.unlisten = unlisten
    untouched_websocket = SimpleNamespace(
        topics=[retained_topic],
        pending_topics=[],
        is_opened=True,
        unlisten=lambda _topic, _token: pytest.fail("unexpected unlisten"),
    )
    token_requests = []

    def get_auth_token():
        token_requests.append(True)
        return "oauth-token"

    pool = SimpleNamespace(
        ws=[websocket, untouched_websocket],
        topic_lock=topic_lock,
        twitch=SimpleNamespace(
            twitch_login=SimpleNamespace(get_auth_token=get_auth_token)
        ),
    )

    WebSocketsPool.remove_streamer_topics(pool, target)

    assert websocket.topics == [retained_topic]
    assert websocket.pending_topics == []
    assert websocket.removed == [(target_topic, "oauth-token")]
    assert token_requests == [True]


def test_websocket_pool_listens_after_releasing_topic_lock():
    class RecordingLock:
        depth = 0

        def __enter__(self):
            self.depth += 1

        def __exit__(self, *_args):
            self.depth -= 1

    topic_lock = RecordingLock()
    listened = []

    def listen(topic, token):
        assert topic_lock.depth == 0
        listened.append((topic, token))

    websocket = SimpleNamespace(
        topics=[],
        pending_topics=[],
        is_opened=True,
        listen=listen,
        unlisten=lambda _topic, _token: pytest.fail("unexpected unlisten"),
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(get_auth_token=lambda: "oauth-token")
        ),
        [],
        {},
    )
    pool.ws = [websocket]
    pool.topic_lock = topic_lock
    topic = SimpleNamespace(streamer=Streamer("target"))

    pool.submit(topic)

    assert listened == [(topic, "oauth-token")]


def test_websocket_pool_does_not_relisten_an_existing_topic():
    topic = SimpleNamespace(streamer=Streamer("target"))
    websocket = SimpleNamespace(
        topics=[topic],
        pending_topics=[],
        is_opened=True,
        listen=lambda _topic, _token: pytest.fail("unexpected duplicate listen"),
        unlisten=lambda _topic, _token: pytest.fail("unexpected unlisten"),
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(
                get_auth_token=lambda: pytest.fail("unexpected token request")
            )
        ),
        [],
        {},
    )
    pool.ws = [websocket]

    pool.submit(topic)

    assert websocket.topics == [topic]
    assert websocket.pending_topics == []


def test_websocket_pool_reuses_capacity_in_older_socket():
    topic = SimpleNamespace(streamer=Streamer("target"))
    available = SimpleNamespace(
        index=0,
        topics=[object() for _index in range(49)],
        pending_topics=[],
        is_opened=False,
    )
    full = SimpleNamespace(
        index=1,
        topics=[object() for _index in range(50)],
        pending_topics=[],
        is_opened=False,
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(get_auth_token=lambda: "oauth-token")
        ),
        [],
        {},
    )
    pool.ws = [available, full]

    pool.submit(topic)

    assert len(pool.ws) == 2
    assert available.topics[-1] is topic
    assert available.pending_topics == [topic]
    assert topic not in full.topics


def test_websocket_pool_finds_existing_topic_on_older_socket():
    streamer = Streamer("target")
    streamer.channel_id = 100
    existing_topic = PubsubTopic("video-playback-by-id", streamer=streamer)
    duplicate_topic = PubsubTopic("video-playback-by-id", streamer=streamer)
    existing = SimpleNamespace(
        index=0,
        topics=[existing_topic],
        pending_topics=[],
        is_opened=True,
        listen=lambda _topic, _token: pytest.fail("unexpected duplicate listen"),
    )
    available = SimpleNamespace(
        index=1,
        topics=[],
        pending_topics=[],
        is_opened=False,
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(
                get_auth_token=lambda: pytest.fail("unexpected token request")
            )
        ),
        [],
        {},
    )
    pool.ws = [existing, available]

    pool.submit(duplicate_topic)

    assert existing.topics == [existing_topic]
    assert available.topics == []


def test_websocket_pool_retires_empty_trailing_sockets():
    target = Streamer("target")
    retained = Streamer("retained")
    closed = []
    first = SimpleNamespace(
        index=0,
        topics=[SimpleNamespace(streamer=retained)],
        pending_topics=[],
        is_opened=True,
    )
    trailing = SimpleNamespace(
        index=1,
        topics=[SimpleNamespace(streamer=target)],
        pending_topics=[],
        is_opened=True,
        forced_close=False,
        close=lambda: closed.append(True),
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(
                get_auth_token=lambda: pytest.fail("unexpected token request")
            )
        ),
        [],
        {},
    )
    pool.ws = [first, trailing]

    pool.remove_streamer_topics(target)

    assert pool.ws == [first]
    assert trailing.forced_close is True
    assert closed == [True]


def test_websocket_selection_and_registration_share_one_lock_scope():
    class RecordingLock:
        depth = 0
        max_depth = 0

        def __enter__(self):
            self.depth += 1
            self.max_depth = max(self.max_depth, self.depth)

        def __exit__(self, *_args):
            self.depth -= 1

    topic_lock = RecordingLock()
    websocket = SimpleNamespace(
        index=0,
        topics=[object() for _index in range(49)],
        pending_topics=[],
        is_opened=False,
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(get_auth_token=lambda: "oauth-token")
        ),
        [],
        {},
    )
    pool.ws = [websocket]
    pool.topic_lock = topic_lock
    topic = SimpleNamespace(streamer=Streamer("target"))

    pool.submit(topic)

    assert topic_lock.max_depth == 1
    assert len(websocket.topics) == 50
    assert websocket.pending_topics == [topic]


def test_websocket_on_open_drains_pending_topics_outside_lock(monkeypatch):
    class RecordingLock:
        depth = 0

        def __enter__(self):
            self.depth += 1

        def __exit__(self, *_args):
            self.depth -= 1

    class ImmediateThread:
        daemon = False

        def __init__(self, target):
            self.target = target

        def start(self):
            self.target()

    topic_lock = RecordingLock()
    topic = SimpleNamespace(streamer=Streamer("target"))
    listened = []
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(get_auth_token=lambda: "oauth-token")
        ),
        [],
        {},
    )
    pool.topic_lock = topic_lock

    def listen(opened_topic, token):
        assert topic_lock.depth == 0
        listened.append((opened_topic, token))

    websocket = SimpleNamespace(
        parent_pool=pool,
        topics=[topic],
        pending_topics=[topic],
        is_opened=False,
        is_closed=True,
        ping=lambda: None,
        listen=listen,
        unlisten=lambda _topic, _token: pytest.fail("unexpected unlisten"),
    )
    pool.ws = [websocket]
    monkeypatch.setattr(
        "TwitchChannelPointsMiner.classes.WebSocketsPool.Thread", ImmediateThread
    )

    WebSocketsPool.on_open(websocket)

    assert websocket.pending_topics == []
    assert listened == [(topic, "oauth-token")]


@pytest.mark.parametrize("replacement_opened", [True, False])
def test_websocket_reconnection_replays_topics_after_releasing_lock(
    monkeypatch, replacement_opened
):
    class RecordingLock:
        depth = 0

        def __enter__(self):
            self.depth += 1

        def __exit__(self, *_args):
            self.depth -= 1

    topic_lock = RecordingLock()
    topic = SimpleNamespace(streamer=Streamer("target"))
    listened = []

    def listen(replayed_topic, token):
        assert topic_lock.depth == 0
        listened.append((replayed_topic, token))

    new_websocket = SimpleNamespace(
        topics=[],
        pending_topics=[],
        is_opened=replacement_opened,
        listen=listen,
        unlisten=lambda _topic, _token: pytest.fail("unexpected unlisten"),
    )
    pool = WebSocketsPool(
        SimpleNamespace(
            twitch_login=SimpleNamespace(get_auth_token=lambda: "oauth-token")
        ),
        [],
        {},
    )
    pool.topic_lock = topic_lock
    old_websocket = SimpleNamespace(
        index=0,
        parent_pool=pool,
        is_reconnecting=False,
        forced_close=False,
        topics=[topic],
        is_closed=False,
        keep_running=True,
    )
    pool.ws = [old_websocket]

    def new_websocket_while_locked(_self, _index):
        assert topic_lock.depth > 0
        return new_websocket

    monkeypatch.setattr(
        WebSocketsPool, "_WebSocketsPool__new", new_websocket_while_locked
    )
    monkeypatch.setattr(
        WebSocketsPool, "_WebSocketsPool__start", lambda _self, _index: None
    )
    monkeypatch.setattr(
        "TwitchChannelPointsMiner.classes.WebSocketsPool.time.sleep",
        lambda _seconds: None,
    )
    monkeypatch.setattr(
        "TwitchChannelPointsMiner.classes.WebSocketsPool.internet_connection_available",
        lambda: True,
    )

    WebSocketsPool.handle_reconnection(old_websocket)

    assert new_websocket.topics == [topic]
    if replacement_opened:
        assert new_websocket.pending_topics == []
        assert listened == [(topic, "oauth-token")]
    else:
        assert new_websocket.pending_topics == [topic]
        assert listened == []


def test_config_endpoint_never_returns_notification_secrets(tmp_path, monkeypatch):
    config = tmp_path / "config.py"
    write_config(config)
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)
    app = Flask(__name__)

    with app.test_request_context("/config", method="GET"):
        response = web_config()

    assert response.status_code == 200
    assert b"secret.example" not in response.data
    assert response.get_json()["notifications"]["discord"]["secrets"] == {
        "webhook_api": True
    }


def test_config_endpoint_does_not_expose_filesystem_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)

    def fail_read(_path):
        raise OSError("/private/config/path: permission denied")

    monkeypatch.setitem(web_config.__globals__, "read_managed_web_config", fail_read)
    app = Flask(__name__)

    with app.test_request_context("/config", method="GET"):
        response = web_config()

    assert response.status_code == 500
    assert response.get_json() == {"error": "Unable to access configuration."}
    assert b"/private/config/path" not in response.data


def test_notification_test_endpoint_uses_saved_provider(tmp_path, monkeypatch):
    config = tmp_path / "config.py"
    write_config(config)
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)
    sent = []
    notification = SimpleNamespace(events=[])
    notification.send = lambda message, event: sent.append((message, event))
    loaded = SimpleNamespace(
        MINER_CONFIG={"logger_settings": SimpleNamespace(discord=notification)}
    )
    monkeypatch.setattr(
        "TwitchChannelPointsMiner.runner._load_config", lambda _path: loaded
    )
    app = Flask(__name__)

    with app.test_request_context("/config/notifications/discord/test", method="POST"):
        response = send_web_notification_test("discord")

    assert response.status_code == 200
    assert response.get_json() == {"message": "Test notification sent."}
    assert len(sent) == 1
    assert "test notification" in sent[0][0].lower()


def test_notification_test_endpoint_requires_complete_enabled_provider(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.py"
    write_config(config)
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)
    loaded = SimpleNamespace(
        MINER_CONFIG={"logger_settings": SimpleNamespace(telegram=None)}
    )
    monkeypatch.setattr(
        "TwitchChannelPointsMiner.runner._load_config", lambda _path: loaded
    )
    app = Flask(__name__)

    with app.test_request_context("/config/notifications/telegram/test", method="POST"):
        response = send_web_notification_test("telegram")

    assert response.status_code == 409
    assert response.get_json() == {
        "error": "Configure and enable this notification first."
    }


def test_notification_test_endpoint_sanitizes_config_load_failures(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)

    def fail_load(_path):
        raise RuntimeError("Unable to parse /private/config/web-config.json")

    monkeypatch.setattr("TwitchChannelPointsMiner.runner._load_config", fail_load)
    app = Flask(__name__)

    with app.test_request_context("/config/notifications/discord/test", method="POST"):
        response = send_web_notification_test("discord")

    assert response.status_code == 500
    assert response.get_json() == {"error": "Unable to send test notification."}
    assert b"/private/config" not in response.data


def test_notification_test_endpoint_returns_sanitized_delivery_failure(
    tmp_path, monkeypatch
):
    config = tmp_path / "config.py"
    write_config(config)
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)
    notification = SimpleNamespace(
        events=[],
        send=lambda _message, _event: (
            False,
            "SMTP authentication failed. Check the username and password.",
        ),
    )
    loaded = SimpleNamespace(
        MINER_CONFIG={"logger_settings": SimpleNamespace(discord=notification)}
    )
    monkeypatch.setattr(
        "TwitchChannelPointsMiner.runner._load_config", lambda _path: loaded
    )
    app = Flask(__name__)

    with app.test_request_context("/config/notifications/discord/test", method="POST"):
        response = send_web_notification_test("discord")

    assert response.status_code == 502
    assert response.get_json() == {
        "error": "SMTP authentication failed. Check the username and password."
    }


def test_runner_loads_and_digests_dashboard_overrides(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    original_digest = _config_digest(config)
    update_managed_web_config(
        config,
        {"action": "remove", "kind": "streamers", "value": "two"},
    )

    loaded = _load_config(config)

    assert [streamer.username for streamer in loaded.STREAMERS] == ["one"]
    assert _config_digest(config) != original_digest


def test_web_override_read_errors_do_not_expose_paths(tmp_path, monkeypatch):
    config = tmp_path / "config.py"
    override = tmp_path / "web-config.json"
    override.write_text("{}", encoding="utf-8")
    original_read_text = type(override).read_text

    def fail_override_read(path, *args, **kwargs):
        if path == override:
            raise OSError(f"permission denied: {override}")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(override), "read_text", fail_override_read)

    with pytest.raises(OSError) as raised:
        load_web_overrides(config)

    assert str(override) in str(raised.value)


def test_invalid_web_override_json_returns_sanitized_error(tmp_path):
    config = tmp_path / "config.py"
    (tmp_path / "web-config.json").write_text("{broken", encoding="utf-8")

    with pytest.raises(
        ConfigEditError, match=r"web-config\.json contains invalid JSON"
    ) as raised:
        load_web_overrides(config)

    assert str(tmp_path) not in str(raised.value)


def test_unknown_notification_secrets_are_not_exposed(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)
    (tmp_path / "web-config.json").write_text(
        json.dumps(
            {
                "notifications": {
                    "gotify": {
                        "secrets": {
                            "endpoint": "https://example.invalid",
                            "unexpected_secret": "do-not-expose",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    migrate_web_config(config)
    result = read_managed_web_config(config)

    assert result["notifications"]["gotify"]["secrets"] == {"endpoint": False}
    assert "unexpected_secret" not in json.dumps(result)


def test_empty_notification_number_is_treated_as_unset(tmp_path):
    config = tmp_path / "config.py"
    write_config(config)

    update_managed_web_config(
        config,
        {
            "action": "update_notification",
            "provider": "email",
            "values": {"enabled": False, "port": ""},
        },
    )

    assert "email=" not in config.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("provider", "values", "message"),
    [
        ("webhook", {"method": None}, "method must be a string"),
        ("matrix", {"room_id": None}, "room_id must be a string"),
        ("discord", {"webhook_api": 123}, "webhook_api must be a string"),
    ],
)
def test_notification_text_fields_require_strings(tmp_path, provider, values, message):
    config = tmp_path / "config.py"
    write_config(config)

    with pytest.raises(ConfigEditError, match=message):
        update_managed_web_config(
            config,
            {
                "action": "update_notification",
                "provider": provider,
                "values": values,
            },
        )


@pytest.mark.parametrize(
    "streamers",
    [None, [None], [{}], [{"username": None}], [{"username": "bad name"}]],
)
def test_malformed_managed_streamers_fail_cleanly(tmp_path, streamers):
    config = tmp_path / "config.py"
    write_config(config)
    (tmp_path / "web-config.json").write_text(
        json.dumps({"streamers": streamers}), encoding="utf-8"
    )

    with pytest.raises(ConfigEditError, match="(?i)managed streamer"):
        migrate_web_config(config)


@pytest.mark.parametrize("categories", ["alpha", {"alpha": True}, [None]])
def test_malformed_managed_categories_fail_cleanly(tmp_path, categories):
    config = tmp_path / "config.py"
    write_config(config)
    (tmp_path / "web-config.json").write_text(
        json.dumps({"categories": categories}), encoding="utf-8"
    )

    with pytest.raises(ConfigEditError, match="Managed categories"):
        migrate_web_config(config)


@pytest.mark.parametrize(
    "sources",
    [None, [], {"followers": "yes"}, {"unknown_source": True}],
)
def test_malformed_managed_sources_fail_cleanly(tmp_path, sources):
    config = tmp_path / "config.py"
    write_config(config)
    (tmp_path / "web-config.json").write_text(
        json.dumps({"sources": sources}), encoding="utf-8"
    )

    with pytest.raises(ConfigEditError, match="(?i)stream source"):
        migrate_web_config(config)


@pytest.mark.parametrize(
    "logging_overrides",
    [
        None,
        [],
        {"unknown": True},
        {"console_level": None},
        {"file_level": "VERBOSE"},
        {"daily_report": "yes"},
        {"daily_report_time": "25:00"},
    ],
)
def test_malformed_managed_logging_fails_cleanly(tmp_path, logging_overrides):
    config = tmp_path / "config.py"
    write_config(config)
    (tmp_path / "web-config.json").write_text(
        json.dumps({"logging": logging_overrides}), encoding="utf-8"
    )

    with pytest.raises(ConfigEditError, match="(?i)log|daily report"):
        migrate_web_config(config)


@pytest.mark.parametrize(
    "notifications",
    [
        None,
        [],
        {"discord": None},
        {"unknown": {}},
        {"discord": {"enabled": "yes"}},
        {"discord": {"fields": []}},
        {"discord": {"secrets": []}},
    ],
)
def test_malformed_managed_notifications_fail_cleanly(tmp_path, notifications):
    config = tmp_path / "config.py"
    write_config(config)
    (tmp_path / "web-config.json").write_text(
        json.dumps({"notifications": notifications}), encoding="utf-8"
    )

    with pytest.raises(ConfigEditError, match="(?i)notification"):
        migrate_web_config(config)
