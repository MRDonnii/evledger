"""Home Assistant tests. Skipped when pytest-homeassistant-custom-component is not installed."""

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.util import dt as dt_util  # noqa: E402


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def clock_at_start_of_hour(freezer):
    """The tests make the current hour the cheap one; start at its beginning so that it lasts the whole
    test (CI failed in the last minutes of an hour), and at noon, so that the hours the tests make cheap lie
    before the default ready-by time (06:45) the next morning whatever the time of day the tests run at (CI
    failed at night, when 06:45 cut the cheap hour)."""
    freezer.move_to(dt_util.now().replace(hour=12, minute=0, second=5, microsecond=0))


@pytest.fixture(autouse=True)
def no_afir_fetch(monkeypatch):
    """The operators' AFIR feeds are never fetched by themselves in tests (a test fetches them on purpose)."""
    from custom_components.evledger import afir
    from custom_components.evledger.afir import Afir

    monkeypatch.setattr(Afir, "ensure_fresh", lambda self, credentials: None)
    monkeypatch.setattr(afir, "FEED_PAUSE", 0)  # the clock stands still in tests
