import os
import re
import time
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

from TwitchChannelPointsMiner.classes.AnalyticsServer import (
    AnalyticsServer,
    BUILD_COMMIT_ENV_VAR,
    MAX_LOG_TAIL_BYTES,
    SHELL_BYPASS_COOKIE,
    SHELL_BYPASS_TOKEN_ENV_VAR,
    TTLResponseCache,
    UPDATE_DISMISSAL_COOKIE,
    bounded_log_start,
    filter_datas,
    get_streamer_summary,
    read_dashboard_prefs,
    seek_log_start,
    streamers_available,
)
from TwitchChannelPointsMiner.classes.Settings import Settings


def test_ttl_response_cache_expires_after_ttl():
    cache = TTLResponseCache(ttl_seconds=0.05)

    cache.set("key", "payload")
    assert cache.get("key") == "payload"

    time.sleep(0.06)
    assert cache.get("key") is None


def test_ttl_response_cache_disabled_with_zero_ttl():
    cache = TTLResponseCache(ttl_seconds=0)

    cache.set("key", "payload")

    assert cache.get("key") is None


def test_ttl_response_cache_clear_drops_all_entries():
    cache = TTLResponseCache(ttl_seconds=60)

    cache.set("a", "1")
    cache.set("b", "2")
    cache.clear()

    assert cache.get("a") is None
    assert cache.get("b") is None


def test_ttl_response_cache_sweeps_expired_entries_on_set():
    # A caller that varies its key per-request (e.g. keying on a file's
    # mtime, as /now_watching does) must not accumulate one entry forever -
    # once its old key has expired, the next unrelated set() call should
    # sweep it away rather than leaving it for a get() that will never come.
    cache = TTLResponseCache(ttl_seconds=0.05)

    cache.set("now_watching:1", "a")
    time.sleep(0.06)
    cache.set("now_watching:2", "b")
    cache.set("unrelated", "c")

    assert len(cache._entries) == 2
    assert "now_watching:1" not in cache._entries
    assert cache.get("now_watching:2") == "b"
    assert cache.get("unrelated") == "c"


def test_streamers_endpoint_uses_ttl_cache(monkeypatch, tmp_path):
    import json as json_module

    from TwitchChannelPointsMiner.classes import AnalyticsServer as analytics_module

    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "example.json").write_text(
        '{"series": [{"x": 10, "y": 100}]}', encoding="utf-8"
    )
    analytics_module.response_cache.clear()
    calls = []
    original_summary = analytics_module.get_streamer_summary

    def counting_summary(streamer):
        calls.append(streamer)
        return original_summary(streamer)

    monkeypatch.setattr(analytics_module, "get_streamer_summary", counting_summary)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    first = client.get("/streamers")
    second = client.get("/streamers")

    assert first.status_code == 200
    assert second.status_code == 200
    assert json_module.loads(first.get_data(as_text=True)) == json_module.loads(
        second.get_data(as_text=True)
    )
    # Second request must be served from cache without re-reading files.
    assert len(calls) == 1

    analytics_module.response_cache.clear()


def test_streamers_available_excludes_now_watching_file(monkeypatch, tmp_path):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "example.json").write_text(
        '{"series": [{"x": 10, "y": 100}]}', encoding="utf-8"
    )
    (tmp_path / "now_watching.json").write_text("[]", encoding="utf-8")
    (tmp_path / "drops_by_category.json").write_text(
        '{"drops": []}', encoding="utf-8"
    )
    (tmp_path / "dashboard_prefs.json").write_text("{}", encoding="utf-8")

    assert streamers_available() == ["example.json"]


def test_now_watching_endpoint_missing_file_returns_empty_list(monkeypatch, tmp_path):
    import json as json_module

    from TwitchChannelPointsMiner.classes import AnalyticsServer as analytics_module

    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    analytics_module.response_cache.clear()
    server = AnalyticsServer(password=None)

    response = server.app.test_client().get("/now_watching")

    assert response.status_code == 200
    assert json_module.loads(response.get_data(as_text=True)) == []
    analytics_module.response_cache.clear()


def test_now_watching_endpoint_round_trips_well_formed_file(monkeypatch, tmp_path):
    import json as json_module

    from TwitchChannelPointsMiner.classes import AnalyticsServer as analytics_module

    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    entries = [
        {"username": "alice", "reason": "drops", "game": "Foo", "channel_points": 10},
        {"username": "bob", "reason": "points", "game": None, "channel_points": 5},
    ]
    (tmp_path / "now_watching.json").write_text(
        json_module.dumps(entries), encoding="utf-8"
    )
    analytics_module.response_cache.clear()
    server = AnalyticsServer(password=None)

    response = server.app.test_client().get("/now_watching")

    assert response.status_code == 200
    assert json_module.loads(response.get_data(as_text=True)) == entries
    analytics_module.response_cache.clear()


def test_now_watching_endpoint_malformed_json_degrades_to_empty_list(
    monkeypatch, tmp_path
):
    import json as json_module

    from TwitchChannelPointsMiner.classes import AnalyticsServer as analytics_module

    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "now_watching.json").write_text("not json", encoding="utf-8")
    analytics_module.response_cache.clear()
    server = AnalyticsServer(password=None)

    response = server.app.test_client().get("/now_watching")

    assert response.status_code == 200
    assert json_module.loads(response.get_data(as_text=True)) == []
    analytics_module.response_cache.clear()


def test_now_watching_endpoint_uses_ttl_cache(monkeypatch, tmp_path):
    import json as json_module

    from TwitchChannelPointsMiner.classes import AnalyticsServer as analytics_module

    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    entries = [{"username": "alice", "reason": "badge", "game": "Foo", "channel_points": 1}]
    now_watching_file = tmp_path / "now_watching.json"
    now_watching_file.write_text(json_module.dumps(entries), encoding="utf-8")
    analytics_module.response_cache.clear()

    calls = []
    original_open = open

    def counting_open(path, *args, **kwargs):
        if str(path) == str(now_watching_file):
            calls.append(path)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(analytics_module, "open", counting_open, raising=False)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    first = client.get("/now_watching")
    second = client.get("/now_watching")

    assert first.status_code == 200
    assert second.status_code == 200
    assert json_module.loads(first.get_data(as_text=True)) == entries
    assert json_module.loads(second.get_data(as_text=True)) == entries
    # Second request must be served from cache without re-reading the file.
    assert len(calls) == 1

    analytics_module.response_cache.clear()


def test_now_watching_endpoint_serves_fresh_data_after_file_update(
    monkeypatch, tmp_path
):
    import json as json_module

    from TwitchChannelPointsMiner.classes import AnalyticsServer as analytics_module

    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    now_watching_file = tmp_path / "now_watching.json"
    now_watching_file.write_text(
        json_module.dumps([{"username": "alice"}]), encoding="utf-8"
    )
    analytics_module.response_cache.clear()
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    first = client.get("/now_watching")
    assert json_module.loads(first.get_data(as_text=True)) == [{"username": "alice"}]

    # Simulate the miner overwriting the file with a new mtime - the cache
    # is keyed on mtime specifically so this must not be served stale even
    # though the shared TTL has not expired.
    updated_mtime = os.path.getmtime(now_watching_file) + 5
    now_watching_file.write_text(
        json_module.dumps([{"username": "bob"}]), encoding="utf-8"
    )
    os.utime(now_watching_file, (updated_mtime, updated_mtime))

    second = client.get("/now_watching")
    assert json_module.loads(second.get_data(as_text=True)) == [{"username": "bob"}]

    analytics_module.response_cache.clear()


def test_bounded_log_start_caps_legacy_request_without_tail_bytes():
    file_size = MAX_LOG_TAIL_BYTES * 10

    assert bounded_log_start(file_size, 0) == file_size - MAX_LOG_TAIL_BYTES


def test_config_endpoints_require_analytics_authentication():
    server = AnalyticsServer(password=None)

    read_response = server.app.test_client().get("/config")
    response = server.app.test_client().post(
        "/config", json={"action": "add", "kind": "streamers", "value": "one"}
    )
    notification_response = server.app.test_client().post(
        "/config/notifications/discord/test"
    )

    assert read_response.status_code == 403
    assert response.status_code == 403
    assert notification_response.status_code == 403
    assert "analytics username and password" in read_response.get_json()["error"]


def test_static_assets_are_served_without_authentication():
    # CSS/JS/images powering the UI, not account data - a browser resends
    # cached Basic Auth credentials automatically for these, but the
    # Windows shell's embedded iframe relies on a cookie that doesn't
    # reliably reach same-origin sub-resource requests there, which
    # previously left the page structure loading while every style, script,
    # and image 401'd and silently failed to apply.
    server = AnalyticsServer(username="user", password="secret")

    response = server.app.test_client().get(
        server.app.static_url_path + "/style.css"
    )

    assert response.status_code == 200


def test_dashboard_shows_version_update_banner_and_footer(monkeypatch):
    monkeypatch.setattr(Settings, "logger", SimpleNamespace(date_format="dd/mm/yy"))
    monkeypatch.setattr(Settings, "latest_release_version", "3.8.0", raising=False)
    monkeypatch.setattr(
        Settings,
        "update_instructions",
        "Pull the latest image and recreate the container.",
        raising=False,
    )
    server = AnalyticsServer(password=None)

    response = server.app.test_client().get("/")
    page = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="update-available-banner"' in page
    assert "Version 3.8.0 is available." in page
    assert "Pull the latest image and recreate the container." in page
    assert "Running version" in page
    assert "Upgrade available: 3.8.0" in page
    assert "Tkd-Alex" in page


def test_dashboard_footer_shows_build_commit_when_set(monkeypatch):
    monkeypatch.setattr(Settings, "logger", SimpleNamespace(date_format="dd/mm/yy"))
    monkeypatch.setenv(BUILD_COMMIT_ENV_VAR, "abc1234")
    server = AnalyticsServer(password=None)

    page = server.app.test_client().get("/").get_data(as_text=True)

    assert "(abc1234)" in page


def test_dashboard_footer_omits_build_commit_when_unset(monkeypatch):
    monkeypatch.setattr(Settings, "logger", SimpleNamespace(date_format="dd/mm/yy"))
    monkeypatch.delenv(BUILD_COMMIT_ENV_VAR, raising=False)
    server = AnalyticsServer(password=None)

    page = server.app.test_client().get("/").get_data(as_text=True)

    assert "(" not in page.split("Running version", 1)[1].split(".", 1)[0]


def test_dashboard_hides_banner_for_dismissed_version_but_keeps_footer(monkeypatch):
    monkeypatch.setattr(Settings, "logger", SimpleNamespace(date_format="dd/mm/yy"))
    monkeypatch.setattr(Settings, "latest_release_version", "3.8.0", raising=False)
    monkeypatch.setattr(Settings, "update_instructions", "Upgrade now.", raising=False)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()
    client.set_cookie(UPDATE_DISMISSAL_COOKIE, "3.8.0")

    page = client.get("/").get_data(as_text=True)

    assert 'id="update-available-banner"' not in page
    assert "Upgrade available: 3.8.0" in page


def test_index_embeds_saved_dashboard_prefs_in_page(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "logger", SimpleNamespace(date_format="dd/mm/yy"))
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "dashboard_prefs.json").write_text(
        '{"dark-mode": "false", "annotations": "true"}', encoding="utf-8"
    )
    server = AnalyticsServer(password=None)

    page = server.app.test_client().get("/").get_data(as_text=True)

    assert '"dark-mode": "false"' in page
    assert '"annotations": "true"' in page


def test_index_embeds_empty_prefs_when_none_saved_yet(monkeypatch):
    monkeypatch.setattr(Settings, "logger", SimpleNamespace(date_format="dd/mm/yy"))
    server = AnalyticsServer(password=None)

    page = server.app.test_client().get("/").get_data(as_text=True)

    assert "var serverDashboardPrefs = {}" in page


def test_dashboard_prefs_endpoint_persists_a_new_value(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    response = client.post("/dashboard_prefs", json={"key": "dark-mode", "value": "false"})

    assert response.status_code == 200
    assert response.get_json() == {"dark-mode": "false"}
    assert read_dashboard_prefs() == {"dark-mode": "false"}


def test_dashboard_prefs_endpoint_removes_key_when_value_is_null(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "dashboard_prefs.json").write_text(
        '{"dark-mode": "false", "annotations": "true"}', encoding="utf-8"
    )
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    response = client.post("/dashboard_prefs", json={"key": "dark-mode", "value": None})

    assert response.status_code == 200
    assert response.get_json() == {"annotations": "true"}
    assert read_dashboard_prefs() == {"annotations": "true"}


def test_dashboard_prefs_endpoint_rejects_unknown_key(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    response = client.post(
        "/dashboard_prefs", json={"key": "not-a-real-pref", "value": "x"}
    )

    assert response.status_code == 400
    assert read_dashboard_prefs() == {}


def test_dashboard_prefs_endpoint_rejects_oversized_value(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    response = client.post(
        "/dashboard_prefs", json={"key": "dark-mode", "value": "x" * 1000}
    )

    assert response.status_code == 400
    assert read_dashboard_prefs() == {}


def test_dashboard_prefs_endpoint_without_analytics_path_is_a_harmless_noop(
    monkeypatch,
):
    monkeypatch.delattr(Settings, "analytics_path", raising=False)
    server = AnalyticsServer(password=None)
    client = server.app.test_client()

    response = client.post("/dashboard_prefs", json={"key": "dark-mode", "value": "x"})

    assert response.status_code == 200


def test_read_dashboard_prefs_ignores_unexpected_keys_and_non_string_values(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "dashboard_prefs.json").write_text(
        '{"dark-mode": "true", "not-allowed": "x", "annotations": 1}',
        encoding="utf-8",
    )

    assert read_dashboard_prefs() == {"dark-mode": "true"}


def test_read_dashboard_prefs_defaults_to_empty_without_analytics_path(monkeypatch):
    monkeypatch.delattr(Settings, "analytics_path", raising=False)

    assert read_dashboard_prefs() == {}


def test_authenticated_config_writes_reach_the_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "config_path", str(tmp_path), raising=False)
    monkeypatch.setitem(
        __import__(
            "TwitchChannelPointsMiner.classes.AnalyticsServer", fromlist=["web_config"]
        ).web_config.__globals__,
        "update_managed_web_config",
        lambda _path, _payload: {"streamers": []},
    )
    server = AnalyticsServer(username="user", password="secret")

    response = server.app.test_client().post(
        "/config",
        json={"action": "add", "kind": "streamers", "value": "one"},
        headers={"Authorization": "Basic dXNlcjpzZWNyZXQ="},
    )

    assert response.status_code == 200


def test_shell_bypass_token_query_param_authenticates_and_sets_cookie(tmp_path, monkeypatch):
    # Simulates the Windows desktop shell's embedded dashboard iframe: it
    # can't attach a custom Authorization header, so it authenticates once
    # via a query param carrying a token only its own launcher process set.
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    monkeypatch.setenv(SHELL_BYPASS_TOKEN_ENV_VAR, "shell-secret")
    server = AnalyticsServer(username="user", password="secret")
    client = server.app.test_client()

    response = client.get("/streamers?shell_token=shell-secret")

    assert response.status_code == 200
    assert response.headers.get_all("Set-Cookie")
    assert f"{SHELL_BYPASS_COOKIE}=shell-secret" in response.headers["Set-Cookie"]


def test_shell_bypass_cookie_alone_authenticates_later_requests(tmp_path, monkeypatch):
    # The dashboard's own JS makes many same-origin fetch() calls that never
    # repeat the query param - only the cookie set on the first navigation
    # keeps those authenticated.
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    monkeypatch.setenv(SHELL_BYPASS_TOKEN_ENV_VAR, "shell-secret")
    server = AnalyticsServer(username="user", password="secret")
    client = server.app.test_client()
    client.set_cookie(SHELL_BYPASS_COOKIE, "shell-secret")

    response = client.get("/streamers")

    assert response.status_code == 200


def test_shell_bypass_wrong_token_still_requires_basic_auth(monkeypatch):
    monkeypatch.setenv(SHELL_BYPASS_TOKEN_ENV_VAR, "shell-secret")
    server = AnalyticsServer(username="user", password="secret")
    client = server.app.test_client()

    response = client.get("/streamers?shell_token=wrong-guess")

    assert response.status_code == 401


def test_shell_bypass_unset_never_authenticates_even_with_matching_query(monkeypatch):
    # Docker and a plain source checkout never set this env var - a client
    # guessing the query param name must gain nothing there.
    monkeypatch.delenv(SHELL_BYPASS_TOKEN_ENV_VAR, raising=False)
    server = AnalyticsServer(username="user", password="secret")
    client = server.app.test_client()

    response = client.get("/streamers?shell_token=anything")

    assert response.status_code == 401


def test_bounded_log_start_honors_smaller_initial_tail():
    file_size = MAX_LOG_TAIL_BYTES * 10

    assert bounded_log_start(file_size, 0, tail_bytes=128 * 1024) == (
        file_size - (128 * 1024)
    )


def test_bounded_log_start_keeps_recent_incremental_position():
    file_size = MAX_LOG_TAIL_BYTES * 10
    position = file_size - 1024

    assert bounded_log_start(file_size, position) == position


def test_bounded_log_start_recovers_from_rotated_log_position():
    file_size = 4096

    assert bounded_log_start(file_size, file_size + 1) == 0


def test_seek_log_start_keeps_complete_line_at_boundary():
    log_file = BytesIO(b"first\nsecond\nthird\n")

    seek_log_start(log_file, 6, discard_partial_line=True)

    assert log_file.read() == b"second\nthird\n"


def test_seek_log_start_discards_partial_first_line():
    log_file = BytesIO(b"first\nsecond\nthird\n")

    seek_log_start(log_file, 8, discard_partial_line=True)

    assert log_file.read() == b"third\n"


def test_get_streamer_summary_uses_latest_timestamp(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "example.json").write_text(
        '{"series": [{"x": 20, "y": 200}, {"x": 10, "y": 100}]}',
        encoding="utf-8",
    )

    assert get_streamer_summary("example.json") == {
        "points": 200,
        "last_activity": 20,
    }


def test_get_streamer_summary_handles_invalid_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "broken.json").write_text("not json", encoding="utf-8")

    assert get_streamer_summary("broken.json") == {
        "points": 0,
        "last_activity": 0,
    }


def test_get_streamer_summary_handles_non_list_series(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "broken-series.json").write_text('{"series": 123}', encoding="utf-8")

    assert get_streamer_summary("broken-series.json") == {
        "points": 0,
        "last_activity": 0,
    }


def test_get_streamer_summary_ignores_non_numeric_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)
    (tmp_path / "malformed-series.json").write_text(
        '{"series": [{"x": "later", "y": 999}, {"x": 10, "y": "bad"}]}',
        encoding="utf-8",
    )

    assert get_streamer_summary("malformed-series.json") == {
        "points": 0,
        "last_activity": 10,
    }


def test_filter_datas_filters_and_sorts_chart_records():
    data = {
        "series": [
            {"x": 2000, "y": 20, "z": "Watch"},
            {"x": 1000, "y": 10, "z": "Watch"},
            {"x": 3000, "y": 30, "z": "Watch"},
        ],
        "annotations": [
            {"x": 2500, "label": "later"},
            {"x": 1500, "label": "earlier"},
        ],
    }

    result = filter_datas(None, None, data)

    assert [entry["x"] for entry in result["series"]] == [1000, 2000, 3000]
    assert [entry["x"] for entry in result["annotations"]] == [1500, 2500]


def test_filter_datas_builds_no_stream_line_from_prior_balance():
    data = {
        "series": [
            {"x": 1000, "y": 10, "z": "Watch"},
            {"x": 2000, "y": 20, "z": "Watch"},
        ]
    }

    result = filter_datas("1970-01-02", "1970-01-02", data)

    assert [entry["y"] for entry in result["series"]] == [20, 20]
    assert all(entry["z"] == "No Stream" for entry in result["series"])


def test_filter_datas_handles_non_list_series_and_annotations():
    result = filter_datas(
        None,
        None,
        {"series": 123, "annotations": {"x": 1000}},
    )

    assert result == {"series": [], "annotations": []}


def test_filter_datas_handles_non_dict_document():
    assert filter_datas(None, None, [{"x": 1000}]) == {
        "series": [],
        "annotations": [],
    }


def test_filter_datas_defaults_missing_prior_balance_to_zero():
    result = filter_datas(
        "1970-01-02",
        "1970-01-02",
        {"series": [{"x": 1000, "z": "Watch"}]},
    )

    assert [entry["y"] for entry in result["series"]] == [0, 0]


def test_filter_datas_ignores_malformed_numeric_fields():
    result = filter_datas(
        "1970-01-02",
        "1970-01-02",
        {
            "series": [
                {"x": "bad", "y": 999},
                {"x": 1000, "y": "bad"},
            ],
            "annotations": [
                {"x": "bad", "label": "invalid"},
                {"x": 120000000, "label": "valid"},
            ],
        },
    )

    assert [entry["y"] for entry in result["series"]] == [0, 0]
    assert [entry["label"] for entry in result["annotations"]] == ["valid"]


def test_points_tab_reapplies_annotations_after_becoming_visible():
    script_path = Path(__file__).resolve().parents[1] / "assets" / "script.js"
    script = script_path.read_text(encoding="utf-8")
    switch_tab = script.split("function switchDashboardTab", 1)[1].split(
        "var startDate", 1
    )[0]

    assert "requestAnimationFrame" in switch_tab
    assert "renderPointsChart();" in switch_tab
    assert "chartRendered" in switch_tab

    assert "chart.render().then" in script
    assert "switchDashboardTab(savedDashboardTab);" in script
    assert "!chartRendered || $('#points-panel').is(':hidden')" in script
    assert 'pointSeries = response["series"] || [];' in script


def test_now_watching_widget_jumps_to_drops_tab_on_click():
    script = (Path(__file__).resolve().parents[1] / "assets" / "script.js").read_text(
        encoding="utf-8"
    )

    assert "function getNowWatching()" in script
    assert "function renderNowWatching(entries)" in script
    assert "'./now_watching'" in script

    render_now_watching = script.split("function renderNowWatching", 1)[1].split(
        "function getNowWatching", 1
    )[0]

    assert "switchDashboardTab('drops');" in render_now_watching
    assert "changeDropCategory(entry.game);" in render_now_watching
    # A drops/badge entry with no known game (entry.game is null) must not
    # be wired to changeDropCategory(null), which would corrupt the saved
    # Drops-tab category selection.
    assert "&& entry.game)" in render_now_watching


def test_points_chart_translates_logger_month_token_for_apexcharts():
    script = (Path(__file__).resolve().parents[1] / "assets" / "script.js").read_text(
        encoding="utf-8"
    )

    assert "function toApexDateFormat(format)" in script
    assert "return format.replace(/mm/g, 'MM');" in script
    assert "format: chartDateFormat" in script
    assert "format: `${chartDateFormat} HH:mm:ss`" in script


def test_analytics_error_clears_only_after_both_endpoints_recover():
    script = (Path(__file__).resolve().parents[1] / "assets" / "script.js").read_text(
        encoding="utf-8"
    )

    assert "var pointsLoaded = false;" in script
    assert "var dropsLoaded = false;" in script
    clear_error = script.split("function clearAnalyticsLoadError", 1)[1].split(
        "function switchDashboardTab", 1
    )[0]
    assert "pointsLoaded && dropsLoaded" in clear_error
    assert "$('#analytics-load-error').text('').hide();" in clear_error

    points_request = script.split("function getStreamers", 1)[1].split(
        "function renderStreamers", 1
    )[0]
    assert "pointsLoaded = true;" in points_request
    assert "pointsLoaded = false;" in points_request

    drops_request = script.split("function getDropsByCategory", 1)[1].split(
        "function getDropTimestamp", 1
    )[0]
    assert "dropsLoaded = true;" in drops_request
    assert "dropsLoaded = false;" in drops_request


def test_analytics_external_blank_links_prevent_reverse_tabnabbing():
    template = (
        Path(__file__).resolve().parents[1] / "assets" / "charts.html"
    ).read_text(encoding="utf-8")
    blank_links = re.findall(r'<a\b[^>]*target="_blank"[^>]*>', template)

    assert blank_links
    assert all('rel="noopener noreferrer"' in link for link in blank_links)


def test_logs_tab_is_lazily_loaded_and_starts_hidden_for_new_users():
    root = Path(__file__).resolve().parents[1]
    template = (root / "assets" / "charts.html").read_text(encoding="utf-8")
    script = (root / "assets" / "script.js").read_text(encoding="utf-8")

    assert 'id="tab-logs"' in template
    assert 'id="logs-panel" style="display: none;"' in template
    assert "id=\"log\"" not in template
    assert "var logsLoaded = false;" in script

    switch_tab = script.split("function switchDashboardTab", 1)[1].split(
        "\nvar startDate", 1
    )[0]
    assert "$('#logs-panel').toggle(isLogs);" in switch_tab
    assert "if (isLogs && !logsLoaded) startLogPolling();" in switch_tab

    start_polling = script.split("function startLogPolling()", 1)[1].split(
        "\nfunction showAnalyticsLoadError", 1
    )[0]
    assert "if (logsLoaded) return;" in start_polling
    assert "logsLoaded = true;" in start_polling


def test_url_hash_overrides_saved_dashboard_tab():
    script = (Path(__file__).resolve().parents[1] / "assets" / "script.js").read_text(
        encoding="utf-8"
    )
    ready_fn = script.split("$(document).ready(function ()", 1)[1].split(
        "$('#auto-update-log').click", 1
    )[0]

    assert "window.location.hash" in ready_fn
    assert "['points', 'drops', 'config', 'logs'].includes(requestedTab)" in ready_fn
    assert "savedDashboardTab = requestedTab;" in ready_fn


def test_log_polling_retries_after_transient_rollover_failure():
    script = (
        Path(__file__).resolve().parents[1] / "assets" / "script.js"
    ).read_text(encoding="utf-8")
    get_log = script.split("function getLog()", 1)[1].split(
        "\nfunction startLogPolling", 1
    )[0]
    retry = get_log.split(".always(function ()", 1)[1]

    assert ".done(function (data, _status, xhr)" in get_log
    assert "setTimeout(getLog, logPollInterval);" in retry
    assert "autoUpdateLog" in retry


def test_dark_theme_keeps_config_panel_headings_readable():
    stylesheet = (
        Path(__file__).resolve().parents[1] / "assets" / "dark-theme.css"
    ).read_text(encoding="utf-8")

    assert "#config-panel .title" in stylesheet
    assert (
        "color: #fff;"
        in stylesheet.split("#config-panel .title", 1)[1].split("}", 1)[0]
    )
    assert "#config-panel .config-item-name" in stylesheet
    assert "#config-panel .config-item strong" in stylesheet
    assert "#config-panel .input::placeholder" in stylesheet


def test_config_messages_render_as_dismissible_toasts():
    root = Path(__file__).resolve().parents[1]
    template = (root / "assets" / "charts.html").read_text(encoding="utf-8")
    script = (root / "assets" / "script.js").read_text(encoding="utf-8")

    assert 'id="toast-container"' in template
    assert "config-message" not in template
    assert "configMessageTimeout" not in script

    show_message = script.split("function showConfigMessage", 1)[1].split(
        "\nfunction loadWebConfig", 1
    )[0]
    assert "$('#toast-container').append(toast);" in show_message
    assert "toast-close" in show_message
    # Success toasts auto-dismiss; errors persist until the user closes them.
    auto_dismiss_branch = show_message.split("if (!isError)", 1)[1]
    assert "setTimeout(dismiss, 5000);" in auto_dismiss_branch


def test_config_ui_exposes_requested_management_controls():
    root = Path(__file__).resolve().parents[1]
    template = (root / "assets" / "charts.html").read_text(encoding="utf-8")
    script = (root / "assets" / "script.js").read_text(encoding="utf-8")

    for selector in (
        "category-settings-form",
        "source-settings-form",
        "logging-settings-form",
        "update-settings-form",
        "notification-settings",
    ):
        assert f'id="{selector}"' in template
    for setting in (
        "favorite",
        "make_predictions",
        "follow_raid",
        "claim_drops",
        "claim_moments",
        "chat",
        "points_limit",
    ):
        assert setting in script
    assert "reorder_categories" in script
    assert "remove-streamer" in script
    assert "web-config.json" not in template
    assert (
        "Dashboard changes are written directly to <code>config.py</code>" in template
    )
    assert "data-secret" in script
    assert "Configured — leave blank to keep" in script
    assert "test-notification" in script
    assert "update_check" in template.lower().replace("-", "_")
    assert "update_updates" in script
    assert "interval_hours: startupOnly ? undefined" in script
    assert "/config/notifications/${encodeURIComponent(provider)}/test" in script
    assert "reorder_streamers" in script
    assert "Sortable.create" in script


def test_web_config_lists_support_drag_and_drop_reordering():
    root = Path(__file__).resolve().parents[1]
    template = (root / "assets" / "charts.html").read_text(encoding="utf-8")
    script = (root / "assets" / "script.js").read_text(encoding="utf-8")

    assert "sortablejs" in template.lower()

    make_sortable_fn = script.split("function makeSortable", 1)[1].split(
        "\nfunction renderConfiguredStreamers", 1
    )[0]
    assert "Sortable.create(containerEl" in make_sortable_fn
    assert "handle: '.drag-handle'" in make_sortable_fn
    assert "ghostClass: 'sortable-ghost'" in make_sortable_fn
    # Dropping a row back where it started still fires onEnd -- must not
    # save when nothing actually moved.
    assert "evt.oldIndex !== evt.newIndex" in make_sortable_fn

    streamers_fn = script.split("function renderConfiguredStreamers", 1)[1].split(
        "\nfunction renderConfiguredCategories", 1
    )[0]
    assert "makeSortable(container[0]" in streamers_fn
    assert "action: 'reorder_streamers'" in streamers_fn

    categories_fn = script.split("function renderConfiguredCategories", 1)[1].split(
        "\nvar SOURCE_LABELS", 1
    )[0]
    assert "makeSortable(container[0]" in categories_fn
    assert "action: 'reorder_categories'" in categories_fn
    # The up/down buttons are gone -- reordering is drag-only.
    assert "move-category-up" not in script
    assert "move-category-down" not in script

    sources_fn = script.split("function renderSourceSettings", 1)[1].split(
        "\nfunction showConfigMessage", 1
    )[0]
    assert "makeSortable(container[0]" in sources_fn

    save_sources_fn = script.split("function saveSourceSettings", 1)[1].split(
        "\nfunction saveLoggingSettings", 1
    )[0]
    assert "order:" in save_sources_fn
    assert "data('source-row')" in save_sources_fn


def test_failed_config_update_resyncs_from_server():
    script = (Path(__file__).resolve().parents[1] / "assets" / "script.js").read_text(
        encoding="utf-8"
    )
    update_web_config_fn = script.split("function updateWebConfig", 1)[1].split(
        "\nfunction saveStreamerSettings", 1
    )[0]
    fail_branch = update_web_config_fn.split(").fail(function (xhr) {", 1)[1].split(
        "}).always(", 1
    )[0]

    # A failed write (e.g. a dropped reorder) must not leave the DOM showing
    # an order/state that was never actually saved.
    assert "loadWebConfig();" in fail_branch


def test_notification_forms_do_not_nest_two_column_grids():
    stylesheet = (
        Path(__file__).resolve().parents[1] / "assets" / "style.css"
    ).read_text(encoding="utf-8")
    notification_fields = stylesheet.split(".notification-fields {", 1)[1].split(
        "}", 1
    )[0]

    assert "grid-template-columns: minmax(0, 1fr);" in notification_fields
    assert ".notification-config" in stylesheet
    assert ".notification-fields .input" in stylesheet
    assert "min-width: 0;" in stylesheet


def test_notification_events_use_clickable_capsules():
    root = Path(__file__).resolve().parents[1]
    script = (root / "assets" / "script.js").read_text(encoding="utf-8")
    stylesheet = (root / "assets" / "style.css").read_text(encoding="utf-8")

    assert "config.notification_event_options" in script
    assert "event-capsules" in script
    assert "event-capsule" in script
    assert "aria-pressed" in script
    assert '.event-capsule[aria-pressed="true"]' in script
    assert ".event-capsules" in stylesheet
