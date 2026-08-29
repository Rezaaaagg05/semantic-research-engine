"""The provider registry (providers/__init__.py).

The registry is the only place that decides which data sources exist and which
one runs by default.  Two properties matter most: OpenAlex is the default, and an
unrecognised name is an error rather than a silent substitution -- quietly
searching a different database than the one asked for would be worse than
failing.
"""

import pytest

import config
import providers
from providers import (
    PROVIDERS,
    OpenAlexProvider,
    SemanticScholarProvider,
    UnknownProvider,
    available_providers,
    canonical_name,
    default_provider_name,
    describe_providers,
    get_provider,
)
from providers.base import Provider


class TestRegistryContents:

    def test_both_providers_are_registered_under_their_own_names(self):
        assert PROVIDERS == {
            "openalex": OpenAlexProvider,
            "semanticscholar": SemanticScholarProvider,
        }

    def test_every_registered_class_implements_the_contract(self):
        for provider_class in PROVIDERS.values():
            assert issubclass(provider_class, Provider)

            provider = provider_class()

            assert isinstance(provider.name, str) and provider.name
            assert isinstance(provider.label, str) and provider.label
            assert isinstance(provider.configuration_hint(), str)
            assert provider.is_configured() in (True, False)

    def test_registry_keys_match_the_class_names(self):
        for name, provider_class in PROVIDERS.items():
            assert name == provider_class.name

    def test_the_base_class_refuses_to_be_used_directly(self):
        with pytest.raises(NotImplementedError):
            Provider().fetch_raw("risk")


class TestDefault:

    def test_openalex_is_the_default(self):
        assert default_provider_name() == "openalex"

    def test_config_agrees(self):
        assert config.DEFAULT_PROVIDER == "openalex"

    def test_no_argument_selects_the_default(self):
        assert get_provider().name == "openalex"

    @pytest.mark.parametrize("value", [None, "", "   "])
    def test_blank_selects_the_default(self, value):
        assert get_provider(value).name == "openalex"

    def test_a_misconfigured_default_falls_back_to_openalex(self, monkeypatch):
        # A typo in the environment must not leave the app with no data source.
        monkeypatch.setattr(config, "DEFAULT_PROVIDER", "nonesuch")

        assert default_provider_name() == "openalex"

    def test_the_default_can_be_changed_by_configuration(self, monkeypatch):
        monkeypatch.setattr(config, "DEFAULT_PROVIDER", "semanticscholar")

        assert default_provider_name() == "semanticscholar"


class TestNameResolution:

    @pytest.mark.parametrize(
        "requested",
        ["openalex", "OpenAlex", "OPENALEX", " openalex ", "open_alex", "open-alex", "alex"],
    )
    def test_openalex_spellings(self, requested):
        assert canonical_name(requested) == "openalex"
        assert get_provider(requested).name == "openalex"

    @pytest.mark.parametrize(
        "requested",
        [
            "semanticscholar",
            "SemanticScholar",
            "semantic scholar",
            "semantic_scholar",
            "semantic-scholar",
            "semantic",
            "s2",
        ],
    )
    def test_semantic_scholar_spellings(self, requested):
        assert canonical_name(requested) == "semanticscholar"
        assert get_provider(requested).name == "semanticscholar"

    def test_an_unknown_name_is_an_error_not_a_substitution(self):
        with pytest.raises(UnknownProvider) as raised:
            get_provider("scopus")

        assert raised.value.kind == "unknown_provider"
        assert "scopus" in raised.value.message
        # The message lists what *is* available, so the mistake is fixable.
        assert "openalex" in raised.value.message

    def test_the_unknown_name_is_carried_on_the_error(self):
        with pytest.raises(UnknownProvider) as raised:
            get_provider("scopus")

        assert raised.value.provider == "scopus"

    def test_keyword_arguments_reach_the_provider(self, fake_session, fake_response):
        session = fake_session(fake_response(200, {"results": []}))

        provider = get_provider("openalex", session=session)

        # Constructed with the injected session, so this instance is offline.
        assert provider.fetch_raw("risk", pages=1) == []
        assert session.call_count == 1


class TestAvailability:

    def test_openalex_is_always_available(self):
        assert "openalex" in available_providers()

    def test_semantic_scholar_is_absent_until_configured(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", "")
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", False)

        assert available_providers() == ["openalex"]

    def test_semantic_scholar_appears_once_configured(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", "not-a-real-key")

        assert available_providers() == ["openalex", "semanticscholar"]

    def test_a_broken_provider_does_not_break_the_listing(self, monkeypatch):
        class Exploding(OpenAlexProvider):
            name = "exploding"

            def is_configured(self):
                raise RuntimeError("boom")

        monkeypatch.setitem(providers.PROVIDERS, "exploding", Exploding)

        assert "openalex" in available_providers()
        assert "exploding" not in available_providers()


class TestDescribeProviders:

    def test_describes_every_provider(self):
        described = describe_providers()

        assert [entry["name"] for entry in described] == ["openalex", "semanticscholar"]

    def test_each_entry_carries_what_the_ui_needs(self):
        for entry in describe_providers():
            assert set(entry) == {"name", "label", "configured", "is_default", "hint"}
            assert isinstance(entry["configured"], bool)
            assert isinstance(entry["is_default"], bool)
            assert entry["hint"]

    def test_exactly_one_provider_is_marked_default(self):
        defaults = [entry for entry in describe_providers() if entry["is_default"]]

        assert len(defaults) == 1
        assert defaults[0]["name"] == "openalex"

    def test_an_unconfigured_provider_says_how_to_enable_itself(self, monkeypatch):
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_API_KEY", "")
        monkeypatch.setattr(config, "SEMANTIC_SCHOLAR_ALLOW_UNAUTHENTICATED", False)

        entry = next(e for e in describe_providers() if e["name"] == "semanticscholar")

        assert entry["configured"] is False
        assert "SEMANTIC_SCHOLAR_API_KEY" in entry["hint"]

    def test_describing_providers_makes_no_network_call(self, monkeypatch):
        def forbidden(*args, **kwargs):  # pragma: no cover - must never run
            raise AssertionError("describe_providers must not touch the network")

        monkeypatch.setattr("providers.http.get_json", forbidden)

        assert describe_providers()
