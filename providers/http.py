"""
Shared, deliberately polite HTTP helper for every provider.

Design rules, all of them intentional:

  * One identified User-Agent.  We never rotate it, never impersonate a
    browser, and never disguise the client.  Scholarly APIs ask to be told who
    is calling; this does that.
  * Bounded retries.  A finite number of attempts with exponential backoff,
    every sleep capped by config.MAX_BACKOFF_SECONDS.  A provider can never
    park a web request for minutes.
  * 429 is respected, not circumvented.  Callers may opt into one bounded
    Retry-After wait/retry; the default remains immediate error propagation.
  * 4xx is never retried.  A bad request or a missing key does not get better
    by asking again.
"""

import time

import requests

import config
from providers.errors import (
    ProviderRateLimited,
    ProviderUnavailable,
)


#: Statuses where retrying actually makes sense.
RETRYABLE_STATUS = frozenset({500, 502, 503, 504, 408, 425})


def default_headers(extra=None):
    headers = {
        "User-Agent": config.USER_AGENT,
        "Accept": "application/json",
    }

    if extra:
        headers.update(extra)

    return headers


def _retry_after_seconds(response):
    """Parse Retry-After, clamped to our own ceiling.  None when absent."""

    raw = response.headers.get("Retry-After")

    if not raw:
        return None

    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None

    # "NaN" and "inf" parse as floats but are not durations, and a NaN would
    # then propagate silently through every comparison it touches.
    if seconds != seconds or seconds in (float("inf"), float("-inf")):
        return None

    if seconds < 0:
        return None

    return seconds


def _backoff_delay(attempt):
    """Exponential backoff for attempt 0, 1, 2 ... capped by config."""

    delay = config.RETRY_BACKOFF_SECONDS * (2 ** attempt)

    return min(delay, config.MAX_BACKOFF_SECONDS)


def get_json(
    url,
    provider,
    params=None,
    headers=None,
    timeout=None,
    max_retries=None,
    session=None,
    sleep=None,
    retry_rate_limited=False,
    rate_limit_retries=None,
    max_rate_limit_wait=None,
):
    """GET ``url`` and return parsed JSON.

    Raises ProviderRateLimited on 429 and ProviderUnavailable on anything else
    that prevents us returning a dict.  Never returns None, never returns a
    partially-parsed body.

    ``session`` and ``sleep`` are injectable so tests can run without network
    access and without real delays. Rate-limit retries are opt-in so providers
    that preserve immediate 429 propagation can continue to do so.
    """

    if timeout is None:
        timeout = config.REQUEST_TIMEOUT

    if max_retries is None:
        max_retries = config.MAX_RETRIES

    if rate_limit_retries is None:
        rate_limit_retries = config.RATE_LIMIT_RETRIES

    if max_rate_limit_wait is None:
        max_rate_limit_wait = config.MAX_RATE_LIMIT_WAIT_SECONDS

    if sleep is None:
        sleep = time.sleep

    getter = session.get if session is not None else requests.get

    attempts = max_retries + 1
    last_error = None
    pending_sleep = None
    rate_limit_attempts = 0

    for attempt in range(attempts):

        if pending_sleep is not None:
            sleep(pending_sleep)
            pending_sleep = None
        elif attempt:
            sleep(_backoff_delay(attempt - 1))

        try:
            response = getter(
                url,
                params=params,
                headers=default_headers(headers),
                timeout=timeout,
            )

        except requests.RequestException as error:
            last_error = ProviderUnavailable(
                f"network error contacting {provider}: {error}",
                provider=provider,
            )
            continue

        status = getattr(response, "status_code", None)

        if status == 429:
            retry_after = _retry_after_seconds(response)

            if retry_rate_limited and rate_limit_attempts < max(0, int(rate_limit_retries)):
                rate_limit_attempts += 1
                delay = retry_after if retry_after is not None else _backoff_delay(attempt)
                pending_sleep = min(
                    max(float(delay), 0.0),
                    max(0.0, float(max_rate_limit_wait)),
                )
                continue

            raise ProviderRateLimited(
                f"{provider} rate limit reached (HTTP 429)",
                provider=provider,
                retry_after=retry_after,
            )

        if status in RETRYABLE_STATUS:
            last_error = ProviderUnavailable(
                f"{provider} returned HTTP {status}",
                provider=provider,
                status_code=status,
            )
            continue

        if status != 200:
            # 4xx and anything else unexpected: fail fast, no retry.
            raise ProviderUnavailable(
                f"{provider} returned HTTP {status}",
                provider=provider,
                status_code=status,
            )

        try:
            payload = response.json()
        except ValueError as error:
            raise ProviderUnavailable(
                f"{provider} returned a non-JSON body: {error}",
                provider=provider,
                status_code=status,
            ) from error

        if not isinstance(payload, dict):
            raise ProviderUnavailable(
                f"{provider} returned {type(payload).__name__}, expected an object",
                provider=provider,
                status_code=status,
            )

        return payload

    raise last_error or ProviderUnavailable(
        f"{provider} could not be reached",
        provider=provider,
    )
