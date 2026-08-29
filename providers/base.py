"""
The provider contract.

A provider is one scholarly data source.  It is responsible for exactly two
things: talking to its API, and translating that API's records into the
canonical paper structure from models.py.  It is responsible for *nothing*
else -- no scoring, no persistence, no ranking, no trend analysis.

Subclasses implement ``fetch_raw``; the base class handles normalization so
every provider produces identical output shapes by construction.
"""

from models import normalize_papers


class Provider:
    """Base class for every data source."""

    #: Registry key, e.g. "openalex".  Must be set by subclasses.
    name = "base"

    #: Human-readable label for the UI.
    label = "Base provider"

    def is_configured(self):
        """True when this provider can perform live requests.

        Providers requiring credentials override this.  A provider that is not
        configured must never be called; the registry checks this first.
        """

        return True

    def configuration_hint(self):
        """One line telling the user how to enable this provider."""

        return ""

    # -- to implement -------------------------------------------------------

    def fetch_raw(self, keyword, pages=None, per_page=None, **kwargs):
        """Return provider-native records as a list of dicts.

        Must raise a providers.errors.ProviderError subclass on failure rather
        than returning an empty list, so the caller can distinguish "no
        results" from "the provider is down".
        """

        raise NotImplementedError

    # -- provided -----------------------------------------------------------

    def search(self, keyword, pages=None, per_page=None, **kwargs):
        """Return canonical papers for ``keyword``.

        Never raises for an empty result set; raises ProviderError subclasses
        for real failures.
        """

        raw = self.fetch_raw(keyword, pages=pages, per_page=per_page, **kwargs)

        return normalize_papers(raw, source=self.name, keyword=keyword)

    def __repr__(self):  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} name={self.name!r}>"
