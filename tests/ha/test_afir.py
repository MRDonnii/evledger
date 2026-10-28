"""The operators' own stations and prices from their AFIR data (Claude AI, 2026-10-27; user: "Jeg mener det er ewii og
Norlys der er her. Er der ikke andre måder vi kan hente laderne og priser")."""

import json
from datetime import timedelta

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.evledger import afir as afir_module
from custom_components.evledger.afir import (
    MONTA_API,
    _next_link,
    afir,
    charge_price,
    price_at,
    site_prices,
    sites_from_json,
    sites_from_xml,
)

from .test_public_default_price import charge_away, poll, public_charges, set_default_price
from .test_smart_charge import DOMAIN, setup

LAT, LON = 55.6761, 12.5683  # an example site (Copenhagen City Hall Square)


def today() -> str:
    return dt_util.now().date().isoformat()


def tomorrow() -> str:
    return (dt_util.now().date() + timedelta(days=1)).isoformat()


def price(value, kind="pricePerKWh", start=None, end=None, **extra):
    item = {"priceType": {"value": kind}, "value": value, "taxIncluded": True, "taxRate": 25, **extra}
    if start is not None:
        times = [{"startTimeOfPeriod": start, "endTimeOfPeriod": end}]
        item["overallPeriod"] = {"overallStartTime": f"{today()}T00:00:00Z",
                                 "overallEndTime": f"{tomorrow()}T23:59:59Z",
                                 "validPeriod": [{"recurringTimePeriodOfDay": times}]}
    return item


def spirii_site(site_id="site-9", operator="Example Energi", lat=LAT, lon=LON):
    """A site the way Spirii writes it: a 150 kW DC point priced per quarter of an hour, two 11 kW AC points."""
    quarters = [price(3.0 if minute < 720 else 4.0, start=f"{minute // 60:02d}:{minute % 60:02d}",
                      end=f"{(minute + 15) // 60:02d}:{(minute + 15) % 60:02d}") for minute in range(0, 1440, 15)]

    def point(plug, watts, prices):
        rate = {"ratePolicy": {"value": "adHoc"}, "applicableCurrency": ["DKK"], "energyPrice": prices}
        connector = {"connectorType": {"value": plug}, "maxPowerAtSocket": watts}
        return {"aegiElectricChargingPoint": {"connector": [connector], "electricEnergy": [{"energyRate": [rate]}]}}

    ac = point("iec62196T2", 11000, [price(2.5), price(5, "flatRate")])
    dc = point("iec62196T2COMBO", 150000, quarters)
    return {"idG": site_id, "locationReference": {"locAreaLocation": {"coordinatesForDisplay": {"latitude": lat,
                                                                                                "longitude": lon}}},
            "operator": {"afacAnOrganisation": {"name": {"values": [{"lang": "en", "value": operator}]}}},
            "name": {"values": [{"lang": "da", "value": "Example Center"}]},
            "energyInfrastructureStation": [{"refillPoint": [dc, ac, json.loads(json.dumps(ac))]}]}


def publication(*sites):
    return {"payload": {"aegiEnergyInfrastructureTablePublication": {"energyInfrastructureTable": [
        {"energyInfrastructureSite": list(sites)}]}}}


def test_a_datex_json_site_is_read_compactly():
    [site] = sites_from_json(json.dumps(publication(spirii_site())), "spirii")
    assert (site["n"], site["o"], site["a"], site["b"]) == ("Example Center", "Example Energi", LAT, LON)
    dc, ac = site["g"]
    assert (dc["kw"], dc["t"], dc["c"], ac["kw"], ac["t"], ac["c"]) == (150.0, ["CCS"], 1, 11.0, ["Type 2"], 2)
    assert len(dc["tf"]["p"]) == 2, "96 quarters of the same dates become the morning and the afternoon"
    noon = dt_util.now().replace(hour=13, minute=5)
    assert price_at(dc["tf"], noon) == 4.0 and price_at(dc["tf"], noon.replace(hour=8)) == 3.0
    prices = site_prices(site, noon)
    assert (prices["ac"], prices["dc"], prices["start_fee"], prices["day_range"]) == (2.5, 4.0, 5, [2.5, 4.0])
    # A charge at 60 kW used DC (with the AC points' start fee as the only one there), at 9 kW AC
    assert charge_price(site, noon, 60, 30) == (4.0, 5.0)
    assert charge_price(site, noon.replace(hour=8), 9, 120) == (2.5, 5.0)


def test_vat_currency_and_old_tariffs():
    rate = {"applicableCurrency": ["EUR"],
            "energyPrice": [{"priceType": "pricePerKWh", "value": 0.5, "taxIncluded": False, "taxRate": 0.25}]}
    point = {"connector": [{"connectorType": "iec62196T2COMBO", "maxPowerAtSocket": 300000}],
             "electricEnergy": [{"energyRate": [rate]}]}
    eco = {"idG": "e1",
           "entrance": [{"locAreaLocation": {"coordinatesForDisplay": {"latitude": 55.0, "longitude": 12.0}}}],
           "energyDistributor": {"afacAnOrganisation": {"name": {"values": [{"value": "Example Charge"}]}}},
           "energyInfrastructureStation": [{"refillPoint": [point]}]}
    [site] = sites_from_json(json.dumps(publication(eco)), "eco")
    assert site["o"] == "Example Charge"
    assert site["g"][0]["tf"]["f"] == pytest.approx(round(0.5 * 1.25 * 7.46038, 4)), "VAT as a fraction, euro"
    # A tariff fetched days ago still gives that time of day's last price
    [old] = sites_from_json(json.dumps(publication(spirii_site())), "spirii")
    later = dt_util.now().replace(hour=13) + timedelta(days=4)
    assert price_at(old["g"][0]["tf"], later) == 4.0


XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<d2:payload xmlns:d2="http://datex2.eu/schema/3/d2Payload" xmlns:com="http://datex2.eu/schema/3/common"
 xmlns:egi="http://datex2.eu/schema/3/energyInfrastructure" xmlns:fac="http://datex2.eu/schema/3/facilities"
 xmlns:loc="http://datex2.eu/schema/3/locationReferencing">
 <egi:energyInfrastructureTable id="1"><egi:energyInfrastructureSite id="S1">
  <fac:name><com:values><com:value lang="da">Example Plads</com:value></com:values></fac:name>
  <fac:locationReference><loc:pointByCoordinates><loc:pointCoordinates><loc:latitude>55.5</loc:latitude>
   <loc:longitude>8.4</loc:longitude></loc:pointCoordinates></loc:pointByCoordinates></fac:locationReference>
  <fac:operator><fac:name><com:values><com:value lang="da">Example Operator</com:value></com:values></fac:name>
  </fac:operator>
  <egi:energyInfrastructureStation id="ST1"><fac:rates><fac:applicableCurrency>DKK</fac:applicableCurrency>
   <fac:rateLineCollection><fac:rateLine><fac:rateLineType>perUnit</fac:rateLineType><fac:value>3.95</fac:value>
   </fac:rateLine></fac:rateLineCollection></fac:rates>
   <egi:refillPoint id="P1"><egi:connector><egi:connectorType>iec62196T2</egi:connectorType>
    <egi:maxPowerAtSocket>22000</egi:maxPowerAtSocket></egi:connector></egi:refillPoint>
  </egi:energyInfrastructureStation></egi:energyInfrastructureSite>
  <egi:energyInfrastructureSite id="S2"><fac:name><com:values><com:value>No Price</com:value></com:values></fac:name>
  <fac:locationReference><loc:pointByCoordinates><loc:pointCoordinates><loc:latitude>55.6</loc:latitude>
   <loc:longitude>8.5</loc:longitude></loc:pointCoordinates></loc:pointByCoordinates></fac:locationReference>
  <egi:energyInfrastructureStation id="ST2"><egi:refillPoint id="P2"><egi:connector>
   <egi:connectorType>iec62196T2COMBO</egi:connectorType><egi:maxPowerAtSocket>75200</egi:maxPowerAtSocket>
  </egi:connector></egi:refillPoint></egi:energyInfrastructureStation></egi:energyInfrastructureSite>
 </egi:energyInfrastructureTable></d2:payload>"""


def test_a_datex_xml_feed_is_read():
    priced, unpriced = sites_from_xml(XML, "totalenergies")
    assert (priced["n"], priced["o"]) == ("Example Plads", "Example Operator")
    assert (priced["g"][0]["kw"], priced["g"][0]["t"]) == (22.0, ["Type 2"])
    assert site_prices(priced, dt_util.now())["ac"] == 3.95
    assert unpriced["g"] == [{"kw": 75.2, "t": ["CCS"], "c": 1, "tf": None}]
    assert sites_from_xml(b"<not xml", "ok") == []


def test_a_zero_price_is_no_price():
    zero = {"priceType": "pricePerKWh", "value": 0, "taxIncluded": True}
    rate = {"applicableCurrency": ["DKK"], "energyPrice": [zero]}
    point = {"connector": [{"connectorType": "iec62196T2", "maxPowerAtSocket": 11000}],
             "electricEnergy": [{"energyRate": [rate]}]}
    site = {"idG": "z", "locationReference": {"coordinatesForDisplay": {"latitude": 55.0, "longitude": 12.0}},
            "energyInfrastructureStation": [{"refillPoint": [point]}]}
    [found] = sites_from_json(json.dumps(publication(site)), "spirii")
    assert found["g"][0]["tf"] is None, "an empty price field is not free charging"


async def test_an_operator_name_matches_as_whole_words(hass: HomeAssistant, request):
    entry, _ = await setup(hass, request)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await hass.services.async_call(DOMAIN, "set_operator_price", {"entry_id": entry.entry_id, "operator": "OK",
                                                                  "price": 3.19}, blocking=True)
    assert coordinator.operator_price("OK a.m.b.a.") == 3.19
    assert coordinator.operator_price("Stokke El") is None


def test_the_next_page_stays_on_the_same_host():
    base = "https://nap.example.com/datex2/v1/locations"
    assert _next_link('<https://nap.example.com/datex2/v1/locations?page=2>; rel="next"', base) \
        == "https://nap.example.com/datex2/v1/locations?page=2"
    assert _next_link('<https://evil.example.org/steal>; rel="next"', base) is None
    assert _next_link(None, base) is None


async def test_monta_is_fetched_with_the_users_application(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(f"{MONTA_API}/auth/token", json={"accessToken": "token-1"})
    pages = f"{MONTA_API}/afir/charge-points"
    aioclient_mock.get(pages, params={"country": "DK", "page": 1, "perPage": 1000},
                       text=json.dumps(publication(spirii_site("m1"))))
    aioclient_mock.get(pages, params={"country": "DK", "page": 2, "perPage": 1000}, text=json.dumps(publication()))
    data = afir(hass)
    credentials = {"monta_client_id": "id", "monta_client_secret": "secret"}
    assert "monta" in data.wanted(credentials) and "monta" not in data.wanted({})
    await data.async_refresh(credentials, ["monta"])
    assert [site["id"] for site in data.feeds["monta"]["sites"]] == ["monta:m1"]
    token_call = next(call for call in aioclient_mock.mock_calls if call[0] == "POST")
    assert token_call[2] == {"clientId": "id", "clientSecret": "secret"}
    assert all(call[3]["Authorization"] == "Bearer token-1" for call in aioclient_mock.mock_calls if call[0] == "GET")
    assert data.status(credentials)["feeds"]["monta"]["sites"] == 1
    assert data.around(LAT, LON, 50, {}) == [], "without the application its stations are not used"
    assert len(data.around(LAT, LON, 50, credentials)) == 1


async def test_a_refused_application_keeps_the_stations_known(hass: HomeAssistant, aioclient_mock):
    aioclient_mock.post(f"{MONTA_API}/auth/token", status=401)
    data = afir(hass)
    data.feeds["monta"] = {"at": "2026-01-01T00:00:00+00:00", "sites": [{"id": "monta:old"}], "error": None}
    await data.async_refresh({"monta_client_id": "id", "monta_client_secret": "wrong"}, ["monta"])
    assert data.feeds["monta"]["sites"] == [{"id": "monta:old"}]
    assert "accept" in data.feeds["monta"]["error"]


def with_sites(hass: HomeAssistant, *sites, feed="spirii") -> None:
    data = afir(hass)
    data._loaded = True
    data.feeds[feed] = {"at": dt_util.utcnow().isoformat(), "sites": list(sites), "error": None}
    data._index = None


async def test_a_charge_at_an_operators_station_gets_its_own_price(hass: HomeAssistant, request, freezer):
    [site] = sites_from_json(json.dumps(publication(spirii_site())), "spirii")
    entry, _ = await setup(hass, request)
    with_sites(hass, site)
    await set_default_price(hass, entry, 2.6)
    await hass.services.async_call(DOMAIN, "set_operator_price", {"entry_id": entry.entry_id,
                                                                  "operator": "Example Energi", "price": 9.0},
                                   blocking=True)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": LAT + 0.0001, "longitude": LON})
    await charge_away(hass, entry, "40", "70", freezer=freezer)  # 18.9 kWh in 25 min: about 45 kW, DC
    [charge] = public_charges(hass, entry)
    assert (charge.station_id, charge.operator, charge.location_name) == ("afir:spirii:site-9", "Example Energi",
                                                                        "Example Center")
    assert charge.price_source == "station"
    assert charge.price == pytest.approx(round(charge.kwh * 4.0 + 5, 2)), "DC at noon + the start fee"


async def test_a_named_place_still_beats_the_operators_price(hass: HomeAssistant, request, freezer):
    [site] = sites_from_json(json.dumps(publication(spirii_site())), "spirii")
    entry, _ = await setup(hass, request)
    with_sites(hass, site)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_remember_place("Mit sted", LAT, LON, 1.5)
    hass.states.async_set("device_tracker.car", "not_home", {"latitude": LAT, "longitude": LON})
    await charge_away(hass, entry, freezer=freezer)
    [charge] = public_charges(hass, entry)
    assert (charge.price_source, charge.price) == ("place", pytest.approx(round(charge.kwh * 1.5, 2)))


async def test_the_map_gets_the_operators_prices(hass: HomeAssistant, request, aioclient_mock, hass_ws_client):
    from custom_components.evledger.public_place import OCM_URL

    # Open Charge Map has the same site (copied, without its operator): the operator's own wins
    aioclient_mock.get(OCM_URL, json=[{"ID": 7, "AddressInfo": {"Title": "Example Center", "Latitude": LAT + 0.0002,
                                                               "Longitude": LON}, "OperatorInfo": None}])
    [site] = sites_from_json(json.dumps(publication(spirii_site())), "spirii")
    entry, _ = await setup(hass, request)
    with_sites(hass, site)
    hass.data[DOMAIN][entry.entry_id].ocm_api_key = "k"
    await poll(hass, entry)
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "evledger/chargers", "entity_id": "sensor.bil_last_charge",
                            "latitude": LAT, "longitude": LON, "radius_km": 2})
    reply = (await client.receive_json())["result"]
    [station] = reply["stations"]
    assert (station["kind"], station["name"], station["price_source"]) == ("afir", "Example Center", "station")
    assert (station["price"], station["price_normal"], station["price_fast"]) == (4.0, 2.5, 4.0)
    assert station["start_fee"] == 5
    assert station["feed"] == "Spirii" and station["points"] == 3
    assert reply["afir"]["feeds"]["spirii"]["sites"] == 1 and reply["afir"]["loading"] is False


async def test_the_feeds_are_kept_over_a_restart(hass: HomeAssistant, hass_storage):
    sites = sites_from_json(json.dumps(publication(spirii_site(), spirii_site("site-10", lat=LAT + 1))), "spirii")
    feed = {"at": dt_util.utcnow().isoformat(), "sites": afir_module._intern(sites), "error": None}
    packed = afir_module._pack(feed)
    assert len(packed["tariffs"]) == 2, "each tariff once, though two sites have them"
    hass_storage["evledger_afir"] = {"version": 1, "data": {"feeds": {"spirii": packed}}}
    data = afir_module.Afir(hass)
    await data.async_load()
    site = data.site("spirii:site-9")
    assert site["n"] == "Example Center" and site["g"] == sites[0]["g"]
    assert site["g"][0]["tf"] is data.site("spirii:site-10")["g"][0]["tf"], "shared again after loading"
    assert data.stale({}) == [feed for feed in data.wanted({}) if feed != "spirii"]
