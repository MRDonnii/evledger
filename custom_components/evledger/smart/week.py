"""The week plan (a ready-by time and, if wanted, its own target for each weekday; a weekday may also be free) and
the one-time ready-by time (used once, then the normal ready-by times take over again)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

DAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MIN_TARGET = 50.0
MAX_TARGET = 100.0
# How far ahead a one-time ready-by time may be set.
ONCE_MAX_AHEAD = timedelta(days=14)

Day = dict  # {"time": time, "target": float | None}; None for a free day


def default_week(ready_by: time, weekend: time | None = None) -> dict[int, Day | None]:
    """The week plan to start from: the ready-by time on every day (the weekend time on Saturday and Sunday)."""
    return {day: {"time": weekend if weekend is not None and day >= 5 else ready_by, "target": None}
            for day in range(7)}


def parse_time(value) -> time:
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    text = str(value).strip()
    return time.fromisoformat(text if len(text) > 5 else text.zfill(5)).replace(second=0, microsecond=0)


def parse_target(value) -> float | None:
    if value in (None, "", "default"):
        return None
    target = float(value)
    if not MIN_TARGET <= target <= MAX_TARGET:
        raise ValueError(f"target {target} outside {MIN_TARGET:.0f}–{MAX_TARGET:.0f} %")
    return round(target)


def parse_week(data) -> dict[int, Day | None]:
    """A week plan from attributes or a service call: {"mon": {"time": "07:00", "target": 90} or null, ...}. Days left
    out are free. Raises ValueError for a bad time or target."""
    if not isinstance(data, dict):
        raise ValueError("the week plan must be a mapping of weekdays")
    unknown = set(data) - set(DAY_KEYS)
    if unknown:
        raise ValueError(f"unknown weekdays: {', '.join(sorted(unknown))}")
    week: dict[int, Day | None] = {}
    for index, key in enumerate(DAY_KEYS):
        day = data.get(key)
        if day in (None, False, "off"):
            week[index] = None
            continue
        if isinstance(day, str):
            day = {"time": day}
        if not isinstance(day, dict) or not day.get("time"):
            raise ValueError(f"{key} needs a time")
        week[index] = {"time": parse_time(day["time"]), "target": parse_target(day.get("target"))}
    return week


def as_attribute(week: dict[int, Day | None]) -> dict[str, dict | None]:
    return {key: ({"time": day["time"].strftime("%H:%M"), "target": day["target"]} if day else None)
            for key, day in ((DAY_KEYS[index], week.get(index)) for index in range(7))}


def has_days(week: dict[int, Day | None]) -> bool:
    return any(week.get(day) for day in range(7))


def deadlines(now: datetime, week: dict[int, Day | None], count: int) -> list[tuple[datetime, float | None]]:
    """The next ready-by times of the week plan after now, each with the day's own target (None: the normal one)."""
    result: list[tuple[datetime, float | None]] = []
    for offset in range(7 * count + 8):
        day = now.date() + timedelta(days=offset)
        entry = week.get(day.weekday())
        if not entry:
            continue
        moment = datetime.combine(day, entry["time"], tzinfo=now.tzinfo)
        if moment > now:
            result.append((moment, entry["target"]))
            if len(result) >= count:
                break
    return result


def earliest(week: dict[int, Day | None]) -> time | None:
    """The earliest ready-by time of the week: the minimum level is kept by then every day, also on free days."""
    times = [day["time"] for day in week.values() if day]
    return min(times) if times else None
