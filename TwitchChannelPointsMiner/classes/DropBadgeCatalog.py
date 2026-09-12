import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from TwitchChannelPointsMiner.classes.TwitchDropsApp import TwitchDropsAppScraper

logger = logging.getLogger(__name__)

CATALOG_VERSION = 2
BADGES_GIST_URL = (
    "https://gist.githubusercontent.com/zarmstrong/"
    "d4fc5f87e2a5258a28421f7fdb8037d6/raw/twitch-badges.json"
)


def _now():
    return datetime.now(timezone.utc)


def _iso_now():
    return _now().isoformat()


def _parse_timestamp(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


# Twitch's own surfaces disagree on how a title's number is written -- a
# drop's reward name may read "Solasta 2 Multiplayer" while the same badge's
# name in the account's earned-badge inventory reads "Solasta II
# Multiplayer". Left unnormalized, a plain word-equality match never
# recognizes the two as the same badge, so a completed campaign is never
# retired and keeps getting rewatched indefinitely. Numbers this small only
# ever appear in game/sequel titles here, so the mapping is unambiguous.
_ROMAN_TO_ARABIC = {
    "i": "1",
    "ii": "2",
    "iii": "3",
    "iv": "4",
    "v": "5",
    "vi": "6",
    "vii": "7",
    "viii": "8",
    "ix": "9",
    "x": "10",
}


def _words(value):
    raw_words = re.findall(r"[a-z0-9]+", str(value or "").casefold())
    return [_ROMAN_TO_ARABIC.get(word, word) for word in raw_words]


def _comparable_badge_words(value):
    value_words = _words(value)
    suffix_removed = False
    if value_words[-2:] == ["chat", "badge"]:
        value_words = value_words[:-2]
        suffix_removed = True
    elif value_words[-1:] == ["badge"]:
        value_words = value_words[:-1]
        suffix_removed = True
    return value_words, suffix_removed


def badge_match_reason(reward_name, game_name, badge_title):
    reward_words, reward_suffix_removed = _comparable_badge_words(reward_name)
    badge_words, badge_suffix_removed = _comparable_badge_words(badge_title)
    game_words = set(_words(game_name))
    if not reward_words or not badge_words:
        return None
    if reward_words == badge_words:
        if reward_suffix_removed or badge_suffix_removed:
            return "exact_title_ignoring_badge_suffix"
        return "exact_title"

    if len(badge_words) > len(reward_words):
        prefix = badge_words[: len(badge_words) - len(reward_words)]
        if badge_words[
            -len(reward_words) :
        ] == reward_words and game_words.intersection(prefix):
            return "game_prefixed_badge_title"

    if len(reward_words) > len(badge_words):
        prefix = reward_words[: len(reward_words) - len(badge_words)]
        if reward_words[-len(badge_words) :] == badge_words and game_words.intersection(
            prefix
        ):
            return "game_prefixed_reward_name"
    return None


def flatten_badges(badge_sets):
    badges = []
    for badge_set in badge_sets:
        if not isinstance(badge_set, dict):
            continue
        for version in badge_set.get("versions", []) or []:
            if isinstance(version, dict):
                badges.append({"set_id": badge_set.get("set_id"), **version})
    return badges


def classify_reward(reward, game_name, badges):
    matches = []
    for badge in badges:
        reason = badge_match_reason(reward.get("name"), game_name, badge.get("title"))
        if reason:
            matches.append({"reason": reason, **badge})
    return {
        "status": "BADGE" if matches else "UNKNOWN",
        "authority": "twitch_helix_global_badges" if matches else None,
        "matches": matches,
    }


def enrich_game_report(report, badges):
    game_name = str(report.get("game") or "")
    classifications = {}
    for drop in report.get("drops", []) or []:
        classification = classify_reward(drop, game_name, badges)
        drop["badge_classification"] = classification
        key = (
            drop.get("name"),
            drop.get("requirement"),
            drop.get("campaign"),
            drop.get("image_url"),
        )
        classifications[key] = classification

    for group in ("campaigns", "upcoming_campaigns", "non_watch_campaigns"):
        for campaign in report.get(group, []) or []:
            for drop in campaign.get("drops", []) or []:
                key = (
                    drop.get("name"),
                    drop.get("requirement"),
                    drop.get("campaign"),
                    drop.get("image_url"),
                )
                drop["badge_classification"] = classifications.get(
                    key, classify_reward(drop, game_name, badges)
                )
    return report


class DropBadgeCatalog:
    __slots__ = [
        "login",
        "path",
        "scraper",
        "session",
        "badge_refresh_hours",
        "state",
    ]

    # Bound how long a game/campaign no longer reported by the front page can
    # linger in state before being dropped - without this, games/campaigns.json
    # grows without limit over weeks/months of continuous uptime, since nothing
    # ever removed an entry once added.
    GAME_STALE_AFTER_DAYS = 3
    CAMPAIGN_RETENTION_AFTER_END_DAYS = 7
    CAMPAIGN_STALE_WITHOUT_END_DAYS = 30

    def __init__(
        self,
        login,
        config_path,
        badge_refresh_hours=24,
        scraper=None,
        session=None,
    ):
        self.login = login
        self.path = Path(config_path) / "drop_badge_catalog.json"
        self.scraper = scraper or TwitchDropsAppScraper(timeout=30)
        self.session = session or requests.Session()
        self.badge_refresh_hours = badge_refresh_hours
        self.state = self._load()

    def _empty_state(self):
        return {
            "version": CATALOG_VERSION,
            "badge_catalog": {},
            "front_page": {},
            "games": {},
            "campaigns": {},
        }

    def _load(self):
        if not self.path.is_file():
            return self._empty_state()
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            logger.warning(
                f"Unable to load Drop badge catalog {self.path}: {error}",
                extra={"emoji": ":warning:"},
            )
            return self._empty_state()
        if state.get("version") != CATALOG_VERSION:
            return self._empty_state()
        for key in ("badge_catalog", "front_page", "games", "campaigns"):
            state.setdefault(key, {})
        return state

    def _save(self):
        self.state["version"] = CATALOG_VERSION
        self.state["saved_at"] = _iso_now()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.state, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        os.replace(temporary, self.path)

    def _badge_catalog_is_fresh(self):
        fetched_at = _parse_timestamp(self.state["badge_catalog"].get("fetched_at"))
        if fetched_at is None:
            return False
        age = (_now() - fetched_at).total_seconds()
        return age < self.badge_refresh_hours * 3600

    def _fetch_badges(self):
        response = self.session.get(
            BADGES_GIST_URL,
            headers={
                "Accept": "application/json",
                "User-Agent": "TwitchDropsMiner/1.0",
            },
            timeout=(5, 30),
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Twitch badge gist did not contain a JSON object")
        badge_sets = payload.get("sets")
        if not isinstance(badge_sets, list):
            raise ValueError("Twitch badge gist did not contain sets")
        self.state["badge_catalog"] = {
            "fetched_at": _iso_now(),
            "source": BADGES_GIST_URL,
            "sets": badge_sets,
        }
        self._save()
        return badge_sets

    @staticmethod
    def _game_signature(game):
        selected = {
            key: game.get(key)
            for key in (
                "slug",
                "game",
                "starts_at",
                "ends_at",
                "upcoming",
                "drop_count",
            )
        }
        encoded = json.dumps(selected, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _campaigns(report):
        campaigns = []
        for group in ("campaigns", "upcoming_campaigns", "non_watch_campaigns"):
            for campaign in report.get(group, []) or []:
                if isinstance(campaign, dict):
                    campaigns.append((group, campaign))
        return campaigns

    def _reclassify_stored_games(self, badges):
        for game in self.state["games"].values():
            report = game.get("report")
            if isinstance(report, dict):
                enrich_game_report(report, badges)
        for stored in self.state["campaigns"].values():
            campaign = stored.get("campaign")
            game_name = stored.get("game")
            if not isinstance(campaign, dict):
                continue
            for drop in campaign.get("drops", []) or []:
                drop["badge_classification"] = classify_reward(drop, game_name, badges)

    def _confirmed_badge_reward_count(self):
        count = 0
        for record in self.state["campaigns"].values():
            if not isinstance(record, dict):
                continue
            campaign = record.get("campaign")
            if not isinstance(campaign, dict):
                continue
            for drop in campaign.get("drops", []) or []:
                if not isinstance(drop, dict):
                    continue
                classification = drop.get("badge_classification")
                if (
                    isinstance(classification, dict)
                    and classification.get("status") == "BADGE"
                ):
                    count += 1
        return count

    def confirmed_badge_rewards(self):
        """Return the catalog identities needed to recognize badge Drops."""
        rewards = []
        seen = set()

        def add_reward(game_slug, game_name, campaign_name, drop):
            if not isinstance(drop, dict):
                return
            classification = drop.get("badge_classification") or {}
            if classification.get("status") != "BADGE":
                return
            badge_names = {
                str(match.get(key) or "").strip()
                for match in classification.get("matches", []) or []
                if isinstance(match, dict)
                for key in ("title", "set_id")
                if str(match.get(key) or "").strip()
            }
            reward_name = str(drop.get("name") or "").strip()
            if reward_name:
                badge_names.add(reward_name)
            if not badge_names:
                return
            requirement = str(drop.get("requirement") or "").strip()
            watch_eligible = (
                requirement.casefold().startswith("watch ") if requirement else None
            )
            identity = (
                str(game_slug or "").strip().casefold(),
                reward_name.casefold(),
                tuple(sorted(name.casefold() for name in badge_names)),
            )
            if identity in seen:
                return
            seen.add(identity)
            rewards.append(
                {
                    "game_slug": str(game_slug or "").strip(),
                    "game": str(game_name or "").strip(),
                    "campaign": str(campaign_name or "").strip(),
                    "reward_name": reward_name,
                    "badge_names": sorted(badge_names),
                    "watch_eligible": watch_eligible,
                }
            )

        for record in self.state["campaigns"].values():
            if not isinstance(record, dict):
                continue
            campaign = record.get("campaign")
            if not isinstance(campaign, dict):
                continue
            for drop in campaign.get("drops", []) or []:
                add_reward(
                    record.get("game_slug"),
                    record.get("game"),
                    campaign.get("name"),
                    drop,
                )

        # The top-level game report also contains badge rewards that are not
        # watch-time campaign rewards, such as badges requiring a subscription.
        # Twitch may still expose those inside timeBasedDrops; retain their
        # requirement here so watch eligibility can reject them.
        for game_slug, record in self.state["games"].items():
            report = record.get("report") if isinstance(record, dict) else None
            if not isinstance(report, dict):
                continue
            for drop in report.get("drops", []) or []:
                add_reward(
                    game_slug,
                    report.get("game"),
                    drop.get("campaign") if isinstance(drop, dict) else None,
                    drop,
                )
        return rewards

    def _prune_stale_state(self, indexed_slugs):
        now = _now()

        stale_game_slugs = []
        for slug, record in self.state["games"].items():
            if slug in indexed_slugs:
                continue
            last_scraped_at = _parse_timestamp(
                record.get("last_scraped_at") if isinstance(record, dict) else None
            )
            if (
                last_scraped_at is None
                or (now - last_scraped_at).total_seconds()
                >= self.GAME_STALE_AFTER_DAYS * 86400
            ):
                stale_game_slugs.append(slug)
        for slug in stale_game_slugs:
            del self.state["games"][slug]

        stale_campaign_ids = []
        for campaign_id, record in self.state["campaigns"].items():
            if not isinstance(record, dict):
                stale_campaign_ids.append(campaign_id)
                continue
            campaign = record.get("campaign")
            ends_at = _parse_timestamp(
                campaign.get("ends_at") if isinstance(campaign, dict) else None
            )
            if ends_at is not None:
                if (
                    now - ends_at
                ).total_seconds() >= self.CAMPAIGN_RETENTION_AFTER_END_DAYS * 86400:
                    stale_campaign_ids.append(campaign_id)
                continue
            last_seen_at = _parse_timestamp(record.get("last_seen_at"))
            if (
                last_seen_at is None
                or (now - last_seen_at).total_seconds()
                >= self.CAMPAIGN_STALE_WITHOUT_END_DAYS * 86400
            ):
                stale_campaign_ids.append(campaign_id)
        for campaign_id in stale_campaign_ids:
            del self.state["campaigns"][campaign_id]

        return len(stale_game_slugs), len(stale_campaign_ids)

    def sync(self, force=False):
        badge_refreshed = False
        badge_sets = self.state["badge_catalog"].get("sets") or []
        if force or not self._badge_catalog_is_fresh() or not badge_sets:
            try:
                badge_sets = self._fetch_badges()
                badge_refreshed = True
            except (requests.RequestException, ValueError) as error:
                if not badge_sets:
                    raise
                logger.warning(
                    f"Using cached Twitch badge catalog after refresh failed: {error}",
                    extra={"emoji": ":warning:"},
                )
        badges = flatten_badges(badge_sets)
        if badge_refreshed:
            self._reclassify_stored_games(badges)

        indexed_games = self.scraper.scrape_front_page()
        self.state["front_page"] = {
            "fetched_at": _iso_now(),
            "games": indexed_games,
        }
        self._save()

        new_campaigns = []
        scraped_games = 0
        changed_games = []
        for game in indexed_games:
            slug = str(game.get("slug") or "")
            if not slug:
                continue
            signature = self._game_signature(game)
            stored_game = self.state["games"].get(slug) or {}
            if not force and stored_game.get("index_signature") == signature:
                continue
            changed_games.append((game, slug, signature))

        logger.info(
            "Drop badge catalog check: "
            f"{len(indexed_games)} indexed games, "
            f"{len(changed_games)} new or changed gist games",
            extra={"emoji": ":mag:", "category_log": True},
        )

        for game, slug, signature in changed_games:
            report = enrich_game_report(self.scraper.scrape(game["url"]), badges)
            scraped_games += 1
            seen_at = _iso_now()
            self.state["games"][slug] = {
                "index_signature": signature,
                "index": game,
                "last_scraped_at": seen_at,
                "report": report,
            }

            for group, campaign in self._campaigns(report):
                campaign_id = str(campaign.get("id") or "")
                if not campaign_id:
                    continue
                existing = self.state["campaigns"].get(campaign_id)
                record = {
                    "first_seen_at": (
                        existing.get("first_seen_at") if existing else seen_at
                    ),
                    "last_seen_at": seen_at,
                    "game_slug": slug,
                    "game": report.get("game"),
                    "source_group": group,
                    "campaign": campaign,
                }
                self.state["campaigns"][campaign_id] = record
                if existing is None:
                    new_campaigns.append(record)

        indexed_slugs = {str(game.get("slug") or "") for game in indexed_games}
        pruned_games, pruned_campaigns = self._prune_stale_state(indexed_slugs)
        if pruned_games or pruned_campaigns:
            logger.info(
                "Drop badge catalog pruned "
                f"{pruned_games} stale games and {pruned_campaigns} stale campaigns",
                extra={"emoji": ":wastebasket:", "category_log": True},
            )

        self.state["last_checked_at"] = _iso_now()
        self._save()
        confirmed_badge_rewards = self._confirmed_badge_reward_count()
        return {
            "indexed_games": len(indexed_games),
            "scraped_games": scraped_games,
            "stored_campaigns": len(self.state["campaigns"]),
            "new_campaigns": new_campaigns,
            "badge_sets": len(badge_sets),
            "badge_versions": len(badges),
            "confirmed_badge_rewards": confirmed_badge_rewards,
            "pruned_games": pruned_games,
            "pruned_campaigns": pruned_campaigns,
            "path": str(self.path),
        }

    @staticmethod
    def _matches_completed_campaign(
        record, campaign, ends_at, completed_campaign_signatures
    ):
        if ends_at is None:
            return False
        game_slug = str(record.get("game_slug") or "").strip().casefold()
        campaign_name = str(campaign.get("name") or "").strip().casefold()
        if not game_slug or not campaign_name:
            return False

        ends_at_epoch = ends_at.timestamp()
        for (
            signature_game_slug,
            signature_campaign_name,
            signature_ends_at,
        ) in completed_campaign_signatures:
            if (
                game_slug == signature_game_slug
                and campaign_name == signature_campaign_name
                and abs(ends_at_epoch - signature_ends_at) <= 1
            ):
                return True
        return False

    def eligible_badge_campaigns(
        self, owned_badge_names=None, completed_campaign_signatures=None
    ):
        owned_badge_names = owned_badge_names or set()
        completed_campaign_signatures = completed_campaign_signatures or set()
        now = _now()
        eligible = []
        for record in self.state["campaigns"].values():
            if record.get("source_group") != "campaigns":
                continue
            campaign = record.get("campaign") or {}
            starts_at = _parse_timestamp(campaign.get("starts_at"))
            ends_at = _parse_timestamp(campaign.get("ends_at"))
            if starts_at is not None and starts_at > now:
                continue
            if ends_at is not None and ends_at <= now:
                continue

            if completed_campaign_signatures and self._matches_completed_campaign(
                record, campaign, ends_at, completed_campaign_signatures
            ):
                continue

            missing_badge_drops = []
            for drop in campaign.get("drops", []) or []:
                if (
                    not str(drop.get("requirement") or "")
                    .casefold()
                    .startswith("watch ")
                ):
                    continue
                classification = drop.get("badge_classification") or {}
                if classification.get("status") != "BADGE":
                    continue
                if any(
                    badge_match_reason(drop.get("name"), record.get("game"), owned_name)
                    for owned_name in owned_badge_names
                ):
                    continue
                missing_badge_drops.append(drop)

            if missing_badge_drops:
                eligible.append({**record, "eligible_drops": missing_badge_drops})
        return eligible
