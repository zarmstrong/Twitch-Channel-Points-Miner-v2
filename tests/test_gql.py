import importlib
from datetime import datetime
from types import SimpleNamespace
from threading import Event

import pytest
import requests

from TwitchChannelPointsMiner.classes.ClientSession import ClientSession
from TwitchChannelPointsMiner.classes.Exceptions import (
    StreamerDoesNotExistException,
    StreamerIsOfflineException,
)
from TwitchChannelPointsMiner.classes.gql.Errors import (
    InvalidJsonShapeException,
    RetryError,
)
from TwitchChannelPointsMiner.classes.gql.Integration import (
    GQL,
    error_context,
    is_recoverable_error,
)
from TwitchChannelPointsMiner.classes.gql.data.Parser import (
    Parser,
    expect_int,
    expect_number,
)
from TwitchChannelPointsMiner.classes.gql.data.response.PlaybackAccessToken import (
    Authorization,
    PlaybackAccessTokenResponse,
)
from TwitchChannelPointsMiner.classes.Twitch import Twitch
from TwitchChannelPointsMiner.utils import AttemptStrategy


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(str(self.status_code))
            error.response = self
            raise error

    def json(self):
        return self.payload


def client_session():
    login = SimpleNamespace(get_auth_token=lambda: "token", get_user_id=lambda: "1")
    return ClientSession(
        login=login,
        user_agent="test-agent",
        version="test-version",
        device_id="test-device",
        session_id="test-session",
    )


@pytest.mark.parametrize("value", [True, False, 1.0, "1"])
def test_expect_int_rejects_non_integer_json_values(value):
    with pytest.raises(InvalidJsonShapeException):
        expect_int(value)


@pytest.mark.parametrize("value", [True, False, "1.5", None])
def test_expect_number_rejects_non_numeric_json_values(value):
    with pytest.raises(InvalidJsonShapeException):
        expect_number(value)


def test_expect_number_accepts_json_ints_and_floats():
    assert expect_number(2) == 2.0
    assert expect_number(1.5) == 1.5


def test_playback_access_token_repr_uses_class_name():
    response = PlaybackAccessTokenResponse("value", "signature", Authorization(False, None))

    assert repr(response).startswith("PlaybackAccessTokenResponse(")


def test_drop_campaign_details_parser_reads_drop_campaign_field():
    payload = {
        "data": {
            "user": {
                "dropCampaign": {
                    "id": "campaign-1",
                    "name": "Campaign",
                    "status": "ACTIVE",
                    "game": {
                        "id": "game-1",
                        "slug": "game",
                        "displayName": "Game",
                    },
                    "allow": {"channels": None},
                    "startAt": "2020-01-01T00:00:00Z",
                    "endAt": "2099-01-01T00:00:00Z",
                    "timeBasedDrops": [],
                }
            }
        }
    }

    response = Parser().parse_drop_campaign_details_response(payload)

    assert response.campaign.id == "campaign-1"


def test_available_drops_parser_preserves_channel_campaign_data():
    campaign = {
        "id": "campaign-1",
        "name": "Campaign",
        "game": {"id": "game-1", "name": "Example Game"},
        "timeBasedDrops": [{"id": "drop-1", "name": "Reward"}],
    }
    response = Parser().parse_drops_highlight_service_available_drops(
        {
            "data": {"channel": {"viewerDropCampaigns": [campaign]}},
            "extensions": {"operationName": "DropsHighlightService_AvailableDrops"},
        }
    )

    assert response.ids == ["campaign-1"]
    assert response.campaigns == [campaign]
    assert response.campaigns_available is True


def test_available_drops_parser_distinguishes_null_campaign_data():
    response = Parser().parse_drops_highlight_service_available_drops(
        {
            "data": {"channel": {"viewerDropCampaigns": None}},
            "extensions": {"operationName": "DropsHighlightService_AvailableDrops"},
        }
    )

    assert response.ids == []
    assert response.campaigns == []
    assert response.campaigns_available is False


def test_get_id_from_login_parses_typed_response_and_session_headers():
    calls = []

    def post(url, json, headers):
        calls.append((url, json, headers))
        return FakeResponse(
            {
                "data": {"user": {"id": "123"}},
                "extensions": {"operationName": "GetIDFromLogin"},
            }
        )

    response = GQL(client_session(), post_request=post).get_id_from_login("example")

    assert response.id == "123"
    assert calls[0][2]["Client-Session-Id"] == "test-session"
    assert calls[0][2]["Client-Version"] == "test-version"


def test_get_id_from_login_returns_empty_id_for_unknown_login():
    payload = {
        "data": {"user": None},
        "extensions": {"operationName": "GetIDFromLogin"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    assert gql.get_id_from_login("does-not-exist").id == ""


def test_multiplier_shape_error_includes_full_json_path():
    payload = {
        "data": {
            "community": {
                "channel": {
                    "self": {
                        "communityPoints": {
                            "balance": 10,
                            "activeMultipliers": [{"factor": "1.5"}],
                            "availableClaim": None,
                        }
                    },
                    "communityPointsSettings": {"goals": []},
                }
            }
        },
        "extensions": {"operationName": "ChannelPointsContext"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    with pytest.raises(RetryError) as error:
        gql.get_channel_points_context("example")

    assert (
        'JSON at ["data", "community", "channel", "self", '
        '"communityPoints", "activeMultipliers", 0, "factor"]' in str(error.value)
    )


def test_retries_transport_errors_then_returns_typed_response():
    attempts = 0

    def post(url, json, headers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise requests.ConnectionError("temporary")
        return FakeResponse(
            {
                "data": {"user": {"id": "123"}},
                "extensions": {"operationName": "GetIDFromLogin"},
            }
        )

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=2, attempt_interval_seconds=0),
        post_request=post,
    )

    assert gql.get_id_from_login("example").id == "123"
    assert attempts == 2


def test_retries_live_service_error_then_returns_typed_response():
    attempts = 0

    def post(url, json, headers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse(
                {
                    "data": {},
                    "errors": [
                        {
                            "message": "service error",
                            "path": ["users", 0, "id"],
                        }
                    ],
                    "extensions": {"operationName": "GetIDFromLogin"},
                }
            )
        return FakeResponse(
            {
                "data": {"user": {"id": "123"}},
                "extensions": {"operationName": "GetIDFromLogin"},
            }
        )

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=2, attempt_interval_seconds=0),
        post_request=post,
    )

    assert gql.get_id_from_login("example").id == "123"
    assert attempts == 2


def test_retries_service_error_when_graphql_data_is_null():
    attempts = 0

    def post(url, json, headers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse(
                {
                    "data": None,
                    "errors": [{"message": "service unavailable"}],
                    "extensions": {"operationName": "GetIDFromLogin"},
                }
            )
        return FakeResponse(
            {
                "data": {"user": {"id": "123"}},
                "extensions": {"operationName": "GetIDFromLogin"},
            }
        )

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=2, attempt_interval_seconds=0),
        post_request=post,
    )

    assert gql.get_id_from_login("example").id == "123"
    assert attempts == 2


def test_retries_service_error_without_response_extensions():
    attempts = 0

    def post(url, json, headers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse(
                {
                    "data": None,
                    "errors": [{"message": "service timeout"}],
                }
            )
        return FakeResponse(
            {
                "data": {"user": {"id": "123"}},
                "extensions": {"operationName": "GetIDFromLogin"},
            }
        )

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=2, attempt_interval_seconds=0),
        post_request=post,
    )

    assert gql.get_id_from_login("example").id == "123"
    assert attempts == 2


def test_exhausted_transport_retries_raise_retry_error():
    def post(url, json, headers):
        raise requests.ConnectionError("offline")

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=2, attempt_interval_seconds=0),
        post_request=post,
    )

    with pytest.raises(RetryError) as error:
        gql.get_id_from_login("example")

    assert len(error.value.errors) == 2


@pytest.mark.parametrize("status_code", [408, 425, 429, 500, 503])
def test_retries_transient_http_statuses(status_code):
    attempts = 0

    def post(url, json, headers):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return FakeResponse({}, status_code=status_code)
        return FakeResponse(
            {
                "data": {"user": {"id": "123"}},
                "extensions": {"operationName": "GetIDFromLogin"},
            }
        )

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=2, attempt_interval_seconds=0),
        post_request=post,
    )

    assert gql.get_id_from_login("example").id == "123"
    assert attempts == 2


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 422])
def test_does_not_retry_permanent_http_statuses(status_code):
    attempts = 0

    def post(url, json, headers):
        nonlocal attempts
        attempts += 1
        return FakeResponse({}, status_code=status_code)

    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=3, attempt_interval_seconds=0),
        post_request=post,
    )

    with pytest.raises(RetryError):
        gql.get_id_from_login("example")

    assert attempts == 1


def test_transport_errors_do_not_capture_noisy_traceback():
    assert error_context(requests.ConnectionError("offline")) is None


def test_permanent_http_errors_keep_debugging_traceback():
    try:
        FakeResponse({}, status_code=404).raise_for_status()
    except requests.HTTPError as error:
        context = error_context(error)

    assert context is not None
    assert "requests.exceptions.HTTPError: 404" in context


def test_request_construction_errors_keep_debugging_traceback():
    try:
        raise requests.exceptions.InvalidURL("invalid")
    except requests.exceptions.InvalidURL as error:
        context = error_context(error)

    assert context is not None
    assert "requests.exceptions.InvalidURL: invalid" in context


def test_non_transport_errors_keep_debugging_traceback():
    try:
        raise ValueError("bad parser")
    except ValueError as error:
        context = error_context(error)

    assert context is not None
    assert "ValueError: bad parser" in context


@pytest.mark.parametrize(
    "error",
    [
        requests.ConnectionError("offline"),
        requests.ReadTimeout("slow"),
        requests.exceptions.ChunkedEncodingError("truncated"),
    ],
)
def test_recoverable_transport_errors(error):
    assert is_recoverable_error(error) is True


def test_non_transport_request_errors_are_not_retried():
    assert is_recoverable_error(requests.exceptions.InvalidURL("invalid")) is False


def test_unauthorized_response_invokes_recovery_callback():
    recovered = []
    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=3, attempt_interval_seconds=0),
        post_request=lambda *args, **kwargs: FakeResponse({}, status_code=401),
        on_unauthorized=lambda: recovered.append(True),
    )

    with pytest.raises(RetryError):
        gql.get_id_from_login("example")

    assert recovered == [True]


def test_unauthorized_json_body_invokes_recovery_callback():
    recovered = []
    gql = GQL(
        client_session(),
        attempt_strategy=AttemptStrategy(attempts=3, attempt_interval_seconds=0),
        post_request=lambda *args, **kwargs: FakeResponse(
            {"status": 401, "message": "Unauthorized"}
        ),
        on_unauthorized=lambda: recovered.append(True),
    )

    with pytest.raises(RetryError):
        gql.get_id_from_login("example")

    assert recovered == [True]


def test_authentication_recovery_clears_cookie_and_requests_restart(tmp_path):
    cookie = tmp_path / "cookies.json"
    cookie.write_text("cached", encoding="utf-8")
    twitch = Twitch.__new__(Twitch)
    twitch.cookies_file = str(cookie)
    twitch.twitch_login = SimpleNamespace(username="example")
    twitch.restart_requested = Event()
    twitch.running = True

    twitch._Twitch__request_authentication_restart()

    assert cookie.exists() is False
    assert twitch.restart_requested.is_set() is True
    assert twitch.running is False


def test_raw_batch_transport_preserves_variable_response_shapes():
    payload = [
        {"data": {"user": {"dropCampaign": {"id": "campaign-1"}}}},
        {"data": {"currentUser": {"dropCampaign": None}}, "errors": []},
    ]
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.post_gql_request_batch_raw(
        "DropCampaignDetails", [{"operationName": "one"}, {"operationName": "two"}]
    )

    assert response == payload


def test_raw_batch_transport_rejects_response_count_mismatch():
    gql = GQL(
        client_session(),
        post_request=lambda *args, **kwargs: FakeResponse([{"data": {}}]),
    )

    with pytest.raises(RetryError) as error:
        gql.post_gql_request_batch_raw(
            "DropCampaignDetails",
            [{"operationName": "one"}, {"operationName": "two"}],
        )

    assert "Expected 2 batched responses, got 1" in str(error.value)


def test_empty_batch_returns_without_making_http_request():
    gql = GQL(
        client_session(),
        post_request=lambda *args, **kwargs: pytest.fail("unexpected HTTP request"),
    )

    assert gql.post_gql_request_batch_raw("EmptyBatch", []) == []


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"attempts": 0}, "attempts must be a positive integer"),
        ({"attempts": True}, "attempts must be a positive integer"),
        (
            {"attempt_interval_seconds": -1},
            "attempt_interval_seconds must be a non-negative number",
        ),
        (
            {"backoff_multiplier": 0.5},
            "backoff_multiplier must be a number greater than or equal to 1",
        ),
        (
            {"max_interval_seconds": -1},
            "max_interval_seconds must be a non-negative number or None",
        ),
        ({"jitter_ratio": -0.1}, "jitter_ratio must be a number between 0 and 1"),
        ({"jitter_ratio": 1.1}, "jitter_ratio must be a number between 0 and 1"),
        ({"jitter_ratio": True}, "jitter_ratio must be a number between 0 and 1"),
    ],
)
def test_attempt_strategy_rejects_invalid_configuration(kwargs, message):
    with pytest.raises(ValueError, match=message):
        AttemptStrategy(**kwargs)


def test_attempt_strategy_applies_bounded_exponential_backoff(monkeypatch):
    sleeps = []
    attempts = 0

    def fail():
        nonlocal attempts
        attempts += 1
        raise requests.ConnectionError("offline")

    attempt_strategy_module = importlib.import_module(
        "TwitchChannelPointsMiner.utils.AttemptStrategy"
    )
    monkeypatch.setattr(attempt_strategy_module.time, "sleep", sleeps.append)
    strategy = AttemptStrategy(
        attempts=4,
        attempt_interval_seconds=1,
        backoff_multiplier=3,
        max_interval_seconds=5,
    )

    result = strategy.make_attempts(
        fail,
        lambda value: None,
        lambda error: True,
        lambda error: None,
    )

    assert result.attempts == 4
    assert sleeps == [1, 3, 5]


def test_attempt_strategy_applies_bounded_retry_jitter(monkeypatch):
    sleeps = []

    def fail():
        raise requests.ConnectionError("offline")

    attempt_strategy_module = importlib.import_module(
        "TwitchChannelPointsMiner.utils.AttemptStrategy"
    )
    monkeypatch.setattr(attempt_strategy_module.time, "sleep", sleeps.append)
    monkeypatch.setattr(
        attempt_strategy_module.random,
        "uniform",
        lambda minimum, maximum: maximum,
    )
    strategy = AttemptStrategy(
        attempts=4,
        attempt_interval_seconds=1,
        backoff_multiplier=3,
        max_interval_seconds=5,
        jitter_ratio=0.2,
    )

    result = strategy.make_attempts(
        fail,
        lambda value: None,
        lambda error: True,
        lambda error: None,
    )

    assert result.attempts == 4
    assert sleeps == pytest.approx([1.2, 3.6, 5])


def test_default_gql_retries_use_jitter():
    gql = GQL(client_session())

    assert gql.attempt_strategy.jitter_ratio == 0.25


def test_channel_follows_stops_when_next_page_has_no_new_cursor(caplog):
    gql = GQL(client_session())
    calls = 0
    page = SimpleNamespace(
        follows=SimpleNamespace(
            edges=[
                SimpleNamespace(cursor="edge-cursor", node=SimpleNamespace(login="one"))
            ],
            page_info=SimpleNamespace(has_next_page=True, end_cursor=None),
        )
    )

    def post(*args, **kwargs):
        nonlocal calls
        calls += 1
        return page

    gql.post_gql_request_single = post

    assert gql.channel_follows() == ["one"]
    assert calls == 1
    assert "without a new end cursor" in caplog.text


def twitch_with_gql(gql):
    twitch = Twitch.__new__(Twitch)
    twitch.gql = gql
    return twitch


def test_get_channel_id_only_classifies_empty_id_as_missing_streamer():
    twitch = twitch_with_gql(
        SimpleNamespace(
            get_id_from_login=lambda username: SimpleNamespace(id="")
        )
    )

    with pytest.raises(StreamerDoesNotExistException):
        twitch.get_channel_id("does-not-exist")


def test_get_channel_id_preserves_transient_gql_failure():
    retry_error = RetryError("GetIDFromLogin", [])

    def fail(username):
        raise retry_error

    twitch = twitch_with_gql(SimpleNamespace(get_id_from_login=fail))

    with pytest.raises(RetryError) as error:
        twitch.get_channel_id("example")

    assert error.value is retry_error


def test_login_refreshes_shared_gql_client_version(monkeypatch, tmp_path):
    twitch = Twitch.__new__(Twitch)
    twitch.cookies_file = str(tmp_path / "missing-cookies.json")
    twitch.twitch_login = SimpleNamespace(login_flow=lambda: False)
    refreshed = []
    monkeypatch.setattr(
        Twitch,
        "update_client_version",
        lambda self: refreshed.append(True) or "current-version",
    )

    twitch.login()

    assert refreshed == [True]


def test_load_channel_points_context_adapts_typed_response(monkeypatch):
    claim = SimpleNamespace(id="claim-1")
    points = SimpleNamespace(
        balance=120,
        active_multipliers=[SimpleNamespace(factor=1.2)],
        available_claim=claim,
    )
    goal = SimpleNamespace(
        id="goal-1",
        title="Goal",
        is_in_stock=True,
        points_contributed=10,
        amount_needed=100,
        per_stream_user_maximum_contribution=20,
        status="STARTED",
    )
    channel = SimpleNamespace(
        edge=SimpleNamespace(community_points=points),
        community_points_settings=SimpleNamespace(goals=[goal]),
    )
    gql = SimpleNamespace(
        get_channel_points_context=lambda username: SimpleNamespace(
            community=SimpleNamespace(channel=channel)
        )
    )
    twitch = twitch_with_gql(gql)
    claimed = []
    contributed = []
    monkeypatch.setattr(
        Twitch,
        "claim_bonus",
        lambda self, streamer, claim_id: claimed.append(claim_id),
    )
    monkeypatch.setattr(
        Twitch,
        "contribute_to_community_goals",
        lambda self, streamer: contributed.append(streamer),
    )
    streamer = SimpleNamespace(
        username="example",
        channel_points=0,
        activeMultipliers=None,
        community_goals={},
        settings=SimpleNamespace(community_goals=True),
    )

    twitch.load_channel_points_context(streamer)

    assert streamer.channel_points == 120
    assert streamer.activeMultipliers == [{"factor": 1.2}]
    assert streamer.community_goals["goal-1"].title == "Goal"
    assert claimed == ["claim-1"]
    assert contributed == [streamer]


def test_get_followers_keeps_legacy_empty_list_on_retry_failure():
    retry_error = RetryError("ChannelFollows", [])

    def fail(limit, order):
        raise retry_error

    twitch = twitch_with_gql(SimpleNamespace(channel_follows=fail))

    assert twitch.get_followers() == []


def test_get_stream_info_adapts_typed_game_and_tags_for_stream_entity():
    typed_game = SimpleNamespace(
        id="game-1", display_name="Example Game", name="Example Game"
    )
    typed_stream = SimpleNamespace(
        id="broadcast-1",
        viewers_count=42,
        tags=[SimpleNamespace(id="tag-1", localized_name="Drops Enabled")],
    )
    typed_user = SimpleNamespace(
        stream=typed_stream,
        broadcast_settings=SimpleNamespace(title="Example", game=typed_game),
    )
    twitch = twitch_with_gql(
        SimpleNamespace(
            video_player_stream_info_overlay_channel=lambda username: SimpleNamespace(
                user=typed_user
            )
        )
    )

    stream_info = twitch.get_stream_info(SimpleNamespace(username="example"))

    assert stream_info["broadcastSettings"]["game"] == {
        "id": "game-1",
        "displayName": "Example Game",
        "name": "Example Game",
    }
    assert stream_info["stream"]["tags"] == [
        {"id": "tag-1", "localizedName": "Drops Enabled"}
    ]


def test_get_stream_info_treats_null_user_as_offline():
    twitch = twitch_with_gql(
        SimpleNamespace(
            video_player_stream_info_overlay_channel=lambda username: SimpleNamespace(
                user=None
            )
        )
    )

    with pytest.raises(StreamerIsOfflineException):
        twitch.get_stream_info(SimpleNamespace(username="deleted-channel"))


def test_get_stream_info_returns_none_on_retry_failure():
    retry_error = RetryError("VideoPlayerStreamInfoOverlayChannel", [])

    def fail(username):
        raise retry_error

    twitch = twitch_with_gql(
        SimpleNamespace(video_player_stream_info_overlay_channel=fail)
    )

    assert twitch.get_stream_info(SimpleNamespace(username="example")) is None


def test_stream_info_parser_accepts_null_user():
    payload = {
        "data": {"user": None},
        "extensions": {"operationName": "VideoPlayerStreamInfoOverlayChannel"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    assert gql.video_player_stream_info_overlay_channel("deleted-channel").user is None


def test_playback_authorization_accepts_null_forbidden_reason():
    payload = {
        "data": {
            "streamPlaybackAccessToken": {
                "value": "token-value",
                "signature": "token-signature",
                "authorization": {
                    "isForbidden": False,
                    "forbiddenReasonCode": None,
                },
            }
        },
        "extensions": {"operationName": "PlaybackAccessToken"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_playback_access_token("example")

    assert response.authorization.is_forbidden is False
    assert response.authorization.forbidden_reason_code is None


def test_channel_points_context_accepts_current_query_shape_without_user_ids():
    payload = {
        "data": {
            "community": {
                "channel": {
                    "self": {
                        "communityPoints": {
                            "balance": 100,
                            "activeMultipliers": [],
                            "availableClaim": None,
                        }
                    },
                    "communityPointsSettings": {"goals": []},
                }
            }
        },
        "extensions": {"operationName": "ChannelPointsContext"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_channel_points_context("example")

    assert response.community.id is None
    assert response.community.display_name is None
    assert response.community.channel.id is None
    assert response.community.channel.edge.community_points.balance == 100


def test_channel_points_context_accepts_unavailable_community_points():
    payload = {
        "data": {
            "community": {
                "channel": {
                    "self": {"communityPoints": None},
                    "communityPointsSettings": None,
                }
            }
        },
        "extensions": {"operationName": "ChannelPointsContext"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_channel_points_context("example")

    assert response.community.channel.edge.community_points is None
    assert response.community.channel.community_points_settings is None


def test_load_channel_points_keeps_state_when_points_are_unavailable():
    gql = SimpleNamespace(
        get_channel_points_context=lambda username: SimpleNamespace(
            community=SimpleNamespace(
                channel=SimpleNamespace(
                    edge=SimpleNamespace(community_points=None),
                    community_points_settings=None,
                )
            )
        )
    )
    twitch = twitch_with_gql(gql)
    streamer = SimpleNamespace(
        username="example",
        channel_points=55,
        activeMultipliers=[{"factor": 2}],
        community_goals={"existing": object()},
        settings=SimpleNamespace(community_goals=True),
    )

    twitch.load_channel_points_context(streamer)

    assert streamer.channel_points == 55
    assert streamer.activeMultipliers == [{"factor": 2}]


def test_partial_channel_points_without_balance_preserves_state():
    payload = {
        "data": {
            "community": {
                "channel": {
                    "self": {"communityPoints": {}},
                }
            }
        },
        "extensions": {"operationName": "ChannelPointsContext"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )
    twitch = twitch_with_gql(gql)
    streamer = SimpleNamespace(
        username="example",
        channel_points=55,
        activeMultipliers=[{"factor": 2}],
        community_goals={},
        settings=SimpleNamespace(community_goals=True),
    )

    twitch.load_channel_points_context(streamer)

    assert streamer.channel_points == 55
    assert streamer.activeMultipliers == [{"factor": 2}]


def test_claim_drop_uses_typed_claim_status(monkeypatch):
    drop = SimpleNamespace(
        drop_instance_id="instance-1",
        id="drop-1",
        dt_match=True,
        is_claimed=False,
    )
    gql = SimpleNamespace(
        claim_drop_rewards=lambda drop_instance_id: SimpleNamespace(
            status="ELIGIBLE_FOR_ALL", errors=[]
        )
    )
    twitch = twitch_with_gql(gql)
    twitch.completed_drop_campaigns = set()
    saved = []
    monkeypatch.setattr(
        Twitch, "_Twitch__drop_variant_entries_from_drop", lambda self, drop: [{}]
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__save_drop_claim_analytics",
        lambda self, drop, **kwargs: saved.append(drop.id),
    )

    assert twitch.claim_drop(drop) is True
    assert saved == ["drop-1"]


def test_claim_drop_returns_false_for_typed_errors():
    drop = SimpleNamespace(drop_instance_id="instance-1")
    twitch = twitch_with_gql(
        SimpleNamespace(
            claim_drop_rewards=lambda drop_instance_id: SimpleNamespace(
                status=None, errors=["not eligible"]
            )
        )
    )

    assert twitch.claim_drop(drop) is False


def test_get_broadcast_id_uses_typed_stream_info(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    monkeypatch.setattr(
        Twitch,
        "get_stream_info",
        lambda self, streamer: {"stream": {"id": "broadcast-1"}},
    )

    assert twitch.get_broadcast_id(SimpleNamespace(username="example")) == "broadcast-1"


def test_get_broadcast_id_returns_none_when_stream_info_is_unavailable(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    monkeypatch.setattr(Twitch, "get_stream_info", lambda self, streamer: None)

    assert twitch.get_broadcast_id(SimpleNamespace(username="example")) is None


def test_inventory_typed_response_retains_richer_raw_payload():
    inventory = {
        "dropCampaignsInProgress": None,
        "gameEventDrops": [{"id": "reward-1", "name": "Reward"}],
        "completedRewardCampaigns": [{"id": "campaign-1"}],
    }
    payload = {
        "data": {"currentUser": {"inventory": inventory}},
        "extensions": {"operationName": "Inventory"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_inventory()

    assert response.campaigns is None
    assert response.inventory == inventory


def test_drop_campaign_data_retains_badge_distribution_type():
    campaign = {
        "id": "campaign-1",
        "name": "Badge Campaign",
        "status": "ACTIVE",
        "game": {"id": "game-1", "slug": "game", "displayName": "Game"},
        "allow": {"channels": None},
        "startAt": "2026-01-01T00:00:00Z",
        "endAt": "2026-02-01T00:00:00Z",
        "timeBasedDrops": [
            {
                "id": "drop-1",
                "name": "Badge Drop",
                "startAt": "2026-01-01T00:00:00Z",
                "endAt": "2026-02-01T00:00:00Z",
                "requiredMinutesWatched": 15,
                "requiredSubs": 0,
                "benefitEdges": [
                    {
                        "benefit": {
                            "id": "benefit-1",
                            "name": "Chat Badge",
                            "distributionType": "BADGE",
                            "imageAssetURL": "https://example/badge.png",
                        },
                        "entitlementLimit": 1,
                    }
                ],
            }
        ],
    }
    payload = [
        {
            "data": {"user": {"dropCampaign": campaign}},
            "extensions": {"operationName": "DropCampaignDetails"},
        }
    ]
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    result = gql.get_drop_campaign_data(["campaign-1"])

    benefit = result[0]["timeBasedDrops"][0]["benefitEdges"][0]["benefit"]
    assert benefit["distributionType"] == "BADGE"
    assert benefit["id"] == "benefit-1"
    assert result[0] is not campaign


def test_inventory_typed_parsing_does_not_replace_raw_campaign_dicts():
    campaign = {
        "id": "campaign-1",
        "name": "Campaign",
        "game": {"id": "game-1", "displayName": "Game"},
        "timeBasedDrops": [
            {
                "id": "drop-1",
                "name": "Reward",
                "endAt": "2099-01-01T00:00:00Z",
                "startAt": "2020-01-01T00:00:00Z",
                "benefitEdges": [{"benefit": {"name": "Badge"}}],
                "requiredMinutesWatched": 10,
                "requiredSubs": 0,
                "self": {
                    "hasPreconditionsMet": True,
                    "currentMinutesWatched": 5,
                    "currentSubs": 0,
                    "dropInstanceID": "instance-1",
                    "isClaimed": False,
                },
            }
        ],
    }
    payload = {
        "data": {"currentUser": {"inventory": {"dropCampaignsInProgress": [campaign]}}},
        "extensions": {"operationName": "Inventory"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_inventory()

    assert response.campaigns[0].id == "campaign-1"
    assert isinstance(response.inventory["dropCampaignsInProgress"][0], dict)
    assert (
        response.inventory["dropCampaignsInProgress"][0]["game"]["displayName"]
        == "Game"
    )
    assert isinstance(
        payload["data"]["currentUser"]["inventory"]["dropCampaignsInProgress"][0],
        dict,
    )


def test_inventory_uses_partial_data_when_optional_field_returns_gql_error():
    inventory = {
        "dropCampaignsInProgress": None,
        "gameEventDrops": None,
        "completedRewardCampaigns": [],
    }
    payload = {
        "data": {"currentUser": {"inventory": inventory}},
        "errors": [
            {
                "message": "service error",
                "path": ["currentUser", "inventory", "gameEventDrops"],
            }
        ],
        "extensions": {"operationName": "Inventory"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_inventory()

    assert response.inventory == inventory
    assert len(response.errors) == 1
    assert response.errors[0].message == "service error"


def test_dashboard_typed_response_retains_full_raw_payload():
    payload = {
        "data": {
            "currentUser": {
                "dropCampaigns": None,
                "rewardCampaigns": [{"id": "campaign-1", "status": "ACTIVE"}],
            },
            "rewardCampaignsAvailableToUser": [{"id": "campaign-2"}],
        },
        "extensions": {"operationName": "ViewerDropsDashboard"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    response = gql.get_viewer_drops_dashboard()

    assert response.campaigns is None
    assert response.raw_response == payload


def test_dashboard_extraction_ignores_non_campaign_values():
    twitch = twitch_with_gql(
        SimpleNamespace(
            get_viewer_drops_dashboard=lambda: SimpleNamespace(
                raw_response={
                    "data": {
                        "currentUser": {
                            "dropCampaigns": [None, "invalid", {"id": "campaign-1"}]
                        }
                    }
                }
            )
        )
    )

    assert twitch._Twitch__get_drops_dashboard() == [
        {"id": "campaign-1", "_is_reward_campaign": False}
    ]


def test_campaign_details_retries_viewer_context_after_null_user():
    calls = []

    def post_batch(operation_name, requests):
        calls.append(requests)
        if len(calls) == 1:
            return [{"data": {"user": None}}]
        return [
            {"data": {"user": {"dropCampaign": {"id": "campaign-1", "name": "Drop"}}}}
        ]

    twitch = twitch_with_gql(SimpleNamespace(post_gql_request_batch_raw=post_batch))
    twitch.twitch_login = SimpleNamespace(
        get_user_id=lambda: "viewer-id", username="viewer"
    )
    twitch.log_drop_checks = False

    campaigns = twitch._Twitch__get_campaigns_details(
        [{"id": "campaign-1"}],
        campaign_channel_id_by_id={"campaign-1": "restricted-channel-id"},
    )

    assert campaigns == [{"id": "campaign-1", "name": "Drop"}]
    assert calls[0][0]["variables"]["channelLogin"] == "restricted-channel-id"
    assert calls[1][0]["variables"]["channelLogin"] == "viewer-id"


def test_campaign_sync_accepts_partial_inventory_without_progress(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.log_drop_checks = False
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_inventory",
        lambda self: {"gameEventDrops": []},
    )

    twitch._Twitch__sync_campaigns([])


def test_category_filter_bypasses_campaign_lookup_when_drops_are_disabled():
    twitch = twitch_with_gql(SimpleNamespace())

    assert twitch.filter_categories_with_active_drops(
        ["Just Chatting"], drops_enabled=False
    ) == ["Just Chatting"]


@pytest.mark.parametrize("inventory", [None, {}])
def test_category_filter_skips_discovery_when_inventory_is_unavailable(
    monkeypatch, inventory
):
    twitch = twitch_with_gql(SimpleNamespace())
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_inventory",
        lambda self: inventory,
    )

    assert twitch.filter_categories_with_active_drops(["Rust", "Diablo IV"]) == []


def test_category_filter_does_not_resurrect_completed_twitch_category(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.category_campaign_eligibility = {}
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_inventory",
        lambda self: {"completedRewardCampaigns": [{"id": "campaign-1"}]},
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__active_drop_category_slugs_from_campaigns",
        lambda self, inventory, requested: ({}, {"two-point-museum"}),
    )
    fallback_calls = []
    monkeypatch.setattr(
        Twitch,
        "_Twitch__awarded_benefits",
        lambda self, inventory: pytest.fail(
            "fallback discovery should not inspect awarded benefits"
        ),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: fallback_calls.append(known_slugs)
        or {"two-point-museum": datetime(2099, 1, 1)},
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__replace_category_campaign_eligibility",
        lambda self, slug, eligibility: None,
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.filter_categories_with_active_drops(["two-point-museum"]) == []
    assert fallback_calls == [{"two-point-museum"}]


def test_category_filter_uses_fallback_for_game_twitch_did_not_expose(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.category_campaign_eligibility = {}
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_inventory",
        lambda self: {"gameEventDrops": []},
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__active_drop_category_slugs_from_campaigns",
        lambda self, inventory, requested: ({}, set()),
    )

    def fallback(self, categories, known_slugs):
        known_slugs.add("two-point-museum")
        return {"two-point-museum": datetime(2099, 1, 1)}

    monkeypatch.setattr(Twitch, "_Twitch__twitchdrops_app_fallback", fallback)

    assert twitch.filter_categories_with_active_drops(["two-point-museum"]) == [
        "two-point-museum"
    ]


@pytest.mark.parametrize(
    ("order", "categories", "expected"),
    [
        (
            "ORDER",
            ["fallback-game", "inventory-game"],
            ["fallback-game", "inventory-game"],
        ),
        (
            "EXPIRATION",
            ["inventory-game", "fallback-game"],
            ["fallback-game", "inventory-game"],
        ),
    ],
)
def test_category_filter_orders_campaigns_across_inventory_and_fallback(
    monkeypatch, order, categories, expected
):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.category_campaign_eligibility = {}
    monkeypatch.setattr(
        Twitch,
        "_Twitch__get_inventory",
        lambda self: {"gameEventDrops": []},
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__active_drop_category_slugs_from_campaigns",
        lambda self, inventory, requested: (
            {"inventory-game": datetime(2099, 2, 1)},
            {"inventory-game"},
        ),
    )
    monkeypatch.setattr(
        Twitch,
        "_Twitch__twitchdrops_app_fallback",
        lambda self, categories, known_slugs: {"fallback-game": datetime(2099, 1, 1)},
    )

    assert (
        twitch.filter_categories_with_active_drops(categories, order=order) == expected
    )


def test_category_streamer_keeps_fallback_eligibility_when_twitch_omits_campaign():
    twitch = twitch_with_gql(
        SimpleNamespace(
            get_available_drops=lambda channel_id: SimpleNamespace(
                campaigns=[], campaigns_available=True
            )
        )
    )
    twitch.log_drop_checks = False
    twitch.category_campaign_eligibility = {("djs", "example-dj"): (1, 1)}
    twitch.twitchdrops_app_campaigns = {
        "djs": [{"id": "fallback-campaign", "channels": [], "game_name": "DJs"}]
    }
    twitch.discovered_open_drop_campaigns = []
    streamer = SimpleNamespace(
        channel_id="channel-1",
        username="example-dj",
        from_category=True,
        from_badge_campaign=False,
        stream=SimpleNamespace(game_name=lambda: "DJs"),
    )

    campaign_ids = twitch._Twitch__get_campaign_ids_from_streamer(streamer)

    assert campaign_ids == ["fallback-campaign"]
    assert twitch.category_campaign_eligibility[("djs", "example-dj")] == (1, 1)


def test_wildcard_streamer_requires_twitch_to_advertise_external_campaign():
    twitch = twitch_with_gql(
        SimpleNamespace(
            get_available_drops=lambda channel_id: SimpleNamespace(
                campaigns=[], campaigns_available=True
            )
        )
    )
    twitch.log_drop_checks = False
    twitch.category_campaign_eligibility = {("djs", "example-dj"): (1, 1)}
    twitch.twitchdrops_app_campaigns = {
        "djs": [{"id": "fallback-campaign", "channels": [], "game_name": "DJs"}]
    }
    twitch.active_drop_campaigns = {}
    twitch.discovered_open_drop_campaigns = []
    streamer = SimpleNamespace(
        channel_id="channel-1",
        username="example-dj",
        from_category=True,
        from_wildcard_category=True,
        from_badge_campaign=False,
        stream=SimpleNamespace(game_name=lambda: "DJs"),
    )

    campaign_ids = twitch._Twitch__get_campaign_ids_from_streamer(streamer)

    assert campaign_ids == []
    assert twitch.category_campaign_eligibility[("djs", "example-dj")] == (0, 1)


def test_category_streamer_honors_empty_twitch_campaign_response_without_fallback():
    twitch = twitch_with_gql(
        SimpleNamespace(
            get_available_drops=lambda channel_id: SimpleNamespace(
                campaigns=[], campaigns_available=True
            )
        )
    )
    twitch.log_drop_checks = False
    twitch.category_campaign_eligibility = {("game", "example"): (1, 1)}
    twitch.twitchdrops_app_campaigns = {}
    streamer = SimpleNamespace(
        channel_id="channel-1",
        username="example",
        from_category=True,
        stream=SimpleNamespace(game_name=lambda: "Game"),
    )

    campaign_ids = twitch._Twitch__get_campaign_ids_from_streamer(streamer)

    assert campaign_ids == []
    assert twitch.category_campaign_eligibility[("game", "example")] == (0, 0)


def test_category_eligibility_replacement_preserves_other_games():
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.category_campaign_eligibility = {
        ("old-game", "stale"): (1, 1),
        ("other-game", "keep"): (1, 2),
    }

    twitch._Twitch__replace_category_campaign_eligibility("old-game", {"fresh": (2, 2)})

    assert twitch.category_campaign_eligibility == {
        ("old-game", "fresh"): (2, 2),
        ("other-game", "keep"): (1, 2),
    }


def test_category_search_uses_fallback_game_name_for_directory_slug(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.twitchdrops_app_campaigns = {"bitcraft": [{"channels": []}]}
    twitch.twitchdrops_app_game_names = {"bitcraft": "BitCraft Online"}
    twitch.category_campaign_eligibility = {}
    calls = []

    def helix_get(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "search/categories":
            return {
                "data": [
                    {"id": "wrong", "name": "BIT RAT : Singularity"},
                    {"id": "bitcraft-id", "name": "BitCraft Online"},
                ]
            }
        return {"data": []}

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category("bitcraft") == []
    assert calls[0] == (
        "search/categories",
        {"query": "BitCraft Online", "first": 100},
    )
    assert calls[1][1]["game_id"] == "bitcraft-id"


def test_category_search_prefers_resolved_game_campaign_allowlist(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.active_drop_campaigns = {
        "actual-game": [{"id": "campaign-1", "channels": ["allowed-channel"]}]
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}
    calls = []

    def helix_get(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "search/categories":
            return {"data": [{"id": "actual-id", "name": "Actual Game"}]}
        assert params["user_login"] == ["allowed-channel"]
        return {
            "data": [
                {
                    "user_login": "allowed-channel",
                    "game_id": "actual-id",
                    "viewer_count": 10,
                }
            ]
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category(
        "configured-alias", drops_enabled=False
    ) == ["allowed-channel"]
    assert [endpoint for endpoint, _ in calls] == ["search/categories", "streams"]
    assert twitch.category_campaign_eligibility == {
        ("actual-game", "allowed-channel"): (1, 1)
    }


def test_category_search_stops_at_drops_directory_result_count(monkeypatch):
    gql = SimpleNamespace(
        post_gql_request_raw=lambda operation, request: {
            "data": {
                "game": {
                    "streams": {
                        "edges": [
                            {"node": {"broadcaster": {"login": "one"}}},
                            {"node": {"broadcaster": {"login": "two"}}},
                        ]
                    }
                }
            }
        }
    )
    twitch = twitch_with_gql(gql)
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}
    stream_calls = []

    def helix_get(endpoint, params):
        if endpoint == "search/categories":
            return {"data": [{"id": "game-id", "name": "Game"}]}
        stream_calls.append(params)
        return {
            "data": [
                {
                    "user_login": login,
                    "game_id": "game-id",
                    "viewer_count": viewers,
                }
                for login, viewers in (("one", 2), ("two", 1))
            ],
            "pagination": {"cursor": "more"},
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category("game", limit=300) == [
        "one",
        "two",
    ]
    assert len(stream_calls) == 1


def test_category_search_rejects_user_created_drops_lookalike(monkeypatch):
    gql = SimpleNamespace(
        post_gql_request_raw=lambda operation, request: {
            "data": {"game": {"streams": {"edges": []}}}
        }
    )
    twitch = twitch_with_gql(gql)
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}

    def helix_get(endpoint, params):
        if endpoint == "search/categories":
            return {"data": [{"id": "game-id", "name": "Game"}]}
        return {
            "data": [
                {
                    "user_login": "lookalike",
                    "game_id": "game-id",
                    "viewer_count": 1,
                    "tags": ["DropsSoon"],
                }
            ]
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category("game") == []


def test_campaign_inventory_merge_keeps_new_drop_and_existing_progress():
    twitch = twitch_with_gql(SimpleNamespace())
    fresh = {
        "id": "campaign-1",
        "timeBasedDrops": [
            {"id": "old-drop", "self": None},
            {"id": "new-drop", "self": None},
        ],
    }
    inventory = {
        "id": "campaign-1",
        "timeBasedDrops": [
            {
                "id": "old-drop",
                "self": {"currentMinutesWatched": 30, "isClaimed": True},
            }
        ],
    }

    merged = twitch._Twitch__merge_campaign_inventory_progress(fresh, inventory)

    assert [drop["id"] for drop in merged["timeBasedDrops"]] == [
        "old-drop",
        "new-drop",
    ]
    assert merged["timeBasedDrops"][0]["self"]["currentMinutesWatched"] == 30
    assert fresh["timeBasedDrops"][0]["self"] is None


def test_campaign_inventory_merge_keeps_inventory_channel_allowlist():
    twitch = twitch_with_gql(SimpleNamespace())
    fresh = {
        "id": "campaign-1",
        "allow": {"channels": None},
        "timeBasedDrops": [{"id": "drop-1", "self": None}],
    }
    inventory = {
        "id": "campaign-1",
        "allow": {
            "channels": [
                {"id": "100", "name": "AllowedOne"},
                {"id": "200", "name": "AllowedTwo"},
            ]
        },
        "timeBasedDrops": [],
    }

    merged = twitch._Twitch__merge_campaign_inventory_progress(fresh, inventory)

    assert merged["allow"] == inventory["allow"]
    assert fresh["allow"] == {"channels": None}


def test_inventory_progress_cache_tracks_only_incomplete_eligible_drops():
    twitch = twitch_with_gql(SimpleNamespace())
    inventory = {
        "dropCampaignsInProgress": [
            {
                "id": "campaign-1",
                "name": "Example campaign",
                "game": {"displayName": "Example Game"},
                "timeBasedDrops": [
                    {
                        "id": "active-drop",
                        "name": "Active reward",
                        "requiredMinutesWatched": 15,
                        "self": {
                            "currentMinutesWatched": 5,
                            "hasPreconditionsMet": True,
                            "isClaimed": False,
                        },
                    },
                    {
                        "id": "claimed-drop",
                        "name": "Claimed reward",
                        "requiredMinutesWatched": 10,
                        "self": {
                            "currentMinutesWatched": 10,
                            "hasPreconditionsMet": True,
                            "isClaimed": True,
                        },
                    },
                ],
            }
        ]
    }

    twitch._Twitch__cache_drop_inventory_progress(inventory)

    assert twitch.drop_inventory_progress == {
        "example-game": (
            (
                "campaign-1",
                "Example campaign",
                "active-drop",
                "Active reward",
                5,
                15,
            ),
        )
    }


def test_category_search_uses_active_twitch_campaign_allowlist(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.active_drop_campaigns = {
        "naraka-bladepoint": [
            {
                "id": "campaign-1",
                "channels": [
                    "narakabladepoint",
                    "naraka_asia",
                    "narakaesports",
                ],
            }
        ]
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}
    calls = []

    def helix_get(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "search/categories":
            return {"data": [{"id": "naraka-id", "name": "NARAKA: BLADEPOINT"}]}
        assert params["user_login"] == [
            "narakabladepoint",
            "naraka_asia",
            "narakaesports",
        ]
        return {
            "data": [
                {
                    "user_login": "narakabladepoint",
                    "game_id": "naraka-id",
                    "viewer_count": 100,
                }
            ]
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category("naraka-bladepoint") == [
        "narakabladepoint"
    ]
    assert [endpoint for endpoint, _ in calls] == ["search/categories", "streams"]
    assert twitch.category_campaign_eligibility == {
        ("naraka-bladepoint", "narakabladepoint"): (1, 1)
    }


def test_category_search_uses_nested_campaign_allowlist(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.active_drop_campaigns = {
        "naraka-bladepoint": [
            {
                "id": "campaign-1",
                "allow": {"channels": [{"name": " NARAKABladepoint "}]},
            }
        ]
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}
    calls = []

    def helix_get(endpoint, params):
        calls.append((endpoint, params))
        if endpoint == "search/categories":
            return {"data": [{"id": "naraka-id", "name": "NARAKA: BLADEPOINT"}]}
        assert params["user_login"] == ["narakabladepoint"]
        return {
            "data": [
                {
                    "user_login": "narakabladepoint",
                    "game_id": "naraka-id",
                    "viewer_count": 100,
                }
            ]
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category("naraka-bladepoint") == [
        "narakabladepoint"
    ]
    assert [endpoint for endpoint, _ in calls] == ["search/categories", "streams"]
    assert twitch.category_campaign_eligibility == {
        ("naraka-bladepoint", "narakabladepoint"): (1, 1)
    }


def test_forced_category_streamer_must_match_active_campaign_allowlist(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.active_drop_campaigns = {
        "naraka-bladepoint": [{"id": "campaign-1", "channels": ["narakabladepoint"]}]
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}
    calls = []

    def helix_get(endpoint, params):
        calls.append(endpoint)
        return {"data": [{"id": "naraka-id", "name": "NARAKA: BLADEPOINT"}]}

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert (
        twitch.get_live_streamers_for_category("naraka-bladepoint|generic-streamer")
        == []
    )
    assert calls == ["search/categories"]


def test_points_only_forced_streamer_ignores_campaign_allowlist(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.active_drop_campaigns = {
        "naraka-bladepoint": [
            {"id": "campaign-1", "channels": ["narakabladepoint"]}
        ]
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}

    def helix_get(endpoint, params):
        if endpoint == "search/categories":
            return {"data": [{"id": "naraka-id", "name": "NARAKA: BLADEPOINT"}]}
        return {
            "data": [
                {
                    "user_login": "generic-streamer",
                    "game_id": "naraka-id",
                    "viewer_count": 100,
                }
            ]
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category(
        "naraka-bladepoint|generic-streamer", drops_enabled=False
    ) == ["generic-streamer"]
    assert twitch.category_campaign_eligibility == {}


def test_forced_category_streamer_normalizes_campaign_allowlist(monkeypatch):
    twitch = twitch_with_gql(SimpleNamespace())
    twitch.active_drop_campaigns = {
        "naraka-bladepoint": [
            {"id": "campaign-1", "channels": [" NARAKABladepoint "]}
        ]
    }
    twitch.twitchdrops_app_campaigns = {}
    twitch.twitchdrops_app_game_names = {}
    twitch.category_campaign_eligibility = {}

    def helix_get(endpoint, params):
        if endpoint == "search/categories":
            return {"data": [{"id": "naraka-id", "name": "NARAKA: BLADEPOINT"}]}
        return {
            "data": [
                {
                    "user_login": "narakabladepoint",
                    "game_id": "naraka-id",
                    "viewer_count": 100,
                }
            ]
        }

    monkeypatch.setattr(
        Twitch, "_Twitch__helix_get", lambda self, *args: helix_get(*args)
    )
    monkeypatch.setattr(Twitch, "_Twitch__log_category", lambda *args, **kwargs: None)

    assert twitch.get_live_streamers_for_category(
        "naraka-bladepoint|NARAKABladepoint", drops_enabled=False
    ) == ["narakabladepoint"]


def test_mod_view_channel_parses_moderator_status():
    payload = {
        "data": {"user": {"self": {"isModerator": True}}},
        "extensions": {"operationName": "ModViewChannelQuery"},
    }
    gql = GQL(
        client_session(), post_request=lambda *args, **kwargs: FakeResponse(payload)
    )

    assert gql.mod_view_channel("example").is_moderator is True
