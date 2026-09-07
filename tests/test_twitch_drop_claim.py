import importlib
import logging
from threading import Lock
from types import SimpleNamespace

from TwitchChannelPointsMiner.classes.entities.Campaign import Campaign
from TwitchChannelPointsMiner.classes.Twitch import Twitch


def campaign_data():
    return {
        "id": "campaign-1",
        "game": {"displayName": "Example Game"},
        "name": "Example Campaign",
        "status": "ACTIVE",
        "allow": {"channels": []},
        "startAt": "2020-01-01T00:00:00Z",
        "endAt": "2099-01-01T00:00:00Z",
        "timeBasedDrops": [
            {
                "id": "drop-1",
                "name": "Reward",
                "benefitEdges": [{"benefit": {"name": "Badge"}}],
                "requiredMinutesWatched": 10,
                "startAt": "2020-01-01T00:00:00Z",
                "endAt": "2099-01-01T00:00:00Z",
            }
        ],
    }


def bare_twitch(monkeypatch, claim_status="ELIGIBLE_FOR_ALL"):
    twitch = object.__new__(Twitch)
    twitch.completed_drop_campaigns = set()
    twitch.campaign_game_slugs = {}
    twitch.log_drop_checks = False
    twitch.category_log_level = logging.INFO
    twitch.category_campaign_eligibility = {}
    twitch.evaluated_category_campaigns = set()
    twitch.twitchdrops_app_campaigns = {}
    twitch.advertised_drop_campaigns = {}
    twitch.campaign_channel_ids = {}
    twitch.campaign_detail_attempts = set()
    twitch.gql = SimpleNamespace(
        claim_drop_rewards=lambda drop_instance_id: SimpleNamespace(
            status=claim_status, errors=[]
        )
    )
    monkeypatch.setattr(
        Twitch, "_Twitch__drop_variant_entries_from_drop", lambda self, drop: []
    )
    return twitch


def advertised_campaign():
    campaign = campaign_data()
    campaign["timeBasedDrops"][0]["benefitEdges"] = [
        {
            "benefit": {
                "id": "reward-1",
                "name": "Reusable Reward",
                "imageAssetURL": "https://example.test/reward.png",
            }
        }
    ]
    return campaign


def category_streamer():
    return SimpleNamespace(
        username="drops-channel",
        channel_id="12345",
        from_category=True,
        from_badge_campaign=False,
        stream=SimpleNamespace(game_name=lambda: "Example Game"),
    )


def test_advertised_campaign_normalizes_null_allow(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    campaign = advertised_campaign()
    campaign["allow"] = None

    normalized = twitch._Twitch__normalize_advertised_campaign(campaign)

    assert normalized["allow"] == {"channels": None}
    assert Campaign(normalized).channels == []


def test_channel_campaign_uses_broadcaster_id_and_in_window_award(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    campaign = advertised_campaign()
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[campaign], campaigns_available=True
        )
    )
    twitch.awarded_game_event_drops = {
        "reward-1": {
            "id": "reward-1",
            "name": "Reusable Reward",
            "imageURL": "https://example.test/reward.png",
            "lastAwardedAt": "2025-01-01T00:00:00Z",
        }
    }
    detail_contexts = []

    def campaign_details(self, campaigns, campaign_channel_id_by_id=None):
        detail_contexts.append(campaign_channel_id_by_id)
        return []

    monkeypatch.setattr(Twitch, "_Twitch__get_campaigns_details", campaign_details)

    assert twitch._Twitch__get_campaign_ids_from_streamer(category_streamer()) == [
        "campaign-1"
    ]
    assert detail_contexts == [{"campaign-1": "12345"}]
    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        0,
        1,
    )


def test_reused_reward_from_earlier_campaign_remains_incomplete(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    campaign = advertised_campaign()
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[campaign], campaigns_available=True
        )
    )
    twitch.awarded_game_event_drops = {
        "reward-1": {
            "id": "reward-1",
            "name": "Reusable Reward",
            "imageURL": "https://example.test/reward.png",
            "lastAwardedAt": "2019-12-31T23:59:59Z",
        }
    }
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, campaigns, campaign_channel_id_by_id=None: [],
    )

    twitch._Twitch__get_campaign_ids_from_streamer(category_streamer())

    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        1,
        1,
    )


def test_overlapping_campaigns_with_same_reward_remain_incomplete(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    first_campaign = advertised_campaign()
    second_campaign = advertised_campaign()
    second_campaign["id"] = "campaign-2"
    second_campaign["name"] = "Second Campaign"
    second_campaign["timeBasedDrops"][0]["id"] = "drop-2"
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[first_campaign, second_campaign], campaigns_available=True
        )
    )
    twitch.awarded_game_event_drops = {
        "reward-1": {
            "id": "reward-1",
            "name": "Reusable Reward",
            "imageURL": "https://example.test/reward.png",
            "lastAwardedAt": "2025-01-01T00:00:00Z",
        }
    }
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, campaigns, campaign_channel_id_by_id=None: [],
    )

    twitch._Twitch__get_campaign_ids_from_streamer(category_streamer())

    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        2,
        2,
    )


def test_authoritative_channel_campaign_result_blocks_wrong_game(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[
                {
                    "id": "unrelated",
                    "game": {"name": "Special Events"},
                    "timeBasedDrops": [],
                }
            ],
            campaigns_available=True,
        )
    )
    twitch.discovered_open_drop_campaigns = [advertised_campaign()]

    assert twitch._Twitch__get_campaign_ids_from_streamer(category_streamer()) == []
    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        0,
        0,
    )


def test_wildcard_external_campaign_requires_matching_twitch_campaign(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    campaign = advertised_campaign()
    campaign["name"] = "Different Campaign"
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[campaign], campaigns_available=True
        )
    )
    twitch.twitchdrops_app_campaigns = {
        "example-game": [
            {
                "id": "external-campaign",
                "name": "Expected Campaign",
                "channels": [],
            }
        ]
    }
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, campaigns, campaign_channel_id_by_id=None: [],
    )
    streamer = category_streamer()
    streamer.from_wildcard_category = True

    assert twitch._Twitch__get_campaign_ids_from_streamer(streamer) == []
    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        0,
        1,
    )


def test_channel_allowlisted_in_authoritative_campaign_survives_empty_channel_query(
    monkeypatch,
):
    # Regression: campaign-restricted categories (e.g. Pokémon GO co-streams)
    # resolve their channel allow-list from Twitch's own authoritative
    # campaign data, so the external gist index is intentionally left empty
    # for them. A channel's own advertised-campaigns query can still come
    # back empty even though it is allow-listed there (common for official
    # co-stream/viewing-party channels) - that must not clobber the eligibility
    # already established from the allow-list.
    twitch = bare_twitch(monkeypatch)
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[], campaigns_available=True
        )
    )
    twitch.discovered_open_drop_campaigns = []
    twitch.active_drop_campaigns = {
        "example-game": [
            {
                "id": "restricted-campaign-1",
                "name": "Restricted Campaign",
                "channels": ["drops-channel"],
            }
        ]
    }
    twitch.category_campaign_eligibility[("example-game", "drops-channel")] = (1, 1)

    assert twitch._Twitch__get_campaign_ids_from_streamer(category_streamer()) == [
        "restricted-campaign-1"
    ]
    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        1,
        1,
    )


def test_channel_not_in_authoritative_campaign_allowlist_still_blocked(monkeypatch):
    # The same authoritative campaign exists, but this channel isn't on its
    # allow-list, so the empty channel query result must still stand.
    twitch = bare_twitch(monkeypatch)
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[], campaigns_available=True
        )
    )
    twitch.discovered_open_drop_campaigns = []
    twitch.active_drop_campaigns = {
        "example-game": [
            {
                "id": "restricted-campaign-1",
                "name": "Restricted Campaign",
                "channels": ["some-other-channel"],
            }
        ]
    }

    assert twitch._Twitch__get_campaign_ids_from_streamer(category_streamer()) == []
    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        0,
        0,
    )


def test_unrestricted_authoritative_campaign_does_not_shield_unrelated_channel(
    monkeypatch,
):
    # Regression: an unrestricted authoritative campaign (empty "channels")
    # sitting alongside a genuinely restricted one for the same game must not
    # make every channel of that game look allow-listed. Only an explicit,
    # non-empty allow-list containing this channel should override the
    # channel's own empty query result.
    twitch = bare_twitch(monkeypatch)
    twitch.gql = SimpleNamespace(
        get_available_drops=lambda channel_id: SimpleNamespace(
            campaigns=[], campaigns_available=True
        )
    )
    twitch.discovered_open_drop_campaigns = []
    twitch.active_drop_campaigns = {
        "example-game": [
            {
                "id": "restricted-campaign-1",
                "name": "Restricted Campaign",
                "channels": ["some-other-channel"],
            },
            {
                "id": "unrestricted-campaign-1",
                "name": "Unrestricted Campaign",
                "channels": [],
            },
        ]
    }

    assert twitch._Twitch__get_campaign_ids_from_streamer(category_streamer()) == []
    assert twitch.category_campaign_eligibility[("example-game", "drops-channel")] == (
        0,
        0,
    )


def test_drops_description_is_none_once_channel_check_confirmed_zero_campaigns(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    twitch.category_campaign_eligibility[("example-game", "drops-channel")] = (0, 0)
    # An unrelated global campaign for the same game must not resurrect a
    # description - the live per-channel check already ruled this channel out.
    twitch.discovered_open_drop_campaigns = [advertised_campaign()]

    assert twitch._Twitch__streamer_drops_description(category_streamer()) is None


def test_drops_description_uses_global_catalog_without_a_channel_check_yet(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    twitch.discovered_open_drop_campaigns = [advertised_campaign()]

    assert (
        twitch._Twitch__streamer_drops_description(category_streamer())
        == "Example Game drops"
    )


def test_drops_description_counts_authoritative_restricted_campaigns(monkeypatch):
    # The gist fallback is intentionally empty for an authoritative
    # campaign-restricted game, so the description must still count
    # campaigns from active_drop_campaigns rather than falling through to
    # the unscoped global catalog.
    twitch = bare_twitch(monkeypatch)
    twitch.discovered_open_drop_campaigns = []
    twitch.active_drop_campaigns = {
        "example-game": [
            {"id": "restricted-1", "name": "A", "channels": ["drops-channel"]},
            {"id": "restricted-2", "name": "B", "channels": ["some-other-channel"]},
        ]
    }

    assert (
        twitch._Twitch__streamer_drops_description(category_streamer())
        == "Example Game drops (1 of 2 campaigns)"
    )


def test_campaign_deadline_logs_include_game_name(monkeypatch, caplog):
    twitch = bare_twitch(monkeypatch)
    campaign = campaign_data()
    campaign["game"] = {"displayName": "Warframe"}
    campaign["name"] = "Prime Time #493"
    campaign["timeBasedDrops"] = [
        {
            "id": "possible-drop",
            "name": "Random Hex Treasure",
            "benefitEdges": [],
            "requiredMinutesWatched": 10,
            "startAt": "2020-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
        },
        {
            "id": "impossible-drop",
            "name": "Long Reward",
            "benefitEdges": [],
            "requiredMinutesWatched": 100000000,
            "startAt": "2020-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
        },
    ]
    caplog.set_level(logging.INFO)

    twitch._Twitch__active_incomplete_drop_deadline(
        campaign,
        completed_drop_ids=set(),
        awarded_benefit_ids=set(),
        awarded_benefit_fingerprints=set(),
    )

    assert (
        "Enough time for [Warframe] Prime Time #493 - Random Hex Treasure:"
        in caplog.text
    )
    assert (
        "Not enough time for [Warframe] Prime Time #493 - Long Reward:" in caplog.text
    )


def test_campaign_deadline_logs_unknown_for_malformed_game(monkeypatch, caplog):
    twitch = bare_twitch(monkeypatch)
    campaign = campaign_data()
    campaign["game"] = "unexpected-game-value"
    caplog.set_level(logging.INFO)

    twitch._Twitch__active_incomplete_drop_deadline(
        campaign,
        completed_drop_ids=set(),
        awarded_benefit_ids=set(),
        awarded_benefit_fingerprints=set(),
    )

    assert "Enough time for [Unknown Game] Example Campaign - Reward:" in caplog.text


def test_claiming_final_drop_waits_for_inventory_confirmation(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    campaign = Campaign(campaign_data())
    drop = campaign.drops[0]
    drop.drop_instance_id = "instance-1"

    assert twitch.claim_drop(drop, campaign=campaign) is True
    assert twitch.completed_drop_campaigns == set()


def test_owned_badge_is_removed_from_advertised_campaign(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.available_badge_names = {"wardog"}
    twitch.drop_badge_rewards = [
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "WARDOGS Beta & Launch",
            "reward_name": "WARDOG",
            "badge_names": ["WARDOG", "wardog"],
            "watch_eligible": True,
        }
    ]
    data = campaign_data()
    data["game"] = {"displayName": "WARDOGS"}
    data["name"] = "WARDOGS Beta & Launch"
    data["timeBasedDrops"][0]["name"] = "WARDOG"
    data["timeBasedDrops"][0]["benefitEdges"] = [{"benefit": {"name": "WARDOG"}}]
    campaign = Campaign(data)
    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: {})

    synced = twitch._Twitch__sync_campaigns([campaign])

    assert synced[0].drops == []


def test_unearned_badge_remains_in_advertised_campaign(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.available_badge_names = {"some other badge"}
    twitch.drop_badge_rewards = [
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "WARDOGS Beta & Launch",
            "reward_name": "WARDOG",
            "badge_names": ["WARDOG", "wardog"],
            "watch_eligible": True,
        }
    ]
    data = campaign_data()
    data["game"] = {"displayName": "WARDOGS"}
    data["name"] = "WARDOGS Beta & Launch"
    data["timeBasedDrops"][0]["name"] = "WARDOG"
    campaign = Campaign(data)
    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: {})

    synced = twitch._Twitch__sync_campaigns([campaign])

    assert [drop.name for drop in synced[0].drops] == ["WARDOG"]


def test_subscription_badge_is_not_treated_as_watch_eligible(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.available_badge_names = set()
    twitch.drop_badge_rewards = [
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "",
            "reward_name": "WARLORD",
            "badge_names": ["WARLORD", "warlord"],
            "watch_eligible": False,
        }
    ]
    data = campaign_data()
    data["game"] = {"displayName": "WARDOGS"}
    data["name"] = "WARDOGS Beta & Launch"
    data["timeBasedDrops"][0]["name"] = "WARLORD"
    data["timeBasedDrops"][0]["benefitEdges"] = [{"benefit": {"name": "WARLORD"}}]
    campaign = Campaign(data)
    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: {})

    synced = twitch._Twitch__sync_campaigns([campaign])

    assert synced[0].drops == []


def test_wardogs_campaign_with_owned_and_subscription_badges_is_ineligible(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    twitch.available_badge_names = {"wardog"}
    twitch.drop_badge_rewards = [
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "WARDOGS Beta & Launch",
            "reward_name": "WARDOG",
            "badge_names": ["WARDOG", "wardog"],
            "watch_eligible": True,
        },
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "",
            "reward_name": "WARLORD",
            "badge_names": ["WARLORD", "warlord"],
            "watch_eligible": False,
        },
    ]
    data = campaign_data()
    data["game"] = {"displayName": "WARDOGS"}
    data["name"] = "WARDOGS Beta & Launch"
    data["timeBasedDrops"][0]["name"] = "WARDOG"
    data["timeBasedDrops"][0]["benefitEdges"] = [{"benefit": {"name": "WARDOG"}}]
    warlord = dict(data["timeBasedDrops"][0])
    warlord.update(
        {
            "id": "drop-2",
            "name": "WARLORD",
            "benefitEdges": [{"benefit": {"name": "WARLORD"}}],
        }
    )
    data["timeBasedDrops"].append(warlord)
    campaign = Campaign(data)
    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: {})

    synced = twitch._Twitch__sync_campaigns([campaign])

    assert synced[0].drops == []


def test_owned_badge_filter_preserves_other_campaign_rewards(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.available_badge_names = {"wardog"}
    twitch.drop_badge_rewards = [
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "WARDOGS Beta & Launch",
            "reward_name": "WARDOG",
            "badge_names": ["WARDOG", "wardog"],
            "watch_eligible": True,
        }
    ]
    data = campaign_data()
    data["game"] = {"displayName": "WARDOGS"}
    data["name"] = "WARDOGS Beta & Launch"
    data["timeBasedDrops"][0]["name"] = "WARDOG"
    ordinary_drop = dict(data["timeBasedDrops"][0])
    ordinary_drop.update(
        {
            "id": "drop-2",
            "name": "Ordinary Reward",
            "benefitEdges": [{"benefit": {"name": "Ordinary Reward"}}],
        }
    )
    data["timeBasedDrops"].append(ordinary_drop)
    campaign = Campaign(data)
    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: {})

    synced = twitch._Twitch__sync_campaigns([campaign])

    assert [drop.name for drop in synced[0].drops] == ["Ordinary Reward"]


def test_drop_eligibility_rechecks_badges_learned_after_campaign_sync(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.available_badge_names = {"wardog"}
    twitch.drop_badge_rewards = []
    data = campaign_data()
    data["game"] = {"displayName": "WARDOGS"}
    data["name"] = "WARDOGS Beta & Launch"
    data["timeBasedDrops"][0]["name"] = "WARDOG"
    campaign = Campaign(data)
    stream = SimpleNamespace(
        campaigns=[campaign],
        campaigns_ids=[campaign.id],
        game_name=lambda: "WARDOGS",
    )
    streamer = SimpleNamespace(
        username="followed-channel",
        from_category=False,
        from_badge_campaign=False,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
        drops_condition=lambda: any(item.drops for item in stream.campaigns),
    )

    assert twitch._Twitch__drops_condition(streamer) is True

    twitch.drop_badge_rewards = [
        {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": "WARDOGS Beta & Launch",
            "reward_name": "WARDOG",
            "badge_names": ["WARDOG", "wardog"],
            "watch_eligible": True,
        }
    ]

    assert twitch._Twitch__drops_condition(streamer) is False
    assert campaign.drops == []


def test_completed_campaign_overrides_category_eligibility(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.completed_drop_campaigns.add("campaign-1")
    twitch.category_campaign_eligibility[("example-game", "channel")] = (1, 1)
    stream = SimpleNamespace(
        campaigns_ids=["campaign-1"],
        game_name=lambda: "Example Game",
    )
    streamer = SimpleNamespace(
        username="channel",
        from_category=True,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
    )

    assert twitch._Twitch__category_drops_condition(streamer) is False


def test_negative_category_refresh_does_not_resurrect_collected_fallback(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.evaluated_category_campaigns.add("example-game")
    twitch.twitchdrops_app_campaigns["example-game"] = [
        {"name": "Collected campaign", "channels": []}
    ]
    stream = SimpleNamespace(
        campaigns_ids=[],
        game_name=lambda: "Example Game",
    )
    streamer = SimpleNamespace(
        username="stale-channel",
        from_category=True,
        from_badge_campaign=False,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
    )

    assert twitch._Twitch__category_drops_condition(streamer) is False


def test_category_condition_uses_authoritative_restricted_campaign_when_eligibility_unset(
    monkeypatch,
):
    # Regression: when eligibility hasn't been cached yet for a campaign-
    # restricted category, the gist fallback (twitchdrops_app_campaigns) is
    # intentionally left empty once Twitch's own inventory is authoritative
    # for that game, so the condition must also honor active_drop_campaigns,
    # which is where the real allow-list ends up in that case.
    twitch = bare_twitch(monkeypatch)
    twitch.active_drop_campaigns = {
        "example-game": [
            {
                "id": "restricted-campaign-1",
                "name": "Restricted Campaign",
                "channels": ["channel"],
            }
        ]
    }
    stream = SimpleNamespace(
        campaigns_ids=[],
        game_name=lambda: "Example Game",
    )
    streamer = SimpleNamespace(
        username="channel",
        from_category=True,
        from_badge_campaign=False,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
    )

    assert twitch._Twitch__category_drops_condition(streamer) is True


def test_category_condition_blocks_channel_not_in_authoritative_restricted_allowlist(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    twitch.active_drop_campaigns = {
        "example-game": [
            {
                "id": "restricted-campaign-1",
                "name": "Restricted Campaign",
                "channels": ["some-other-channel"],
            }
        ]
    }
    stream = SimpleNamespace(
        campaigns_ids=[],
        game_name=lambda: "Example Game",
    )
    streamer = SimpleNamespace(
        username="channel",
        from_category=True,
        from_badge_campaign=False,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
    )

    assert twitch._Twitch__category_drops_condition(streamer) is False


def test_discovered_eligibility_applies_to_existing_configured_streamer(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    twitch.evaluated_category_campaigns.add("example-game")
    twitch.category_campaign_eligibility[("example-game", "configured")] = (1, 1)
    stream = SimpleNamespace(
        campaigns_ids=[],
        campaigns=[],
        game_name=lambda: "Example Game",
    )
    streamer = SimpleNamespace(
        username="configured",
        from_category=False,
        from_badge_campaign=False,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
        drops_condition=lambda: False,
    )

    assert twitch._Twitch__drops_condition(streamer) is True


def test_bulk_inventory_claim_waits_for_refreshed_inventory(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    data = campaign_data()
    data["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 10,
        "dropInstanceID": "instance-1",
        "isClaimed": False,
    }
    inventory = {"dropCampaignsInProgress": [data]}
    monkeypatch.setattr(Twitch, "_Twitch__get_inventory", lambda self: inventory)
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(twitch_module.time, "sleep", lambda seconds: None)

    twitch.claim_all_drops_from_inventory()

    assert twitch.completed_drop_campaigns == set()


def test_completed_reward_campaign_ids_suppress_stale_campaigns(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    inventory = {
        "completedRewardCampaigns": [
            {"id": "campaign-1"},
            {"campaign": {"id": "campaign-2"}},
        ]
    }

    completed = twitch._Twitch__completed_campaign_ids_from_inventory(inventory)
    twitch.completed_drop_campaigns.update(completed)

    assert twitch.completed_drop_campaigns == {"campaign-1", "campaign-2"}


def test_completed_badge_campaign_signatures_matches_by_name_and_end_time(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    inventory = {
        "completedRewardCampaigns": [
            {
                "campaign": {
                    "id": "twitch-real-id-1",
                    "game": {"displayName": "Infinity Nikki"},
                    "name": "Infinity Nikki Drops Campaign",
                    "endAt": "2026-08-01T13:58:17.429Z",
                }
            },
            # Missing end time: must not produce a signature, mirroring
            # __fallback_reward_was_captured's strictness (no exact-equality
            # fallback when tolerance-based comparison can't be done).
            {
                "campaign": {
                    "id": "twitch-real-id-2",
                    "game": {"displayName": "No End Time Game"},
                    "name": "Some Campaign",
                }
            },
        ]
    }

    signatures = twitch.completed_badge_campaign_signatures(inventory)

    assert len(signatures) == 1
    (game_slug, campaign_name, ends_at_epoch), = signatures
    assert game_slug == "infinity-nikki"
    assert campaign_name == "infinity nikki drops campaign"
    from datetime import datetime, timezone

    expected_epoch = datetime(
        2026, 8, 1, 13, 58, 17, 429000, tzinfo=timezone.utc
    ).timestamp()
    assert abs(ends_at_epoch - expected_epoch) < 0.01


def test_all_claimed_inventory_drops_confirm_campaign_completion(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    data = campaign_data()
    data["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 10,
        "dropInstanceID": "instance-1",
        "isClaimed": True,
    }

    completed = twitch._Twitch__completed_campaign_ids_from_inventory(
        {"dropCampaignsInProgress": [data]}
    )

    assert completed == {"campaign-1"}


def test_all_captured_unclaimed_inventory_drops_confirm_campaign_completion(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    data = campaign_data()
    data["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 10,
        "dropInstanceID": "instance-1",
        "isClaimed": False,
    }

    completed = twitch._Twitch__completed_campaign_ids_from_inventory(
        {"dropCampaignsInProgress": [data]}
    )

    assert completed == {"campaign-1"}


def test_partially_captured_inventory_campaign_is_not_completed(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    data = campaign_data()
    data["timeBasedDrops"].append(
        {
            "id": "drop-2",
            "name": "Second Reward",
            "benefitEdges": [{"benefit": {"name": "Badge"}}],
            "requiredMinutesWatched": 10,
            "startAt": "2020-01-01T00:00:00Z",
            "endAt": "2099-01-01T00:00:00Z",
        }
    )
    data["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 10,
        "dropInstanceID": "instance-1",
        "isClaimed": False,
    }
    data["timeBasedDrops"][1]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 4,
        "dropInstanceID": None,
        "isClaimed": False,
    }

    completed = twitch._Twitch__completed_campaign_ids_from_inventory(
        {"dropCampaignsInProgress": [data]}
    )

    assert completed == set()


def test_fully_captured_unclaimed_campaign_stops_category_watch(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    data = campaign_data()
    data["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 10,
        "dropInstanceID": "instance-1",
        "isClaimed": False,
    }
    twitch.completed_drop_campaigns.update(
        twitch._Twitch__completed_campaign_ids_from_inventory(
            {"dropCampaignsInProgress": [data]}
        )
    )
    twitch.category_campaign_eligibility[("example-game", "channel")] = (1, 1)
    stream = SimpleNamespace(
        campaigns_ids=["campaign-1"],
        game_name=lambda: "Example Game",
    )
    streamer = SimpleNamespace(
        username="channel",
        from_category=True,
        settings=SimpleNamespace(claim_drops=True),
        is_online=True,
        stream=stream,
    )

    assert twitch._Twitch__category_drops_condition(streamer) is False


def test_completed_campaign_keeps_game_authoritative_after_twitch_removes_it(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    sparse_campaign = campaign_data()
    sparse_campaign.pop("game")
    sparse_campaign.pop("timeBasedDrops")
    dashboard_campaigns = [sparse_campaign]
    inventory_campaign = campaign_data()
    inventory_campaign["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 0,
        "dropInstanceID": None,
        "isClaimed": False,
    }
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_drops_dashboard",
        lambda self, status="OPEN": dashboard_campaigns,
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_reward_campaigns_raw_query",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_open_drop_campaigns_from_helix",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, campaigns: campaigns,
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__awarded_benefits",
        lambda self, inventory: (set(), set()),
    )

    twitch._Twitch__active_drop_category_slugs_from_campaigns(
        {"dropCampaignsInProgress": [inventory_campaign]}, {"example-game"}
    )
    dashboard_campaigns.clear()

    deadlines, twitch_games = twitch._Twitch__active_drop_category_slugs_from_campaigns(
        {
            "dropCampaignsInProgress": [],
            "completedRewardCampaigns": [{"id": "campaign-1"}],
        },
        {"example-game"},
    )

    assert deadlines == {}
    assert twitch_games == {"example-game"}


def test_active_campaign_keeps_authenticated_channel_allowlist(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    campaign = campaign_data()
    campaign["allow"] = {
        "channels": [
            {"id": "100", "name": "AllowedOne"},
            {"id": "200", "name": "AllowedTwo"},
        ]
    }
    campaign["timeBasedDrops"][0]["self"] = {
        "hasPreconditionsMet": True,
        "currentMinutesWatched": 5,
        "dropInstanceID": None,
        "isClaimed": False,
    }
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_drops_dashboard",
        lambda self, status="OPEN": [campaign],
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_reward_campaigns_raw_query",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_open_drop_campaigns_from_helix",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, campaigns: campaigns,
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__awarded_benefits",
        lambda self, inventory: (set(), set()),
    )

    deadlines, twitch_games = twitch._Twitch__active_drop_category_slugs_from_campaigns(
        {"dropCampaignsInProgress": [campaign]}, {"example-game"}
    )

    assert set(deadlines) == {"example-game"}
    assert twitch_games == {"example-game"}
    assert twitch.active_drop_campaigns == {
        "example-game": [
            {
                "id": "campaign-1",
                "name": "Example Campaign",
                "channels": ["allowedone", "allowedtwo"],
            }
        ]
    }


def test_completed_campaign_game_is_resolved_when_open_dashboard_omits_it(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    detail_requests = []
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_drops_dashboard",
        lambda self, status="OPEN": [],
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_reward_campaigns_raw_query",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_open_drop_campaigns_from_helix",
        lambda self: ([], []),
    )

    def resolve_details(self, campaigns):
        detail_requests.extend(campaigns)
        return [campaign_data()] if campaigns else []

    monkeypatch.setattr(Twitch, "_Twitch__get_campaigns_details", resolve_details)
    monkeypatch.setattr(
        Twitch,
        "_Twitch__awarded_benefits",
        lambda self, inventory: (set(), set()),
    )

    deadlines, twitch_games = twitch._Twitch__active_drop_category_slugs_from_campaigns(
        {"completedRewardCampaigns": [{"id": "campaign-1"}]},
        {"example-game"},
    )

    assert detail_requests == [{"id": "campaign-1"}]
    assert deadlines == {}
    assert twitch_games == {"example-game"}


def test_full_completed_inventory_campaign_prevents_fallback_resurrection(monkeypatch):
    twitch = bare_twitch(monkeypatch)
    detail_requests = []
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_drops_dashboard",
        lambda self, status="OPEN": [],
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_reward_campaigns_raw_query",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_open_drop_campaigns_from_helix",
        lambda self: ([], []),
    )

    def resolve_details(self, campaigns):
        detail_requests.extend(campaigns)
        return []

    monkeypatch.setattr(Twitch, "_Twitch__get_campaigns_details", resolve_details)
    monkeypatch.setattr(
        Twitch,
        "_Twitch__awarded_benefits",
        lambda self, inventory: (set(), set()),
    )
    completed_campaign = {
        "id": "minecraft-campaign",
        "name": "Boss Run Marathon",
        "status": "COMPLETED",
        "game": {"id": "27471", "displayName": "Minecraft"},
        "rewards": [{"name": "Frog Hoodie"}],
    }

    deadlines, twitch_games = twitch._Twitch__active_drop_category_slugs_from_campaigns(
        {"completedRewardCampaigns": [completed_campaign]},
        {"minecraft"},
    )

    assert detail_requests == []
    assert deadlines == {}
    assert twitch_games == {"minecraft"}
    assert twitch.campaign_game_slugs == {"minecraft-campaign": "minecraft"}


def test_wrapped_completed_inventory_campaign_prevents_fallback_resurrection(
    monkeypatch,
):
    twitch = bare_twitch(monkeypatch)
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_drops_dashboard",
        lambda self, status="OPEN": [],
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_reward_campaigns_raw_query",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_open_drop_campaigns_from_helix",
        lambda self: ([], []),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_campaigns_details",
        lambda self, campaigns: [],
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__awarded_benefits",
        lambda self, inventory: (set(), set()),
    )
    completed_record = {
        "campaign": {
            "id": "warhounds-campaign",
            "name": "Closed Playtest",
            "status": "COMPLETED",
            "game": {"displayName": "Warhounds"},
        }
    }

    deadlines, twitch_games = twitch._Twitch__active_drop_category_slugs_from_campaigns(
        {"completedRewardCampaigns": [completed_record]},
        {"warhounds"},
    )

    assert deadlines == {}
    assert twitch_games == {"warhounds"}
    assert twitch.campaign_game_slugs == {"warhounds-campaign": "warhounds"}


def test_drop_report_snapshot_uses_analytics_mutex():
    class RecordingLock:
        def __init__(self):
            self.entered = 0

        def __enter__(self):
            self.entered += 1

        def __exit__(self, exc_type, exc_value, traceback):
            return False

    twitch = object.__new__(Twitch)
    twitch.analytics_mutex = RecordingLock()
    twitch.drop_report_state = {"drop": {"current_minutes_watched": 25}}

    snapshot = twitch.drop_report_snapshot()

    assert snapshot == {"drop": {"current_minutes_watched": 25}}
    assert twitch.analytics_mutex.entered == 1
    assert snapshot is not twitch.drop_report_state
    assert snapshot["drop"] is not twitch.drop_report_state["drop"]


class _SyncThread:
    def __init__(self, target, args=(), name=None, daemon=None):
        self._target = target
        self._args = args

    def start(self):
        self._target(*self._args)


def prompt_claim_twitch(monkeypatch, claimed):
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(twitch_module, "Thread", _SyncThread)
    monkeypatch.setattr(
        Twitch,
        "claim_all_drops_from_inventory",
        lambda self: claimed.append("claim"),
    )
    twitch = object.__new__(Twitch)
    twitch.prompt_claim_lock = Lock()
    twitch.prompt_claim_pass_lock = Lock()
    twitch.prompt_claim_last = {}
    return twitch


def test_prompt_claim_debounce_is_per_drop(monkeypatch):
    claimed = []
    twitch = prompt_claim_twitch(monkeypatch, claimed)
    drop = SimpleNamespace(
        name="Reward", minutes_required=10, drop_instance_id="instance-1"
    )
    other_drop = SimpleNamespace(
        name="Second Reward", minutes_required=10, drop_instance_id="instance-2"
    )
    campaign = SimpleNamespace(id="campaign-1", name="Example campaign")

    twitch._Twitch__claim_completed_drop_promptly(drop, campaign)
    twitch._Twitch__claim_completed_drop_promptly(drop, campaign)
    twitch._Twitch__claim_completed_drop_promptly(other_drop, campaign)

    # Repeats for the same drop are debounced, but a second drop completing
    # within the debounce window still gets its own prompt claim.
    assert claimed == ["claim", "claim"]
    assert set(twitch.prompt_claim_last) == {"instance-1", "instance-2"}


def test_prompt_claim_never_debounces_a_first_claim_on_a_fresh_clock(monkeypatch):
    # time.monotonic() is time since boot, so on a fresh machine it can be
    # below the debounce window. A first claim must not be treated as a
    # repeat of a "never claimed" default.
    claimed = []
    twitch = prompt_claim_twitch(monkeypatch, claimed)
    twitch_module = importlib.import_module("TwitchChannelPointsMiner.classes.Twitch")
    monkeypatch.setattr(twitch_module.time, "monotonic", lambda: 100.0)
    drop = SimpleNamespace(
        name="Reward", minutes_required=10, drop_instance_id="instance-1"
    )
    campaign = SimpleNamespace(id="campaign-1", name="Example campaign")

    twitch._Twitch__claim_completed_drop_promptly(drop, campaign)

    assert claimed == ["claim"]
    assert set(twitch.prompt_claim_last) == {"instance-1"}


def test_prompt_claim_skips_while_another_claim_pass_is_running(monkeypatch):
    claimed = []
    twitch = prompt_claim_twitch(monkeypatch, claimed)
    drop = SimpleNamespace(
        name="Reward", minutes_required=10, drop_instance_id="instance-1"
    )
    campaign = SimpleNamespace(id="campaign-1", name="Example campaign")

    twitch.prompt_claim_pass_lock.acquire()
    try:
        twitch._Twitch__claim_completed_drop_promptly(drop, campaign)
    finally:
        twitch.prompt_claim_pass_lock.release()

    # The debounce still records the drop, but the claim pass is skipped while
    # another one is running (the sync cycle covers it).
    assert claimed == []
    assert set(twitch.prompt_claim_last) == {"instance-1"}
