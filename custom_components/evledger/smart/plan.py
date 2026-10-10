"""Pure charge plan calculation. No Home Assistant imports, so it can be unit tested directly."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from itertools import groupby

SLOT = timedelta(minutes=15)
SLOT_MINUTES = 15
HOUR_MINUTES = 60
# Windows whose total price differs by less than this are treated as equal; the later one wins,
# so the car is charged as close to the deadline as the price allows.
TIE_EPSILON = 0.08

PRICE_LIST_KEYS = ("prices", "raw_today", "raw_tomorrow", "today_prices", "tomorrow_prices",
                   "forecast", "data")
START_KEYS = ("start", "start_time", "startsAt", "from", "hour", "time")
END_KEYS = ("end", "end_time", "endsAt", "to")
PRICE_KEYS = ("price", "value", "total", "price_inc_vat", "electricity_price")


@dataclass(frozen=True)
class PriceSlot:
    start: datetime
    end: datetime
    price: float


@dataclass(frozen=True)
class PlanInput:
    soc: float | None
    target_soc: float
    capacity_kwh: float
    efficiency: float
    power_kw: float
    price_factor: float
    deadline: datetime | None
    slots: list[PriceSlot]


@dataclass(frozen=True)
class PlanResult:
    missing_battery_kwh: float | None
    missing_wall_kwh: float | None
    minutes_needed: float | None
    start: datetime | None
    end: datetime | None
    price: float | None


def _to_datetime(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _first(item: dict, keys: tuple[str, ...]):
    for key in keys:
        if key in item and item[key] is not None:
            return item[key]
    return None


def parse_price_attributes(attributes: dict) -> list[PriceSlot]:
    """Read price intervals from a price entity's attributes (Strømligning, Nord Pool,
    Energi Data Service and similar) and split them into 15-minute slots."""
    raw: list[tuple[datetime, datetime | None, float]] = []
    for key in PRICE_LIST_KEYS:
        items = attributes.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            start = _to_datetime(_first(item, START_KEYS))
            price = _first(item, PRICE_KEYS)
            try:
                price = float(price)
            except (TypeError, ValueError):
                continue
            if start is None or start.tzinfo is None or math.isnan(price):
                continue
            end = _to_datetime(_first(item, END_KEYS))
            raw.append((start, end if end and end.tzinfo else None, price))
    raw.sort(key=lambda entry: entry[0])
    slots: dict[datetime, PriceSlot] = {}
    for index, (start, end, price) in enumerate(raw):
        if end is None:
            # No end given: the interval lasts until the next start, or as long as the previous one.
            following = raw[index + 1][0] if index + 1 < len(raw) else None
            previous = raw[index - 1][0] if index else None
            if following and following - start <= timedelta(hours=1):
                end = following
            elif previous and start - previous <= timedelta(hours=1):
                end = start + (start - previous)
            else:
                end = start + timedelta(hours=1)
        cursor = start
        while cursor + SLOT <= end:
            slots.setdefault(cursor, PriceSlot(cursor, cursor + SLOT, price))
            cursor += SLOT
    return sorted(slots.values(), key=lambda slot: slot.start)


def next_deadline(now: datetime, ready_by: time, weekend: time | None = None) -> datetime:
    """The next occurrence of the ready-by clock time, in now's time zone. With a weekend time, Saturdays and
    Sundays use that one instead (e.g. later than on workdays)."""
    for days in range(8):
        day = now.date() + timedelta(days=days)
        clock = weekend if weekend is not None and day.weekday() >= 5 else ready_by
        candidate = datetime.combine(day, clock, tzinfo=now.tzinfo)
        if candidate > now:
            return candidate
    raise AssertionError("unreachable: a ready-by time comes within a week")


def calculate(data: PlanInput, now: datetime) -> PlanResult:
    if data.soc is None or data.capacity_kwh <= 0:
        return PlanResult(None, None, None, None, None, None)
    missing_pct = max(data.target_soc - data.soc, 0.0)
    missing_battery = data.capacity_kwh * missing_pct / 100
    missing_wall = missing_battery / data.efficiency if data.efficiency > 0 else 0.0
    minutes = missing_wall / data.power_kw * 60 if data.power_kw > 0 else 0.0
    result = dict(missing_battery_kwh=round(missing_battery, 2), missing_wall_kwh=round(missing_wall, 2),
                  minutes_needed=round(minutes))
    if missing_wall <= 0 or data.power_kw <= 0 or data.deadline is None:
        return PlanResult(**result, start=None, end=None, price=None)

    needed = max(math.ceil(minutes / 15 - 1e-9), 1)
    usable = [slot for slot in data.slots if slot.end > now and slot.end <= data.deadline]
    best_total: float | None = None
    best_index: int | None = None
    for index in range(len(usable) - needed + 1):
        chunk = usable[index:index + needed]
        if any(chunk[j].end != chunk[j + 1].start for j in range(needed - 1)):
            continue
        total = sum(slot.price for slot in chunk)
        if best_total is None or total < best_total - TIE_EPSILON:
            best_total, best_index = total, index
        elif abs(total - best_total) <= TIE_EPSILON:
            best_index = index
    if best_index is None:
        return PlanResult(**result, start=None, end=None, price=None)

    start = usable[best_index].start
    remaining = missing_wall
    cost = 0.0
    for slot in usable[best_index:]:
        if remaining <= 0:
            break
        kwh = min(data.power_kw * 0.25, remaining)
        cost += kwh * slot.price * data.price_factor
        remaining -= kwh
    return PlanResult(**result, start=start, end=start + timedelta(minutes=minutes),
                      price=round(cost, 2))


# ---------------------------------------------------------------------------------------------
# Charge schedules (0.2): quarter-hour slots picked per charge mode, also non-contiguous.
# ---------------------------------------------------------------------------------------------

MODE_SMART = "smart"
MODE_FIXED = "fixed"
MODE_NOW = "now"
MODE_PRICE_CAP = "price_cap"
MODE_OFF = "off"
MODE_MANUAL = "manual"
MODES = (MODE_SMART, MODE_FIXED, MODE_NOW, MODE_PRICE_CAP, MODE_OFF, MODE_MANUAL)
# Plans that can be the default: used when a car is plugged in and returned to after another plan has run.
DEFAULT_MODES = (MODE_SMART, MODE_FIXED, MODE_PRICE_CAP, MODE_NOW, MODE_MANUAL)
# The price slots the plans are made of: quarters, or whole hours with the mean price of their quarters.
RESOLUTION_QUARTER = "quarter"
RESOLUTION_HOUR = "hour"
RESOLUTIONS = (RESOLUTION_QUARTER, RESOLUTION_HOUR)

# How many days back an unknown price may be borrowed from (same clock time).
ESTIMATE_DAYS_BACK = 7
# A plan split into several blocks must be at least this much cheaper than the best single block;
# every extra block is another start/stop of the charger and another wake-up of the car.
SPLIT_MIN_SAVING = 0.05
# Charging windows whose cost differs by less than this share of the cheapest (and at least WINDOW_TIE_MIN in
# money) are equally cheap: prices a fraction of a cent apart must not move or stop a planned charge.
WINDOW_TIE = 0.005
WINDOW_TIE_MIN = 0.02
# "Prefer green power": windows that cost at most this much more (share, or money) count as equally cheap, and the one
# with the least CO2 per kWh wins.
GREEN_TIE = 0.03
GREEN_TIE_MIN = 0.10


@dataclass(frozen=True)
class TimelineSlot:
    start: datetime
    end: datetime
    price: float
    estimated: bool
    # The grid's CO2 per kWh in the slot (g/kWh), when known ("prefer green power").
    co2: float | None = None


@dataclass(frozen=True)
class Constraint:
    """The battery must hold target_soc when the deadline is reached."""

    deadline: datetime
    target_soc: float


@dataclass(frozen=True)
class ScheduleInput:
    mode: str
    soc: float | None
    target_soc: float
    capacity_kwh: float
    efficiency: float
    power_kw: float
    price_factor: float
    timeline: list[TimelineSlot]
    constraints: tuple[Constraint, ...] = ()
    window: tuple[datetime, datetime] | None = None
    price_cap: float | None = None
    min_soc: float | None = None
    # Price cap: may slots above the cap be used when the slots below it cannot reach the target in time?
    cap_override: bool = True
    # The start of the plan already running or announced: kept while it is still among the cheapest windows, so a
    # charge is neither stopped nor moved for a difference of a fraction of a cent.
    keep_start: datetime | None = None
    # Among (nearly) equally cheap windows, take the one with the least CO2 per kWh.
    green: bool = False


@dataclass(frozen=True)
class PlannedSlot:
    start: datetime
    end: datetime
    kwh: float
    price: float
    estimated: bool


@dataclass(frozen=True)
class ChargeBlock:
    start: datetime
    end: datetime
    kwh: float
    cost: float
    estimated: bool


@dataclass(frozen=True)
class Schedule:
    slots: tuple[PlannedSlot, ...] = ()
    blocks: tuple[ChargeBlock, ...] = ()
    charge_now: bool = False
    energy_kwh: float = 0.0
    cost: float | None = None
    estimated: bool = False
    target_soc: float | None = None
    shortfall_kwh: float = 0.0
    # Price cap: the energy planned above the cap to reach the target in time, the highest price paid
    # for it, what it costs more than at the cap, and the battery level the slots below the cap reach.
    over_cap_kwh: float = 0.0
    over_cap_max_price: float | None = None
    over_cap_extra: float = 0.0
    cap_soc: float | None = None
    # The grid's CO2 per kWh for the planned energy (g/kWh), when known.
    co2: float | None = None

    def next_block(self, now: datetime) -> ChargeBlock | None:
        return next((block for block in self.blocks if block.end > now), None)


def floor_quarter(value: datetime) -> datetime:
    utc = value.astimezone(UTC)
    return utc.replace(minute=utc.minute - utc.minute % 15, second=0, microsecond=0)


def floor_slot(value: datetime, minutes: int = SLOT_MINUTES) -> datetime:
    """The start of the price slot that holds value: the quarter, or the whole hour on the local clock."""
    if minutes < 60:
        return floor_quarter(value)
    return value.replace(minute=0, second=0, microsecond=0).astimezone(UTC)


def build_timeline(now: datetime, known: list[PriceSlot], horizon: datetime,
                   minutes: int = SLOT_MINUTES) -> list[TimelineSlot]:
    """Slots of a quarter (or a whole hour, with the mean of its quarters) from the current one until horizon.
    A quarter without a published price borrows the price at the same clock time on an earlier day, or the
    mean of the known prices."""
    by_start = {slot.start: slot.price for slot in known}
    mean = sum(by_start.values()) / len(by_start) if by_start else 0.0
    end = max(horizon, max((slot.end for slot in known), default=horizon))
    step = timedelta(minutes=minutes)
    timeline: list[TimelineSlot] = []
    cursor = floor_slot(now, minutes)
    zone = now.tzinfo
    while cursor < end:
        prices: list[float] = []
        estimated = False
        quarter = cursor
        while quarter < cursor + step:
            if quarter in by_start:
                prices.append(by_start[quarter])
            else:
                prices.append(next((by_start[earlier] for days in range(1, ESTIMATE_DAYS_BACK + 1)
                                    if (earlier := quarter - timedelta(days=days)) in by_start), mean))
                estimated = True
            quarter += SLOT
        timeline.append(TimelineSlot(cursor.astimezone(zone), (cursor + step).astimezone(zone),
                                     sum(prices) / len(prices), estimated))
        cursor += step
    return timeline


def _split(timeline: list[TimelineSlot], cuts) -> list[TimelineSlot]:
    """Hour slots split where a fixed window starts or ends or a deadline falls, so the part inside can be used."""
    points = sorted({cut for cut in cuts if cut is not None})
    result: list[TimelineSlot] = []
    for slot in timeline:
        start = slot.start
        for cut in points:
            if start < cut < slot.end:
                result.append(TimelineSlot(start, cut, slot.price, slot.estimated, slot.co2))
                start = cut
        result.append(slot if start == slot.start else TimelineSlot(start, slot.end, slot.price, slot.estimated,
                                                                     slot.co2))
    return result


def fixed_window(now: datetime, start: time, end: time) -> tuple[datetime, datetime]:
    """The fixed charging window that contains now, otherwise the next one. end <= start wraps midnight."""
    today = now.date()
    candidates = []
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        begin = datetime.combine(day, start, tzinfo=now.tzinfo)
        finish_day = day + timedelta(days=1) if end <= start else day
        candidates.append((begin, datetime.combine(finish_day, end, tzinfo=now.tzinfo)))
    return next(window for window in candidates if window[1] > now)


def _slot_kwh(slot: TimelineSlot, now: datetime, power_kw: float) -> float:
    minutes = (slot.end - max(slot.start, now)).total_seconds() / 60
    return max(minutes, 0.0) * power_kw / 60


def build_schedule(data: ScheduleInput, now: datetime) -> Schedule:
    """Pick the slots to charge in. Every mode except off and now also has to meet the constraints
    (ready-by time, temporary trip); missing energy is then bought in the cheapest slots in time."""
    if data.mode == MODE_OFF:
        return Schedule()
    charge_now = data.mode == MODE_NOW
    if data.soc is None or data.capacity_kwh <= 0 or data.power_kw <= 0 or data.efficiency <= 0:
        return Schedule(charge_now=charge_now)

    def wall(target: float | None) -> float:
        if target is None:
            return 0.0
        return max(target - data.soc, 0.0) * data.capacity_kwh / 100 / data.efficiency

    cuts = [*(data.window or ()), *(constraint.deadline for constraint in data.constraints)]
    usable = [slot for slot in _split(data.timeline, cuts) if slot.end > now]
    kwh = {slot.start: _slot_kwh(slot, now, data.power_kw) for slot in usable}
    chosen: dict[datetime, TimelineSlot] = {}

    def energy(before: datetime | None = None) -> float:
        return sum(kwh[start] for start, slot in chosen.items() if before is None or slot.end <= before)

    def take(candidates, need: float, key, before: datetime | None = None) -> None:
        have = energy(before)
        pool = [slot for slot in candidates if slot.start not in chosen and kwh[slot.start] > 0]
        for _, group in groupby(sorted(pool, key=key), key=lambda slot: key(slot)[0]):
            group = list(group)
            while group and have < need - 1e-9:
                # Among equal prices, extend an already chosen block before opening a new one, so the
                # charger is started and stopped as few times as possible.
                slot = next((item for item in group if item.start in ends or item.end in starts), group[0])
                group.remove(slot)
                chosen[slot.start] = slot
                have += kwh[slot.start]
                starts.add(slot.start)
                ends.add(slot.end)
            if have >= need - 1e-9:
                return

    starts: set[datetime] = set()
    ends: set[datetime] = set()

    def chronological(slot: TimelineSlot):
        return (slot.start,)

    def cheapest(slot: TimelineSlot):
        # Equal prices: the later slot wins, so the battery sits full for as short a time as possible.
        return (round(slot.price, 6), -slot.start.timestamp())

    need = 0.0
    shortfall = 0.0
    capped = data.mode == MODE_PRICE_CAP and data.price_cap is not None

    def under_cap(slot: TimelineSlot) -> bool:
        return not slot.estimated and slot.price * data.price_factor <= data.price_cap + 1e-9

    if data.mode == MODE_NOW:
        need = wall(data.target_soc)
        take(usable, need, chronological)
    elif data.mode == MODE_FIXED and data.window:
        # The cheapest quarters inside the window, done by its end (one block unless splitting saves enough).
        begin, finish = data.window
        need = wall(data.target_soc)
        inside = [slot for slot in usable if slot.end > begin and slot.start < finish]
        take(inside, need, cheapest)
        window = _cheapest_window(inside, need, kwh, data.price_factor, data.keep_start, data.green)
        if chosen and window and data.green and _greener(window, list(chosen.values()), need, kwh, data.price_factor):
            chosen.clear()
            chosen.update({slot.start: slot for slot in window})
        if chosen and window and not _contiguous(chosen.values()):
            split_cost = _allocation_cost(chosen.values(), need, kwh, data.price_factor)
            if split_cost > _allocation_cost(window, need, kwh, data.price_factor) * (1 - SPLIT_MIN_SAVING):
                chosen.clear()
                chosen.update({slot.start: slot for slot in window})
        kept = _keep_running(list(chosen.values()), inside, need, kwh, data.price_factor, data.keep_start)
        if kept:
            chosen.clear()
            chosen.update({slot.start: slot for slot in kept})
        # A window too short for the target: as much as fits, and how much is missing.
        shortfall = need - energy()
    elif data.mode == MODE_PRICE_CAP:
        if data.min_soc is not None and data.soc < data.min_soc:
            take(usable, wall(data.min_soc), chronological)
        if data.price_cap is not None:
            need = wall(data.target_soc)
            take([slot for slot in usable if under_cap(slot)], need, chronological)
        need = max(need, wall(data.min_soc))
    # Slots taken for the minimum level are charged whatever the price; they never count as over the cap.
    for_minimum = set(chosen) if capped and data.min_soc is not None and data.soc < data.min_soc else set()

    # A later deadline that asks for no more than an earlier one is already met by it.
    constraints: list[Constraint] = []
    for constraint in sorted(data.constraints, key=lambda item: item.deadline):
        if not constraints or constraint.target_soc > max(item.target_soc for item in constraints):
            constraints.append(constraint)

    top = data.target_soc if data.mode != MODE_PRICE_CAP or data.price_cap is not None else data.min_soc
    if data.mode != MODE_NOW:
        for constraint in constraints:
            target_kwh = wall(constraint.target_soc)
            before = [slot for slot in usable if slot.end <= constraint.deadline]
            if capped and not data.cap_override:
                before = [slot for slot in before if under_cap(slot)]
            take(before, target_kwh, cheapest, constraint.deadline)
            shortfall = max(shortfall, target_kwh - energy(constraint.deadline))
            need = max(need, target_kwh)
            top = max(top or 0.0, constraint.target_soc)

    if data.mode in (MODE_SMART, MODE_MANUAL) and len(constraints) == 1 and chosen:
        deadline = constraints[0].deadline
        window = _cheapest_window([slot for slot in usable if slot.end <= deadline], need, kwh,
                                  data.price_factor, data.keep_start, data.green)
        if window and data.green and _greener(window, list(chosen.values()), need, kwh, data.price_factor):
            chosen = {slot.start: slot for slot in window}
        if window and not _contiguous(chosen.values()):
            split_cost = _allocation_cost(chosen.values(), need, kwh, data.price_factor)
            if split_cost > _allocation_cost(window, need, kwh, data.price_factor) * (1 - SPLIT_MIN_SAVING):
                chosen = {slot.start: slot for slot in window}
        kept = _keep_running(list(chosen.values()), [slot for slot in usable if slot.end <= deadline], need, kwh,
                             data.price_factor, data.keep_start)
        if kept:
            chosen = {slot.start: slot for slot in kept}

    # Charging happens in time order and stops when the energy is in the battery.
    remaining = need
    planned: list[PlannedSlot] = []
    over_kwh = over_extra = 0.0
    over_max: float | None = None
    co2_grams = co2_kwh = 0.0
    for slot in sorted(chosen.values(), key=lambda item: item.start):
        if remaining <= 1e-9:
            break
        amount = min(kwh[slot.start], remaining)
        remaining -= amount
        if slot.co2 is not None:
            co2_grams += amount * slot.co2
            co2_kwh += amount
        if capped and slot.start not in for_minimum and not under_cap(slot):
            price = slot.price * data.price_factor
            over_kwh += amount
            over_extra += amount * max(price - data.price_cap, 0.0)
            over_max = price if over_max is None else max(over_max, price)
        begin = max(slot.start, now)
        finish = begin + timedelta(hours=amount / data.power_kw)
        planned.append(PlannedSlot(begin, min(finish, slot.end), amount, slot.price, slot.estimated))

    blocks: list[ChargeBlock] = []
    for slot in planned:
        cost = slot.kwh * slot.price * data.price_factor
        if blocks and blocks[-1].end >= slot.start:
            last = blocks[-1]
            blocks[-1] = ChargeBlock(last.start, slot.end, last.kwh + slot.kwh, last.cost + cost,
                                     last.estimated or slot.estimated)
        else:
            blocks.append(ChargeBlock(slot.start, slot.end, slot.kwh, cost, slot.estimated))

    if not charge_now:
        charge_now = any(slot.start <= now < slot.end for slot in planned)
    total = sum(slot.kwh for slot in planned)
    return Schedule(
        slots=tuple(planned),
        blocks=tuple(blocks),
        charge_now=charge_now,
        energy_kwh=round(total, 2),
        cost=round(sum(block.cost for block in blocks), 2) if planned else None,
        estimated=any(slot.estimated for slot in planned),
        target_soc=top,
        shortfall_kwh=round(max(shortfall, 0.0), 2),
        over_cap_kwh=round(over_kwh, 2),
        over_cap_max_price=round(over_max, 4) if over_max is not None else None,
        over_cap_extra=round(over_extra, 2),
        cap_soc=(min(data.soc + (total - over_kwh) * data.efficiency / data.capacity_kwh * 100, 100.0)
                 if capped else None),
        co2=round(co2_grams / co2_kwh) if co2_kwh > 1e-9 and co2_kwh >= total * 0.5 else None,
    )


def _contiguous(slots) -> bool:
    ordered = sorted(slots, key=lambda slot: slot.start)
    return all(a.end == b.start for a, b in zip(ordered, ordered[1:], strict=False))


def _allocation_cost(slots, need: float, kwh: dict, factor: float) -> float:
    """Cost of charging need kWh in these slots, in time order."""
    remaining, cost = need, 0.0
    for slot in sorted(slots, key=lambda item: item.start):
        if remaining <= 1e-9:
            break
        amount = min(kwh[slot.start], remaining)
        cost += amount * slot.price * factor
        remaining -= amount
    return cost


def _keep_running(chosen: list[TimelineSlot], slots: list[TimelineSlot], need: float, kwh: dict, factor: float,
                  keep_start: datetime | None) -> list[TimelineSlot] | None:
    """The charge running now (or the start already announced) as one run from keep_start, when the new choice is not
    clearly cheaper than going on with it (WINDOW_TIE). On 10 Oct a running charge stopped at 01:15 to finish at
    05:15 for 0.2 øre; a charge must not stop or move for that."""
    if keep_start is None or not chosen:
        return None
    run: list[TimelineSlot] = []
    have = 0.0
    for slot in slots:
        if not run and not (slot.start <= keep_start < slot.end):
            continue
        if run and run[-1].end != slot.start:
            return None
        run.append(slot)
        have += kwh.get(slot.start, 0.0)
        if have >= need - 1e-9:
            break
    if not run or have < need - 1e-9:
        return None
    new_cost = _allocation_cost(chosen, need, kwh, factor)
    kept_cost = _allocation_cost(run, need, kwh, factor)
    if kept_cost <= new_cost + max(new_cost * WINDOW_TIE, WINDOW_TIE_MIN) + 1e-9:
        return run
    return None


def _co2(slots, need: float, kwh: dict) -> float | None:
    """CO2 per kWh of charging need kWh in these slots in time order; None unless every slot used has it."""
    remaining, grams, energy = need, 0.0, 0.0
    for slot in sorted(slots, key=lambda item: item.start):
        if remaining <= 1e-9:
            break
        if slot.co2 is None:
            return None
        amount = min(kwh[slot.start], remaining)
        grams += amount * slot.co2
        energy += amount
        remaining -= amount
    return grams / energy if energy > 1e-9 else None


def _greener(window, chosen, need: float, kwh: dict, factor: float) -> bool:
    """The window is cleaner than the chosen slots and costs at most GREEN_TIE more."""
    if not chosen or {slot.start for slot in window} == {slot.start for slot in chosen}:
        return False
    window_co2, chosen_co2 = _co2(window, need, kwh), _co2(chosen, need, kwh)
    if window_co2 is None or (chosen_co2 is not None and window_co2 >= chosen_co2):
        return False
    cost = _allocation_cost(chosen, need, kwh, factor)
    return _allocation_cost(window, need, kwh, factor) <= cost + max(cost * GREEN_TIE, GREEN_TIE_MIN) + 1e-9


def _cheapest_window(slots: list[TimelineSlot], need: float, kwh: dict, factor: float,
                     keep_start: datetime | None = None, green: bool = False) -> list[TimelineSlot] | None:
    """The cheapest run of consecutive slots that holds need kWh. Runs within WINDOW_TIE of the cheapest count as
    equally cheap: the one starting at keep_start (the charge running or announced) is kept, otherwise the latest
    wins (the battery sits full for the shortest time). So the plan does not jump for a fraction of a cent."""
    runs: list[tuple[float, list[TimelineSlot]]] = []
    for index in range(len(slots)):
        have, run = 0.0, []
        for slot in slots[index:]:
            if run and run[-1].end != slot.start:
                break
            run.append(slot)
            have += kwh[slot.start]
            if have >= need - 1e-9:
                runs.append((_allocation_cost(run, need, kwh, factor), list(run)))
                break
    if not runs:
        return None
    cheapest = min(cost for cost, _ in runs)
    near = [run for cost, run in runs if cost <= cheapest + max(cheapest * WINDOW_TIE, WINDOW_TIE_MIN) + 1e-9]
    if keep_start is not None:
        kept = next((run for run in near if run[0].start <= keep_start < run[0].end), None)
        if kept is not None:
            return kept
    if green:
        # Nearly as cheap and cleaner: the window with the least CO2 per kWh among those within GREEN_TIE.
        close = [(co2, run) for cost, run in runs
                 if cost <= cheapest + max(cheapest * GREEN_TIE, GREEN_TIE_MIN) + 1e-9
                 and (co2 := _co2(run, need, kwh)) is not None]
        if close:
            return min(close, key=lambda item: item[0])[1]
    return near[-1]
