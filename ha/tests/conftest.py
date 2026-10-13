"""Runs the custom integrations against Home Assistant's own test harness:

    pip install pytest-homeassistant-custom-component   # with the Python current HA needs (3.14)
    cd ha && python -m pytest

CI runs only the plain tests in tests/ (house.py without Home Assistant); these are run by hand."""

import pytest

# Taken first, so Home Assistant finds genbu here and not in the harness's own testing config.
import custom_components  # noqa: F401


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


# What the release channels have, by manifest address; a test puts a manifest here. Nothing goes to GitHub.
RELEASES: dict[str, dict] = {}


@pytest.fixture(autouse=True)
def releases(monkeypatch):
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.genbu.coordinator import GenbuHouse

    async def manifest(self, url: str) -> dict:
        if url not in RELEASES:
            raise HomeAssistantError(f"no release there ({url})")
        return dict(RELEASES[url])

    RELEASES.clear()
    monkeypatch.setattr(GenbuHouse, "_manifest", manifest)
    yield RELEASES
    RELEASES.clear()
