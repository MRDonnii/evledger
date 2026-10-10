"""Learned departure times: when the car usually leaves home on each weekday, from the ledger's trips. A weekday
without regular departures has no ready-by time; the plan then aims at the next day the car usually leaves."""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

from homeassistant.util import dt as dt_util

LEARN_WEEKS = 8
# Too short a history learns nothing (every weekday would look free).
MIN_HISTORY_DAYS = 21
# A weekday needs this many departures in LEARN_WEEKS to count as a day the car leaves.
MIN_DEPARTURES = 3
# Only the first departure of the morning counts.
EARLIEST = time(4, 0)
LATEST = time(13, 0)
# Ready a little before an early one of the usual departures.
MARGIN = timedelta(minutes=15)
HOME_RADIUS_KM = 0.5
DAY_NAMES = ("man", "tir", "ons", "tor", "fre", "lør", "søn")


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def learn(trips, home: tuple[float, float], now: datetime) -> dict:
    """{"history_days": n, "times": {weekday: time or None} or None while the history is too short}."""
    since = now - timedelta(weeks=LEARN_WEEKS)
    firsts: dict[date, time] = {}
    oldest: datetime | None = None
    for trip in trips:
        started = dt_util.parse_datetime(trip.started_at or "")
        if started is None:
            continue
        local = dt_util.as_local(started)
        oldest = local if oldest is None or local < oldest else oldest
        if local < since or trip.start_lat is None or trip.start_lon is None:
            continue
        if _km((trip.start_lat, trip.start_lon), home) > HOME_RADIUS_KM or not EARLIEST <= local.time() <= LATEST:
            continue
        day = local.date()
        if day not in firsts or local.time() < firsts[day]:
            firsts[day] = local.time()
    history = (now - oldest).days if oldest else 0
    if history < MIN_HISTORY_DAYS:
        return {"history_days": history, "times": None}
    by_weekday: dict[int, list[time]] = {weekday: [] for weekday in range(7)}
    for day, moment in firsts.items():
        by_weekday[day.weekday()].append(moment)
    times: dict[int, time | None] = {}
    for weekday, moments in by_weekday.items():
        if len(moments) < MIN_DEPARTURES:
            times[weekday] = None
            continue
        moments.sort()
        early = datetime.combine(date(2000, 1, 3), moments[int(0.25 * (len(moments) - 1))]) - MARGIN
        times[weekday] = early.replace(minute=early.minute - early.minute % 5, second=0, microsecond=0).time()
    return {"history_days": history, "times": times}


def deadlines(now: datetime, times: dict[int, time | None], count: int, days: int = 8) -> list[datetime]:
    """The next departures from the learned weekdays, at least half an hour ahead."""
    result = []
    for offset in range(days + 1):
        day = (now + timedelta(days=offset)).date()
        moment = times.get(day.weekday())
        if moment is None:
            continue
        deadline = datetime.combine(day, moment, tzinfo=now.tzinfo)
        if deadline > now + timedelta(minutes=30):
            result.append(deadline)
            if len(result) >= count:
                break
    return result


def as_text(times: dict[int, time | None] | None) -> dict[str, str | None] | None:
    if times is None:
        return None
    return {DAY_NAMES[weekday]: (moment.strftime("%H:%M") if moment else None) for weekday, moment in times.items()}
