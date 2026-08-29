"""The shared HTTP helper (providers/http.py).

This is where the politeness rules live, so this is where they get tested: one
honest User-Agent, bounded retries, 429 respected rather than worked around, and
4xx never retried.  Every test runs against an injected fake session, so the
suite never touches the network and never sleeps.
"""

import pytest
import requests

import config
from providers.errors import ProviderRateLimited, ProviderUnavailable
from providers.http import (
    RETRYABLE_STATUS,
    default_headers,
    get_json,
)


def call(session, sleep=None, **kwargs):
    return get_json(
        "https://example.test/works",
        provider="testprovider",
        session=session,
        sleep=sleep or (lambda seconds: None),
        **kwargs,
    )


class TestSuccess:

    def test_returns_the_parsed_payload(self, fake_session, fake_response):
        session = fake_session(fake_response(200, {"results": [1, 2, 3]}))

        assert call(session) == {"results": [1, 2, 3]}
        assert session.call_count == 1

    def test_params_are_passed_through(self, fake_session, fake_response):
        session = fake_session(fake_response(200, {}))

        call(session, params={"search": "risk"})

        assert session.calls[0]["params"] == {"search": "risk"}

    def test_timeout_is_passed_through(self, fake_session, fake_response):
        session = fake_session(fake_response(200, {}))

        call(session, timeout=7.5)

        assert session.calls[0]["timeout"] == 7.5

    def test_timeout_defaults_to_the_configured_value(self, fake_session, fake_response):
        session = fake_session(fake_response(200, {}))

        call(session)

        assert session.calls[0]["timeout"] == config.REQUEST_TIMEOUT


class TestHeaders:

    def test_identifies_itself_with_one_honest_user_agent(self):
        headers = default_headers()

        assert headers["User-Agent"] == config.USER_AGENT
        assert headers["Accept"] == "application/json"

    def test_never_impersonates_a_browser(self):
        # Spoofing a browser User-Agent to dodge provider protections is
        # exactly what this project does not do.
        agent = default_headers()["User-Agent"]

        for token in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko", "Edge"):
            assert token not in agent

    def test_extra_headers_are_merged_not_replaced(self):
        headers = default_headers({"x-api-key": "value"})

        assert headers["x-api-key"] == "value"
        assert headers["User-Agent"] == config.USER_AGENT

    def test_the_user_agent_is_sent_on_every_attempt(self, fake_session, fake_response):
        session = fake_session([fake_response(503, {}), fake_response(200, {})])

        call(session)

        agents = {request["headers"]["User-Agent"] for request in session.calls}

        # One agent, unchanged between retries -- no rotation.
        assert agents == {config.USER_AGENT}


class TestRateLimiting:

    def test_429_raises_immediately_without_retrying(self, fake_session, fake_response):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited):
            call(session)

        # One attempt only: a rate limit is respected, not hammered.
        assert session.call_count == 1

    def test_retry_after_is_reported(self, fake_session, fake_response):
        session = fake_session(fake_response(429, {}, headers={"Retry-After": "30"}))

        with pytest.raises(ProviderRateLimited) as raised:
            call(session)

        assert raised.value.retry_after == 30.0

    def test_a_missing_retry_after_is_not_invented(self, fake_session, fake_response):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited) as raised:
            call(session)

        assert raised.value.retry_after is None

    @pytest.mark.parametrize("value", ["soon", "", "-5", "NaN ", "inf", "-inf"])
    def test_an_unparseable_retry_after_is_ignored(self, fake_session, fake_response, value):
        # Regression: "NaN" and "inf" parse as floats, and a NaN retry_after
        # then compares false against everything it is measured against.
        session = fake_session(fake_response(429, {}, headers={"Retry-After": value}))

        with pytest.raises(ProviderRateLimited) as raised:
            call(session)

        assert raised.value.retry_after is None

    def test_the_error_carries_the_provider_and_status(self, fake_session, fake_response):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited) as raised:
            call(session)

        assert raised.value.provider == "testprovider"
        assert raised.value.status_code == 429
        assert raised.value.kind == "rate_limited"

    def test_no_sleeping_happens_on_a_rate_limit(self, fake_session, fake_response, recorded_sleep):
        session = fake_session(fake_response(429, {}))

        with pytest.raises(ProviderRateLimited):
            call(session, sleep=recorded_sleep)

        # We tell the caller to come back later rather than blocking the request.
        assert recorded_sleep.durations == []


class TestRetries:

    @pytest.mark.parametrize("status", sorted(RETRYABLE_STATUS))
    def test_retryable_statuses_are_retried_then_give_up(
        self, fake_session, fake_response, status
    ):
        session = fake_session(fake_response(status, {}))

        with pytest.raises(ProviderUnavailable) as raised:
            call(session)

        assert session.call_count == config.MAX_RETRIES + 1
        assert raised.value.status_code == status

    def test_a_retry_can_succeed(self, fake_session, fake_response):
        session = fake_session([fake_response(503, {}), fake_response(200, {"ok": True})])

        assert call(session) == {"ok": True}
        assert session.call_count == 2

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
    def test_client_errors_are_never_retried(self, fake_session, fake_response, status):
        session = fake_session(fake_response(status, {}))

        with pytest.raises(ProviderUnavailable):
            call(session)

        # Asking again does not fix a bad request or a missing key.
        assert session.call_count == 1

    def test_retry_count_is_configurable_and_bounded(self, fake_session, fake_response):
        session = fake_session(fake_response(500, {}))

        with pytest.raises(ProviderUnavailable):
            call(session, max_retries=0)

        assert session.call_count == 1

    def test_backoff_grows_but_stays_under_the_ceiling(
        self, fake_session, fake_response, recorded_sleep
    ):
        session = fake_session(fake_response(500, {}))

        with pytest.raises(ProviderUnavailable):
            call(session, sleep=recorded_sleep, max_retries=4)

        assert len(recorded_sleep.durations) == 4
        assert recorded_sleep.durations == sorted(recorded_sleep.durations)
        assert all(delay <= config.MAX_BACKOFF_SECONDS for delay in recorded_sleep.durations)

    def test_network_errors_are_retried_then_reported(self, fake_response):
        class BrokenSession:
            def __init__(self):
                self.calls = 0

            def get(self, url, params=None, headers=None, timeout=None):
                self.calls += 1
                raise requests.RequestException("connection reset")

        session = BrokenSession()

        with pytest.raises(ProviderUnavailable) as raised:
            call(session)

        assert session.calls == config.MAX_RETRIES + 1
        assert "network error" in raised.value.message


class TestMalformedResponses:

    def test_a_non_json_body_is_an_error_not_a_crash(self, fake_session, fake_response):
        session = fake_session(
            fake_response(200, body_error=ValueError("Expecting value: line 1 column 1"))
        )

        with pytest.raises(ProviderUnavailable) as raised:
            call(session)

        assert "non-JSON" in raised.value.message

    @pytest.mark.parametrize("payload", [[], [1, 2], "text", 42, None, True])
    def test_a_non_object_payload_is_rejected(self, fake_session, fake_response, payload):
        session = fake_session(fake_response(200, payload))

        with pytest.raises(ProviderUnavailable):
            call(session)

    def test_a_missing_status_code_is_treated_as_a_failure(self, fake_session):
        class Weird:
            headers = {}

            def json(self):
                return {}

        session = fake_session([Weird()])

        with pytest.raises(ProviderUnavailable):
            call(session)


class TestErrorContract:

    def test_errors_serialize_for_logging_without_leaking_headers(
        self, fake_session, fake_response
    ):
        session = fake_session(fake_response(500, {}))

        with pytest.raises(ProviderUnavailable) as raised:
            call(session)

        payload = raised.value.as_dict()

        assert payload["kind"] == "unavailable"
        assert payload["provider"] == "testprovider"
        assert payload["status_code"] == 500
        assert isinstance(payload["message"], str)

    def test_rate_limit_errors_include_retry_after_in_their_dict(
        self, fake_session, fake_response
    ):
        session = fake_session(fake_response(429, {}, headers={"Retry-After": "12"}))

        with pytest.raises(ProviderRateLimited) as raised:
            call(session)

        assert raised.value.as_dict()["retry_after"] == 12.0
