import json
import stat

import pytest

from TwitchChannelPointsMiner.TwitchChannelPointsMiner import _migrate_analytics_data
from TwitchChannelPointsMiner.classes.Settings import Settings
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer
from TwitchChannelPointsMiner.data_migration import (
    ANALYTICS_DATA_VERSION,
    DataMigrationError,
    migrate_analytics_directory,
)


def test_migrate_analytics_directory_versions_existing_files(tmp_path):
    streamer_file = tmp_path / "channel.json"
    drops_file = tmp_path / "drops_by_category.json"
    streamer_payload = {"series": [{"x": 1, "y": 10}]}
    drops_payload = {"drops": [{"drop_id": "one"}]}
    streamer_file.write_text(json.dumps(streamer_payload), encoding="utf-8")
    drops_file.write_text(json.dumps(drops_payload), encoding="utf-8")
    streamer_file.chmod(0o640)

    assert migrate_analytics_directory(tmp_path) == 2

    assert json.loads(streamer_file.read_text(encoding="utf-8")) == {
        **streamer_payload,
        "version": ANALYTICS_DATA_VERSION,
    }
    assert json.loads(drops_file.read_text(encoding="utf-8")) == {
        **drops_payload,
        "version": ANALYTICS_DATA_VERSION,
    }
    assert (
        json.loads((tmp_path / "channel.json.v0.bak").read_text(encoding="utf-8"))
        == streamer_payload
    )
    assert stat.S_IMODE(streamer_file.stat().st_mode) == 0o640
    assert migrate_analytics_directory(tmp_path) == 0


def test_migrate_analytics_directory_skips_now_watching_file(tmp_path):
    now_watching_file = tmp_path / "now_watching.json"
    now_watching_file.write_text(
        json.dumps([{"username": "alice", "reason": "points"}]), encoding="utf-8"
    )

    assert migrate_analytics_directory(tmp_path) == 0

    assert json.loads(now_watching_file.read_text(encoding="utf-8")) == [
        {"username": "alice", "reason": "points"}
    ]


def test_migrate_analytics_directory_skips_dashboard_prefs_file(tmp_path):
    # Reproduces a real crash: dashboard_prefs.json is rewritten wholesale
    # (never carrying a "version" field) every time a dashboard preference
    # changes - see AnalyticsServer.dashboard_prefs(). If this file were
    # ever versioned, the very first migration would leave a
    # dashboard_prefs.json.v0.bak backup behind; the next preference change
    # then resets the live file back to "no version field" (a wholesale
    # rewrite, not a migration), so the *next* launch would see it as
    # unmigrated again, collide with that already-existing backup, and
    # raise - stopping the app on every subsequent launch. Must be skipped
    # entirely, the same as now_watching.json, regardless of how many times
    # it's been rewritten or what backups already exist beside it.
    prefs_file = tmp_path / "dashboard_prefs.json"
    prefs_file.write_text(json.dumps({"dark-mode": "false"}), encoding="utf-8")
    (tmp_path / "dashboard_prefs.json.v0.bak").write_text(
        json.dumps({"dark-mode": "true"}), encoding="utf-8"
    )

    assert migrate_analytics_directory(tmp_path) == 0

    assert json.loads(prefs_file.read_text(encoding="utf-8")) == {"dark-mode": "false"}


def test_migrate_analytics_directory_rejects_future_versions(tmp_path):
    path = tmp_path / "channel.json"
    path.write_text(
        json.dumps({"version": ANALYTICS_DATA_VERSION + 1}), encoding="utf-8"
    )

    with pytest.raises(DataMigrationError, match="unsupported version"):
        migrate_analytics_directory(tmp_path)


def test_analytics_startup_reports_migration_errors_with_context(tmp_path):
    path = tmp_path / "channel.json"
    path.write_text(
        json.dumps({"version": ANALYTICS_DATA_VERSION + 1}), encoding="utf-8"
    )

    with pytest.raises(
        RuntimeError,
        match=r"Unable to migrate analytics data in .*unsupported version",
    ) as raised:
        _migrate_analytics_data(tmp_path)

    assert isinstance(raised.value.__cause__, DataMigrationError)


def test_migrate_analytics_directory_skips_symlinks(tmp_path):
    external = tmp_path / "external-data"
    external.mkdir()
    target = external / "private.json"
    original = {"series": [{"x": 1, "y": 10}]}
    target.write_text(json.dumps(original), encoding="utf-8")
    link = tmp_path / "linked.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating symlinks is not supported in this environment")

    assert migrate_analytics_directory(tmp_path) == 0

    assert json.loads(target.read_text(encoding="utf-8")) == original
    assert not (tmp_path / "linked.json.v0.bak").exists()


def test_migrate_analytics_directory_rejects_symlinked_root(tmp_path):
    target = tmp_path / "external-analytics"
    target.mkdir()
    link = tmp_path / "analytics"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks is not supported in this environment")

    with pytest.raises(DataMigrationError, match="symlinked analytics directory"):
        migrate_analytics_directory(link)


def test_analytics_migration_does_not_follow_predictable_temporary_symlink(tmp_path):
    path = tmp_path / "channel.json"
    path.write_text(json.dumps({"series": []}), encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("do not overwrite", encoding="utf-8")
    predictable = tmp_path / "channel.json.migrating"
    try:
        predictable.symlink_to(external)
    except OSError:
        pytest.skip("Creating symlinks is not supported in this environment")

    assert migrate_analytics_directory(tmp_path) == 1
    assert external.read_text(encoding="utf-8") == "do not overwrite"
    assert predictable.is_symlink()


def test_new_streamer_analytics_include_version(tmp_path):
    Settings.analytics_path = str(tmp_path)
    streamer = Streamer("channel")
    streamer.channel_points = 100

    streamer.persistent_series()

    payload = json.loads((tmp_path / "channel.json").read_text(encoding="utf-8"))
    assert payload["version"] == ANALYTICS_DATA_VERSION
    assert payload["series"][0]["y"] == 100


def test_streamer_analytics_do_not_share_default_series_data(tmp_path):
    Settings.analytics_path = str(tmp_path)
    streamer = Streamer("channel")
    streamer.channel_points = 100

    streamer.persistent_series(event_type="Watch")
    streamer.channel_points = 200
    streamer.persistent_series(event_type=None)

    series = json.loads((tmp_path / "channel.json").read_text(encoding="utf-8"))[
        "series"
    ]
    assert series[0]["z"] == "Watch"
    assert series[1]["y"] == 200
    assert "z" not in series[1]
