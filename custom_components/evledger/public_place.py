"""Where a public charge happens: a Home Assistant zone the car is in, or the nearest Tesla Supercharger (Claude AI,
2026-10-27; user: "hvis lokationer på tesla ladere kan komme med så er det endnu bedre").

The Supercharger sites are supercharge.info's public list. It is fetched when a public charge first needs it, then at
most once a week, and kept in .storage, so a charge without network still gets its name."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SITES_URL = "https://supercharge.info/service/supercharge/allSites"
SITES_MAX_AGE = timedelta(days=7)
SITES_RETRY = timedelta(hours=1)
SUPERCHARGER_RADIUS_M = 300
# Sites that do not charge (yet or any more)
NOT_CHARGING = {"PLAN", "PERMIT", "CONSTRUCTION", "CLOSED_PERM", "VOTING"}


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(a))


class Superchargers:
    """The Supercharger sites as [latitude, longitude, name] (the country taken out of the name)."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, 1, f"{DOMAIN}_superchargers")
        self.sites: list[list] | None = None
        self.fetched: datetime | None = None
        self._loaded = False
        self._retry_after: datetime | None = None

    async def _load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load() or {}
        if isinstance(data.get("sites"), list):
            self.sites = data["sites"]
            self.fetched = dt_util.parse_datetime(data.get("fetched") or "")

    async def _refresh(self) -> None:
        now = dt_util.utcnow()
        if self._retry_after is not None and now < self._retry_after:
            return
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(SITES_URL, timeout=aiohttp.ClientTimeout(total=40),
                                   headers={"User-Agent": "EV Ledger (Home Assistant integration)"}) as response:
                response.raise_for_status()
                raw = await response.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            self._retry_after = now + SITES_RETRY
            _LOGGER.debug("Supercharger list not fetched: %s", err)
            return
        sites = []
        for site in raw if isinstance(raw, list) else []:
            if not isinstance(site, dict) or site.get("status") in NOT_CHARGING:
                continue
            gps = site.get("gps") or {}
            lat, lon, name = gps.get("latitude"), gps.get("longitude"), str(site.get("name") or "").strip()
            if not (isinstance(lat, (int, float)) and isinstance(lon, (int, float)) and name):
                continue
            country = str((site.get("address") or {}).get("country") or "")
            if country:
                name = name.replace(f", {country}", "").strip(" ,")
            sites.append([round(float(lat), 6), round(float(lon), 6), name])
        if not sites:
            self._retry_after = now + SITES_RETRY
            return
        self.sites, self.fetched = sites, now
        await self._store.async_save({"fetched": now.isoformat(), "sites": sites})

    async def near(self, lat: float, lon: float) -> str | None:
        """The name of a Supercharger within SUPERCHARGER_RADIUS_M of the point, the nearest one."""
        await self._load()
        if self.sites is None or self.fetched is None or dt_util.utcnow() - self.fetched > SITES_MAX_AGE:
            await self._refresh()
        best: tuple[float, str] | None = None
        for site_lat, site_lon, name in self.sites or []:
            if abs(site_lat - lat) > 0.01 or abs(site_lon - lon) > 0.02:  # well over 300 m away
                continue
            meters = distance_m(lat, lon, site_lat, site_lon)
            if meters <= SUPERCHARGER_RADIUS_M and (best is None or meters < best[0]):
                best = (meters, name)
        return f"Supercharger {best[1]}" if best else None


def superchargers(hass: HomeAssistant) -> Superchargers:
    """One list for all cars (its own key: hass.data[DOMAIN] holds only the cars' coordinators)."""
    key = f"{DOMAIN}_superchargers"
    if not isinstance(hass.data.get(key), Superchargers):
        hass.data[key] = Superchargers(hass)
    return hass.data[key]


def zone_at(hass: HomeAssistant, lat: float, lon: float):
    """The zone the point is in, the smallest when zones overlap (as Home Assistant picks a zone); passive zones and
    zones without a position are left out."""
    best = None
    for zone in hass.states.async_all("zone"):
        attrs = zone.attributes
        zlat, zlon, radius = attrs.get("latitude"), attrs.get("longitude"), attrs.get("radius")
        if attrs.get("passive") or not all(isinstance(v, (int, float)) for v in (zlat, zlon, radius)):
            continue
        if distance_m(lat, lon, zlat, zlon) <= radius and (best is None or radius < best[0]):
            best = (radius, zone)
    return best[1] if best else None


async def async_place(hass: HomeAssistant, lat: float | None, lon: float | None) -> tuple[str | None, str | None]:
    """(zone entity id, name) of where the car is: a zone first (its own price may apply), else a Supercharger."""
    if lat is None or lon is None:
        return None, None
    zone = zone_at(hass, lat, lon)
    if zone is not None and zone.entity_id != "zone.home":
        return zone.entity_id, str(zone.attributes.get("friendly_name") or zone.name)
    try:
        name = await superchargers(hass).near(lat, lon)
    except Exception:  # noqa: BLE001 - a place name is an extra, never a reason to lose the charge
        _LOGGER.debug("Supercharger lookup failed", exc_info=True)
        name = None
    return None, name
