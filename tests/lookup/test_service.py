"""Requires a dedicated Redis: both providers composed through LookupService."""

import pytest

from netools.core.errors import ServiceError
from netools.lookup.service import LookupService


async def test_combined_pipeline_with_real_redis(harness_factory, registry_factory, settings):
    risk = harness_factory()
    registry = registry_factory()
    lookup = LookupService(settings, risk.service, registry.service)
    results = await lookup.batch(["8.8.8.8", "2606:4700:4700::1111", "8.8.8.8"], lookup.lookup)
    assert all(item.result.risk.score == 23 and item.result.info is not None for item in results)
    assert len(registry.calls) == 2
    assert sum(not test for _, test, _ in risk.mock.calls) == 2
    again = await lookup.lookup("8.8.8.8")
    assert again.risk.cached and again.info.cached
    assert again.info_error is None


async def test_info_only_lookups_share_cache_with_combined(
    harness_factory, registry_factory, settings
):
    risk = harness_factory()
    registry = registry_factory()
    lookup = LookupService(settings, risk.service, registry.service)
    info = await lookup.info("8.8.8.8")
    assert info.data.ip == "8.8.8.8" and not info.cached
    combined = await lookup.lookup("8.8.8.8")
    assert combined.info.cached and not combined.risk.cached
    assert len(registry.calls) == 1


async def test_info_without_provider_is_an_explicit_error(harness_factory):
    risk = harness_factory()
    with pytest.raises(ServiceError) as raised:
        await risk.lookup.info("8.8.8.8")
    assert raised.value.code == "ipregistry_not_configured"
    combined = await risk.lookup.lookup("8.8.8.8")
    assert combined.info is None
    assert combined.info_error.code == "ipregistry_not_configured"
    assert combined.risk.score == 23
