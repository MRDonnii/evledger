"""Tyre sets: which set is on the car since when, what each set uses, how fast it wears, and when to change.

Pure logic without Home Assistant, so it can be tested on its own. Times are aware datetimes or ISO 8601 strings.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from datetime import date, datetime, timedelta
from typing import Any

KIND_SUMMER = "summer"
KIND_WINTER = "winter"
KIND_STUDDED = "studded"
KIND_ALL_SEASON = "all_season"
KINDS = (KIND_SUMMER, KIND_WINTER, KIND_STUDDED, KIND_ALL_SEASON)
WINTER_KINDS = (KIND_WINTER, KIND_STUDDED)

# Recommended least tread depth (mm) before a set is replaced (FDM: summer 3 mm, winter 4 mm); 1.6 mm is the legal
# minimum in Denmark.
MIN_TREAD_MM = {KIND_SUMMER: 3.0, KIND_WINTER: 4.0, KIND_STUDDED: 4.0, KIND_ALL_SEASON: 4.0}
LEGAL_TREAD_MM = 1.6

# The 7-degree rule: winter tyres grip better below about 7 °C, summer tyres above.
CHANGE_AT_C = 7.0
SUGGEST_DAYS = 5  # the mean of the trips' outside temperature over these days decides
SUGGEST_MIN_READINGS = 3
# Studded tyres are allowed in Denmark from 1 November to 15 April (both included).
STUDS_FROM = (11, 1)
STUDS_UNTIL = (4, 15)

# Outside temperature bands (°C, lower edge) for comparing the sets at the same temperature: winter use is mostly the
# cold, not the tyres.
BANDS = (-99, 0, 5, 10, 15, 20)
BAND_NAMES = ("under 0", "0–5", "5–10", "10–15", "15–20", "20+")
# A band counts in the comparison when both sets drove at least this far in it: a trip's energy is read from whole
# battery percent, and the routes vary, so short distances say little.
COMPARE_MIN_KM = 100.0
# How sure the difference is, by the km compared on the other set: below the first "low", below the second "medium".
CONFIDENCE_KM = (500.0, 1500.0)


def _when(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@dataclass
class TyreSet:
    """One set of four wheels: the tyres and the rims they sit on."""

    id: str
    name: str
    kind: str = KIND_SUMMER
    brand: str | None = None  # tyre brand, e.g. "Michelin"
    model: str | None = None  # tyre model, e.g. "Pilot Sport 4"
    width_mm: int | None = None  # 235
    aspect_pct: int | None = None  # 45
    rim_inch: float | None = None  # 18
    load_speed: str | None = None  # "98W"
    dot: str | None = None  # production week + year, e.g. "2321"
    rim_name: str | None = None  # e.g. "Aero 18"
    rim_width_in: float | None = None  # 8.5 (J)
    rim_offset_mm: int | None = None  # ET 40
    purchased: str | None = None  # ISO date
    price: float | None = None
    km_before: float = 0.0  # what the set had driven before EV Ledger knew it
    storage: str | None = None  # where it is when not on the car, e.g. "Dækhotel Euromaster"
    note: str | None = None
    retired: bool = False
    treads: list[dict] = field(default_factory=list)  # [{id, at, mm, each: [FL, FR, RL, RR], odometer_km}]

    @property
    def size(self) -> str | None:
        """Tyre size as on the sidewall, e.g. "235/45 R18 98W"."""
        if not (self.width_mm and self.aspect_pct and self.rim_inch):
            return None
        rim = f"{self.rim_inch:g}"
        return f"{self.width_mm}/{self.aspect_pct} R{rim}" + (f" {self.load_speed}" if self.load_speed else "")

    @property
    def rim(self) -> str | None:
        """Rim as text, e.g. "Aero 18 · 8.5J ET40"."""
        parts = [self.rim_name or (f'{self.rim_inch:g}"' if self.rim_inch else None)]
        if self.rim_width_in:
            offset = f" ET{self.rim_offset_mm}" if self.rim_offset_mm is not None else ""
            parts.append(f"{self.rim_width_in:g}J{offset}")
        elif self.rim_offset_mm is not None:
            parts.append(f"ET{self.rim_offset_mm}")
        text = " · ".join(p for p in parts if p)
        return text or None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TyreSet:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class TyreChange:
    """A set put on the car at a time (and odometer reading)."""

    id: str
    set_id: str
    at: str  # ISO 8601
    odometer_km: float | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TyreChange:
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})


@dataclass
class Period:
    """A set on the car from one change to the next (or now)."""

    set_id: str
    change_id: str
    start: datetime
    end: datetime | None  # None: still on
    odo_start: float | None
    odo_end: float | None

    @property
    def km(self) -> float | None:
        if self.odo_start is None or self.odo_end is None:
            return None
        return max(self.odo_end - self.odo_start, 0.0)

    def holds(self, when: datetime) -> bool:
        return self.start <= when and (self.end is None or when < self.end)


def periods(changes: list[TyreChange], odometer_now: float | None) -> list[Period]:
    """The changes as periods, oldest first; the last one runs to now."""
    ordered = sorted((c for c in changes if _when(c.at)), key=lambda c: _when(c.at))
    result = []
    for index, change in enumerate(ordered):
        following = ordered[index + 1] if index + 1 < len(ordered) else None
        result.append(Period(
            set_id=change.set_id, change_id=change.id, start=_when(change.at),
            end=_when(following.at) if following else None,
            odo_start=_num(change.odometer_km),
            odo_end=_num(following.odometer_km) if following else _num(odometer_now),
        ))
    return result


def active_set(changes: list[TyreChange], when: datetime | None = None) -> str | None:
    """The set on the car at that time (now without a time)."""
    found = None
    for period in periods(changes, None):
        if when is None or period.start <= when:
            found = period.set_id
    return found


def odometer_at(when: datetime, trips: list[Any], odometer_now: float | None, now: datetime) -> float | None:
    """The odometer at that time from the trips: inside a trip, after the last trip before it, or now."""
    if when >= now - timedelta(minutes=5):
        return _num(odometer_now)
    best: tuple[datetime, float] | None = None
    for trip in trips:
        start, end = _when(trip.started_at), _when(trip.ended_at)
        if start is None:
            continue
        if end is not None and start <= when < end and trip.start_odometer_km is not None:
            return float(trip.start_odometer_km)
        if end is not None and end <= when and trip.end_odometer_km is not None and (best is None or end > best[0]):
            best = (end, float(trip.end_odometer_km))
    if best is not None:
        return best[1]
    # Before the first trip the ledger knows: the odometer at its start, when it is a few days at most.
    first = min((t for t in trips if _when(t.started_at) and t.start_odometer_km is not None),
                key=lambda t: _when(t.started_at), default=None)
    if first is not None and _when(first.started_at) - when <= timedelta(days=3):
        return float(first.start_odometer_km)
    return None


def trip_energy_kwh(trip: Any, capacity_kwh: float | None) -> float | None:
    """A trip's energy from its battery drop. A drop of 0 counts (short trips round to whole percent both ways);
    a rise (charged on the way) does not."""
    if not capacity_kwh or trip.start_battery_pct is None or trip.end_battery_pct is None:
        return None
    if not trip.distance_km or trip.distance_km <= 0 or trip.ended_at is None:
        return None
    drop = trip.start_battery_pct - trip.end_battery_pct
    if drop < 0:
        return None
    return drop / 100 * capacity_kwh


def band_of(temp_c: float) -> int:
    index = 0
    for i, edge in enumerate(BANDS):
        if temp_c >= edge:
            index = i
    return index


def _wh_per_km(kwh: float, km: float) -> float | None:
    return round(kwh * 1000 / km) if km > 0 else None


def _new_use() -> dict:
    return {"km": 0.0, "kwh": 0.0, "trips": 0, "temp_sum": 0.0, "temp_n": 0,
            "bands": [{"km": 0.0, "kwh": 0.0} for _ in BANDS]}


def _add_trip(use: dict, trip: Any, energy: float | None) -> None:
    if energy is None:
        return
    use["km"] += trip.distance_km
    use["kwh"] += energy
    use["trips"] += 1
    temp = _num(trip.start_outside_temp_c)
    if temp is not None:
        use["temp_sum"] += temp
        use["temp_n"] += 1
        band = use["bands"][band_of(temp)]
        band["km"] += trip.distance_km
        band["kwh"] += energy


def _use_out(use: dict) -> dict:
    return {
        "driving_km": round(use["km"], 1),
        "trips": use["trips"],
        "wh_per_km": _wh_per_km(use["kwh"], use["km"]),
        "mean_temp_c": round(use["temp_sum"] / use["temp_n"], 1) if use["temp_n"] else None,
        "bands": {BAND_NAMES[i]: {"km": round(b["km"], 1), "wh_per_km": _wh_per_km(b["kwh"], b["km"])}
                  for i, b in enumerate(use["bands"]) if b["km"] > 0},
    }


def _charges_in(charges: list[Any], period: Period, now: datetime) -> tuple[float, float, float]:
    """kWh, cost and the priced kWh of the charges that ended inside the period."""
    kwh = cost = priced = 0.0
    for charge in charges:
        ended = _when(charge.ended_at)
        if ended is None or not (period.start <= ended < (period.end or now + timedelta(days=1))):
            continue
        kwh += charge.kwh or 0.0
        if charge.price is not None:
            cost += charge.price
            priced += charge.kwh or 0.0
    return kwh, cost, priced


def _cost_per_km(kwh: float, cost: float, priced: float, km: float) -> float | None:
    """Cost per km; a few charges without a price are counted at the mean price of the others (at least 80 % of the
    energy must have a price)."""
    if km < 50 or kwh <= 0 or priced < kwh * 0.8:
        return None
    return round(cost * (kwh / priced) / km, 2)


def _tread_rate(tyre: TyreSet, set_periods: list[Period]) -> dict:
    """Wear from the tread readings: mm per 1000 km on this set, and the km left to the recommended least depth."""
    readings = sorted((r for r in tyre.treads if _num(r.get("mm")) is not None and _when(r.get("at"))),
                      key=lambda r: _when(r["at"]))
    out: dict[str, Any] = {"tread_mm": None, "tread_at": None, "tread_min_mm": MIN_TREAD_MM.get(tyre.kind, 3.0),
                           "wear_mm_per_1000_km": None, "km_left": None}
    if not readings:
        return out
    last = readings[-1]
    out["tread_mm"] = round(float(last["mm"]), 1)
    out["tread_at"] = last["at"]
    first = next((r for r in readings if _num(r.get("odometer_km")) is not None), None)
    end = next((r for r in reversed(readings) if _num(r.get("odometer_km")) is not None), None)
    if first is None or end is None or first is end:
        return out
    lo, hi = float(first["odometer_km"]), float(end["odometer_km"])
    km = 0.0
    for period in set_periods:  # only the km this set was on the car between the two readings
        if period.odo_start is None or period.odo_end is None:
            continue
        km += max(min(period.odo_end, hi) - max(period.odo_start, lo), 0.0)
    worn = float(first["mm"]) - float(end["mm"])
    if km < 500 or worn <= 0:
        return out
    rate = worn / km * 1000
    out["wear_mm_per_1000_km"] = round(rate, 2)
    out["km_left"] = max(round((float(last["mm"]) - out["tread_min_mm"]) / rate * 1000, -2), 0)
    return out


def stats(sets: list[TyreSet], changes: list[TyreChange], trips: list[Any], charges: list[Any],
          capacity_kwh: float | None, odometer_now: float | None, now: datetime) -> dict[str, Any]:
    """Everything the tyre sensor shows: each set with its km, use, cost and wear, each season, and the sets compared
    at the same outside temperature."""
    all_periods = periods(changes, odometer_now)
    by_id = {s.id: s for s in sets}
    uses = {s.id: _new_use() for s in sets}
    season_uses = [_new_use() for _ in all_periods]
    for trip in trips:
        start = _when(trip.started_at)
        if start is None:
            continue
        for index, period in enumerate(all_periods):
            if period.holds(start):
                energy = trip_energy_kwh(trip, capacity_kwh)
                if period.set_id in uses:
                    _add_trip(uses[period.set_id], trip, energy)
                _add_trip(season_uses[index], trip, energy)
                break

    seasons = []
    totals = {s.id: {"km": 0.0, "kwh_charged": 0.0, "cost": 0.0, "cost_km": 0.0, "priced": 0.0, "days": 0.0}
              for s in sets}
    for period, use in zip(all_periods, season_uses):
        kwh, cost, priced = _charges_in(charges, period, now)
        km = period.km
        days = ((period.end or now) - period.start).total_seconds() / 86400
        tyre = by_id.get(period.set_id)
        if tyre is not None:
            total = totals[tyre.id]
            total["days"] += days
            if km is not None:
                total["km"] += km
                total["kwh_charged"] += kwh
                total["cost"] += cost
                total["cost_km"] += km
                total["priced"] += priced
        seasons.append({
            "change_id": period.change_id, "set_id": period.set_id, "set": tyre.name if tyre else None,
            "kind": tyre.kind if tyre else None, "from": period.start.isoformat(),
            "to": period.end.isoformat() if period.end else None, "days": round(days),
            "odometer_from": round(period.odo_start) if period.odo_start is not None else None,
            "odometer_to": round(period.odo_end) if period.odo_end is not None else None,
            "km": round(km, 1) if km is not None else None,
            "charged_kwh_per_100_km": round(kwh / km * 100, 1) if km and km >= 50 else None,
            "cost_per_km": _cost_per_km(kwh, cost, priced, km) if km else None,
            **_use_out(use),
        })

    out_sets = []
    for tyre in sets:
        use = uses[tyre.id]
        total = totals[tyre.id]
        own = [p for p in all_periods if p.set_id == tyre.id]
        out_sets.append({
            **tyre.to_dict(), "size": tyre.size, "rim": tyre.rim, "mounted": bool(own and own[-1].end is None),
            "changes": len(own), "km": round(total["km"], 1), "total_km": round(total["km"] + (tyre.km_before or 0), 1),
            "days_on": round(total["days"]),
            "charged_kwh_per_100_km": (round(total["kwh_charged"] / total["cost_km"] * 100, 1)
                                       if total["cost_km"] >= 50 else None),
            "cost_per_km": _cost_per_km(total["kwh_charged"], total["cost"], total["priced"], total["cost_km"]),
            **_use_out(use), **_tread_rate(tyre, own),
        })

    return {"sets": out_sets, "seasons": list(reversed(seasons)), "comparison": compare(sets, uses)}


def compare(sets: list[TyreSet], uses: dict[str, dict]) -> dict[str, Any] | None:
    """Each set against the reference set (the first summer set, else the first one) only in the temperature bands
    both drove in, weighted by their km there: the tyres' own difference with the cold taken out."""
    if len(sets) < 2:
        return None
    reference = next((s for s in sets if s.kind == KIND_SUMMER), sets[0])
    ref_bands = uses[reference.id]["bands"]
    result: dict[str, Any] = {"reference": reference.name, "reference_id": reference.id, "sets": []}
    for tyre in sets:
        if tyre.id == reference.id:
            continue
        bands = uses[tyre.id]["bands"]
        shared = []
        for i, (ref, own) in enumerate(zip(ref_bands, bands)):
            if ref["km"] >= COMPARE_MIN_KM and own["km"] >= COMPARE_MIN_KM:
                shared.append((i, ref, own))
        diff = None
        if shared:
            weight = sum(ref["km"] + own["km"] for _, ref, own in shared)
            ref_wh = sum((ref["kwh"] / ref["km"]) * (ref["km"] + own["km"]) for _, ref, own in shared) / weight
            own_wh = sum((own["kwh"] / own["km"]) * (ref["km"] + own["km"]) for _, ref, own in shared) / weight
            diff = round((own_wh - ref_wh) / ref_wh * 100, 1) if ref_wh else None
        km = sum(own["km"] for _, _, own in shared)
        result["sets"].append({
            "set_id": tyre.id, "set": tyre.name, "difference_pct": diff,
            "bands": [BAND_NAMES[i] for i, _, _ in shared], "km_compared": round(km, 1),
            "confidence": None if diff is None else ("low" if km < CONFIDENCE_KM[0]
                                                     else "medium" if km < CONFIDENCE_KM[1] else "high"),
        })
    return result


def _in_studs_season(day: date) -> bool:
    md = (day.month, day.day)
    return md >= STUDS_FROM or md <= STUDS_UNTIL


def suggestion(active: TyreSet | None, sets: list[TyreSet], temps: list[tuple[datetime, float]],
               now: datetime) -> dict[str, Any] | None:
    """Time to change? By the 7-degree rule on the mean outside temperature of the last days' trips, and the studs
    season. Returns {"to": kind, "set_id", "set", "reason", "key"} or None. "key" is the same for the whole season,
    so a message is sent once."""
    if active is None or active.kind == KIND_ALL_SEASON:
        return None
    today = now.date()
    since = now - timedelta(days=SUGGEST_DAYS)
    recent = [t for when, t in temps if when >= since]
    mean = sum(recent) / len(recent) if len(recent) >= SUGGEST_MIN_READINGS else None
    winter_now = active.kind in WINTER_KINDS
    candidates = [s for s in sets if not s.retired and s.id != active.id]
    to_winter = next((s for s in candidates if s.kind == KIND_WINTER), None) or next(
        (s for s in candidates if s.kind == KIND_STUDDED and _in_studs_season(today)), None)
    to_summer = next((s for s in candidates if s.kind == KIND_SUMMER), None) or next(
        (s for s in candidates if s.kind == KIND_ALL_SEASON), None)

    if active.kind == KIND_STUDDED and not _in_studs_season(today):
        return {"to": KIND_SUMMER, "set_id": to_summer.id if to_summer else None,
                "set": to_summer.name if to_summer else None, "urgent": True,
                "reason": "Pigdæk er kun tilladt 1. november – 15. april.", "key": f"studs-{today.year}",
                "mean_temp_c": round(mean, 1) if mean is not None else None}
    if mean is None:
        return None
    season = today.year if today.month >= 7 else today.year - 1  # a winter season runs over New Year
    if not winter_now and today.month in (9, 10, 11, 12, 1, 2, 3) and mean < CHANGE_AT_C:
        return {"to": KIND_WINTER, "set_id": to_winter.id if to_winter else None,
                "set": to_winter.name if to_winter else None, "urgent": mean < 3,
                "reason": f"Gennemsnitligt {mean:.1f} °C de sidste {SUGGEST_DAYS} dage — under {CHANGE_AT_C:g} °C "
                          "greber vinterdæk bedst.".replace(".", ",", 1),
                "key": f"winter-{season}", "mean_temp_c": round(mean, 1)}
    if winter_now and today.month in (3, 4, 5, 6) and mean > CHANGE_AT_C + 3:
        return {"to": KIND_SUMMER, "set_id": to_summer.id if to_summer else None,
                "set": to_summer.name if to_summer else None, "urgent": False,
                "reason": f"Gennemsnitligt {mean:.1f} °C de sidste {SUGGEST_DAYS} dage — sommerdæk bruger mindre "
                          "og bremser bedre nu.".replace(".", ",", 1),
                "key": f"summer-{season}", "mean_temp_c": round(mean, 1)}
    return None
