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
    test, whatever the time of day the tests run at (CI failed in the last minutes of an hour)."""
    freezer.move_to(dt_util.now().replace(minute=0, second=5, microsecond=0))
