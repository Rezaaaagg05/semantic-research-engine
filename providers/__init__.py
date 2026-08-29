"""
Provider registry.

One place that knows which data sources exist, which one is the default, and
which are usable right now.  Everything upstream asks for a provider by name
and gets an object satisfying the providers.base.Provider contract.

Importing this package performs no network calls and reads no credentials
beyond what config already loaded, so it is safe to import from tests.
"""

import config
from providers.base import Provider
from providers.errors import (
    ProviderError,
    ProviderNotConfigured,
    ProviderRateLimited,
    ProviderUnavailable,
    SearchPipelineError,
    UnknownProvider,
)
from providers.openalex import OpenAlexProvider
from providers.semanticscholar import SemanticScholarProvider


__all__ = [
    "Provider",
    "ProviderError",
    "ProviderNotConfigured",
    "ProviderRateLimited",
    "ProviderUnavailable",
    "SearchPipelineError",
    "UnknownProvider",
    "OpenAlexProvider",
    "SemanticScholarProvider",
    "PROVIDERS",
    "default_provider_name",
    "get_provider",
    "available_providers",
    "describe_providers",
]


#: Registry: name -> provider class.  OpenAlex first; it is the default.
PROVIDERS = {
    OpenAlexProvider.name: OpenAlexProvider,
    SemanticScholarProvider.name: SemanticScholarProvider,
}


#: Names people are likely to type or that older code used.
ALIASES = {
    "open_alex": OpenAlexProvider.name,
    "open-alex": OpenAlexProvider.name,
    "alex": OpenAlexProvider.name,
    "s2": SemanticScholarProvider.name,
    "semantic": SemanticScholarProvider.name,
    "semantic_scholar": SemanticScholarProvider.name,
    "semantic-scholar": SemanticScholarProvider.name,
}


def canonical_name(name):
    """Resolve aliases and casing to a registry key."""

    key = (name or "").strip().lower().replace(" ", "")

    return ALIASES.get(key, key)


def default_provider_name():
    """Configured default, falling back to OpenAlex if it is misconfigured."""

    name = canonical_name(config.DEFAULT_PROVIDER)

    if name in PROVIDERS:
        return name

    return OpenAlexProvider.name


def get_provider(name=None, **kwargs):
    """Instantiate a provider by name.

    ``name`` of None or "" selects the configured default.  Raises
    UnknownProvider for an unregistered name -- callers should surface that
    rather than silently substituting a different data source.
    """

    if name is None or not str(name).strip():
        resolved = default_provider_name()
    else:
        resolved = canonical_name(name)

    provider_class = PROVIDERS.get(resolved)

    if provider_class is None:
        raise UnknownProvider(
            f"unknown provider {name!r}; available: "
            f"{', '.join(sorted(PROVIDERS))}",
            provider=str(name),
        )

    return provider_class(**kwargs)


def available_providers():
    """Names of providers that could run a search right now."""

    names = []

    for name, provider_class in PROVIDERS.items():

        try:
            if provider_class().is_configured():
                names.append(name)
        except Exception:  # pragma: no cover - defensive
            continue

    return sorted(names)


def describe_providers():
    """Registry summary for the UI: name, label, readiness, and how to enable."""

    described = []
    default = default_provider_name()

    for name in sorted(PROVIDERS):

        provider = PROVIDERS[name]()

        described.append(
            {
                "name": name,
                "label": provider.label,
                "configured": bool(provider.is_configured()),
                "is_default": name == default,
                "hint": provider.configuration_hint(),
            }
        )

    return described
