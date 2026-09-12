import logging
import threading
from datetime import datetime
from types import SimpleNamespace

from TwitchChannelPointsMiner.TwitchChannelPointsMiner import TwitchChannelPointsMiner
from TwitchChannelPointsMiner.classes.Chat import ChatPresence
from TwitchChannelPointsMiner.classes.Settings import Settings
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.classes.entities.Streamer import Streamer, StreamerSettings


def _bare_twitch(monkeypatch, deadlines, requested_slugs_seen=None):
    """A Twitch instance with the campaign-fetch internals stubbed out so
    get_wildcard_categories_with_active_drops's own exclude/limit/pin/sort
    logic can be exercised directly, independent of campaign JSON parsing
    (already covered for the shared helper by test_twitch_drop_claim.py).
    """
    twitch = object.__new__(Twitch)
    twitch.category_campaign_deadlines = {}
    twitch.category_log_level = logging.DEBUG
    twitch.twitchdrops_app_deadlines = {}
    twitch.twitchdrops_app_catalog_complete = True

    def fake_active_drop_category_slugs_from_campaigns(
        self, inventory, requested_category_slugs
    ):
        if requested_slugs_seen is not None:
            requested_slugs_seen.append(requested_category_slugs)
        return dict(deadlines), set(deadlines)

    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: {"present": True})
    monkeypatch.setattr(
        Twitch,
        "_Twitch__active_drop_category_slugs_from_campaigns",
        fake_active_drop_category_slugs_from_campaigns,
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: {},
    )
    return twitch


def test_get_category_slugs_treats_none_as_empty():
    twitch = object.__new__(Twitch)

    assert twitch.get_category_slugs(None) == set()


def test_get_wildcard_categories_returns_full_set_sorted_by_expiration(monkeypatch):
    deadlines = {
        "slow-game": datetime(2099, 1, 1),
        "urgent-game": datetime(2020, 1, 1),
        "mid-game": datetime(2050, 1, 1),
    }
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops()

    assert result == ["urgent-game", "mid-game", "slow-game"]


def test_get_wildcard_categories_unfiltered_request_is_passed_through(monkeypatch):
    seen = []
    twitch = _bare_twitch(monkeypatch, {"game": datetime(2099, 1, 1)}, seen)

    twitch.get_wildcard_categories_with_active_drops()

    assert seen == [None]


def test_preferred_filter_keeps_external_catalog_scoped(monkeypatch):
    twitch = _bare_twitch(
        monkeypatch, {"preferred-game": datetime(2099, 1, 1)}
    )
    fallback_requests = []
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: fallback_requests.append(categories)
        or {},
    )

    result = twitch.filter_categories_with_active_drops(
        ["preferred-game"], inventory={"present": True}
    )

    assert result == ["preferred-game"]
    assert fallback_requests == [["preferred-game"]]


def test_get_wildcard_categories_excludes_configured_category_slugs(monkeypatch):
    deadlines = {
        "excluded-game": datetime(2020, 1, 1),
        "other-game": datetime(2050, 1, 1),
    }
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops(
        exclude_category_slugs={"excluded-game"}
    )

    assert result == ["other-game"]


def test_get_wildcard_categories_drops_enabled_false_returns_empty(monkeypatch):
    twitch = _bare_twitch(monkeypatch, {"game": datetime(2099, 1, 1)})

    assert twitch.get_wildcard_categories_with_active_drops(drops_enabled=False) == []


def test_get_wildcard_categories_limit_throttles_new_additions(monkeypatch):
    deadlines = {
        "first": datetime(2020, 1, 1),
        "second": datetime(2021, 1, 1),
        "third": datetime(2022, 1, 1),
        "fourth": datetime(2023, 1, 1),
    }
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops(
        limit=2, pin_active=False
    )

    assert result == ["first", "second"]


def test_get_wildcard_categories_pin_keeps_tracked_slug_past_top_n(monkeypatch):
    # "pinned" would fall outside the top 2 soonest-expiring slugs on a
    # straight re-sort, but pinning must keep it because it's still eligible.
    # Room for one new addition remains (limit=2, one pinned slug), and
    # "first" is the soonest-expiring of the unpinned candidates.
    deadlines = {
        "first": datetime(2020, 1, 1),
        "second": datetime(2021, 1, 1),
        "pinned": datetime(2099, 1, 1),
    }
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops(
        limit=2, pinned_category_slugs={"pinned"}, pin_active=True
    )

    assert set(result) == {"pinned", "first"}


def test_get_wildcard_categories_pin_can_exceed_limit(monkeypatch):
    # Three already-tracked (pinned) categories against a limit of 2: pinning
    # only throttles *new* additions, so the returned count intentionally
    # exceeds `limit` here rather than evicting a pinned category.
    deadlines = {
        "pin-a": datetime(2030, 1, 1),
        "pin-b": datetime(2040, 1, 1),
        "pin-c": datetime(2050, 1, 1),
        "brand-new": datetime(2020, 1, 1),
    }
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops(
        limit=2,
        pinned_category_slugs={"pin-a", "pin-b", "pin-c"},
        pin_active=True,
    )

    assert set(result) == {"pin-a", "pin-b", "pin-c"}
    assert len(result) > 2


def test_get_wildcard_categories_pin_inactive_drops_slug_outside_top_n(monkeypatch):
    deadlines = {
        "first": datetime(2020, 1, 1),
        "second": datetime(2021, 1, 1),
        "would-be-pinned": datetime(2099, 1, 1),
    }
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops(
        limit=2, pinned_category_slugs={"would-be-pinned"}, pin_active=False
    )

    assert result == ["first", "second"]


def test_get_wildcard_categories_pinned_slug_no_longer_eligible_is_dropped(monkeypatch):
    # The pinned category's campaign genuinely closed -- it's no longer in
    # the eligible set at all, so pinning cannot resurrect it.
    deadlines = {"still-open": datetime(2020, 1, 1)}
    twitch = _bare_twitch(monkeypatch, deadlines)

    result = twitch.get_wildcard_categories_with_active_drops(
        pinned_category_slugs={"closed-campaign"}, pin_active=True
    )

    assert result == ["still-open"]


def test_get_wildcard_categories_replaces_rather_than_merges_deadlines(monkeypatch):
    # The unfiltered fetch is always a superset of what a same-cycle preferred
    # pass could have found (wildcard only triggers once that pass found
    # nothing), so it must replace category_campaign_deadlines rather than
    # merge into it -- a merge would let a closed campaign's stale deadline
    # linger forever whenever `categories` is empty and this is the only
    # per-cycle source.
    twitch = _bare_twitch(monkeypatch, {"still-open": datetime(2099, 1, 1)})
    twitch.category_campaign_deadlines = {"closed-campaign": datetime(2020, 1, 1)}

    twitch.get_wildcard_categories_with_active_drops()

    assert twitch.category_campaign_deadlines == {"still-open": datetime(2099, 1, 1)}


def test_wildcard_ranks_external_missing_campaign_ahead_of_later_twitch_campaigns(
    monkeypatch,
):
    twitch = _bare_twitch(
        monkeypatch,
        {
            "dead-by-daylight": datetime(2099, 1, 1),
            "special-events": datetime(2099, 2, 1),
        },
    )
    twitch.twitchdrops_app_catalog_complete = False
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: {
            "urgent-external-game": datetime(2050, 1, 1)
        },
    )

    result = twitch.get_wildcard_categories_with_active_drops()

    assert result == [
        "urgent-external-game",
        "dead-by-daylight",
        "special-events",
    ]
    assert twitch.category_campaign_deadlines == {
        "urgent-external-game": datetime(2050, 1, 1),
        "dead-by-daylight": datetime(2099, 1, 1),
        "special-events": datetime(2099, 2, 1),
    }


def test_wildcard_does_not_override_twitch_authoritative_deadline(monkeypatch):
    twitch = _bare_twitch(
        monkeypatch, {"dead-by-daylight": datetime(2099, 1, 1)}
    )
    twitch.twitchdrops_app_catalog_complete = False
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: {
            "dead-by-daylight": datetime(2020, 1, 1),
            "external-game": datetime(2050, 1, 1),
        },
    )

    result = twitch.get_wildcard_categories_with_active_drops()

    assert result == ["external-game", "dead-by-daylight"]
    assert twitch.category_campaign_deadlines["dead-by-daylight"] == datetime(
        2099, 1, 1
    )


def test_wildcard_external_addition_survives_fallback_mutating_known_slugs(
    monkeypatch,
):
    # Regression test: the real __twitchdrops_app_fallback records every
    # front-page match (not just Twitch-authoritative ones) by mutating the
    # `known_category_slugs` set it's given, in place -- it does this for its
    # own internal "skip if already Twitch-authoritative" bookkeeping. The
    # wildcard pass must diff fallback_deadlines against a snapshot taken
    # *before* that call, not the (now mutated) live set, or every external
    # addition the fallback just found would appear to already be a "Twitch"
    # slug and get silently dropped -- exactly what happened in production
    # (see the coffeedicee bug report: "0 external additions" forever, even
    # with dozens of genuinely open external campaigns logged moments
    # earlier).
    twitch = _bare_twitch(
        monkeypatch, {"dead-by-daylight": datetime(2099, 1, 1)}
    )
    twitch.twitchdrops_app_catalog_complete = False

    def mutating_fallback(self, categories, known_slugs):
        known_slugs.add("urgent-external-game")
        return {"urgent-external-game": datetime(2050, 1, 1)}

    monkeypatch.setattr(
        Twitch, "_Twitch__twitchdrops_app_fallback", mutating_fallback
    )

    result = twitch.get_wildcard_categories_with_active_drops()

    assert result == ["urgent-external-game", "dead-by-daylight"]


def test_wildcard_reuses_preloaded_external_catalog(monkeypatch):
    twitch = _bare_twitch(
        monkeypatch, {"dead-by-daylight": datetime(2099, 1, 1)}
    )
    twitch.twitchdrops_app_deadlines = {
        "urgent-external-game": datetime(2050, 1, 1)
    }
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: pytest.fail(
            "the preloaded full catalog should be reused"
        ),
    )

    result = twitch.get_wildcard_categories_with_active_drops(
        refresh_external_catalog=False
    )

    assert result == ["urgent-external-game", "dead-by-daylight"]


class FakeWebSocketsPool:
    def __init__(self):
        self.topics = []
        self.removed = []

    def submit(self, topic):
        self.topics.append(topic)

    def remove_streamer_topics(self, streamer):
        self.removed.append(streamer.username)


class FakeIrcChat:
    def __init__(self):
        self.stopped = False
        self.joined = False

    def is_alive(self):
        return True

    def stop(self):
        self.stopped = True

    def join(self, timeout=None):
        self.joined = True


class FakeTwitch:
    def __init__(self, eligible_categories, wildcard_categories, empty_categories=()):
        self.eligible_categories = eligible_categories
        self.wildcard_categories = wildcard_categories
        # Categories that are eligible (an active campaign exists) but have
        # zero live matching channels right now -- lets tests reproduce "a
        # preferred category is technically eligible but produced no
        # streamer this cycle" without needing real campaign/stream data.
        self.empty_categories = set(empty_categories)
        self.wildcard_calls = []
        self.filter_calls = []
        self.inventory_fetch_count = 0
        self.selectors = []
        self.twitch_login = SimpleNamespace(get_auth_token=lambda: "token")
        self.discovered_open_drop_campaigns = None

    def get_drops_inventory(self):
        self.inventory_fetch_count += 1
        return {"fetched": self.inventory_fetch_count}

    def filter_categories_with_active_drops(self, categories, **kwargs):
        self.filter_calls.append(kwargs)
        return self.eligible_categories

    def get_category_slugs(self, categories):
        return {category for category in categories}

    def get_game_name_slug(self, game_name):
        return (game_name or "").lower().replace(" ", "-")

    def get_wildcard_categories_with_active_drops(self, **kwargs):
        self.wildcard_calls.append(kwargs)
        return self.wildcard_categories

    def get_live_streamers_for_category(self, selector, **kwargs):
        self.selectors.append((selector, kwargs))
        if selector in self.empty_categories:
            return []
        return [f"{selector}-streamer"]

    def get_channel_id(self, username):
        return f"id-{username}"

    def load_channel_points_context(self, streamer):
        return None

    def check_streamer_online(self, streamer):
        streamer.is_online = True


def _bare_miner(twitch):
    defaults = StreamerSettings(chat=ChatPresence.NEVER)
    defaults.default()
    defaults.bet.default()
    Settings.streamer_settings = defaults

    miner = TwitchChannelPointsMiner.__new__(TwitchChannelPointsMiner)
    miner.username = "testuser"
    miner.twitch = twitch
    miner.streamers = []
    miner.original_streamers = []
    miner.ws_pool = FakeWebSocketsPool()
    miner.config_reload_lock = threading.Lock()
    miner.sync_campaigns_thread = object()
    return miner


def test_wildcard_discovery_only_runs_when_preferred_pass_finds_a_streamer():
    # The preferred pass here has an eligible category AND finds a live
    # streamer for it -- capacity isn't idle, so wildcard must stand down.
    twitch = FakeTwitch(eligible_categories=["preferred-game"], wildcard_categories=[])
    miner = _bare_miner(twitch)

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["preferred-game"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
        wildcard_category_limit=10,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    )

    assert twitch.wildcard_calls == []
    assert [streamer.username for streamer in miner.streamers] == [
        "preferred-game-streamer"
    ]
    assert all(
        streamer.from_wildcard_category is False for streamer in miner.streamers
    )


def test_wildcard_discovery_runs_when_eligible_category_has_no_live_streamer():
    # Reproduces the real-world case: a preferred category has an active
    # campaign (so eligible_categories is non-empty) but zero live matching
    # channels this cycle -- capacity is genuinely idle, so wildcard must
    # still trigger even though no preferred category was "exhausted" in the
    # old (campaign-eligibility-only) sense.
    twitch = FakeTwitch(
        eligible_categories=["pokemon-go"],
        wildcard_categories=["some-other-game"],
        empty_categories={"pokemon-go"},
    )
    miner = _bare_miner(twitch)

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["pokemon-go"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
        wildcard_category_limit=10,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    )

    assert len(twitch.wildcard_calls) == 1
    assert [streamer.username for streamer in miner.streamers] == [
        "some-other-game-streamer"
    ]
    assert miner.streamers[0].from_wildcard_category is True


def test_standdown_does_not_retire_existing_wildcard_streamer_in_unrelated_game():
    # Regression test for the review-identified bug: a preferred category
    # (gameA) becoming eligible must not retire a wildcard streamer tracked
    # in a completely unrelated game (gameX) whose own campaign was never
    # re-evaluated this cycle.
    twitch = FakeTwitch(eligible_categories=["gameA"], wildcard_categories=[])
    miner = _bare_miner(twitch)
    gamex = Streamer("gamex-streamer", from_category=True, from_wildcard_category=True)
    gamex.is_online = True
    gamex.stream.game = {"name": "GameX", "displayName": "GameX"}
    gamex.irc_chat = FakeIrcChat()
    miner.streamers = [gamex]
    miner.original_streamers = [10]

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["gameA", "gameB"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
        wildcard_category_limit=10,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    )

    # Discovery correctly stood down (the traffic safeguard, unchanged)...
    assert twitch.wildcard_calls == []
    # ...but the previously-tracked wildcard streamer must survive untouched
    # (gameA legitimately gained its own preferred-category streamer too --
    # that's expected and irrelevant to gameX's fate).
    assert gamex in miner.streamers
    assert gamex.from_wildcard_category is True
    assert gamex.from_category is True
    assert "gamex-streamer" not in miner.ws_pool.removed
    assert gamex.irc_chat.stopped is False


def test_real_wildcard_discovery_still_retires_genuinely_closed_streamer():
    # Regression guard: confirm the stand-down fix didn't disable legitimate
    # cleanup. This cycle preferred categories are genuinely exhausted, a
    # real wildcard discovery pass runs, and it no longer includes gameX at
    # all -- gameX must still be retired in this case.
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=["still-open-game"])
    miner = _bare_miner(twitch)
    gamex = Streamer("gamex-streamer", from_category=True, from_wildcard_category=True)
    gamex.is_online = True
    gamex.stream.game = {"name": "GameX", "displayName": "GameX"}
    gamex.irc_chat = FakeIrcChat()
    miner.streamers = [gamex]
    miner.original_streamers = [10]

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["gameA"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
        wildcard_category_limit=10,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    )

    assert len(twitch.wildcard_calls) == 1
    assert "gamex-streamer" not in [
        streamer.username for streamer in miner.streamers
    ]
    assert miner.ws_pool.removed == ["gamex-streamer"]
    assert gamex.irc_chat.stopped is True


def test_wildcard_streamer_survives_transient_standdown_then_resumes():
    # Full scenario from the review narrative: (a) wildcard discovers and
    # tracks gameX while preferred categories are idle; (b) a preferred
    # category becomes eligible for one cycle -- gameX must survive
    # untouched, not rediscovered; (c) the preferred category closes again
    # -- wildcard discovery resumes and gameX is still the same tracked
    # instance, confirming it was never actually dropped in step (b).
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=["gamex"])
    miner = _bare_miner(twitch)
    refresh_kwargs = dict(
        wildcard_categories=True,
        wildcard_category_limit=10,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    )

    # (a) idle preferred categories -> wildcard discovers and tracks gameX.
    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["gameA"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        **refresh_kwargs,
    )
    assert [streamer.username for streamer in miner.streamers] == ["gamex-streamer"]
    tracked = miner.streamers[0]
    assert tracked.from_wildcard_category is True

    # (b) gameA becomes eligible for one cycle -> wildcard stands down, but
    # gameX must not be retired.
    twitch.eligible_categories = ["gameA"]
    twitch.wildcard_calls = []
    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["gameA"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        **refresh_kwargs,
    )
    # gameA being eligible legitimately adds its own preferred streamer, but
    # gameX (still the same tracked instance) must survive alongside it,
    # untouched, rather than being retired or replaced.
    assert twitch.wildcard_calls == []
    assert tracked in miner.streamers
    assert tracked.from_wildcard_category is True
    assert "gamex-streamer" not in miner.ws_pool.removed

    # (c) gameA closes again -> wildcard discovery resumes and finds gameX
    # still tracked, without having rediscovered it from scratch.
    twitch.eligible_categories = []
    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["gameA"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        **refresh_kwargs,
    )
    assert len(twitch.wildcard_calls) == 1
    assert tracked in miner.streamers
    assert "gamex-streamer" not in miner.ws_pool.removed


def test_wildcard_discovery_runs_and_tags_streamers_once_preferred_is_exhausted():
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=["wild-game"])
    miner = _bare_miner(twitch)

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["preferred-game"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
        wildcard_category_limit=5,
        wildcard_category_streamer_limit=1,
        wildcard_category_pin_active=True,
    )

    assert len(twitch.wildcard_calls) == 1
    assert twitch.wildcard_calls[0]["limit"] == 5
    assert twitch.wildcard_calls[0]["exclude_category_slugs"] == {"preferred-game"}
    assert [streamer.username for streamer in miner.streamers] == ["wild-game-streamer"]
    assert miner.streamers[0].from_category is True
    assert miner.streamers[0].from_wildcard_category is True


def test_refresh_fetches_drops_inventory_once_and_shares_it_with_wildcard_pass():
    # Both filter_categories_with_active_drops and
    # get_wildcard_categories_with_active_drops need the drops inventory;
    # the miner should fetch it once per refresh and pass the same object to
    # both instead of each fetching it independently over GraphQL.
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=["wild-game"])
    miner = _bare_miner(twitch)

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["preferred-game"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
    )

    assert twitch.inventory_fetch_count == 1
    assert twitch.filter_calls[0]["inventory"] == {"fetched": 1}
    assert "include_all_fallback" not in twitch.filter_calls[0]
    assert twitch.wildcard_calls[0]["inventory"] == {"fetched": 1}
    assert twitch.wildcard_calls[0]["refresh_external_catalog"] is True


def test_hot_reload_updates_periodic_wildcard_settings():
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=[])
    miner = _bare_miner(twitch)

    miner.refresh_categories(
        {
            "categories": [],
            "wildcard_categories": True,
            "wildcard_category_limit": 4,
            "wildcard_category_streamer_limit": 2,
            "wildcard_category_pin_active": False,
        }
    )

    assert miner.wildcard_categories is True
    assert miner.wildcard_category_limit == 4
    assert miner.wildcard_category_streamer_limit == 2
    assert miner.wildcard_category_pin_active is False
    assert twitch.wildcard_calls[0]["limit"] == 4
    assert twitch.wildcard_calls[0]["pin_active"] is False


def test_hot_reload_normalizes_invalid_wildcard_limits():
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=[])
    miner = _bare_miner(twitch)

    miner.refresh_categories(
        {
            "categories": [],
            "wildcard_categories": True,
            "wildcard_category_limit": None,
            "wildcard_category_streamer_limit": 0,
        }
    )

    assert miner.wildcard_category_limit == 10
    assert miner.wildcard_category_streamer_limit == 1


def test_refresh_skips_inventory_fetch_when_drops_disabled():
    twitch = FakeTwitch(eligible_categories=[], wildcard_categories=[])
    miner = _bare_miner(twitch)

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["preferred-game"],
        [],
        False,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=True,
    )

    assert twitch.inventory_fetch_count == 0


def test_wildcard_disabled_retires_previously_discovered_wildcard_streamers():
    twitch = FakeTwitch(eligible_categories=["preferred-game"], wildcard_categories=[])
    miner = _bare_miner(twitch)
    stale_wildcard = Streamer(
        "stale-wild", from_category=True, from_wildcard_category=True
    )
    miner.streamers = [stale_wildcard]
    miner.original_streamers = [10]

    miner._TwitchChannelPointsMiner__refresh_category_streamers(
        ["preferred-game"],
        [],
        True,
        2,
        "VIEWERS_DESC",
        "ORDER",
        ChatPresence.NEVER,
        logging.INFO,
        wildcard_categories=False,
    )

    assert "stale-wild" not in [s.username for s in miner.streamers]
    assert "stale-wild" in miner.ws_pool.removed


def test_reconcile_scoped_to_wildcard_does_not_evict_preferred_streamers():
    miner = TwitchChannelPointsMiner.__new__(TwitchChannelPointsMiner)
    preferred = Streamer("preferred", from_category=True)
    wildcard = Streamer("wild", from_category=True, from_wildcard_category=True)
    miner.streamers = [preferred, wildcard]
    miner.original_streamers = [10, 20]
    miner.ws_pool = FakeWebSocketsPool()

    # A wildcard-scoped reconcile with an empty discovery list must only
    # retire the wildcard streamer, not the preferred-category one.
    miner._TwitchChannelPointsMiner__reconcile_category_streamers([], wildcard=True)

    assert [s.username for s in miner.streamers] == ["preferred"]
    assert miner.ws_pool.removed == ["wild"]


def test_reconcile_scoped_to_preferred_does_not_evict_wildcard_streamers():
    miner = TwitchChannelPointsMiner.__new__(TwitchChannelPointsMiner)
    preferred = Streamer("preferred", from_category=True)
    wildcard = Streamer("wild", from_category=True, from_wildcard_category=True)
    miner.streamers = [preferred, wildcard]
    miner.original_streamers = [10, 20]
    miner.ws_pool = FakeWebSocketsPool()

    miner._TwitchChannelPointsMiner__reconcile_category_streamers([], wildcard=False)

    assert [s.username for s in miner.streamers] == ["wild"]
    assert miner.ws_pool.removed == ["preferred"]
