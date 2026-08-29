"""
Provider errors.

Callers need to tell three situations apart, because the right response differs:

    ProviderRateLimited   the provider asked us to slow down (HTTP 429).
                          Back off, tell the user, do not hammer.

    ProviderUnavailable   network failure, timeout, 5xx, malformed payload.
                          Transient; retrying later is reasonable.

    ProviderNotConfigured the provider needs credentials we do not have.
                          Never retried -- it will not fix itself.

All three derive from ProviderError, so a route can catch one type and still
report something meaningful.
"""


class ProviderError(Exception):
    """Base class for every provider-layer failure."""

    #: Short machine-readable label surfaced to templates and logs.
    kind = "provider_error"

    def __init__(self, message, provider=None, status_code=None):
        super().__init__(message)

        self.provider = provider
        self.status_code = status_code

    @property
    def message(self):
        return str(self)

    def as_dict(self):
        return {
            "kind": self.kind,
            "provider": self.provider,
            "status_code": self.status_code,
            "message": self.message,
        }


class ProviderUnavailable(ProviderError):
    """The provider could not be reached, or answered with something unusable."""

    kind = "unavailable"


class ProviderRateLimited(ProviderError):
    """The provider is rate-limiting us (HTTP 429 or equivalent)."""

    kind = "rate_limited"

    def __init__(self, message, provider=None, status_code=429, retry_after=None):
        super().__init__(message, provider=provider, status_code=status_code)

        self.retry_after = retry_after

    def as_dict(self):
        payload = super().as_dict()
        payload["retry_after"] = self.retry_after
        return payload


class ProviderNotConfigured(ProviderError):
    """The provider is missing required configuration, such as an API key."""

    kind = "not_configured"


class UnknownProvider(ProviderError):
    """A provider name was requested that is not registered."""

    kind = "unknown_provider"


class SearchPipelineError(Exception):
    """A search could not be orchestrated (empty keyword, bad argument, ...).

    Deliberately separate from ProviderError: a provider failure means the
    search itself was fine, while a SearchPipelineError means the request was
    not worth sending anywhere.
    """

    def __init__(self, message):
        super().__init__(message)

    @property
    def message(self):
        return str(self)
