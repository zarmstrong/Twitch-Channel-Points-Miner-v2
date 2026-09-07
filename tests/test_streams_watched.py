import importlib
import inspect
import json
import logging
from datetime import datetime, timedelta
from threading import Lock
from types import SimpleNamespace

import pytest
import requests

from TwitchChannelPointsMiner.TwitchChannelPointsMiner import (
    TwitchChannelPointsMiner,
    _normalize_badge_drop_streamer_limit,
    _normalize_drop_progress_stall_minutes,
    _normalize_streamer_source_priority,
    _normalize_streams_watched,
)
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.Settings import Priority, Settings, StreamerSource
from TwitchChannelPointsMiner.classes.entities.Raid import Raid
from TwitchChannelPointsMiner.classes.entities.Streamer import (
    Streamer,
    StreamerSettings,
)
from TwitchChannelPointsMiner.utils.Utils import set_default_settings


def test_streams_watched_defaults_to_two():
    parameter = inspect.signature(TwitchChannelPointsMiner.__init__).parameters[
        "streams_watched"
    ]

    assert parameter.default == 2


@pytest.mark.parametrize("value", [0, 3, True, "1", None])
def test_streams_watched_invalid_values_use_default(caplog, value):
    assert _normalize_streams_watched(value) == 2
    assert "streams_watched must be either 1 or 2" in caplog.text


@pytest.mark.parametrize("value", [1, 2])
def test_streams_watched_supported_values_are_preserved(caplog, value):
    assert _normalize_streams_watched(value) == value
    assert caplog.text == ""


def test_minute_watcher_accepts_streams_watched_argument():
    parameter = inspect.signature(Twitch.send_minute_watched_events).parameters[
        "streams_watched"
    ]

    assert parameter.default == 2


def test_drop_progress_stall_defaults_to_ten_minutes():
    mine_parameter = inspect.signature(TwitchChannelPointsMiner.mine).parameters[
        "drop_progress_stall_minutes"
    ]
    watcher_parameter = inspect.signature(Twitch.send_minute_watched_events).parameters[
        "drop_progress_stall_minutes"
    ]

    assert mine_parameter.default == 10
    assert watcher_parameter.default == 10


@pytest.mark.parametrize("value", [-1, 1, 4.9, True, "10", None])
def test_drop_progress_stall_invalid_values_use_default(caplog, value):
    assert _normalize_drop_progress_stall_minutes(value) == 10
    assert "drop_progress_stall_minutes must be 0 or at least 5" in caplog.text


@pytest.mark.parametrize("value", [0, 5, 10, 12.5])
def test_drop_progress_stall_valid_values_are_preserved(caplog, value):
    assert _normalize_drop_progress_stall_minutes(value) == float(value)
    assert caplog.text == ""


def test_streamer_source_priority_default_is_immutable():
    parameter = inspect.signature(TwitchChannelPointsMiner.__init__).parameters[
        "streamer_source_priority"
    ]

    assert parameter.default == (
        StreamerSource.STREAMERS,
        StreamerSource.FOLLOWERS,
        StreamerSource.CATEGORIES,
        StreamerSource.BADGES,
        StreamerSource.WILDCARD_CATEGORIES,
    )


def _watch_streamer(
    username,
    from_category=False,
    drops_eligible=False,
    from_badge_campaign=False,
    from_followers=False,
    from_wildcard_category=False,
    favorite=False,
    points=0,
    points_limit=None,
    watch_streak=False,
):
    stream = SimpleNamespace(
        update_elapsed=lambda: 0,
        update_minute_watched=lambda: None,
        spade_url=f"https://spade.test/{username}",
        encode_payload=lambda: "payload",
        campaigns=[],
        campaigns_ids=[],
        game={"displayName": username},
        game_name=lambda: username,
        watch_streak_missing=watch_streak,
        minute_watched=0,
    )
    return SimpleNamespace(
        username=username,
        is_online=True,
        is_watching=False,
        online_at=0,
        from_category=from_category,
        from_badge_campaign=from_badge_campaign,
        from_followers=from_followers,
        from_wildcard_category=from_wildcard_category,
        channel_points=points,
        offline_at=0,
        stream=stream,
        settings=SimpleNamespace(
            claim_drops=drops_eligible,
            favorite=favorite,
            points_limit=points_limit,
            watch_streak=watch_streak,
        ),
        drops_condition=lambda: drops_eligible,
    )


def _run_one_watch_iteration(
    monkeypatch,
    streamers,
    streams_watched,
    source_priority=None,
    priority=None,
    drop_inventory_progress=None,
    drop_watch_health=None,
    drop_progress_stall_minutes=10,
    drop_pick_stickiness_minutes=0,
    last_drop_pick_streamer=None,
    category_campaign_deadlines=None,
    now=None,
    twitch_out=None,
    post_side_effects=None,
):
    twitch = Twitch.__new__(Twitch)
    twitch.running = True
    twitch.analytics_mutex = Lock()
    twitch.user_agent = "test-agent"
    twitch.completed_drop_campaigns = set()
    twitch.category_campaign_eligibility = {
        (
            twitch._Twitch__slugify(streamer.stream.game_name()),
            streamer.username,
        ): (1, 1)
        for streamer in streamers
        if streamer.from_category and streamer.drops_condition()
    }
    twitch.category_campaign_deadlines = category_campaign_deadlines or {}
    twitch.last_category_drop_selection = None
    twitch.last_wildcard_category_drop_selection = None
    twitch.twitchdrops_app_campaigns = {}
    twitch.last_drop_pick_streamer = last_drop_pick_streamer
    twitch.drop_inventory_progress = drop_inventory_progress or {}
    twitch.drop_inventory_progress_updated_at = (
        now if drop_inventory_progress and now is not None else 0
    )
    twitch.drop_watch_health = drop_watch_health or {}
    if twitch_out is not None:
        twitch_out.append(twitch)
    posted = []

    if now is not None:
        twitch_module = importlib.import_module(
            "TwitchChannelPointsMiner.classes.Twitch"
        )
        monkeypatch.setattr(twitch_module.time, "time", lambda: now)

    if post_side_effects is not None:
        responses = list(post_side_effects)

        def _post(url, **kwargs):
            posted.append(url)
            assert (
                responses
            ), "requests.post called more times than post_side_effects provided"
            effect = responses.pop(0)
            if isinstance(effect, Exception):
                raise effect
            return effect

        monkeypatch.setattr(requests, "post", _post)
    else:
        monkeypatch.setattr(
            requests,
            "post",
            lambda url, **kwargs: posted.append(url)
            or SimpleNamespace(status_code=500),
        )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__chuncked_sleep",
        lambda self, *args, **kwargs: setattr(self, "running", False),
    )

    twitch.send_minute_watched_events(
        streamers,
        priority or [Priority.ORDER],
        streams_watched=streams_watched,
        source_priority=source_priority,
        drop_progress_stall_minutes=drop_progress_stall_minutes,
    )
        drop_pick_stickiness_minutes=drop_pick_stickiness_minutes,
    return posted


def _drop_progress(current=5):
    return (
        (
            "campaign-1",
            "Example campaign",
            "drop-1",
            "Example drop",
            current,
            15,
        ),
    )


def _drop_watch_health(username, progress=None, last_progress_at=0):
    return {
        "game": {
            "username": username,
            "progress": progress or _drop_progress(),
            "last_progress_at": last_progress_at,
            "blocked_until": {},
            "rotation_from": None,
            "waiting_for_alternative": None,
        }
    }


def test_minute_watcher_prioritizes_favorites(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("first"),
            _watch_streamer("favorite", favorite=True),
        ],
        streams_watched=1,
        priority=[Priority.FAVORITE, Priority.ORDER],
    )

    assert posted == ["https://spade.test/favorite"]


def test_minute_watcher_persists_now_watching_analytics(monkeypatch, tmp_path):
    monkeypatch.setattr(Settings, "enable_analytics", True)
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)

    _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("badge-streamer", from_badge_campaign=True),
            _watch_streamer(
                "category-streamer", from_category=True, drops_eligible=True
            ),
            _watch_streamer("points-streamer"),
        ],
        streams_watched=3,
        priority=[Priority.ORDER],
        source_priority=[
            StreamerSource.BADGES,
            StreamerSource.CATEGORIES,
            StreamerSource.STREAMERS,
        ],
    )

    now_watching_file = tmp_path / "now_watching.json"
    assert now_watching_file.is_file()
    entries = json.loads(now_watching_file.read_text(encoding="utf-8"))
    entries_by_username = {entry["username"]: entry for entry in entries}

    assert entries_by_username["badge-streamer"]["reason"] == "badge"
    assert entries_by_username["category-streamer"]["reason"] == "drops"
    assert entries_by_username["points-streamer"]["reason"] == "points"
    assert entries_by_username["badge-streamer"]["game"] == "badge-streamer"


def test_minute_watcher_writes_empty_now_watching_when_nothing_watched(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Settings, "enable_analytics", True)
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)

    _run_one_watch_iteration(
        monkeypatch,
        [],
        streams_watched=1,
    )

    now_watching_file = tmp_path / "now_watching.json"
    assert now_watching_file.is_file()
    assert json.loads(now_watching_file.read_text(encoding="utf-8")) == []


def test_minute_watcher_skips_now_watching_when_analytics_disabled(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(Settings, "enable_analytics", False)
    monkeypatch.setattr(Settings, "analytics_path", str(tmp_path), raising=False)

    _run_one_watch_iteration(
        monkeypatch,
        [_watch_streamer("points-streamer")],
        streams_watched=1,
    )

    assert not (tmp_path / "now_watching.json").exists()


def test_minute_watcher_retries_once_on_connection_error(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)

    posted = _run_one_watch_iteration(
        monkeypatch,
        [_watch_streamer("solo")],
        streams_watched=1,
        post_side_effects=[
            requests.exceptions.ConnectionError("boom"),
            SimpleNamespace(status_code=204),
        ],
    )

    assert posted == ["https://spade.test/solo", "https://spade.test/solo"]
    assert "retrying once" in caplog.text
    assert "Error while trying to send minute watched" not in caplog.text


def test_minute_watcher_gives_up_after_two_connection_errors(monkeypatch, caplog):
    caplog.set_level(logging.WARNING)
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module, "internet_connection_available", lambda *a, **k: True
    )

    posted = _run_one_watch_iteration(
        monkeypatch,
        [_watch_streamer("solo")],
        streams_watched=1,
        post_side_effects=[
            requests.exceptions.ConnectionError("boom"),
            requests.exceptions.ConnectionError("boom again"),
        ],
    )

    assert posted == ["https://spade.test/solo", "https://spade.test/solo"]
    assert "retrying once" in caplog.text
    assert "Error while trying to send minute watched" in caplog.text


def test_minute_watcher_fills_slot_after_selecting_favorite(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("first"),
            _watch_streamer("favorite", favorite=True),
            _watch_streamer("third"),
        ],
        streams_watched=2,
        priority=[Priority.FAVORITE, Priority.ORDER],
    )

    assert posted == ["https://spade.test/first", "https://spade.test/favorite"]


def test_minute_watcher_skips_streamers_at_their_points_limit(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("capped", points=500, points_limit=500),
            _watch_streamer("eligible", points=499, points_limit=500),
        ],
        streams_watched=2,
    )

    assert posted == ["https://spade.test/eligible"]


def test_pending_watch_streak_bypasses_points_limit(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer(
                "capped-streak",
                points=500,
                points_limit=500,
                watch_streak=True,
            )
        ],
        streams_watched=1,
        priority=[Priority.STREAK],
    )

    assert posted == ["https://spade.test/capped-streak"]


def test_pending_watch_streak_honors_explicit_zero_timestamp():
    streamer = _watch_streamer("streak", watch_streak=True)
    streamer.offline_at = -60

    assert Twitch._has_pending_watch_streak(streamer, now=0) is False


def test_minute_watcher_posts_to_two_explicit_streamers(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [_watch_streamer("one"), _watch_streamer("two")],
        streams_watched=2,
    )

    assert posted == ["https://spade.test/one", "https://spade.test/two"]


def test_stale_category_streamer_is_refreshed_even_when_ineligible(monkeypatch):
    # A category streamer excluded from streamers_index by a cached negative
    # __drops_condition result must still reach check_streamer_online once its
    # stream data goes stale - otherwise the negative can never be refreshed
    # (it used to only run for streamers already in streamers_index).
    stale_category = _watch_streamer(
        "stale-category", from_category=True, drops_eligible=False
    )
    stale_category.stream.update_elapsed = lambda: 200

    # A non-category streamer stale by the same amount must NOT be refreshed
    # yet - it keeps the coarser 10-minute (600s) gate, not the 2-minute
    # (120s) gate used for category sources.
    fresh_explicit = _watch_streamer("fresh-explicit")
    fresh_explicit.stream.update_elapsed = lambda: 200

    checked = []
    monkeypatch.setattr(
        Twitch,
        "check_streamer_online",
        lambda self, streamer: checked.append(streamer.username),
    )

    _run_one_watch_iteration(
        monkeypatch,
        [stale_category, fresh_explicit],
        streams_watched=2,
    )

    assert checked == ["stale-category"]


def test_explicit_streamer_is_refreshed_at_ten_minute_gate(monkeypatch):
    stale_explicit = _watch_streamer("stale-explicit")
    stale_explicit.stream.update_elapsed = lambda: 600

    checked = []
    monkeypatch.setattr(
        Twitch,
        "check_streamer_online",
        lambda self, streamer: checked.append(streamer.username),
    )

    _run_one_watch_iteration(
        monkeypatch,
        [stale_explicit],
        streams_watched=1,
    )

    assert checked == ["stale-explicit"]


def test_stale_category_streamer_refresh_flips_it_into_watch_rotation(monkeypatch):
    # End-to-end companion to test_stale_category_streamer_is_refreshed_even_when_ineligible:
    # that test only proves check_streamer_online gets *called* on a stale,
    # ineligible category streamer. This proves a refresh that flips
    # eligibility to positive within the same iteration actually lands the
    # streamer in streamers_index and gets it watched, not just re-checked.
    # (_run_one_watch_iteration/_watch_streamer tie claim_drops to
    # drops_condition() and pre-seed eligibility from it, so this test builds
    # the Twitch/streamer state directly to start from "not yet eligible".)
    category_streamer = _watch_streamer(
        "revives", from_category=True, drops_eligible=True
    )
    category_streamer.stream.update_elapsed = lambda: 200

    twitch = Twitch.__new__(Twitch)
    twitch.running = True
    twitch.user_agent = "test-agent"
    twitch.completed_drop_campaigns = set()
    twitch.category_campaign_eligibility = {}
    twitch.category_campaign_deadlines = {}
    twitch.last_category_drop_selection = None
    twitch.twitchdrops_app_campaigns = {}
    twitch.drop_inventory_progress = {}
    twitch.drop_inventory_progress_updated_at = 0
    twitch.drop_watch_health = {}

    def fake_check_streamer_online(self, streamer):
        slug = self._Twitch__slugify(streamer.stream.game_name())
        self.category_campaign_eligibility[(slug, streamer.username)] = (1, 1)

    monkeypatch.setattr(Twitch, "check_streamer_online", fake_check_streamer_online)

    posted = []
    monkeypatch.setattr(
        requests,
        "post",
        lambda url, **kwargs: posted.append(url) or SimpleNamespace(status_code=500),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__chuncked_sleep",
        lambda self, *args, **kwargs: setattr(self, "running", False),
    )

    twitch.send_minute_watched_events(
        [category_streamer],
        [Priority.ORDER],
        streams_watched=1,
    )

    assert posted == ["https://spade.test/revives"]


def test_drop_priority_applies_across_streamer_sources(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("configured-one"),
            _watch_streamer("pathofexile"),
            _watch_streamer("live-drop", from_category=True, drops_eligible=True),
        ],
        streams_watched=2,
        priority=[Priority.DROPS, Priority.ORDER],
    )

    assert posted == [
        "https://spade.test/configured-one",
        "https://spade.test/live-drop",
    ]


def test_minute_watcher_marks_only_selected_streamers_as_watched(monkeypatch):
    streamers = [_watch_streamer("selected"), _watch_streamer("waiting")]

    _run_one_watch_iteration(monkeypatch, streamers, streams_watched=1)

    assert streamers[0].is_watching is True
    assert streamers[1].is_watching is False


def test_minute_watcher_rotates_stalled_drop_streamer(monkeypatch, caplog):
    stalled = _watch_streamer("stalled", from_category=True, drops_eligible=True)
    replacement = _watch_streamer(
        "replacement", from_category=True, drops_eligible=True
    )
    for streamer in (stalled, replacement):
        streamer.stream.game_name = lambda: "Game"
    stalled.is_watching = True

    posted = _run_one_watch_iteration(
        monkeypatch,
        [stalled, replacement],
        streams_watched=1,
        drop_inventory_progress={"game": _drop_progress()},
        drop_watch_health=_drop_watch_health("stalled"),
        now=600,
    )

    assert posted == ["https://spade.test/replacement"]
    assert stalled.is_watching is False
    assert replacement.is_watching is True
    assert "rotating to another eligible Game channel (replacement)" in caplog.text


def test_minute_watcher_keeps_stalled_streamer_without_alternative(monkeypatch, caplog):
    only_streamer = _watch_streamer("only", from_category=True, drops_eligible=True)
    only_streamer.stream.game_name = lambda: "Game"
    only_streamer.is_watching = True

    posted = _run_one_watch_iteration(
        monkeypatch,
        [only_streamer],
        streams_watched=1,
        drop_inventory_progress={"game": _drop_progress()},
        drop_watch_health=_drop_watch_health("only"),
        now=600,
    )

    assert posted == ["https://spade.test/only"]
    assert "no other eligible live Game channel is available" in caplog.text


def test_drop_progress_advancement_resets_stall_timer(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    replacement = _watch_streamer(
        "replacement", from_category=True, drops_eligible=True
    )
    replacement.stream.game_name = lambda: "Game"
    replacement.is_watching = True
    health = _drop_watch_health(
        "replacement", progress=_drop_progress(current=5), last_progress_at=0
    )
    health["game"]["rotation_from"] = "stalled"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [replacement],
        streams_watched=1,
        drop_inventory_progress={"game": _drop_progress(current=6)},
        drop_watch_health=health,
        now=600,
    )

    assert posted == ["https://spade.test/replacement"]
    assert health["game"]["last_progress_at"] == 600
    assert health["game"]["rotation_from"] is None
    assert (
        "Drop progress resumed on replacement after rotating from stalled"
        in caplog.text
    )


def test_stale_inventory_does_not_rotate_drop_streamer(monkeypatch, caplog):
    stalled = _watch_streamer("stalled", from_category=True, drops_eligible=True)
    replacement = _watch_streamer(
        "replacement", from_category=True, drops_eligible=True
    )
    for streamer in (stalled, replacement):
        streamer.stream.game_name = lambda: "Game"
    stalled.is_watching = True
    health = _drop_watch_health("stalled")
    twitch = Twitch.__new__(Twitch)
    twitch.completed_drop_campaigns = set()
    twitch.category_campaign_eligibility = {
        ("game", "stalled"): (1, 1),
        ("game", "replacement"): (1, 1),
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.drop_inventory_progress = {"game": _drop_progress()}
    twitch.drop_inventory_progress_updated_at = 300
    twitch.drop_watch_health = health

    cooldowns = twitch._Twitch__drop_progress_streamer_cooldowns(
        [stalled, replacement], now=600, stall_seconds=600
    )

    assert cooldowns == set()
    assert "Drop progress has not changed" not in caplog.text


def test_raid_is_joined_only_from_watched_streamer():
    joined_raids = []
    twitch = Twitch.__new__(Twitch)
    twitch.gql = SimpleNamespace(join_raid=joined_raids.append)
    watched = Streamer("watched")
    watched.is_watching = True
    waiting = Streamer("waiting")

    twitch.update_raid(watched, Raid("watched-raid", "target-one"))
    twitch.update_raid(waiting, Raid("waiting-raid", "target-two"))

    assert joined_raids == ["watched-raid"]


def test_minute_watcher_uses_second_slot_for_explicit_stream(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("category", from_category=True, drops_eligible=True),
            _watch_streamer("explicit"),
        ],
        streams_watched=2,
    )

    assert posted == [
        "https://spade.test/explicit",
        "https://spade.test/category",
    ]


def test_minute_watcher_stops_completed_category_stream(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [_watch_streamer("completed-category", from_category=True)],
        streams_watched=1,
    )

    assert posted == []


def test_minute_watcher_ignores_stale_campaigns_after_category_completion(
    monkeypatch,
):
    streamer = _watch_streamer(
        "completed-category", from_category=True, drops_eligible=True
    )
    streamer.stream.game_name = lambda: "Completed Game"
    streamer.stream.campaigns_ids = ["campaign-1"]
    streamer.settings.claim_drops = True

    posted = _run_one_watch_iteration(
        monkeypatch,
        [streamer, _watch_streamer("next-streamer")],
        streams_watched=1,
    )

    assert posted == ["https://spade.test/next-streamer"]


def test_minute_watcher_backfills_slot_after_extra_category_stream(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("category-one", True, True),
            _watch_streamer("category-two", True, True),
            _watch_streamer("explicit"),
        ],
        streams_watched=2,
    )

    assert posted == [
        "https://spade.test/explicit",
        "https://spade.test/category-one",
    ]


def test_badge_source_can_be_given_first_priority(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("explicit"),
            _watch_streamer("category", True, True),
            _watch_streamer("badge", True, True, True),
        ],
        streams_watched=1,
        source_priority=[
            StreamerSource.BADGES,
            StreamerSource.STREAMERS,
            StreamerSource.CATEGORIES,
        ],
    )

    assert posted == ["https://spade.test/badge"]


def test_preferred_category_wins_shared_discovered_slot_over_wildcard(monkeypatch):
    # Twitch only accrues Drops progress on one watched stream regardless of
    # source, so the preferred-category and wildcard-category tiers share a
    # single discovered-stream slot per cycle rather than getting one each --
    # otherwise the second slot would be wasted from a Drops perspective.
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("preferred", True, True),
            _watch_streamer("wildcard", True, True, from_wildcard_category=True),
        ],
        streams_watched=2,
    )

    assert posted == ["https://spade.test/preferred"]


def test_freed_wildcard_slot_backfills_with_explicit_streamer(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("preferred", True, True),
            _watch_streamer("wildcard", True, True, from_wildcard_category=True),
            _watch_streamer("explicit"),
        ],
        streams_watched=2,
    )

    assert sorted(posted) == [
        "https://spade.test/explicit",
        "https://spade.test/preferred",
    ]


def test_wildcard_category_one_per_cycle_prefers_soonest_expiring(monkeypatch):
    slow_game = _watch_streamer(
        "later-deadline",
        from_category=True,
        drops_eligible=True,
        from_wildcard_category=True,
    )
    slow_game.stream.game_name = lambda: "Slow Game"
    urgent_game = _watch_streamer(
        "sooner-deadline",
        from_category=True,
        drops_eligible=True,
        from_wildcard_category=True,
    )
    urgent_game.stream.game_name = lambda: "Urgent Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [slow_game, urgent_game],
        streams_watched=2,
        category_campaign_deadlines={
            "slow-game": datetime(2099, 1, 1),
            "urgent-game": datetime(2020, 1, 1),
        },
    )

    # Only one discovered stream is watched per cycle, whether it's a
    # preferred-category or wildcard pick (see
    # test_preferred_category_wins_shared_discovered_slot_over_wildcard for
    # the cross-tier case) -- among two wildcard candidates competing for
    # that single shared slot, the soonest-expiring one wins.
    assert posted == ["https://spade.test/sooner-deadline"]


def test_follower_source_can_be_prioritized_over_explicit_streamers(monkeypatch):
    posted = _run_one_watch_iteration(
        monkeypatch,
        [
            _watch_streamer("explicit"),
            _watch_streamer("followed", from_followers=True),
        ],
        streams_watched=1,
        source_priority=[
            StreamerSource.FOLLOWERS,
            StreamerSource.STREAMERS,
        ],
    )

    assert posted == ["https://spade.test/followed"]
def test_watched_streamer_log_includes_selection_reason(monkeypatch):
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )
    streamers = [
        _watch_streamer("explicit"),
        _watch_streamer("campaign", True, True),
        _watch_streamer("badge", True, True, True),
    ]

    _run_one_watch_iteration(
        monkeypatch,
        streamers,
        streams_watched=2,
        source_priority=[
            StreamerSource.BADGES,
            StreamerSource.STREAMERS,
            StreamerSource.CATEGORIES,
        ],
    )

    watch_message = next(
        message for message in messages if "Watching for points:" in message
    )
    assert "badge (badge drop)" in watch_message
    assert "explicit (streamer)" in watch_message
    assert "badge (badge drop; badge drops)" in watch_message


def test_source_priority_appends_omitted_sources():
    assert _normalize_streamer_source_priority([StreamerSource.BADGES]) == [
        StreamerSource.BADGES,
        StreamerSource.STREAMERS,
        StreamerSource.FOLLOWERS,
        StreamerSource.CATEGORIES,
        StreamerSource.WILDCARD_CATEGORIES,
    ]




def test_source_priority_default_order_sorts_wildcard_last():
    assert _normalize_streamer_source_priority([]) == [
        StreamerSource.STREAMERS,
        StreamerSource.FOLLOWERS,
        StreamerSource.CATEGORIES,
        StreamerSource.BADGES,
        StreamerSource.WILDCARD_CATEGORIES,
    ]


@pytest.mark.parametrize("value", [0, 3, True, "1", None])
def test_badge_drop_streamer_limit_rejects_values_other_than_one_or_two(caplog, value):
    assert _normalize_badge_drop_streamer_limit(value) == 1
    assert "badge_drop_streamer_limit must be either 1 or 2" in caplog.text


@pytest.mark.parametrize("value", [1, 2])
def test_badge_drop_streamer_limit_accepts_one_or_two(caplog, value):
    assert _normalize_badge_drop_streamer_limit(value) == value
    assert caplog.text == ""


def test_category_drop_pick_prefers_soonest_expiring_campaign(monkeypatch):
    # Discovered first (lower array index) but its campaign expires later -
    # discovery order must not win over expiration.
    slow_game = _watch_streamer(
        "later-deadline", from_category=True, drops_eligible=True
    )
    slow_game.stream.game_name = lambda: "Slow Game"
    urgent_game = _watch_streamer(
        "sooner-deadline", from_category=True, drops_eligible=True
    )
    urgent_game.stream.game_name = lambda: "Urgent Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [slow_game, urgent_game],
        streams_watched=1,
        category_campaign_deadlines={
            "slow-game": datetime(2099, 1, 1),
            "urgent-game": datetime(2020, 1, 1),
        },
    )

    assert posted == ["https://spade.test/sooner-deadline"]


def test_drop_pick_stickiness_keeps_current_within_margin(monkeypatch):
    # The previously picked streamer's campaign expires 10 minutes later than
    # the best alternative - inside the stickiness margin, so no churn.
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [sooner, current],
        streams_watched=1,
        category_campaign_deadlines={
            "current-game": datetime(2099, 1, 2),
            "sooner-game": datetime(2099, 1, 1, 23, 50),
        },
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
    )

    assert posted == ["https://spade.test/current-pick"]


def test_drop_pick_stickiness_switches_when_alternative_beats_margin(monkeypatch):
    # The alternative's campaign expires 5 hours sooner - well beyond the
    # stickiness margin, so the pick must switch despite stickiness.
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [current, sooner],
        streams_watched=1,
        category_campaign_deadlines={
            "current-game": datetime(2099, 1, 2),
            "sooner-game": datetime(2099, 1, 1, 19, 0),
        },
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
    )

    assert posted == ["https://spade.test/sooner-pick"]


def test_drop_pick_stickiness_disabled_always_picks_soonest(monkeypatch):
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [sooner, current],
        streams_watched=1,
        category_campaign_deadlines={
            "current-game": datetime(2099, 1, 2),
            "sooner-game": datetime(2099, 1, 1, 23, 50),
        },
        drop_pick_stickiness_minutes=0,
        last_drop_pick_streamer="current-pick",
    )

    assert posted == ["https://spade.test/sooner-pick"]


def test_drop_pick_stickiness_updates_tracked_pick(monkeypatch):
    # The tracked pick must follow the actually watched streamer so the next
    # cycle's stickiness compares against the right campaign.
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    twitch_out = []
    _run_one_watch_iteration(
        monkeypatch,
        [current],
        streams_watched=1,
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="someone-else",
        twitch_out=twitch_out,
    )

    assert twitch_out[0].last_drop_pick_streamer == "current-pick"


def test_drop_pick_hold_keeps_current_while_drop_can_finish(monkeypatch):
    # A challenger whose campaign expires well beyond the stickiness margin
    # must still lose to the current pick while the current pick's game has an
    # in-progress drop that can finish before its campaign deadline.
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [current, sooner],
        streams_watched=2,
        category_campaign_deadlines={
            "current-game": datetime(2099, 1, 2),
            "sooner-game": datetime(2099, 1, 1, 19, 0),
        },
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
        drop_inventory_progress={"current-game": _drop_progress(current=10)},
        now=1_700_000_000,
    )

    assert posted == ["https://spade.test/current-pick"]


def test_drop_pick_hold_releases_when_drop_cannot_finish(monkeypatch):
    # When the in-progress drop needs more minutes than the campaign has left,
    # holding is pointless - the more urgent campaign must win the slot.
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    deadline_in_minutes = 120
    posted = _run_one_watch_iteration(
        monkeypatch,
        [current, sooner],
        streams_watched=2,
        category_campaign_deadlines={
            "current-game": datetime.utcnow() + timedelta(minutes=deadline_in_minutes),
            "sooner-game": datetime.utcnow()
            + timedelta(minutes=deadline_in_minutes // 2),
        },
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
        drop_inventory_progress={
            "current-game": (
                ("campaign-1", "Example campaign", "drop-1", "Example drop", 0, 220),
            ),
        },
        now=1_700_000_000,
    )

    assert posted == ["https://spade.test/sooner-pick"]


def test_drop_pick_survives_transient_eligibility_failure(monkeypatch):
    # A category refresh can leave the previously picked streamer with stale,
    # empty per-channel campaign state for a cycle. While its game still has
    # an in-progress drop in the inventory, the pick must not rotate to
    # another campaign. The failure is transient state, not a disabled
    # claim_drops setting - the hold requires claims to stay enabled.
    current = _watch_streamer(
        "current-pick", from_wildcard_category=True, drops_eligible=True
    )
    current.drops_condition = lambda: False
    current.stream.game_name = lambda: "Current Game"
    challenger = _watch_streamer(
        "challenger", from_wildcard_category=True, drops_eligible=True
    )
    challenger.stream.game_name = lambda: "Challenger Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [current, challenger],
        streams_watched=1,
        priority=[Priority.DROPS],
        category_campaign_deadlines={"current-game": datetime(2099, 1, 2)},
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
        drop_inventory_progress={"current-game": _drop_progress(current=10)},
        now=1_700_000_000,
    )

    assert posted == ["https://spade.test/current-pick"]


def test_drop_pick_not_held_when_campaign_left_inventory(monkeypatch):
    # With no in-progress drop for its game, the transient hold must not keep
    # an otherwise ineligible streamer in the watch rotation.
    current = _watch_streamer(
        "current-pick", from_wildcard_category=True, drops_eligible=True
    )
    current.drops_condition = lambda: False
    current.stream.game_name = lambda: "Current Game"
    challenger = _watch_streamer(
        "challenger", from_wildcard_category=True, drops_eligible=True
    )
    challenger.stream.game_name = lambda: "Challenger Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [current, challenger],
        streams_watched=1,
        priority=[Priority.DROPS],
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
        drop_inventory_progress={},
        now=1_700_000_000,
    )

    assert posted == ["https://spade.test/challenger"]


def test_drop_pick_not_held_when_channel_offline(monkeypatch):
    current = _watch_streamer(
        "current-pick", from_wildcard_category=True, drops_eligible=True
    )
    current.stream.game_name = lambda: "Current Game"
    current.is_online = False
    challenger = _watch_streamer(
        "challenger", from_wildcard_category=True, drops_eligible=True
    )
    challenger.stream.game_name = lambda: "Challenger Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [current, challenger],
        streams_watched=1,
        priority=[Priority.DROPS],
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
        drop_inventory_progress={"current-game": _drop_progress(current=10)},
        now=1_700_000_000,
    )

    assert posted == ["https://spade.test/challenger"]


def test_drop_pick_hold_logs_reason(monkeypatch):
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )

    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    _run_one_watch_iteration(
        monkeypatch,
        [current, sooner],
        streams_watched=2,
        category_campaign_deadlines={
            "current-game": datetime(2099, 1, 2),
            "sooner-game": datetime(2099, 1, 1, 19, 0),
        },
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
        drop_inventory_progress={"current-game": _drop_progress(current=10)},
        now=1_700_000_000,
    )

    selection_messages = [
        message
        for message in messages
        if "Selected" in message and "for drops" in message
    ]
    assert len(selection_messages) == 1
    assert "current-pick" in selection_messages[0]
    assert "holding in-progress drop 'Example drop'" in selection_messages[0]
    assert "campaign deadline in" in selection_messages[0]


def test_streak_priority_does_not_slot_discovered_streamers(monkeypatch):
    # Discovered category/wildcard/badge streamers are created with
    # watch_streak=False, so a freshly-online channel with a missing streak
    # can no longer grab a watch slot at the STREAK level - only explicitly
    # configured streamers (and enabled followed channels) do. The freed slot
    # then goes to the soonest-expiring campaign at the DROPS level.
    configured = _watch_streamer("configured-streak", watch_streak=True)
    streaky_fresh = _watch_streamer(
        "streaky-fresh", from_category=True, drops_eligible=True
    )
    streaky_fresh.settings.watch_streak = False
    streaky_fresh.stream.watch_streak_missing = True
    streaky_fresh.stream.game_name = lambda: "Later Game"
    soonest = _watch_streamer("category-soon", from_category=True, drops_eligible=True)
    soonest.stream.game_name = lambda: "Urgent Game"

    posted = _run_one_watch_iteration(
        monkeypatch,
        [configured, streaky_fresh, soonest],
        streams_watched=2,
        priority=[Priority.STREAK, Priority.DROPS],
        category_campaign_deadlines={
            "later-game": datetime(2099, 1, 3),
            "urgent-game": datetime(2099, 1, 1),
        },
    )

    assert posted == [
        "https://spade.test/configured-streak",
        "https://spade.test/category-soon",
    ]


def test_discovered_streamer_watch_streak_false_survives_defaults():
    # set_default_settings only fills None fields, so the watch_streak=False
    # passed at the discovered-streamer creation sites must survive the merge
    # with the user's global streamer settings (watch_streak=True here).
    original = getattr(Settings, "streamer_settings", None)
    Settings.streamer_settings = StreamerSettings(watch_streak=True)
    try:
        settings = set_default_settings(
            StreamerSettings(claim_drops=True, watch_streak=False),
            Settings.streamer_settings,
        )
    finally:
        Settings.streamer_settings = original

    assert settings.watch_streak is False


def test_followers_source_enabled_detection():
    miner = TwitchChannelPointsMiner.__new__(TwitchChannelPointsMiner)

    miner.configured_source_priority = None
    assert miner._followers_source_enabled() is True

    miner.configured_source_priority = [
        StreamerSource.STREAMERS,
        StreamerSource.CATEGORIES,
    ]
    assert miner._followers_source_enabled() is False

    miner.configured_source_priority = [StreamerSource.FOLLOWERS]
    assert miner._followers_source_enabled() is True


def test_drop_pick_logs_only_slotted_candidate_reason(monkeypatch):
    # A higher-priority allocation (FAVORITE) took the only watch slot for a
    # later-expiring category stream; the soonest-expiring eligible campaign
    # never competed for a slot. The log must not claim "soonest-expiring".
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )

    urgent = _watch_streamer("urgent-drop", from_category=True, drops_eligible=True)
    urgent.stream.game_name = lambda: "Urgent Game"
    fav_later = _watch_streamer(
        "fav-later", from_category=True, drops_eligible=True, favorite=True
    )
    fav_later.stream.game_name = lambda: "Later Game"

    _run_one_watch_iteration(
        monkeypatch,
        [fav_later, urgent],
        streams_watched=1,
        priority=[Priority.FAVORITE, Priority.DROPS],
        category_campaign_deadlines={
            "urgent-game": datetime(2099, 1, 1),
            "later-game": datetime(2099, 1, 2),
        },
    )

    selection = [
        message
        for message in messages
        if "Selected" in message and "for drops" in message
    ]
    assert len(selection) == 1
    assert "fav-later" in selection[0]
    assert "only slotted category candidate" in selection[0]
    assert "urgent-drop" in selection[0]
    assert "got no watch slot this cycle" in selection[0]
    assert "soonest-expiring of" not in selection[0]


def test_drop_pick_logs_slotted_subset_reason(monkeypatch):
    # Several slotted category streams plus an unslotted sooner-expiring one:
    # the reason must scope the expiration claim to the slotted subset and
    # name the unslotted campaign.
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )

    urgent = _watch_streamer("urgent-drop", from_category=True, drops_eligible=True)
    urgent.stream.game_name = lambda: "Urgent Game"
    fav_a = _watch_streamer(
        "fav-a", from_category=True, drops_eligible=True, favorite=True
    )
    fav_a.stream.game_name = lambda: "Fav A Game"
    fav_b = _watch_streamer(
        "fav-b", from_category=True, drops_eligible=True, favorite=True
    )
    fav_b.stream.game_name = lambda: "Fav B Game"

    _run_one_watch_iteration(
        monkeypatch,
        [fav_b, fav_a, urgent],
        streams_watched=2,
        priority=[Priority.FAVORITE, Priority.DROPS],
        category_campaign_deadlines={
            "urgent-game": datetime(2099, 1, 1),
            "fav-a-game": datetime(2099, 1, 2),
            "fav-b-game": datetime(2099, 1, 3),
        },
    )

    selection = [
        message
        for message in messages
        if "Selected" in message and "for drops" in message
    ]
    assert len(selection) == 1
    assert "fav-a" in selection[0]
    assert "soonest-expiring of 2 slotted category campaigns" in selection[0]
    assert "1 eligible campaigns not slotted this cycle" in selection[0]
    assert "urgent-drop" in selection[0]


def test_drop_pick_stickiness_hold_logs_stickiness_reason(monkeypatch):
    # Within the stickiness margin the previous pick keeps its slot over the
    # more urgent challenger - the log must attribute that to stickiness.
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__chuncked_sleep",
        lambda self, *args, **kwargs: setattr(self, "running", False),
    )

    fortnite = _watch_streamer(
        "brasil_fortnite", from_category=True, drops_eligible=True
    )
    fortnite.stream.game_name = lambda: "Fortnite"
    fortnite.stream.game = {"displayName": "Fortnite"}
    division = _watch_streamer(
        "nothingbutskillz", from_category=True, drops_eligible=True
    )
    division.stream.game_name = lambda: "The Division 2"
    division.stream.game = {"displayName": "The Division 2"}

    twitch_out = []
    _run_one_watch_iteration(
        monkeypatch,
        [division, fortnite],
        streams_watched=1,
        category_campaign_deadlines={
            "fortnite": datetime(2020, 1, 1),
            "the-division-2": datetime(2099, 1, 1),
        },
        twitch_out=twitch_out,
    )

    def selection_messages():
        return [m for m in messages if "Selected" in m and "for drops" in m]

    assert len(selection_messages()) == 1
    reason = selection_messages()[0]
    assert "brasil_fortnite" in reason
    assert "Fortnite" in reason
    assert "The Division 2" in reason

    # Re-running against the same, unchanged pick must not repeat the log line.
    messages.clear()
    twitch = twitch_out[0]
    twitch.running = True
    twitch.send_minute_watched_events(
        [division, fortnite],
        [Priority.ORDER],
        streams_watched=1,
    )

    assert selection_messages() == []


def test_category_drop_pick_log_distinguishes_no_slot_from_no_eligible(monkeypatch):
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )


    selection = [
        message
        for message in messages
        if "Selected" in message and "for drops" in message
    ]
    assert len(selection) == 1
    assert "current-pick" in selection[0]
    assert "held by stickiness" in selection[0]
    assert "sooner-pick" in selection[0]
    assert "soonest-expiring of" not in selection[0]


def test_category_drop_pick_logs_selection_reason_only_on_change(monkeypatch):
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    current = _watch_streamer("current-pick", from_category=True, drops_eligible=True)
    current.stream.game_name = lambda: "Current Game"
    sooner = _watch_streamer("sooner-pick", from_category=True, drops_eligible=True)
    sooner.stream.game_name = lambda: "Sooner Game"

    _run_one_watch_iteration(
        monkeypatch,
        [sooner, current],
        streams_watched=1,
        category_campaign_deadlines={
            "current-game": datetime(2099, 1, 2),
            "sooner-game": datetime(2099, 1, 1, 23, 50),
        },
        drop_pick_stickiness_minutes=15,
        last_drop_pick_streamer="current-pick",
    )
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__chuncked_sleep",
        lambda self, *args, **kwargs: setattr(self, "running", False),
    )

    category_streamer = _watch_streamer(
        "queued-category", from_category=True, drops_eligible=True
    )
    category_streamer.stream.game_name = lambda: "Some Game"
    category_streamer.stream.game = {"displayName": "Some Game"}

    # First cycle: nothing else competes for the slot, so it gets watched
    # and logged normally.
    twitch_out = []
    _run_one_watch_iteration(
        monkeypatch,
        [category_streamer],
        streams_watched=2,
        twitch_out=twitch_out,
    )
    assert any(
        "Selected" in m and "for drops" in m and "queued-category" in m
        for m in messages
    )

    # Second cycle (same miner state): two explicit streamers now fill both
    # watch slots ahead of the category source, bumping the previously
    # eligible category stream out entirely - it never gets a chance to
    # watch, which is a different situation from "nothing is eligible".
    messages.clear()
    twitch = twitch_out[0]
    twitch.running = True
    twitch.send_minute_watched_events(
        [_watch_streamer("one"), _watch_streamer("two"), category_streamer],
        [Priority.ORDER],
        streams_watched=2,
    )

    no_slot_messages = [m for m in messages if "eligible but no watch slot free" in m]
    assert len(no_slot_messages) == 1
    assert "queued-category" in no_slot_messages[0]
    assert "Some Game" in no_slot_messages[0]
    assert not any("Selected" in m and "for drops" in m for m in messages)
    assert not any("No category-discovered drop stream is" in m for m in messages)


def test_category_drop_pick_logs_when_first_candidate_appears_with_no_slot(
    monkeypatch,
):
    # Both "no eligible campaign at all" and "eligible but no watch slot
    # free" leave chosen_username as None - the dedup key must still treat
    # that transition as a change worth logging.
    messages = []
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(
        twitch_module.logger,
        "info",
        lambda message, **kwargs: messages.append(message),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__chuncked_sleep",
        lambda self, *args, **kwargs: setattr(self, "running", False),
    )

    def category_pick_messages():
        return [
            m
            for m in messages
            if ("Selected" in m and "for drops" in m)
            or "eligible but no watch slot free" in m
            or "No category-discovered drop stream is" in m
        ]

    # First cycle: no category streamer at all - no category-pick message
    # should be logged (the routine "Watching for points" line still is).
    twitch_out = []
    _run_one_watch_iteration(
        monkeypatch,
        [_watch_streamer("one"), _watch_streamer("two")],
        streams_watched=2,
        twitch_out=twitch_out,
    )
    assert category_pick_messages() == []

    # Second cycle (same miner state): an eligible category streamer shows
    # up, but both slots are still held by the explicit streamers - this is
    # a different situation from "nothing eligible" and must be logged.
    category_streamer = _watch_streamer(
        "late-arrival", from_category=True, drops_eligible=True
    )
    category_streamer.stream.game_name = lambda: "Late Game"
    category_streamer.stream.game = {"displayName": "Late Game"}

    twitch = twitch_out[0]
    twitch.running = True
    # This streamer didn't exist when _run_one_watch_iteration built the
    # eligibility cache from the first cycle's streamer list, so register it
    # directly - mirrors what a real category-discovery refresh would do.
    twitch.category_campaign_eligibility[("late-game", "late-arrival")] = (1, 1)
    twitch.send_minute_watched_events(
        [_watch_streamer("one"), _watch_streamer("two"), category_streamer],
        [Priority.ORDER],
        streams_watched=2,
    )

    no_slot_messages = [m for m in messages if "eligible but no watch slot free" in m]
    assert len(no_slot_messages) == 1
    assert "late-arrival" in no_slot_messages[0]
