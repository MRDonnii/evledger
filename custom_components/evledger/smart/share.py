"""Several cars on one charger: the car that is plugged in (and at home) takes the charger with its own plan, model
and ledger; the others leave it alone. One car on a charger works as before."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from .planner import ChargePlanner

SHARES_KEY = "evledger_charger_shares"


class ChargerShare:
    """The EV Ledger cars with smart charging on the same charger (found by the charger's entities)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.planners: list[ChargePlanner] = []
        # Chosen with "Charge now" while no car (or the wrong one) reports: kept until the cable comes out.
        self.forced: ChargePlanner | None = None
        self._last_owner: ChargePlanner | None = None

    @property
    def shared(self) -> bool:
        return len(self.planners) > 1

    def owner(self) -> ChargePlanner | None:
        """The car on the charger: chosen by hand, else the one whose own plug sensor says plugged in (the most
        recent if several), else the only car at home. None while that cannot be told."""
        if self.forced in self.planners:
            return self.forced
        plugged = []
        for planner in self.planners:
            state, changed = planner.car_plug_state()
            if state is True and planner.car_home() is not False:
                plugged.append((changed.timestamp() if changed else 0.0, planner))
        if plugged:
            return max(plugged, key=lambda item: item[0])[1]
        # A car's plug sensor follows only when the car is awake: right after plugging in it may still say unplugged.
        home = [planner for planner in self.planners if planner.car_home() is True]
        if len(home) == 1:
            return home[0]
        return None

    def check(self) -> None:
        """When the car on the charger changes, the other cars look again (their status and plans)."""
        owner = self.owner()
        if owner is self._last_owner:
            return
        self._last_owner = owner
        for planner in list(self.planners):
            self.hass.loop.call_soon(planner.async_recalculate)

    def unplugged(self) -> None:
        self.forced = None


def join(hass: HomeAssistant, planner: ChargePlanner) -> ChargerShare | None:
    """Put the planner with the other cars on its charger."""
    if planner.backend is None:
        return None
    key = frozenset(planner.backend.entities)
    share = hass.data.setdefault(SHARES_KEY, {}).setdefault(key, ChargerShare(hass))
    if planner not in share.planners:
        share.planners.append(planner)
    share.check()
    return share


def leave(hass: HomeAssistant, planner: ChargePlanner, share: ChargerShare | None) -> None:
    if share is None:
        return
    if planner in share.planners:
        share.planners.remove(planner)
    if share.forced is planner:
        share.forced = None
    if not share.planners:
        for key, value in list(hass.data.get(SHARES_KEY, {}).items()):
            if value is share:
                del hass.data[SHARES_KEY][key]
    else:
        share.check()
