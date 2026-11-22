"""Button entities: the public charge form, and smart charging's (only when smart charging is on)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .public_form import buttons
from .smart.ent_button import build


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    entities = buttons(coordinator, entry)
    planner = getattr(coordinator, "smart", None)
    if planner is not None:
        entities.extend(build(planner))
    async_add_entities(entities)
