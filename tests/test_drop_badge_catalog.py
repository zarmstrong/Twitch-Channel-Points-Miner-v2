import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from TwitchChannelPointsMiner.classes.DropBadgeCatalog import (
    DropBadgeCatalog,
    badge_match_reason,
    flatten_badges,
)


class FakeResponse:
    def __init__(self, payload=None):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return (
            self.payload
            if self.payload is not None
            else {
                "sets": [
                    {
                        "set_id": "example-badge",
                        "versions": [{"id": "1", "title": "Example Badge"}],
                    }
                ]
            }
        )


class FakeSession:
    def __init__(self, payload=None):
        self.calls = 0
        self.payload = payload

    def get(self, *args, **kwargs):
        self.calls += 1
        return FakeResponse(self.payload)


class FakeScraper:
    def __init__(self):
        self.games = [
            {
                "slug": "example",
                "game": "Example Game",
                "url": "https://twitchdrops.app/game/example",
                "starts_at": "2026-01-01T00:00:00Z",
                "ends_at": "2026-01-02T00:00:00Z",
                "upcoming": False,
                "drop_count": 1,
            }
        ]
        self.scrape_calls = 0

    def scrape_front_page(self):
        return copy.deepcopy(self.games)

    def scrape(self, url):
        self.scrape_calls += 1
        drop = {
            "name": "Example Badge",
            "requirement": "Watch 1h",
            "campaign": "Campaign",
            "image_url": "reward.png",
        }
        return {
            "source": url,
            "game": "Example Game",
            "campaigns": [
                {
                    "id": f"campaign-{self.games[0]['drop_count']}",
                    "name": "Campaign",
                    "drops": [copy.deepcopy(drop)],
                }
            ],
            "upcoming_campaigns": [],
            "non_watch_campaigns": [],
            "drops": [drop],
        }


def test_flatten_badges_preserves_set_and_version_attributes():
    badges = flatten_badges(
        [
            {
                "set_id": "example",
                "versions": [
                    {
                        "id": "1",
                        "title": "Example Badge",
                        "description": "Example description",
                    }
                ],
            }
        ]
    )

    assert badges == [
        {
            "set_id": "example",
            "id": "1",
            "title": "Example Badge",
            "description": "Example description",
        }
    ]


def test_fetch_badges_rejects_non_object_json(tmp_path):
    catalog = DropBadgeCatalog(
        SimpleNamespace(),
        tmp_path,
        scraper=FakeScraper(),
        session=FakeSession([]),
    )

    with pytest.raises(ValueError, match="did not contain a JSON object"):
        catalog._fetch_badges()


def test_badge_matching_accepts_safe_title_variants():
    assert badge_match_reason("Blue LED", "Example Game", "Blue LED") == "exact_title"
    assert (
        badge_match_reason("Blue LED Badge", "Example Game", "Blue LED")
        == "exact_title_ignoring_badge_suffix"
    )
    assert (
        badge_match_reason(
            "Android Triangle",
            "Detroit: Become Human",
            "Detroit Android Triangle",
        )
        == "game_prefixed_badge_title"
    )


def test_badge_matching_normalizes_roman_numerals():
    # Regression: Twitch's drop reward name and the account's earned-badge
    # inventory name disagree on numeral style for the same badge (e.g.
    # "Solasta 2 Multiplayer" vs "Solasta II Multiplayer"), which otherwise
    # leaves a completed badge campaign perpetually "unearned".
    assert (
        badge_match_reason("Solasta 2 Multiplayer", "Solasta II", "Solasta II Multiplayer")
        == "exact_title"
    )
    assert (
        badge_match_reason("Solasta II Multiplayer", "Solasta II", "Solasta 2 Multiplayer")
        == "exact_title"
    )


def test_badge_matching_rejects_unrelated_badge_words():
    assert (
        badge_match_reason(
            "Badge of Glory Emote",
            "Mobile Legends",
            "Glory Tournament Badge",
        )
        is None
    )


def test_sync_persists_catalog_and_only_scrapes_changed_games(tmp_path):
    scraper = FakeScraper()
    session = FakeSession()
    catalog = DropBadgeCatalog(
        SimpleNamespace(get_auth_token=lambda: "token"),
        tmp_path,
        scraper=scraper,
        session=session,
    )

    first = catalog.sync()
    catalog.state["campaigns"].update(
        {
            "invalid-record": None,
            "invalid-campaign": {"campaign": None},
            "invalid-drop": {"campaign": {"drops": [None, "invalid"]}},
        }
    )
    second = catalog.sync()
    scraper.games[0]["drop_count"] = 2
    third = catalog.sync()

    assert first["scraped_games"] == 1
    assert first["confirmed_badge_rewards"] == 1
    assert len(first["new_campaigns"]) == 1
    assert (
        first["new_campaigns"][0]["campaign"]["drops"][0]["badge_classification"][
            "status"
        ]
        == "BADGE"
    )
    assert second["scraped_games"] == 0
    assert second["new_campaigns"] == []
    assert second["confirmed_badge_rewards"] == 1
    assert third["scraped_games"] == 1
    assert len(third["new_campaigns"]) == 1
    assert scraper.scrape_calls == 2
    assert session.calls == 1
    assert (tmp_path / "drop_badge_catalog.json").is_file()


def test_sync_prunes_stale_games_and_campaigns_but_keeps_recent_ones(tmp_path):
    catalog = DropBadgeCatalog(
        SimpleNamespace(get_auth_token=lambda: "token"),
        tmp_path,
        scraper=FakeScraper(),
        session=FakeSession(),
    )
    now = datetime.now(timezone.utc)

    catalog.state["games"] = {
        "long-gone": {
            "index": {"slug": "long-gone"},
            "last_scraped_at": (now - timedelta(days=10)).isoformat(),
            "report": {},
        },
        "recently-dropped": {
            "index": {"slug": "recently-dropped"},
            "last_scraped_at": (now - timedelta(hours=1)).isoformat(),
            "report": {},
        },
    }
    catalog.state["campaigns"] = {
        "long-expired": {
            "first_seen_at": (now - timedelta(days=20)).isoformat(),
            "last_seen_at": (now - timedelta(days=10)).isoformat(),
            "game_slug": "long-gone",
            "campaign": {"ends_at": (now - timedelta(days=10)).isoformat()},
        },
        "recently-expired": {
            "first_seen_at": (now - timedelta(days=5)).isoformat(),
            "last_seen_at": (now - timedelta(days=1)).isoformat(),
            "game_slug": "long-gone",
            "campaign": {"ends_at": (now - timedelta(days=1)).isoformat()},
        },
        "no-end-date-stale": {
            "first_seen_at": (now - timedelta(days=40)).isoformat(),
            "last_seen_at": (now - timedelta(days=31)).isoformat(),
            "game_slug": "long-gone",
            "campaign": {},
        },
    }

    result = catalog.sync()

    assert "long-gone" not in catalog.state["games"]
    assert "recently-dropped" in catalog.state["games"]
    assert "example" in catalog.state["games"]

    assert "long-expired" not in catalog.state["campaigns"]
    assert "no-end-date-stale" not in catalog.state["campaigns"]
    assert "recently-expired" in catalog.state["campaigns"]

    assert result["pruned_games"] == 1
    assert result["pruned_campaigns"] == 2


def test_eligible_badge_campaigns_only_returns_active_unearned_watch_badges(
    tmp_path,
):
    catalog = DropBadgeCatalog(
        SimpleNamespace(get_auth_token=lambda: "token"),
        tmp_path,
        scraper=FakeScraper(),
        session=FakeSession(),
    )
    now = datetime.now(timezone.utc)
    badge_drop = {
        "name": "Example Badge",
        "requirement": "Watch 1h",
        "badge_classification": {"status": "BADGE"},
    }
    catalog.state["campaigns"] = {
        "eligible": {
            "game_slug": "example-game",
            "game": "Example Game",
            "source_group": "campaigns",
            "campaign": {
                "starts_at": (now - timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
                "drops": [copy.deepcopy(badge_drop)],
            },
        },
        "upcoming": {
            "game_slug": "example-game",
            "game": "Example Game",
            "source_group": "campaigns",
            "campaign": {
                "starts_at": (now + timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=2)).isoformat(),
                "drops": [copy.deepcopy(badge_drop)],
            },
        },
        "subscriber": {
            "game_slug": "example-game",
            "game": "Example Game",
            "source_group": "non_watch_campaigns",
            "campaign": {"drops": [copy.deepcopy(badge_drop)]},
        },
    }

    eligible = catalog.eligible_badge_campaigns()
    owned = catalog.eligible_badge_campaigns({"Example Badge"})

    assert [record["game_slug"] for record in eligible] == ["example-game"]
    assert eligible[0]["eligible_drops"][0]["name"] == "Example Badge"
    assert owned == []


def test_eligible_badge_campaigns_recognizes_owned_badge_with_mismatched_numeral(
    tmp_path,
):
    # Regression: the drop's reward name uses an Arabic numeral ("Solasta 2
    # Multiplayer") while the account's earned-badge inventory reports the
    # same badge with a Roman numeral ("Solasta II Multiplayer"). Before
    # numeral normalization, this campaign was never recognized as already
    # earned and kept getting rewatched indefinitely.
    catalog = DropBadgeCatalog(
        SimpleNamespace(get_auth_token=lambda: "token"),
        tmp_path,
        scraper=FakeScraper(),
        session=FakeSession(),
    )
    now = datetime.now(timezone.utc)
    catalog.state["campaigns"] = {
        "solasta-launch": {
            "game_slug": "solasta-ii",
            "game": "Solasta II",
            "source_group": "campaigns",
            "campaign": {
                "starts_at": (now - timedelta(hours=1)).isoformat(),
                "ends_at": (now + timedelta(hours=1)).isoformat(),
                "drops": [
                    {
                        "name": "Solasta 2 Multiplayer",
                        "requirement": "Watch 1h",
                        "badge_classification": {"status": "BADGE"},
                    }
                ],
            },
        },
    }

    unearned = catalog.eligible_badge_campaigns()
    earned = catalog.eligible_badge_campaigns({"Solasta II Multiplayer"})

    assert [record["game_slug"] for record in unearned] == ["solasta-ii"]
    assert earned == []


def test_eligible_badge_campaigns_excludes_campaign_matched_by_completion_signature(
    tmp_path,
):
    """Reproduces the Infinity Nikki case from issue #119: a Drop-campaign
    chat badge Twitch already awarded is never returned by AvailableBadges,
    so badge-name matching alone can never mark the campaign as owned. A
    completed-campaign signature (built from the authenticated inventory's
    completedRewardCampaigns, matched by game/campaign name and end time
    instead of by ID) must be enough to exclude it on its own.
    """
    catalog = DropBadgeCatalog(
        SimpleNamespace(get_auth_token=lambda: "token"),
        tmp_path,
        scraper=FakeScraper(),
        session=FakeSession(),
    )
    now = datetime.now(timezone.utc)
    ends_at = now + timedelta(hours=1)
    badge_drop = {
        "name": "Infinity Nikki Badge",
        "requirement": "Watch 1h",
        "badge_classification": {"status": "BADGE"},
    }
    catalog.state["campaigns"] = {
        "infinity-nikki-campaign": {
            "game_slug": "infinity-nikki",
            "game": "Infinity Nikki",
            "source_group": "campaigns",
            "campaign": {
                "name": "Infinity Nikki Drops Campaign",
                "starts_at": (now - timedelta(hours=1)).isoformat(),
                "ends_at": ends_at.isoformat(),
                "drops": [copy.deepcopy(badge_drop)],
            },
        },
    }

    # owned_badge_names is empty, mirroring the reporter's 19-badge
    # AvailableBadges list, which never contains this already-earned badge.
    without_signature = catalog.eligible_badge_campaigns()
    assert [record["game_slug"] for record in without_signature] == [
        "infinity-nikki"
    ]

    matching_signature = {("infinity-nikki", "infinity nikki drops campaign", ends_at.timestamp())}
    with_signature = catalog.eligible_badge_campaigns(
        completed_campaign_signatures=matching_signature
    )
    assert with_signature == []

    # A completed campaign that doesn't name-match any catalog record must
    # not affect unrelated records' eligibility.
    unrelated_signature = {("other-game", "some other campaign", ends_at.timestamp())}
    still_eligible = catalog.eligible_badge_campaigns(
        completed_campaign_signatures=unrelated_signature
    )
    assert [record["game_slug"] for record in still_eligible] == [
        "infinity-nikki"
    ]


def test_confirmed_badge_rewards_exposes_catalog_identities(tmp_path):
    catalog = DropBadgeCatalog(
        SimpleNamespace(get_auth_token=lambda: "token"),
        tmp_path,
        scraper=FakeScraper(),
        session=FakeSession(),
    )
    catalog.state["campaigns"] = {
        "wardogs": {
            "game_slug": "wardogs",
            "game": "WARDOGS",
            "campaign": {
                "name": "WARDOGS Beta & Launch",
                "drops": [
                    {
                        "name": "WARDOG",
                        "requirement": "Watch 30m",
                        "badge_classification": {
                            "status": "BADGE",
                            "matches": [
                                {
                                    "set_id": "wardog",
                                    "title": "WARDOG",
                                }
                            ],
                        },
                    },
                    {
                        "name": "Ordinary Reward",
                        "badge_classification": {"status": "UNKNOWN"},
                    },
                ],
            },
        }
    }
    catalog.state["games"] = {
        "wardogs": {
            "report": {
                "game": "WARDOGS",
                "drops": [
                    {
                        "name": "WARLORD",
                        "requirement": "1 sub",
                        "campaign": None,
                        "badge_classification": {
                            "status": "BADGE",
                            "matches": [
                                {
                                    "set_id": "warlord",
                                    "title": "WARLORD",
                                }
                            ],
                        },
                    }
                ],
            }
        }
    }

    assert catalog.confirmed_badge_rewards() == [
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
