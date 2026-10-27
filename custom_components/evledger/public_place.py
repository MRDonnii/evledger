"""Where a public charge happens (Claude AI, 2026-10-27; user: "hvis lokationer på tesla ladere kan komme med så er det
endnu bedre" and "norlys, clever og dem der nu er … byg det hele ind"). In this order:

1. a place the user named before (kept by the ledger with its price per kWh, within PLACE_RADIUS_M),
2. a Home Assistant zone the car is in (it can have its own price),
3. a Tesla Supercharger within SUPERCHARGER_RADIUS_M, from supercharge.info's public list (fetched when first needed,
   then at most once a week, kept in .storage so it works offline),
4. a station in the operators' own AFIR data (afir.py: their stations and ad hoc prices; Spirii's, OK's and others'
   without a key, Monta's – EWII, Norlys … – and Eco-Movement's – Clever, Circle K … – with the user's own access),
5. the nearest charging station in Open Charge Map (with the user's own free API key), with its operator.

Open Charge Map has names for most stations in Denmark, but the operator for only some (2026-10: PowerGo, Tesla, Clever,
Circle K, Ionity …; Norlys hardly). A place the user names once is therefore the reliable way."""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .afir import afir, site_station
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SITES_URL = "https://supercharge.info/service/supercharge/allSites"
SITES_MAX_AGE = timedelta(days=7)
SITES_RETRY = timedelta(hours=1)
SUPERCHARGER_RADIUS_M = 300
PLACE_RADIUS_M = 100
OCM_URL = "https://api.openchargemap.io/v3/poi/"
OCM_RADIUS_KM = 0.3
OCM_CACHE = timedelta(hours=6)
# Stations this close to the car are where it may be charging; one clearly nearer than the rest is taken without asking
CANDIDATE_RADIUS_M = 150
SURE_WITHIN_M = 40
# An Open Charge Map station this close to an AFIR station is that station
AFIR_SAME_SITE_M = 60
# Open Charge Map's stand-ins for "no operator"
OCM_NO_OPERATOR = ("unknown", "business owner", "private residence")
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
        except Exception as err:  # noqa: BLE001 - no list this time; names fall back to the other sources
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
            power = site.get("powerKilowatt")
            sites.append([round(float(lat), 6), round(float(lon), 6), name,
                          float(power) if isinstance(power, (int, float)) else None])
        if not sites:
            self._retry_after = now + SITES_RETRY
            return
        self.sites, self.fetched = sites, now
        await self._store.async_save({"fetched": now.isoformat(), "sites": sites})

    async def around(self, lat: float, lon: float, radius_m: float) -> list[dict]:
        """The Superchargers within radius_m of the point, nearest first."""
        await self._load()
        if self.sites is None or self.fetched is None or dt_util.utcnow() - self.fetched > SITES_MAX_AGE:
            await self._refresh()
        box_lat, box_lon = radius_m / 111_000 + 0.001, radius_m / 55_000 + 0.002
        found = []
        for site in self.sites or []:
            site_lat, site_lon, name = site[0], site[1], site[2]
            if abs(site_lat - lat) > box_lat or abs(site_lon - lon) > box_lon:
                continue
            meters = distance_m(lat, lon, site_lat, site_lon)
            if meters <= radius_m:
                found.append({"name": f"Supercharger {name}", "operator": "Tesla", "latitude": site_lat,
                              "longitude": site_lon, "distance_m": round(meters), "max_kw": site[3] if len(site) > 3 else None,
                              "kind": "supercharger"})
        return sorted(found, key=lambda item: item["distance_m"])

    async def near(self, lat: float, lon: float) -> str | None:
        """The name of a Supercharger within SUPERCHARGER_RADIUS_M of the point, the nearest one."""
        found = await self.around(lat, lon, SUPERCHARGER_RADIUS_M)
        return found[0]["name"] if found else None


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


def nearest_place(places: list[dict] | None, lat: float, lon: float) -> dict | None:
    """The named place within PLACE_RADIUS_M of the point, the nearest."""
    best: tuple[float, dict] | None = None
    for place in places or []:
        try:
            meters = distance_m(lat, lon, float(place["latitude"]), float(place["longitude"]))
        except (KeyError, TypeError, ValueError):
            continue
        if meters <= PLACE_RADIUS_M and (best is None or meters < best[0]):
            best = (meters, place)
    return best[1] if best else None


def _operator_name(title: str | None) -> str | None:
    """"Tesla (Tesla-only charging)" -> "Tesla", "E.ON (DK)" -> "E.ON"; None for Open Charge Map's "unknown" ones."""
    if not title or any(word in title.lower() for word in OCM_NO_OPERATOR):
        return None
    return title.split(" (")[0].strip() or None


class OpenChargeMap:
    """Stations around a point (name, operator, position, power, connectors), kept for OCM_CACHE per area."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._cache: dict[tuple, tuple[datetime, list[dict]]] = {}

    async def stations(self, key: str, lat: float, lon: float, radius_km: float = OCM_RADIUS_KM,
                       maxresults: int = 10) -> list[dict]:
        """The stations within radius_km, nearest first ([] when Open Charge Map cannot be reached)."""
        spot = (round(lat, 3 if radius_km > 1 else 4), round(lon, 3 if radius_km > 1 else 4), round(radius_km, 1),
                maxresults)
        cached = self._cache.get(spot)
        if cached is not None and dt_util.utcnow() - cached[0] < OCM_CACHE:
            return cached[1]
        params = {"output": "json", "latitude": f"{lat:.6f}", "longitude": f"{lon:.6f}", "distance": radius_km,
                  "distanceunit": "KM", "maxresults": maxresults, "compact": "false", "verbose": "false"}
        try:
            session = async_get_clientsession(self.hass)
            async with session.get(OCM_URL, params=params, timeout=aiohttp.ClientTimeout(total=20),
                                   headers={"X-API-Key": key, "User-Agent": "EV Ledger (Home Assistant integration)"}) as response:
                response.raise_for_status()
                raw = await response.json(content_type=None)
        except Exception as err:  # noqa: BLE001 - Open Charge Map is an extra
            _LOGGER.debug("Open Charge Map: %s", err)
            return []
        found = []
        for station in raw if isinstance(raw, list) else []:
            info = station.get("AddressInfo") or {}
            s_lat, s_lon = info.get("Latitude"), info.get("Longitude")
            if not (isinstance(s_lat, (int, float)) and isinstance(s_lon, (int, float))):
                continue
            meters = distance_m(lat, lon, s_lat, s_lon)
            if meters > radius_km * 1000:
                continue
            title = str(info.get("Title") or "").strip() or None
            operator = _operator_name((station.get("OperatorInfo") or {}).get("Title"))
            connectors, powers = [], []
            for conn in station.get("Connections") or []:
                power = conn.get("PowerKW")
                if isinstance(power, (int, float)) and power > 0:
                    powers.append(float(power))
                connectors.append({"type": ((conn.get("ConnectionType") or {}).get("Title") or "").split(" (")[0] or None,
                                   "kw": power if isinstance(power, (int, float)) else None,
                                   "count": conn.get("Quantity") or 1})
            name = title
            if operator and title and operator.lower() not in title.lower():
                name = f"{operator} – {title}"
            found.append({"name": name or operator, "operator": operator, "latitude": s_lat, "longitude": s_lon,
                          "distance_m": round(meters), "max_kw": max(powers) if powers else None,
                          "connectors": connectors, "points": station.get("NumberOfPoints"),
                          "usage_cost": (station.get("UsageCost") or None), "kind": "ocm",
                          "id": f"ocm{station.get('ID')}"})
        found.sort(key=lambda item: item["distance_m"])
        self._cache[spot] = (dt_util.utcnow(), found)
        return found

    async def station(self, key: str, lat: float, lon: float) -> tuple[str | None, str | None]:
        """The nearest station's (name, operator)."""
        found = await self.stations(key, lat, lon)
        return (found[0]["name"], found[0]["operator"]) if found else (None, None)


def open_charge_map(hass: HomeAssistant) -> OpenChargeMap:
    key = f"{DOMAIN}_open_charge_map"
    if not isinstance(hass.data.get(key), OpenChargeMap):
        hass.data[key] = OpenChargeMap(hass)
    return hass.data[key]


async def async_afir_stations(hass: HomeAssistant, lat: float, lon: float, radius_m: float,
                              credentials: dict | None) -> list[dict]:
    """The stations of the operators' AFIR data within radius_m, nearest first (old feeds are fetched again in the
    background; the stations known now are used meanwhile)."""
    try:
        data = afir(hass)
        await data.async_load()
        data.ensure_fresh(credentials or {})
        return [site_station(site, meters) for meters, site in data.around(lat, lon, radius_m, credentials or {})]
    except Exception:  # noqa: BLE001 - each source on its own
        _LOGGER.debug("AFIR lookup failed", exc_info=True)
        return []


def without_duplicates(stations: list[dict], afir_stations: list[dict], within_m: float = AFIR_SAME_SITE_M) -> list[dict]:
    """Open Charge Map's stations that are not an AFIR station already (Open Charge Map copies many from other lists,
    mostly without the operator; the operator's own data wins)."""
    return [station for station in stations
            if not any(distance_m(station["latitude"], station["longitude"], other["latitude"], other["longitude"])
                       <= within_m for other in afir_stations)]


async def async_candidates(hass: HomeAssistant, lat: float, lon: float, ocm_key: str | None,
                           credentials: dict | None = None) -> list[dict]:
    """The stations the car may be charging at (within CANDIDATE_RADIUS_M), nearest first: Superchargers, the operators'
    AFIR stations and Open Charge Map's (an Open Charge Map Tesla entry at a Supercharger is that Supercharger)."""
    try:
        found = await superchargers(hass).around(lat, lon, CANDIDATE_RADIUS_M)
    except Exception:  # noqa: BLE001 - each source on its own
        _LOGGER.debug("Supercharger lookup failed", exc_info=True)
        found = []
    stations = await async_afir_stations(hass, lat, lon, CANDIDATE_RADIUS_M, credentials)
    found += stations
    if ocm_key:
        for station in without_duplicates(await open_charge_map(hass).stations(ocm_key, lat, lon,
                                                                                CANDIDATE_RADIUS_M / 1000), stations):
            tesla = "tesla" in (station.get("operator") or "").lower() or "supercharger" in (station.get("name") or "").lower()
            if tesla and any(distance_m(station["latitude"], station["longitude"], sc["latitude"], sc["longitude"]) < 120
                             for sc in found if sc["kind"] == "supercharger"):
                continue
            found.append(station)
    return sorted(found, key=lambda item: item["distance_m"])


def is_certain(candidates: list[dict]) -> bool:
    """Whether the nearest candidate is surely where the car charges: the only one, all of one operator, or clearly
    nearer than the next (within SURE_WITHIN_M, the next at least twice as far and 80 m away)."""
    if len(candidates) <= 1:
        return True
    if len({(item.get("operator") or item.get("name") or "").lower() for item in candidates}) == 1:
        return True
    first, second = candidates[0]["distance_m"], candidates[1]["distance_m"]
    return first <= SURE_WITHIN_M and second >= max(80, 2 * first)


async def async_place(hass: HomeAssistant, lat: float | None, lon: float | None, places: list[dict] | None = None,
                      ocm_key: str | None = None, credentials: dict | None = None) -> dict:
    """Where the car is: {"zone", "name", "operator", "max_kw", "place", "station_id", "candidates", "certain"} (place =
    a place the user named, with its price; station_id = the AFIR station, with the operator's price; candidates = the
    stations it may be charging at when it is not certain which)."""
    found: dict = {"zone": None, "name": None, "operator": None, "max_kw": None, "place": None, "station_id": None,
                   "candidates": [], "certain": True}
    if lat is None or lon is None:
        return found
    if (place := nearest_place(places, lat, lon)) is not None:
        found.update(name=place.get("name"), operator=place.get("operator"), max_kw=place.get("max_kw"), place=place)
        return found
    zone = zone_at(hass, lat, lon)
    if zone is not None and zone.entity_id != "zone.home":
        found.update(zone=zone.entity_id, name=str(zone.attributes.get("friendly_name") or zone.name))
        return found
    try:
        candidates = await async_candidates(hass, lat, lon, ocm_key, credentials)
        if not candidates:  # nothing right here: a Supercharger or a station a little further away still names it
            candidates = (await superchargers(hass).around(lat, lon, SUPERCHARGER_RADIUS_M))[:1]
            if not candidates:
                candidates = (await async_afir_stations(hass, lat, lon, SUPERCHARGER_RADIUS_M, credentials))[:1]
            if not candidates and ocm_key:
                candidates = (await open_charge_map(hass).stations(ocm_key, lat, lon))[:1]
    except Exception:  # noqa: BLE001 - a place name is an extra, never a reason to lose the charge
        _LOGGER.debug("Station lookup failed", exc_info=True)
        candidates = []
    if candidates:
        best = candidates[0]
        found.update(name=best.get("name"), operator=best.get("operator"), max_kw=best.get("max_kw"),
                     station_id=best.get("id") if best.get("kind") == "afir" else None,
                     candidates=candidates[:4], certain=is_certain(candidates))
    return found
