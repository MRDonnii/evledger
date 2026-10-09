"""Smart charging text entities (only when smart charging is on)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .smart.ent_text import build


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    planner = getattr(hass.data[DOMAIN][entry.entry_id], "smart", None)
    if planner is not None:
        async_add_entities(build(planner))
