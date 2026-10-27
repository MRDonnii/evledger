"""Public charging stations with the operators' own prices, from their AFIR data (Claude AI, 2026-10-27; user: "Jeg mener
det er ewii og Norlys der er her. Er der ikke andre måder vi kan hente laderne og priser").

Since 14 April 2026 the EU's AFIR (Article 20) makes every operator of public chargers publish its stations and its ad hoc
price in DATEX II through the national access point; Denmark's is Vejdirektoratet's Dataudveksler. The feeds used:

- Spirii's public feed (no key): Spirii, PowerGo, Verdo, Aura, EDF, Looad, Shell Recharge, DEAS, nef Strøm …
- OK (its key is public in the access point's catalogue): OK's stations, without prices (OK's operator price applies).
- EV-EL and TotalEnergies (no key).
- Monta's AFIR endpoints with the user's own Monta Public API application: EWII, Norlys, go'energi, NRGi, Uno-X, Energi
  Fyn, Apcoa …
- Eco-Movement with a token from Eco-Movement: Clever, Circle K, Q8, Allego, Ionity, Lidl, Fastned …

Spirii's operator ids and how the feeds write prices (dates as midnight UTC, local times of day, VAT as a fraction at
Eco-Movement) follow github.com/zipel/ChargingPrices (MIT). A feed is fetched at most every FEED_MAX_AGE and only when a
map or a public charge needs it, parsed site by site in an executor (Spirii writes a price per quarter of an hour, some
70 MB per 1,000 sites), and kept compact in .storage. A site is {"id", "n" name, "o" operator, "a" latitude, "b"
longitude, "s" feed, "g" groups of charge points with the same power and tariff: {"kw", "t" plug types, "c" count,
"tf" tariff}}; a tariff is {"p" [[first date, last date, from minute, to minute, kr/kWh]], "f" kr/kWh at other times,
"s" start fee, "m" [[kr/minute, from minute]]}.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import math
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

FEED_MAX_AGE = timedelta(hours=6)
FEED_RETRY = timedelta(minutes=30)
FEED_PAUSE = 0.25  # seconds between two requests to the same feed
USER_AGENT = "EV Ledger (Home Assistant integration)"
EUR_TO_DKK = 7.46038  # Denmark's peg; Mercedes-Benz' chargers publish in euro

SPIRII_URL = "https://api.spirii.com/v2/afir/energy-infrastructure-tables"
# The operators with Danish sites in Spirii's feed (it gives at most 1,000 sites per request); 17 is a test operator
SPIRII_CPOS = (1, 4, 7, 8, 10, 16, 20, 25, 36, 37, 44, 47, 50, 56, 57, 59, 83, 84, 90, 99, 106, 140)
OPEN_XML_FEEDS = {
    "ok": "https://ocpi-emobility.okcloud.dk/datex/locations?token=2f917dd0-d3c9-4cbd-b698-343b3df62232",
    "ev-el": "https://api-ev-el.bluebell.dk/datex2/v3/EVEL/EnergyInfrastructureTablePublication",
    "totalenergies": "https://api-te.bluebell.dk/datex2/v3/TEE/EnergyInfrastructureTablePublication",
}
MONTA_API = "https://public-api.monta.com/api/v1"
MONTA_PER_PAGE = 1000
MONTA_MAX_PAGES = 60
ECO_URL = "https://nap-dk.eco-movement.com/datex2/v1/locations"
ECO_MAX_PAGES = 40
FEED_NAMES = {"spirii": "Spirii", "ok": "OK", "ev-el": "EV-EL", "totalenergies": "TotalEnergies", "monta": "Monta",
              "eco": "Eco-Movement"}
PLUGS = {"iec62196t2": "Type 2", "iec62196t2combo": "CCS", "chademo": "CHAdeMO", "iec62196t1": "Type 1",
         "iec62196t1combo": "CCS1", "teslaconnector": "Tesla", "teslar": "Tesla", "teslas": "Tesla",
         "domesticf": "Schuko", "iec60309x2three32": "CEE", "iec62196t3a": "Type 3", "nacs": "NACS"}
# A station from this power counts as DC; an average charging power above AC_MAX_KW was on a DC point
AC_MAX_KW = 22.5
MINUTES_PER_DAY = 1440


# ---------------------------------------------------------------- reading DATEX II (JSON)

def _enum(node: Any) -> str | None:
    """A DATEX enum: {"value": "pricePerKWh"} (Spirii) or the bare string."""
    if isinstance(node, dict):
        node = node.get("value")
    return node if isinstance(node, str) and node else None


def _multi(node: Any) -> str | None:
    """A multilingual text ({"values": [{"lang", "value"}]}), Danish first; or a bare string."""
    if isinstance(node, str):
        return node.strip() or None
    if isinstance(node, dict):
        values = node.get("values")
        if isinstance(values, list):
            texts = [(item.get("lang"), item.get("value")) for item in values if isinstance(item, dict)]
            for lang, value in sorted(texts, key=lambda item: item[0] != "da"):
                if isinstance(value, str) and value.strip():
                    return value.strip()
        if isinstance(node.get("value"), str):
            return node["value"].strip() or None
    return None


def _num(node: Any) -> float | None:
    if isinstance(node, dict):
        node = node.get("value")
    if isinstance(node, bool):
        return None
    try:
        value = float(node)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _find(node: Any, key: str, depth: int = 0) -> Any:
    """The first value under key anywhere in node (depth-first, bounded)."""
    if depth > 12:
        return None
    if isinstance(node, dict):
        if key in node:
            return node[key]
        for value in node.values():
            if (found := _find(value, key, depth + 1)) is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            if (found := _find(value, key, depth + 1)) is not None:
                return found
    return None


def _coordinates(node: Any, depth: int = 0) -> tuple[float, float] | None:
    """The first object with a latitude and a longitude."""
    if depth > 12:
        return None
    if isinstance(node, dict):
        lat, lon = _num(node.get("latitude")), _num(node.get("longitude"))
        if lat is not None and lon is not None and -90 <= lat <= 90 and -180 <= lon <= 180:
            return lat, lon
        for value in node.values():
            if (found := _coordinates(value, depth + 1)) is not None:
                return found
    elif isinstance(node, list):
        for value in node:
            if (found := _coordinates(value, depth + 1)) is not None:
                return found
    return None


def _minute_of_day(text: Any) -> int | None:
    """"21:45" -> 1305, "24:00" -> 1440."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{1,2}:\d{2}(:\d{2})?", text.strip()):
        return None
    hours, minutes = text.strip().split(":")[:2]
    return min(int(hours) * 60 + int(minutes), MINUTES_PER_DAY)


def _kroner(price: dict, currency: str | None) -> float | None:
    """The price including VAT, in kroner (DATEX writes VAT in percent, Eco-Movement mostly as a fraction)."""
    value = _num(price.get("value"))
    if value is None or value < 0:
        return None
    rate = _num(price.get("taxRate"))
    included = price.get("taxIncluded")
    if included is False and rate and rate > 0:
        value *= 1 + (rate if rate <= 1 else rate / 100)
    if currency and currency.upper() == "EUR":
        value *= EUR_TO_DKK
    elif currency and currency.upper() != "DKK":
        return None
    return round(value, 4)


def tariff_from_prices(prices: Any, currency: str | None) -> dict | None:
    """A DATEX energyPrice list as a compact tariff (None when it has no price per kWh or start fee)."""
    if not isinstance(prices, list):
        return None
    periods: list[list] = []
    fallback = start = None
    minute: list[list] = []
    for price in prices:
        if not isinstance(price, dict) or (value := _kroner(price, currency)) is None:
            continue
        kind = _enum(price.get("priceType"))
        if kind == "pricePerKWh":
            period = price.get("overallPeriod")
            if not isinstance(period, dict):
                fallback = value if fallback is None else fallback
                continue
            first = str(period.get("overallStartTime") or "")[:10]
            last = str(period.get("overallEndTime") or "")[:10] or "9999-12-31"
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", first):
                fallback = value if fallback is None else fallback
                continue
            windows = []
            for valid in period.get("validPeriod") or []:
                for time in (valid.get("recurringTimePeriodOfDay") or []) if isinstance(valid, dict) else []:
                    start_min = _minute_of_day(time.get("startTimeOfPeriod")) if isinstance(time, dict) else None
                    end_min = _minute_of_day(time.get("endTimeOfPeriod")) if isinstance(time, dict) else None
                    if start_min is not None and end_min is not None and end_min > start_min:
                        windows.append((start_min, end_min))
            for start_min, end_min in windows or [(0, MINUTES_PER_DAY)]:
                periods.append([first, last, start_min, end_min, value])
        elif kind in ("flatRate", "basePrice"):
            start = value
        elif kind == "pricePerMinute":
            applicability = price.get("timeBasedApplicability") or {}
            from_minute = _num(applicability.get("fromMinute")) if isinstance(applicability, dict) else None
            minute.append([value, int(from_minute or 0)])
    if not periods and fallback is None and start is None:
        return None
    tariff: dict = {"p": _merge(periods)}
    if fallback is not None:
        tariff["f"] = fallback
    if start is not None:
        tariff["s"] = start
    if minute:
        tariff["m"] = minute
    return tariff


def _merge(periods: list[list]) -> list[list]:
    """Neighbouring windows of the same dates and price as one (Spirii's 192 quarters of an hour mostly become two)."""
    merged: list[list] = []
    for item in sorted(periods, key=lambda p: (p[0], p[1], p[2])):
        last = merged[-1] if merged else None
        if last and last[0] == item[0] and last[1] == item[1] and last[4] == item[4] and last[3] >= item[2]:
            last[3] = max(last[3], item[3])
        else:
            merged.append(list(item))
    return merged


def price_at(tariff: dict | None, when: datetime) -> float | None:
    """The tariff's price per kWh at a local time; an old tariff gives the last known price for that time of day."""
    if not tariff:
        return None
    day, minute = when.date().isoformat(), when.hour * 60 + when.minute
    best = stale = None
    for first, last, start, end, value in tariff.get("p") or []:
        if not start <= minute < end:
            continue
        if first <= day <= last:
            if best is None or first > best[0]:
                best = (first, value)
        elif first <= day and (stale is None or first > stale[0]):
            stale = (first, value)
    if best is not None:
        return best[1]
    if tariff.get("f") is not None:
        return tariff["f"]
    return stale[1] if stale is not None else None


def price_range(tariff: dict | None, when: datetime) -> tuple[float, float] | None:
    """The lowest and highest price per kWh of the tariff on that local day."""
    if not tariff:
        return None
    day = when.date().isoformat()
    values = [value for first, last, _start, _end, value in tariff.get("p") or [] if first <= day <= last]
    if tariff.get("f") is not None:
        values.append(tariff["f"])
    return (min(values), max(values)) if values else None


def _plug(node: Any) -> str | None:
    kind = _enum(node)
    return PLUGS.get(kind.lower(), kind) if kind else None


def _group(points: list[tuple[float | None, set[str], dict | None]]) -> list[dict]:
    """Charge points of the same power and tariff as one group with a count."""
    groups: dict[str, dict] = {}
    for kw, plugs, tariff in points:
        key = json.dumps([kw, tariff], sort_keys=True)
        group = groups.setdefault(key, {"kw": kw, "t": [], "c": 0, "tf": tariff})
        group["c"] += 1
        group["t"] = sorted(set(group["t"]) | plugs)
    return sorted(groups.values(), key=lambda g: -(g["kw"] or 0))


def site_from_json(site: dict, feed: str) -> dict | None:
    """One DATEX II (JSON) site as a compact site, or None without a position or charge points."""
    coords = _coordinates(site.get("locationReference")) or _coordinates(site.get("entrance"))
    if coords is None:
        return None
    operator = (_multi(_find(site.get("operator"), "name")) or _multi(_find(site.get("energyDistributor"), "name")))
    name = _multi(site.get("name")) or _multi(site.get("brand"))
    points = []
    for station in site.get("energyInfrastructureStation") or []:
        if not isinstance(station, dict):
            continue
        operator = operator or _multi(_find(station.get("energyDistributor"), "name"))
        for refill in station.get("refillPoint") or []:
            point = refill.get("aegiElectricChargingPoint", refill) if isinstance(refill, dict) else None
            if not isinstance(point, dict):
                continue
            watts, plugs = [], set()
            for connector in point.get("connector") or []:
                if isinstance(connector, dict):
                    if (power := _num(connector.get("maxPowerAtSocket"))) is not None and power > 0:
                        watts.append(power)
                    if plug := _plug(connector.get("connectorType")):
                        plugs.add(plug)
            tariff = None
            energies = list(point.get("electricEnergy") or [])
            energies += [product.get("aegiElectricEnergy") for product in point.get("energyProduct") or []
                         if isinstance(product, dict)]
            for energy in energies:
                for rate in (energy.get("energyRate") or []) if isinstance(energy, dict) else []:
                    if not isinstance(rate, dict) or _enum(rate.get("ratePolicy")) not in (None, "adHoc"):
                        continue
                    currencies = rate.get("applicableCurrency")
                    currency = (currencies[0] if isinstance(currencies, list) and currencies else currencies)
                    tariff = tariff_from_prices(rate.get("energyPrice"), currency if isinstance(currency, str) else None)
                    if tariff:
                        break
                if tariff:
                    break
            points.append((round(max(watts) / 1000, 1) if watts else None, plugs, tariff))
    if not points:
        return None
    if not name:
        address = _find(site.get("locationReference"), "address")
        street = _multi(_find(_find(address, "addressLine"), "text")) if address else None
        city = _multi(address.get("city")) if isinstance(address, dict) else None
        name = ", ".join(part for part in (street, city) if part) or operator
    site_id = site.get("idG") or site.get("id") or f"{coords[0]:.5f},{coords[1]:.5f}"
    return {"id": f"{feed}:{site_id}", "n": name, "o": operator, "a": round(coords[0], 6), "b": round(coords[1], 6),
            "s": feed, "g": _group(points)}


def sites_from_json(raw: bytes | str, feed: str) -> list[dict]:
    """Every site of a DATEX II (JSON) publication, read one site at a time (the whole tree of a large feed with its
    prices per quarter of an hour would take far more memory than the text)."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
    decoder = json.JSONDecoder()
    sites: list[dict] = []
    for match in re.finditer(r'"energyInfrastructureSite"\s*:\s*\[', text):
        index = match.end()
        while True:
            while index < len(text) and text[index] in " \t\r\n,":
                index += 1
            if index >= len(text) or text[index] != "{":
                break
            try:
                site, index = decoder.raw_decode(text, index)
            except ValueError:
                break
            if isinstance(site, dict) and (compact := site_from_json(site, feed)) is not None:
                sites.append(compact)
    return sites


# ---------------------------------------------------------------- reading DATEX II (XML)

def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _child(element: ET.Element, name: str) -> ET.Element | None:
    return next((child for child in element if _local(child.tag) == name), None)


def _children(element: ET.Element, name: str) -> list[ET.Element]:
    return [child for child in element if _local(child.tag) == name]


def _descendant(element: ET.Element | None, name: str) -> ET.Element | None:
    if element is None:
        return None
    return next((item for item in element.iter() if _local(item.tag) == name), None)


def _xml_text(element: ET.Element | None) -> str | None:
    """A multilingual text (…<values><value>…) or a plain one."""
    if element is None:
        return None
    value = _descendant(element, "value")
    text = (value.text if value is not None else element.text) or ""
    return text.strip() or None


def _xml_tariff(holder: ET.Element) -> dict | None:
    """A station's or point's price: a RateTable's per-unit line (bluebell) or DATEX energyPrice elements."""
    for rates in _children(holder, "rates"):
        currency = _xml_text(_child(rates, "applicableCurrency"))
        for line in rates.iter():
            if _local(line.tag) != "rateLine":
                continue
            kind = _xml_text(_child(line, "rateLineType"))
            value = _num(_xml_text(_child(line, "value")))
            if value is None or currency not in (None, "DKK", "EUR"):
                continue
            value = round(value * (EUR_TO_DKK if currency == "EUR" else 1), 4)
            if kind == "perUnit":
                return {"p": [], "f": value}
    prices = []
    currency = None
    for element in holder.iter():
        name = _local(element.tag)
        if name == "applicableCurrency" and currency is None:
            currency = (element.text or "").strip() or None
        elif name == "energyPrice":
            price: dict = {"priceType": _xml_text(_child(element, "priceType")),
                           "value": _xml_text(_child(element, "value"))}
            included = _xml_text(_child(element, "taxIncluded"))
            if included is not None:
                price["taxIncluded"] = included.lower() == "true"
            price["taxRate"] = _xml_text(_child(element, "taxRate"))
            prices.append(price)
    return tariff_from_prices(prices, currency) if prices else None


def site_from_xml(site: ET.Element, feed: str) -> dict | None:
    lat = _num(_xml_text(_descendant(site, "latitude")))
    lon = _num(_xml_text(_descendant(site, "longitude")))
    if lat is None or lon is None:
        return None
    name = _xml_text(_child(site, "name"))
    operator_element = _child(site, "operator")
    operator = _xml_text(_child(operator_element, "name")) if operator_element is not None else None
    points = []
    for station in _children(site, "energyInfrastructureStation"):
        station_tariff = _xml_tariff(station)
        for refill in _children(station, "refillPoint"):
            watts, plugs = [], set()
            for connector in _children(refill, "connector"):
                if (power := _num(_xml_text(_child(connector, "maxPowerAtSocket")))) is not None and power > 0:
                    watts.append(power)
                if (kind := _xml_text(_child(connector, "connectorType"))) is not None:
                    plugs.add(PLUGS.get(kind.lower(), kind))
            points.append((round(max(watts) / 1000, 1) if watts else None, plugs, _xml_tariff(refill) or station_tariff))
    if not points:
        return None
    return {"id": f"{feed}:{site.get('id') or f'{lat:.5f},{lon:.5f}'}", "n": name or operator, "o": operator,
            "a": round(lat, 6), "b": round(lon, 6), "s": feed, "g": _group(points)}


def sites_from_xml(raw: bytes, feed: str) -> list[dict]:
    """Every site of a DATEX II (XML) publication, one site at a time."""
    sites: list[dict] = []
    try:
        for _event, element in ET.iterparse(io.BytesIO(raw), events=("end",)):
            if _local(element.tag) == "energyInfrastructureSite":
                if (compact := site_from_xml(element, feed)) is not None:
                    sites.append(compact)
                element.clear()
    except ET.ParseError as err:
        _LOGGER.debug("%s: XML not read: %s", feed, err)
    return sites


# ---------------------------------------------------------------- the feeds

class Afir:
    """The stations of every feed, shared by all cars (hass.data), fetched when needed and kept in .storage."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store: Store = Store(hass, 1, f"{DOMAIN}_afir")
        self.feeds: dict[str, dict] = {}  # feed -> {"at": iso, "sites": [...], "error": str | None}
        self._loaded = False
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._retry: dict[str, datetime] = {}
        self._index: dict[str, dict] | None = None

    async def async_load(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        data = await self._store.async_load() or {}
        if isinstance(data.get("feeds"), dict):
            self.feeds = {name: _unpack(feed) for name, feed in data["feeds"].items() if isinstance(feed, dict)}
            self._index = None

    def wanted(self, credentials: dict) -> list[str]:
        """The feeds that can be fetched with these credentials."""
        feeds = ["spirii", *OPEN_XML_FEEDS]
        if credentials.get("monta_client_id") and credentials.get("monta_client_secret"):
            feeds.append("monta")
        if credentials.get("eco_token"):
            feeds.append("eco")
        return feeds

    def stale(self, credentials: dict) -> list[str]:
        now = dt_util.utcnow()
        out = []
        for feed in self.wanted(credentials):
            fetched = dt_util.parse_datetime((self.feeds.get(feed) or {}).get("at") or "")
            if (fetched is None or now - fetched > FEED_MAX_AGE) and now >= self._retry.get(feed, now):
                out.append(feed)
        return out

    def refreshing(self) -> bool:
        return self._task is not None and not self._task.done()

    def ensure_fresh(self, credentials: dict) -> None:
        """Fetch the old feeds in the background (the stations known now are used meanwhile)."""
        if self.refreshing() or not self.stale(credentials):
            return
        self._task = self.hass.async_create_background_task(self.async_refresh(credentials), "evledger_afir_refresh")

    async def async_refresh(self, credentials: dict, feeds: list[str] | None = None) -> None:
        async with self._lock:
            await self.async_load()
            session = async_get_clientsession(self.hass)
            for feed in feeds or self.stale(credentials):
                try:
                    sites = await self._fetch(session, feed, credentials)
                except Exception as err:  # noqa: BLE001 - each feed on its own; the old stations stay
                    self._retry[feed] = dt_util.utcnow() + FEED_RETRY
                    old = self.feeds.get(feed) or {}
                    self.feeds[feed] = {**old, "error": str(err)[:200] or type(err).__name__}
                    _LOGGER.debug("AFIR feed %s not fetched", feed, exc_info=True)
                    continue
                self.feeds[feed] = {"at": dt_util.utcnow().isoformat(), "sites": _intern(sites), "error": None}
                self._index = None
                _LOGGER.debug("AFIR feed %s: %d sites", feed, len(sites))
            await self._store.async_save({"feeds": {name: _pack(data) for name, data in self.feeds.items()}})

    async def _get(self, session: aiohttp.ClientSession, url: str, **kwargs) -> tuple[bytes, Any]:
        headers = {"User-Agent": USER_AGENT, "Accept-Encoding": "gzip", **kwargs.pop("headers", {})}
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=180), **kwargs) as response:
            response.raise_for_status()
            return await response.read(), response.headers

    async def _fetch(self, session: aiohttp.ClientSession, feed: str, credentials: dict) -> list[dict]:
        executor = self.hass.async_add_executor_job
        if feed == "spirii":
            sites: list[dict] = []
            for cpo in SPIRII_CPOS:
                raw, _headers = await self._get(session, SPIRII_URL, params={"countryIsos": "DK", "cpoId": cpo},
                                                headers={"Accept": "application/json"})
                sites += await executor(sites_from_json, raw, feed)
                await _pause()
            if not sites:
                raise ValueError("no sites")
            return sites
        if feed in OPEN_XML_FEEDS:
            raw, _headers = await self._get(session, OPEN_XML_FEEDS[feed])
            return await executor(sites_from_xml, raw, feed)
        if feed == "monta":
            async with session.post(f"{MONTA_API}/auth/token", timeout=aiohttp.ClientTimeout(total=30),
                                    json={"clientId": credentials["monta_client_id"],
                                          "clientSecret": credentials["monta_client_secret"]},
                                    headers={"User-Agent": USER_AGENT}) as response:
                if response.status in (400, 401, 403):
                    raise PermissionError("Monta did not accept the client id and secret")
                response.raise_for_status()
                token = (await response.json(content_type=None)).get("accessToken")
            if not token:
                raise PermissionError("Monta gave no access token")
            by_id: dict[str, dict] = {}
            for page in range(1, MONTA_MAX_PAGES + 1):
                raw, _headers = await self._get(session, f"{MONTA_API}/afir/charge-points",
                                                params={"country": "DK", "page": page, "perPage": MONTA_PER_PAGE},
                                                headers={"Authorization": f"Bearer {token}",
                                                         "Accept": "application/json"})
                found = await executor(sites_from_json, raw, feed)
                for site in found:
                    known = by_id.get(site["id"])
                    if known is None:
                        by_id[site["id"]] = site
                    else:  # a site split over two pages
                        known["g"] = _regroup(known["g"] + site["g"])
                if not found:
                    break
                await _pause()
            return list(by_id.values())
        if feed == "eco":
            sites = []
            url: str | None = ECO_URL
            for _page in range(ECO_MAX_PAGES):
                if url is None:
                    break
                raw, headers = await self._get(session, url, headers={"Authorization": f"Bearer {credentials['eco_token']}",
                                                                      "Accept": "application/json"})
                sites += await executor(sites_from_json, raw, feed)
                url = _next_link(headers.get("Link"), ECO_URL)
            return sites
        raise ValueError(f"unknown feed {feed}")

    # ------------------------------------------------------------ lookups

    def _sites(self, credentials: dict | None = None):
        wanted = set(self.wanted(credentials or {})) if credentials is not None else None
        for feed, data in self.feeds.items():
            if wanted is not None and feed not in wanted:
                continue
            yield from data.get("sites") or []

    def site(self, site_id: str | None) -> dict | None:
        if not site_id:
            return None
        if self._index is None:
            self._index = {site["id"]: site for site in self._sites()}
        return self._index.get(site_id)

    def around(self, lat: float, lon: float, radius_m: float, credentials: dict | None = None) -> list[tuple[float, dict]]:
        """(distance in metres, site) within radius_m, nearest first."""
        from .public_place import distance_m

        box_lat, box_lon = radius_m / 111_000 + 0.0005, radius_m / 55_000 + 0.001
        found = []
        for site in self._sites(credentials):
            if abs(site["a"] - lat) > box_lat or abs(site["b"] - lon) > box_lon:
                continue
            if (meters := distance_m(lat, lon, site["a"], site["b"])) <= radius_m:
                found.append((meters, site))
        return sorted(found, key=lambda item: item[0])

    def status(self, credentials: dict) -> dict:
        return {"feeds": {feed: {"name": FEED_NAMES.get(feed, feed), "sites": len((self.feeds.get(feed) or {}).get("sites") or []),
                                 "fetched": (self.feeds.get(feed) or {}).get("at"),
                                 "error": (self.feeds.get(feed) or {}).get("error")}
                          for feed in self.wanted(credentials)},
                "loading": self.refreshing()}


def _intern(sites: list[dict]) -> list[dict]:
    """The same tariff as one object (an operator's spot-price tariff is the same at hundreds of sites: Spirii's 654
    tariffs were 105 different ones)."""
    seen: dict[str, dict] = {}
    for site in sites:
        for group in site.get("g") or []:
            if group.get("tf"):
                group["tf"] = seen.setdefault(json.dumps(group["tf"], sort_keys=True), group["tf"])
    return sites


def _pack(feed: dict) -> dict:
    """A feed for .storage: each tariff once in "tariffs", the groups pointing to it with "k"."""
    tariffs: list[dict] = []
    keys: dict[int, int] = {}
    sites = []
    for site in feed.get("sites") or []:
        groups = []
        for group in site.get("g") or []:
            tariff = group.get("tf")
            packed = {key: value for key, value in group.items() if key != "tf"}
            if tariff:
                if id(tariff) not in keys:
                    keys[id(tariff)] = len(tariffs)
                    tariffs.append(tariff)
                packed["k"] = keys[id(tariff)]
            groups.append(packed)
        sites.append({**site, "g": groups})
    return {**feed, "sites": sites, "tariffs": tariffs}


def _unpack(feed: dict) -> dict:
    tariffs = feed.get("tariffs") or []
    sites = []
    for site in feed.get("sites") or []:
        if not isinstance(site, dict):
            continue
        groups = []
        for group in site.get("g") or []:
            unpacked = {key: value for key, value in group.items() if key != "k"}
            index = group.get("k")
            unpacked["tf"] = tariffs[index] if isinstance(index, int) and 0 <= index < len(tariffs) else group.get("tf")
            groups.append(unpacked)
        sites.append({**site, "g": groups})
    return {key: value for key, value in feed.items() if key != "tariffs"} | {"sites": _intern(sites)}


async def _pause() -> None:
    if FEED_PAUSE:
        await asyncio.sleep(FEED_PAUSE)


def _regroup(groups: list[dict]) -> list[dict]:
    merged: dict[str, dict] = {}
    for group in groups:
        key = json.dumps([group.get("kw"), group.get("tf")], sort_keys=True)
        if key in merged:
            merged[key]["c"] += group.get("c", 1)
            merged[key]["t"] = sorted(set(merged[key]["t"]) | set(group.get("t") or []))
        else:
            merged[key] = dict(group)
    return sorted(merged.values(), key=lambda g: -(g.get("kw") or 0))


def _next_link(header: str | None, base: str) -> str | None:
    """The next page from a Link header, only on the same host (the token is never sent anywhere else)."""
    if not header:
        return None
    for part in header.split(","):
        match = re.search(r"<([^>]+)>\s*;\s*rel=\"?next\"?", part)
        if match:
            url = match.group(1)
            host = re.match(r"https://[^/]+", base)
            if host and url.startswith(host.group(0) + "/"):
                return url
    return None


def afir(hass: HomeAssistant) -> Afir:
    key = f"{DOMAIN}_afir"
    if not isinstance(hass.data.get(key), Afir):
        hass.data[key] = Afir(hass)
    return hass.data[key]


# ---------------------------------------------------------------- a site's price

def site_station(site: dict, meters: float | None = None) -> dict:
    """A site as a station for the map and the candidates (the ledger's station shape)."""
    powers = [group["kw"] for group in site.get("g") or [] if group.get("kw")]
    plugs: dict[tuple, int] = {}
    for group in site.get("g") or []:
        for plug in group.get("t") or ["Stik"]:
            plugs[(plug, group.get("kw"))] = plugs.get((plug, group.get("kw")), 0) + group.get("c", 1)
    station = {"name": site.get("n") or site.get("o"), "operator": site.get("o"), "latitude": site["a"],
               "longitude": site["b"], "max_kw": max(powers) if powers else None,
               "points": sum(group.get("c", 1) for group in site.get("g") or []) or None,
               "connectors": [{"type": plug, "kw": kw, "count": count} for (plug, kw), count in plugs.items()],
               "kind": "afir", "id": f"afir:{site['id']}", "feed": FEED_NAMES.get(site.get("s"), site.get("s"))}
    if meters is not None:
        station["distance_m"] = round(meters)
    return station


def site_prices(site: dict, when: datetime) -> dict:
    """A site's price per kWh now on AC and on DC (the cheapest point of each), its start fee and minute fees."""
    ac = dc = None
    start = None
    minute: list[list] = []
    span: tuple[float, float] | None = None
    for group in site.get("g") or []:
        tariff = group.get("tf")
        price = price_at(tariff, when)
        if price is None:
            continue
        if (group.get("kw") or 0) > AC_MAX_KW:
            dc = price if dc is None else min(dc, price)
        else:
            ac = price if ac is None else min(ac, price)
        if tariff.get("s") is not None:
            start = tariff["s"] if start is None else min(start, tariff["s"])
        for fee in tariff.get("m") or []:
            if fee not in minute:
                minute.append(fee)
        if (day := price_range(tariff, when)) is not None:
            span = day if span is None else (min(span[0], day[0]), max(span[1], day[1]))
    return {"ac": ac, "dc": dc, "start_fee": start, "minute_fees": minute,
            "day_range": list(span) if span and span[0] != span[1] else None}


def charge_price(site: dict, started: datetime, avg_kw: float | None, minutes: float | None) -> tuple[float | None, float]:
    """The price per kWh of a charge at the site (DC when the car charged faster than AC can, else AC; the price at the
    start), and its fixed costs (start fee, and minute fees from the first minute)."""
    prices = site_prices(site, started)
    dc_used = avg_kw is not None and avg_kw > AC_MAX_KW
    per_kwh = (prices["dc"] if dc_used else prices["ac"]) if avg_kw is not None else None
    if per_kwh is None:
        per_kwh = prices["dc"] if prices["dc"] is not None else prices["ac"]
    extra = prices["start_fee"] or 0.0
    for per_minute, from_minute in prices["minute_fees"]:
        if from_minute == 0 and minutes:
            extra += per_minute * minutes
    return per_kwh, round(extra, 2)
