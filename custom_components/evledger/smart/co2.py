"""The CO2 forecast of the Danish power grid (Energinet's open data at Energi Data Service): grams per kWh in five
minute steps for today and tomorrow, by price area (DK1 west, DK2 east)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta

from .plan import floor_quarter

_LOGGER = logging.getLogger(__name__)

URL = "https://api.energidataservice.dk/dataset/CO2EmisProg"
REFRESH = timedelta(minutes=30)


def price_area(latitude: float, longitude: float) -> str | None:
    """DK1 (Jutland, Funen) or DK2 (Zealand, Lolland-Falster, Bornholm); None outside Denmark."""
    if not (54.4 <= latitude <= 57.9 and 7.9 <= longitude <= 15.3):
        return None
    return "DK1" if longitude < 11.0 else "DK2"


async def async_fetch(session, area: str, now: datetime) -> dict[datetime, float]:
    """The quarters' mean CO2 per kWh, keyed by the quarter's start (UTC)."""
    start = (now.astimezone(UTC) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    end = (now.astimezone(UTC) + timedelta(days=2)).strftime("%Y-%m-%dT%H:%M")
    params = {"start": start, "end": end, "timezone": "utc", "filter": json.dumps({"PriceArea": [area]}),
              "columns": "Minutes5UTC,CO2Emission", "sort": "Minutes5UTC ASC", "limit": 2000}
    async with session.get(URL, params=params, timeout=20) as response:
        response.raise_for_status()
        data = await response.json()
    sums: dict[datetime, list[float]] = {}
    for record in data.get("records") or []:
        try:
            moment = datetime.fromisoformat(str(record["Minutes5UTC"])).replace(tzinfo=UTC)
            value = float(record["CO2Emission"])
        except (KeyError, TypeError, ValueError):
            continue
        sums.setdefault(floor_quarter(moment), []).append(value)
    return {quarter: sum(values) / len(values) for quarter, values in sums.items()}
