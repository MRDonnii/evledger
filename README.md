<p align="center">
  <img src="logo.png" width="120" alt="EV Ledger logo">
</p>

<h1 align="center">EV Ledger</h1>

<p align="center">
  Track every trip and every charge — home or public — as one unified cost ledger in Home Assistant,
  and (optionally) let it charge the car in the cheapest hours before you leave.
</p>

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/MRDonnii/evledger/releases"><img src="https://img.shields.io/github/v/release/MRDonnii/evledger?include_prereleases" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/MRDonnii/evledger" alt="License"></a>
</p>

---

## What it does

EV Ledger sits on top of integrations you've already got installed and turns
their raw entity states into a proper **trip and charging ledger**:

- **Trips** — detected automatically from your vehicle's own odometer/location
  entities. No extra hardware, no extra API, no dependency on a self-hosted
  tool like TeslaMate.
- **Home charging** — reads live power/session-energy from your charger
  (Zaptec today) and, if available, the actual price from a cost-reporting
  source (Monta today).
- **Public/away charging** — most public charging networks have no Home
  Assistant integration at all, so EV Ledger notices the session (via your
  vehicle's own charging state) and lets you fill in the price afterwards
  with one service call — no dashboard needed, works great from a phone
  shortcut or the car's own charging screen.
- **A handful of ledger sensors** (trip/charge totals, cost per km, sessions
  still waiting for a price) designed to be dropped straight into a custom
  Lovelace dashboard.
- **Efficiency vs. rated consumption** — real-world Wh/km, bucketed by outside
  temperature (cold/mild/warm) at trip time, compared against your vehicle's
  official WLTP-rated consumption. See "Efficiency comparison" below.
- **Smart charging (optional, off by default)** — charge plans (cheapest before
  departure, fixed time, price cap, charge now, pause, manual) with a default
  plan, a temporary trip with an address, start/stop of the charger (Zaptec or
  any switch), plan messages and questions on your phones, and a plan that keeps
  being followed through charger reboots and Home Assistant restarts. See
  "Smart charging" below.

EV Ledger never talks to a vehicle or charger vendor's API directly — it only
reads (and, with smart charging, operates) entities that another integration
already created. That's deliberate:
it means EV Ledger has **zero extra dependencies**, works with whatever
combination of integrations you already run, and can't get you rate-limited
or logged out anywhere.

## Supported providers (today)

| Role | Provider | What it gives EV Ledger |
|---|---|---|
| Vehicle | [Tesla Custom Integration](https://github.com/alandtse/tesla) | battery %, odometer, location, charging state, lock state |
| Charger (live power) | [Zaptec](https://www.home-assistant.io/integrations/zaptec/) | live power, session energy |
| Charger (actual cost) | [Monta](https://github.com/erlendsellie/monta_ha) | actual cost of the last completed session |
| Charger (estimated cost) | Any electricity-price sensor (Nordpool, Energi Data Service, Strømligning, ...) | kWh × current price — used when Monta isn't configured or isn't fresh enough |
| Charger (anywhere) | Manual entry | one service call: kWh + price + location |
| Charger (control) | Zaptec, or any switch that starts/stops charging | smart charging starts and stops the charger |

More vehicle and charger providers are meant to be added over time — the
provider interface (`custom_components/evledger/providers/`) is intentionally
small. Pull requests for new providers are very welcome.

## Installation

### Via HACS (recommended)

1. HACS → the "⋮" menu (top right) → **Custom repositories**
2. Repository: `https://github.com/MRDonnii/evledger`, category: **Integration**
3. Search for **EV Ledger** in HACS → **Download**
4. Restart Home Assistant
5. Settings → Devices & services → **Add Integration** → search **EV Ledger**

*(Once accepted into the default HACS store, step 1–2 won't be needed.)*

### Manual

Copy `custom_components/evledger` into your Home Assistant `custom_components/`
folder and restart.

## Setting it up

Pick a device for each role — that's it:

1. **Vehicle name** and **currency** (e.g. DKK, EUR, USD).
2. **Vehicle** — a device picker scoped to the Tesla Custom Integration. If
   you have more than one car, this is how EV Ledger knows which one this
   entry is for. Its entities (battery, odometer, location, charging state,
   lock, outside temperature) are resolved automatically.
3. **Charger** (Zaptec) — pick your charger device, or leave it empty if you
   don't have one. Its power/energy entities are resolved automatically.
4. **Charging control** (Monta) — same idea; leave it empty to skip Monta
   entirely.
5. **Power price** — pick your electricity-price device (Nordpool, Energi
   Data Service, Strømligning, ...), or leave it empty. Used for home
   charging cost when Monta isn't configured or isn't fresh enough.
6. **Efficiency comparison** (optional) — pick your model/trim from a built-in
   list of Tesla's published WLTP figures, or "Custom" and enter your own
   battery capacity + rated consumption right there on the same page.

Leaving a device picker empty simply leaves that role out of the setup —
there's no separate "which providers do you want" step. Submit, and you're
done, unless something couldn't be auto-detected from a device you picked,
in which case a second, much shorter page asks only for the specific
sensor that's missing.

**Want to set every sensor yourself instead?** Tick **Advanced setup** on
the first page. It skips nothing — every sensor field for every role you
picked a device for shows up on the review page, pre-filled with whatever
was auto-detected, so you can check or override anything before saving.

You can revisit all of this later from the integration's **Configure**
button — one page of every current sensor, editable directly. Clearing a
Zaptec/Monta/price entity there drops that role.

## Efficiency comparison

If you gave EV Ledger an outside-temperature sensor and picked (or entered) a
model spec, `sensor.<vehicle>_efficiency` reports real-world Wh/km — estimated
per trip from the battery-percent drop × your battery capacity — bucketed by
the outside temperature at the start of each trip:

- **cold**: below 5°C
- **mild**: 5–15°C
- **warm**: 15°C and up

Its attributes carry each bucket's Wh/km, trip count, and % deviation from
your configured rated consumption, plus the overall state.

**The model list is a starting point, not ground truth.** The Tesla Custom
Integration exposes no VIN, trim, or battery-size data — the figures in
`tesla_models.py` are Tesla's own published EU WLTP numbers by model
generation/trim, which can still be off for your exact wheel size or model
year. If the closest match doesn't feel right, use "Custom" in the efficiency
step and enter your own battery capacity and rated consumption (check your
delivery paperwork or Tesla account for the exact number). Corrections and
new model entries via PR are welcome.

## Month-by-month history and performance score

Every trip and charge is kept forever (nothing is ever pruned), so nothing
about "going back in time" needs a special feature — `sensor.<vehicle>_trips`
and `sensor.<vehicle>_charges`' totals (and `total_cost`/`total_distance`)
are always computed over the *entire* history, only their `trips`/`charges`
attribute lists are capped to the most recent 20 for a manageable dashboard
tile.

If efficiency comparison is configured, `sensor.<vehicle>_monthly_performance`
buckets that same full history by calendar month (up to 24 months back) and
scores each one: `rated_wh_per_km ÷ actual_wh_per_km × 100` — 100 means you
drove exactly as efficiently as the official rated figure, above 100 beats
it, below 100 falls short. Its `months` attribute is the "look back" table:
newest month first, each with distance, cost, Wh/km, and score. The state
itself is a plain `measurement` sensor too, so HA's built-in **History**
graph on that entity lets you scrub back through every past reading, not
just the last 24 months.

## Home charging cost sources

Home charge sessions try each configured cost source in order and use the
first one that answers:

1. **Monta** — the actual billed cost of the session, matched by timestamp.
2. **Spot price** — kWh (from Zaptec) × your price sensor's current state, as
   an estimate. One price point at session end, not a time-weighted average
   across the session — good enough for most sessions, less so for very long
   ones spanning a price change. Assumes your sensor's state is already in
   your configured currency per kWh (convert first with a template sensor if
   yours reports in øre/cents).
3. Neither → the session is flagged `needs_review` with `kwh` still recorded
   from Zaptec, same as an unpriced public charge.

## Logging a public charge

```yaml
service: evledger.log_public_charge
data:
  entry_id: <your vehicle's config entry id>
  kwh: 24.5
  price: 145.50
  location_name: "Ionity Kolding"
```

If EV Ledger already noticed the car charging away from home and is waiting
for a price, this fills that session in. Otherwise it creates a new one.
Handy as a script tied to a phone widget/shortcut.

## Fixing a mistake

Trips and charges can be wrong — a phantom trip from GPS drift, a charge
logged with a typo'd price. Two services remove a record permanently
(there's no undo):

```yaml
service: evledger.delete_charge
data:
  entry_id: <your vehicle's config entry id>
  charge_id: <the charge's id, from sensor.<vehicle>_charges' `charges` list>
```

```yaml
service: evledger.delete_trip
data:
  entry_id: <your vehicle's config entry id>
  trip_id: <the trip's id, from sensor.<vehicle>_trips' `trips` list>
```

## Dashboard sensors

Each vehicle gets:

| Entity | State | Useful attributes |
|---|---|---|
| `sensor.<vehicle>_trips` | trip count | `trips` (recent list), `total_distance_km` |
| `sensor.<vehicle>_charges` | charge count | `charges` (recent list), `total_kwh`, `total_price`, `home_*`, `public_*` |
| `sensor.<vehicle>_charging_status` | `idle` / `home` / `public` | current open session, if any |
| `sensor.<vehicle>_cost_per_km` | all-time avg cost/km | — |
| `sensor.<vehicle>_pending_review` | count needing a price | `pending` (list) |
| `sensor.<vehicle>_efficiency` | overall Wh/km | `cold_wh_per_km`, `mild_wh_per_km`, `warm_wh_per_km`, `*_deviation_pct`, `*_trip_count`, `rated_wh_per_km` (only created if efficiency comparison is configured) |
| `sensor.<vehicle>_monthly_performance` | current month's score (%) | `months` (up to 24 months back, newest first — each with `distance_km`, `wh_per_km`, `score`, `cost`, `cost_per_km`, `trip_count`); only created if efficiency comparison is configured |
| `sensor.<vehicle>_total_charging_cost` | running total spent (monetary, `state_class: total`) | — |
| `sensor.<vehicle>_total_distance` | running total km driven (`state_class: total_increasing`) | — |
| `sensor.<vehicle>_last_trip` | most recent trip's distance | full trip record |
| `sensor.<vehicle>_last_charge` | most recent charge's price | full charge record |

`total_cost` and `total_distance` carry proper `device_class`/`state_class`,
so Home Assistant's own **Statistics graph** card gives you month-over-month
(or any period) views natively — no need to build that yourself.

## Dashboards

[`dashboards/example-view.yaml`](dashboards/example-view.yaml) is a full
ready-to-copy view built entirely from built-in card types (tile, markdown,
statistics-graph, conditional) — glance tiles, last trip/charge, an
efficiency tile, a "needs price" nag banner, month-over-month statistics
graphs, and Jinja-templated recent-trips/recent-charges tables. See
[`dashboards/README.md`](dashboards/README.md) for how to use it.

## Smart charging (optional)

EV Ledger can also plan the charging by electricity price and start/stop the charger itself
(formerly the separate [EV Smart Charge](https://github.com/MRDonnii/ha-ev-smart-charge)
integration). It is **off by default**; nothing changes for existing setups until it is switched on.

Settings → Devices & services → EV Ledger → **Configure** → (first page unchanged) → **Smart charging**:

- **Smart charging** on. Everything else is optional and filled in from the ledger: the car's
  battery, the spot price sensor, the Zaptec charger (its "Charger mode" sensor is found next to
  the charge power sensor) and the car's own plug sensor.
- **Charger control**: Zaptec, or any switch that starts/stops charging (OCPP, Monta, Easee, …).
- **Phones to confirm the plan on** and **Only phones that are home** (Companion app).
- **Car climate (preconditioning)**: empty finds the car's `climate.*` on this car only (its battery sensor's device or
  a device with the same name in another car integration, preferring Tesla Fleet, Teslemetry or Tessie);
  choose one when another integration sends the commands (see *Controlling the car* below).
- **Trip calendar** and **Calendar keyword**: events in the next 36 hours become the temporary plan
  (see *Trips from a calendar* below).

New entities on the car's device:

| Entity | What it does |
|---|---|
| `select.<car>_charge_mode` | Cheapest before departure, Fixed time, Charge now, Price cap, Pause, Manual. A plugged-in car runs the default plan; any other plan returns to it when the car is unplugged. |
| `select.<car>_default_plan` | The default plan: Cheapest before departure (out of the box), Fixed time, Price cap, Charge now or Manual. When it changes, a plan that was running as the old default follows; a temporary plan is kept until it has run. |
| `number.<car>_target_soc`, `time.<car>_ready_by` | Target and ready-by time for the cheapest plan and the price cap. The target is never higher than the charge limit set in the car (Tesla Custom, Tesla Fleet, Teslemetry, Tessie). |
| `time.<car>_fixed_charging_start/end` | Fixed time charges in the cheapest quarters inside the window and is done by its end. |
| `number.<car>_price_cap`, `number.<car>_minimum_soc`, `switch.<car>_exceed_price_cap` | Price cap charges below the cap (and always up to the minimum). When that cannot reach the target by the ready-by time, the phones are asked first (**Approve** / **Stop above the cap**, with the level the cap reaches, the energy above it, the highest price and the extra cost); without an answer it charges on to the target. The switch shows and changes the answer; it is on again at the next plug-in. |
| `datetime.<car>_temporary_departure`, `text.<car>_trip_destination`, `switch.<car>_round_trip` | Temporary plan: departure and destination (address, `lat,lon` or `zone.*`); the road distance comes from OpenStreetMap and the plan charges for the trip plus margin and reserve. |
| `switch.<car>_confirm_plan_on_phone`, `button.<car>_confirm_plan` | When on, a plugged-in car waits for an answer on the phones (Confirm / Charge now / Pause); without an answer the plan runs after 60 minutes. Confirm keeps the plan that waits (e.g. the default plan). |
| `switch.<car>_message_when_charging_starts` | When charging starts: the plan, when the charging period ends, the expected price and energy, the target and the battery now, with **Pause**. Not again for a short stop within 30 minutes. |
| `switch.<car>_notify_plan_on_phone`, `button.<car>_send_plan_to_phone` | Phone messages: the active plan with time, price and Charge now / Pause when the car is plugged in or the plan changes; a warning once when the target cannot be reached in time (plugged in late, fixed window too short, a trip above the car's charge limit). |
| `sensor.<car>_charge_status`, `..._next_charge_start/end`, `..._planned_charge_cost/energy` | The plan. `planned_charge_cost` has an `alternatives` attribute with the price of every plan. |
| `binary_sensor.<car>_charge_now` | On while the plan wants to charge; usable without charger control. |
| `switch.<car>_message_when_charging_is_done` | When the plan is done (or the cable comes out), one message with the whole charge: kWh, price and price per kWh, the battery before and after, and when it charged – summed over all periods of a split plan, from the ledger's sessions. On by default. |
| `switch.<car>_reminder_to_plug_in`, `time.<car>_evening_check_at`, `number.<car>_remind_below` | The evening check (21:00 by default), once a day: when the car is home without the cable and its battery is below the level (50 %, or below what a planned trip needs), a reminder to plug in. In the same check, a warning when the charger is offline while a plan waits. |
| `switch.<car>_another_time_at_the_weekend`, `time.<car>_ready_by_at_the_weekend` | Another ready-by time on Saturdays and Sundays (09:00 by default), e.g. later than on workdays. |
| `switch.<car>_precondition_the_car_for_ready_by`, `number.<car>_precondition_minutes_before` | 20 minutes (by default) before the ready-by time or a trip's departure, while the car is home, the phones are asked **Forvarm / Spring over**; the climate is only turned on after **Forvarm** (no answer, nothing happens). If the car is still plugged in 30 minutes after, the climate is turned off again. Off by default; needs car control. |

Prices without a published value yet (e.g. tomorrow's before 13:00) are estimated from the same
time on earlier days. A start waits until the plan has wanted charging for 15 s; a stop the
charger did not act on is repeated after 45 s. Right after a restart the last battery level is used at once, and until the prices are
loaded again the charging periods planned before the restart are followed, so a restart at the
planned start does not lose the charge. A charger that is offline for 10 minutes while the plan
wants to charge is reported on the phones at once; the charge starts as soon as it answers again. A car that stops at its own charge limit is done:
it is neither started again nor reported as stopped from outside. Settings, the chosen plan, a temporary plan and an
open phone question all survive a restart. The
[`th-tesla-dashboard-card`](https://github.com/MRDonnii/ha-smart-home-cards/tree/main/src/cards/th-tesla-dashboard-card)
shows and controls all of it with `smart_charge: select.<car>_charge_mode`.

### Trips from a calendar

With a **trip calendar**, EV Ledger reads the next 36 hours every 15 minutes. The first event with
the **keyword** in its title or description (or, without a keyword, the first event with an
address) becomes the temporary plan: its address is the destination, and the car leaves so it is
there at the event's start (the drive time plus 10 minutes, or 45 minutes before when there is no
address). Moving or deleting the event moves or clears the plan. A trip set by hand is never
replaced, and a calendar trip cleared by hand is not added again. All-day events are skipped.

### Controlling the car

Preconditioning (and the charge limit the plan respects) needs a car integration that can send
commands to the car. Tesla now only accepts commands signed with a *virtual key*:

1. **Tesla Fleet** (built into Home Assistant): add the integration, then pair its virtual key
   with the car (the integration shows a link; open it on the phone with the Tesla app and
   approve the key in the car). Or **Teslemetry** / **Tessie**, which handle the key for you.
2. Check `sensor.<car>_charge_status`: `car_climate_entity` names the climate entity EV Ledger
   will use, `car_charge_limit_entity` the charge limit, `car_at_home` the car's location.
3. If the commands come from another integration than the battery sensor, choose the climate
   entity under **Smart charging → Car climate**.

Every home session that ends also fires the event `evledger_charge_finished` (the vehicle and the
ledger entry: kWh, price, start and end, battery before and after) for your own automations.

## Roadmap

- [ ] MQTT export of ledger data (for anyone who wants to build a standalone
      app or dashboard outside Home Assistant)
- [ ] More vehicle providers (official Tesla integration, other EVs)
- [ ] More charger providers (Easee, Wallbox)
- [ ] Time-weighted spot price cost (instead of a single price-at-session-end point)

## Contributing

Issues and PRs welcome. The codebase is deliberately small — `coordinator.py`
does trip/charge detection, `providers/` holds the pluggable data sources,
`store.py` persists everything as a Home Assistant `Store`.

## License

[MIT](LICENSE)
