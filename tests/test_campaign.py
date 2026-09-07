from TwitchChannelPointsMiner.classes.entities.Campaign import Campaign
from TwitchChannelPointsMiner.classes.entities.CommunityGoal import CommunityGoal


def drop_data(drop_id="drop-1"):
    return {
        "id": drop_id,
        "name": "Reward",
        "benefitEdges": [{"benefit": {"name": "Badge"}}],
        "requiredMinutesWatched": 10,
        "startAt": "2020-01-01T00:00:00Z",
        "endAt": "2099-01-01T00:00:00Z",
    }


def campaign_data():
    return {
        "id": "campaign-1",
        "game": {"displayName": "Example Game"},
        "name": "Example Campaign",
        "status": "ACTIVE",
        "allow": {"channels": [{"id": "100"}, {"id": "200"}]},
        "startAt": "2020-01-01T00:00:00Z",
        "endAt": "2099-01-01T00:00:00Z",
        "timeBasedDrops": [drop_data(), drop_data("drop-2")],
    }


def test_campaign_extracts_channels_and_builds_drops():
    campaign = Campaign(campaign_data())

    assert campaign.channels == ["100", "200"]
    assert [drop.id for drop in campaign.drops] == ["drop-1", "drop-2"]
    assert campaign.dt_match is True


def test_campaign_treats_unrestricted_channels_as_empty_list():
    data = campaign_data()
    data["allow"]["channels"] = None

    assert Campaign(data).channels == []


def test_clear_drops_removes_claimed_and_inactive_drops():
    campaign = Campaign(campaign_data())
    campaign.drops[0].is_claimed = True
    campaign.drops[1].dt_match = False

    campaign.clear_drops()

    assert campaign.drops == []


def test_has_watchable_drops_reflects_progress_and_claim_state():
    campaign = Campaign(campaign_data())

    assert campaign.has_watchable_drops() is True

    campaign.drops[0].current_minutes_watched = 10
    campaign.drops[1].current_minutes_watched = 4
    assert campaign.has_watchable_drops() is True

    campaign.drops[1].current_minutes_watched = 10
    assert campaign.has_watchable_drops() is False

    campaign.drops[1].is_claimed = True
    campaign.drops[1].current_minutes_watched = 0
    assert campaign.has_watchable_drops() is False

    campaign.drops[1].is_claimed = False
    campaign.drops[1].dt_match = False
    assert campaign.has_watchable_drops() is False


def test_sync_drops_updates_matching_drop_and_invokes_claim_callback():
    campaign = Campaign(campaign_data())
    claimed = []

    def claim(drop, campaign):
        claimed.append((drop.id, campaign.id))
        return True

    campaign.sync_drops(
        [
            {
                "id": "drop-1",
                "self": {
                    "hasPreconditionsMet": True,
                    "currentMinutesWatched": 10,
                    "dropInstanceID": "instance-1",
                    "isClaimed": False,
                },
            },
            {"id": "unknown", "self": {}},
        ],
        claim,
    )

    assert claimed == [("drop-1", "campaign-1")]
    assert campaign.drops[0].is_claimed is True
    assert campaign.drops[0].current_minutes_watched == 10


def test_community_goal_parses_graphql_and_pubsub_shapes():
    gql = CommunityGoal.from_gql(
        {
            "id": "goal-1",
            "title": "Goal",
            "isInStock": True,
            "pointsContributed": 250,
            "amountNeeded": 1000,
            "perStreamUserMaximumContribution": 100,
            "status": "STARTED",
        }
    )
    pubsub = CommunityGoal.from_pubsub(
        {
            "id": "goal-1",
            "title": "Goal",
            "is_in_stock": True,
            "points_contributed": 250,
            "goal_amount": 1000,
            "per_stream_maximum_user_contribution": 100,
            "status": "STARTED",
        }
    )

    assert gql == pubsub
    assert gql.amount_left() == 750
