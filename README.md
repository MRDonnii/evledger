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
- **Smart charging (optional, part of the setup)** — charge plans (cheapest before
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
| Vehicle | [Tesla Custom Integration](https://github.com/alandtse/tesla), [Tesla Fleet](https://www.home-assistant.io/integrations/tesla_fleet/), [Teslemetry](https://www.home-assistant.io/integrations/teslemetry/) or [Tessie](https://www.home-assistant.io/integrations/tessie/) — picked as a device | battery %, odometer, location, charging state, lock state |
| Vehicle (any other car) | Its sensors, picked by hand | battery %, odometer, location and a charging sensor (on/off, or a state that says `charging`) |
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

Everything is set up in one flow: the ledger, the charger, the power price and smart charging.
What you need in Home Assistant first:

- **The car**: Tesla Custom, Tesla Fleet, Teslemetry or Tessie (or any car integration with a battery,
  odometer, location and charging sensor). To let EV Ledger warm the car before departure, the car
  integration must be able to send commands (Tesla Fleet with the virtual key, Teslemetry or Tessie).
- **The charger**: Zaptec (found automatically), or any charger with a switch that starts and stops
  charging. For Zaptec, turn on **Authorization required** in the Zaptec portal, so the car does not start
  by itself when it is plugged in and the plan decides. If the charger runs through Monta or another OCPP
  backend, that backend controls it: switch the charger back to Zaptec's own control first.
- **The power price** with a price list in its attributes: Strømligning, Energi Data Service or Nord Pool. With
  Strømligning, pick its **current price** sensor (with VAT): it is the whole price you pay per kWh (spot price,
  tariffs, fees and VAT), and EV Ledger takes tomorrow's prices from Strømligning's own "tomorrow" sensor by itself.
  The other price lists on Strømligning's device (the spot price alone, the grid tariff) are not used.
- **The Home Assistant app** on the phones that should get the messages.

Then Settings → Devices & services → **Add Integration** → **EV Ledger**, and pick a device for each role:

1. **Vehicle name** and **currency** (e.g. DKK, EUR, USD).
2. **Vehicle** — your car from Tesla Custom, Tesla Fleet, Teslemetry or Tessie. If
   you have more than one car, this is how EV Ledger knows which one this
   entry is for. Its entities (battery, odometer, location, charging state,
   lock, outside temperature) are resolved automatically; Tesla Fleet's odometer, which
   is off by default, is turned on. Leave it empty for another car and pick its sensors
   on the next page.
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
there's no separate "which providers do you want" step. If something couldn't be
auto-detected from a device you picked, a second, much shorter page asks only for the
specific sensor that's missing. The last page is **Smart charging** (see below): it is
ticked when you picked a power price, and the phones for the messages are chosen there.
Submit, and you're done.

**A second car** is a second EV Ledger entry. Cars on the same charger share it: the one
that is plugged in gets it (see *Several cars on one charger*).

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

## Tyre sets (summer, winter, all-season, studded)

Keep track of which wheels are on the car and what each set costs you to drive on:

- **Sets** – add each set with `evledger.save_tyre_set`: name, kind (`summer`, `winter`, `studded`, `all_season`),
  tyre brand/model, size (width, profile, rim diameter, load/speed index – shown as e.g. `235/45 R18 98W`), DOT,
  rim (name, width in J, offset ET), bought, price, km driven before EV Ledger, where it is stored and a note.
- **Changes** – `select.<car>_tyre_set` records a change now, at the odometer now. `evledger.change_tyres` can also
  record one in the past (the odometer then is read from the recorded trips, or give it). `update_tyre_change` and
  `delete_tyre_change` fix mistakes.
- **Tread depth** – `evledger.log_tread_depth` (one value, or one per tyre; the least counts). Two readings some
  hundred km apart give the wear per 1000 km on that set and the km left to the recommended least depth
  (summer 3 mm, winter 4 mm; the legal minimum is 1.6 mm).
- **`sensor.<car>_tyres`** – state is the set on the car; attributes `size`, `rim`, `mounted_at`, `season_km`,
  `season_wh_per_km`, `tread_mm`, `suggestion`, `sets` (each set's km, driving Wh/km per outside temperature band,
  charged kWh/100 km, cost/km, wear), `seasons` (every period on the car) and `comparison`.
- **A fair comparison** – winter use is mostly the cold, not the tyres. `comparison` holds each set against the summer
  set only in the outside temperature bands both have driven ≥ 30 km in, weighted by km: the tyres' own difference.
- **Reminder** – `suggestion` follows the 7-degree rule (mean outside temperature of the last 5 days' trips) and the
  Danish studded-tyre season (1 November – 15 April). With smart charging's phones,
  `switch.<car>_tyre_change_reminder` (off by default) sends one message per season.

Driving Wh/km comes from the trips' battery drop and needs the battery capacity (set by the model in the setup).

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
2. **Spot price** — while the charge runs, every bit of energy Zaptec counts is
   priced at the price of that moment: the quarter from your price sensor's
   price list (or the mean of the hour when smart charging plans in whole
   hours), else the sensor's state. Only when that is not possible, the kWh
   × the price at the end. Assumes your sensor's state is already in your
   configured currency per kWh (convert first with a template sensor if yours
   reports in øre/cents).
3. Neither → the session is flagged `needs_review` with `kwh` still recorded
   from Zaptec, same as an unpriced public charge.

Zaptec's session counter runs from plug-in to unplug, also through a pause. A charge that stops and
starts again while the car stays plugged in (e.g. a smart charging plan in several periods) is
recorded as one session per period, each with only the energy it added.

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

The same from a dashboard, without helpers of your own: type the kWh, the price and the place in
`number.<vehicle>_public_charge_energy`, `number.<vehicle>_public_charge_price` and
`text.<vehicle>_public_charge_location`, and press `button.<vehicle>_log_public_charge`. The fields are
emptied when the charge is saved.

## Fixing a mistake

Trips and charges can be wrong — a phantom trip from GPS drift, a charge
logged with a typo'd price, a home charge counted twice by a charger counter.
A charge (home or public) can be corrected:

```yaml
service: evledger.update_charge
data:
  entry_id: <your vehicle's config entry id>
  charge_id: <the charge's id, from sensor.<vehicle>_charges' `charges` list>
  kwh: 7.03
  price: 2.77
  note: "Counted twice by the charger"
```

A charge EV Ledger did not record itself (for example from before it was set up) can be added, so the monthly and
yearly costs are complete; it counts in the month it ended:

```yaml
service: evledger.add_charge
data:
  entry_id: <your vehicle's config entry id>
  kwh: 35.5
  price: 21.30            # leave out when unknown: the charge then waits for a price
  started_at: "2026-03-05 01:00"
  ended_at: "2026-03-05 05:00"
  location: home          # or public
  note: "From the old charge log"
```

Added charges from before the first recorded trip do not count in the cost per km (there are no kilometres for them).
Added home charges also count in the lifetime `home_charging_energy` total, so adding a long history makes it jump
once. Home Assistant's statistics count that jump as charging at the moment it happened: correct it once with
Developer tools → Statistics (adjust sum) or `recorder.adjust_sum_statistics` by minus the added kWh.

Two services remove a record permanently (there's no undo):

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

Something looks wrong and you want to report it? **Settings → Devices & services → EV Ledger → ⋮ → Download
diagnostics** gives what EV Ledger reads from the car, charger and price sensors, its open sessions and latest
records, and (with smart charging) the plan and the charger control — with the car's positions and the phones left out.

When an entity EV Ledger reads is gone — the car, charger or price integration renamed or removed it, or it was
disabled — or a phone chosen for the messages no longer exists, **Settings → Repairs** says which ones, five minutes
after a start (the other integrations may still be loading) and every half hour after that. The message goes away by
itself when they are back, e.g. after choosing the new ones under **Configure**.

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
| `sensor.<vehicle>_distance_today` | km driven today (the trips that ended today and the one under way) | — |
| `sensor.<vehicle>_home_charging_power` | the home charger's power while this car charges, in kW whatever unit the charger reports (0 while another car on a shared charger has it) | — |
| `sensor.<vehicle>_home_charging_energy` (+ `_today`, `_this_month`) | kWh charged at home: in total, today and this month (the charges that ended in the period and the one running; `state_class: total`, works in the Energy dashboard) | — |
| `sensor.<vehicle>_home_charging_cost_today` | what charging at home cost today | `kwh` |
| `sensor.<vehicle>_charging_cost_this_year` | what charging cost this year, home and away | `kwh`, `unpriced`, `previous_year`, `previous_year_kwh`, `months` (the last 12 months, oldest first: `month`, `cost`, `kwh`, `home_cost`, `public_cost`, `charges`, `unpriced`), `months_year_before` (the same 12 months a year earlier, for comparing) |

The list attributes (`trips`, `charges`, `pending` and the `months` of the monthly performance and this year's
cost) are there for cards; they are not kept in the recorder's history, so a long ledger does not fill the database.

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
integration). It is the last page of the setup (suggested when a power price was picked) and can be
changed or switched off later; nothing changes for existing setups until it is switched on.

The setup's last page, or later Settings → Devices & services → EV Ledger → **Configure** → (first page
unchanged) → **Smart charging**:

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
| `select.<car>_charge_mode` | Cheapest before departure, Fixed time, Charge now, Price cap, Pause, Manual. A plugged-in car runs the default plan; any other plan returns to it when the car is unplugged. **Charge now** returns to it as soon as it has charged the car to the target (two minutes after), so a small drop of the battery afterwards is topped up by the default plan and not right away at any price (unless Charge now is the default plan). |
| `select.<car>_default_plan` | The default plan: Cheapest before departure (out of the box), Fixed time, Price cap, Charge now or Manual. When it changes, a plan that was running as the old default follows; a temporary plan is kept until it has run. |
| `number.<car>_target_soc`, `time.<car>_ready_by` | Target and ready-by time for the cheapest plan and the price cap. The target is never higher than the charge limit set in the car (Tesla Custom, Tesla Fleet, Teslemetry, Tessie). Changing the target also sets the car's own charge limit (Tesla: 50–100 %), through Tesla Fleet, Teslemetry or Tessie when the car is also there. |
| `time.<car>_fixed_charging_start/end` | Fixed time charges in the cheapest quarters inside the window and is done by its end. |
| `number.<car>_price_cap`, `number.<car>_minimum_soc`, `switch.<car>_exceed_price_cap` | Price cap charges below the cap (and always up to the minimum). When that cannot reach the target by the ready-by time, the phones are asked first (**Approve** / **Stop above the cap**, with the level the cap reaches, the energy above it, the highest price and the extra cost); without an answer it charges on to the target. The switch shows and changes the answer; it is on again at the next plug-in. |
| `datetime.<car>_temporary_departure`, `text.<car>_trip_destination`, `switch.<car>_round_trip` | Temporary plan: departure and destination (address, `lat,lon` or `zone.*`); the road distance comes from OpenStreetMap and the plan charges for the trip plus margin and reserve. |
| `switch.<car>_confirm_plan_on_phone`, `button.<car>_confirm_plan` | When on, a plugged-in car waits for an answer on the phones (Confirm / Charge now / Pause); without an answer the plan runs after 60 minutes. Confirm keeps the plan that waits (e.g. the default plan). |
| `switch.<car>_message_when_charging_starts` | When charging starts: the plan, when the charging period ends, the expected price and energy, the target and the battery now, with **Pause**. Not again for a short stop within 30 minutes. |
| `switch.<car>_notify_plan_on_phone`, `button.<car>_send_plan_to_phone` | Phone messages: the active plan with time, price and Charge now / Pause when the car is plugged in or the plan changes; a warning once when the target cannot be reached in time (plugged in late, fixed window too short, a trip above the car's charge limit). |
| `sensor.<car>_home_charging_cost_today` | What charging at home cost today: the charges that ended today and the one running, priced as it charges (attribute `kwh`). |
| `sensor.<car>_charge_status`, `..._next_charge_start/end`, `..._planned_charge_cost/energy` | The plan. `planned_charge_cost` has an `alternatives` attribute with the price of every plan. `next_charge_start` has the plan's periods (`blocks`) and the prices it chose from (`prices`: start, price, estimated; not kept in the recorder), so a card can chart when the car would charge – also before it is plugged in. While the car charges up to its own charge limit in the plan's last period, the end is the car's own "full at" time (Tesla Custom, Tesla Fleet, Teslemetry, Tessie): the car knows that it charges slower near 100 %. |
| `select.<car>_price_resolution` | **Quarter** (15 min) or **Hour**: the plans use quarters, or whole hours with the mean price of the hour (a fixed window or a ready-by time inside an hour still counts). The ledger prices its charges the same way. The charge mode select has a `price_resolution` attribute, so price cards can show the prices the same way. |
| `switch.<car>_learn_charging_power_and_efficiency` | Learns the charging power (charges of 30 minutes or more that end below 90 %) and the efficiency (charges that raise the battery level by 10 % or more) from the ledger's home charges, and plans with them once two charges agree; the numbers for power and efficiency then show the learned values. The attribute `learned` has the values and how many charges they rest on. On by default. |
| `switch.<car>_monthly_summary_on_the_phone`, `button.<car>_send_monthly_summary` | On the 1st of the month from 09:00: last month's home charges (count, kWh, price, price per kWh), what the plans saved and the public charges, once. The button sends this month so far. |
| `binary_sensor.<car>_charge_now` | On while the plan wants to charge; usable without charger control. |
| `switch.<car>_message_when_charging_is_done` | When the plan is done (or the cable comes out), one message with the whole charge: kWh, price and price per kWh, the battery before and after, when it charged – summed over all periods of a split plan, from the ledger's sessions – and what the plan saved against charging right away when the car was plugged in ("Charge now"). On by default. |
| `switch.<car>_reminder_to_plug_in`, `time.<car>_evening_check_at`, `number.<car>_remind_below` | The evening check (21:00 by default), once a day: when the car is home without the cable and its battery is below the level (50 %, or below what a planned trip needs), a reminder to plug in. In the same check, a warning when the charger is offline while a plan waits. Also half an hour before the plan's cheapest start, under the same conditions: "Billigste ladning starter kl. …" (once per start, quiet at night). |
| `switch.<car>_message_when_power_is_cheap`, `number.<car>_cheap_power_message_below` | When the price drops below the level (0.50 by default) between 08:00 and 21:00 and the car is home without the cable and not near its target: a message with **Charge now** (at most every 3 hours). Off by default. |
| `switch.<car>_wait_for_a_cheaper_day` | **Cheapest** only: when a night within the next three departures is clearly cheaper (at least 15 % per kWh and 2.00 in all, from the price forecast) and the battery lasts until then with 10 % above the minimum level (judged from the ledger's driving of the last 14 days), nothing is charged tonight and the plan aims at that day. The plan message says why (`waiting_for` on the next charge start sensor). A charge already running is not stopped. Off by default. |
| `switch.<car>_learn_departure_times` | Learns when the car usually leaves home on each weekday (the first trip of the morning from home, the last 8 weeks, at least 3 per weekday, ready a quarter before an early one) and uses that as the ready-by time; a weekday without regular departures has none, and the plan aims at the next day the car leaves (the usual ready-by time still keeps the minimum level). Needs 3 weeks of trips; the learned times are on the switch (`departures`). Off by default. |
| `switch.<car>_prefer_green_power` | Among nearly equally cheap windows (at most 3 % or 0.10 more), the one with the least CO2 per kWh from Energinet's forecast (Denmark, DK1/DK2 from Home Assistant's location). The plan's CO2 is on the next charge start sensor (`co2`) and in the plan message. Off by default. |
| `switch.<car>_ask_for_the_price_of_public_charges` | When the car has charged away from home, a message asks for the price: answer in the message with the price (the kWh estimated from the battery) or kWh and price ("23,4 82,50"), and the charge is saved. Off by default. |
| `switch.<car>_message_if_the_target_is_not_reached` | Half an hour after the ready-by time: a message (with **Charge now**) when the car is home on the charger more than 3 % below the level the plan aimed for, e.g. after Home Assistant or the charger was down in the night. Off by default. |
| `switch.<car>_another_time_at_the_weekend`, `time.<car>_ready_by_at_the_weekend` | Another ready-by time on Saturdays and Sundays (09:00 by default), e.g. later than on workdays. |
| `switch.<car>_week_plan` | **Week plan**: a ready-by time for each weekday, each with the normal target or its own (e.g. 90 % on Fridays), and free days without a time (the plan then aims at the next day with one). While on, it is used instead of the ready-by time, the weekend time and the learned departure times; the minimum level is still kept by the week's earliest time every day. Switched on the first time, it starts from the ready-by times used so far. The days are on the switch (`days`), with the next ready-by time, its target and what set it (`next_ready_by`, `next_target`, `next_source`: `once`, `week`, `learned` or `time`). Set it with `evledger.set_week_plan`. Off by default. |
| `datetime.<car>_ready_by_once` | **One-time ready-by time** (at most 14 days ahead, with the normal target or its own in the `target` attribute): the plan aims at it instead of the normal ready-by time. A normal ready-by time before it keeps the minimum level, and "wait for a cheaper day" does not wait past it. Once it has passed it is cleared and the normal times apply again. Set it with `evledger.set_ready_by_once`, clear it with `evledger.clear_ready_by_once`. |

**Keep the battery at the target while heating** (`switch.<car>_keep_the_battery_at_the_target_while_heating`, `number.<car>_price_limit_while_heating`): while the car's climate runs (heating or cooling) and the car is on the charger, the charger starts when the battery is 1 % below the target and charges back to it, so the climate runs on power from the grid instead of the battery. Only while the price now is at most the limit (3 kr/kWh by default), never while the plan is paused or manual. The switch shows whether it holds now and why not (`active`, `reason`: `climate_off`, `not_plugged_in`, `too_expensive`, `at_target`, `paused`, …), the price now and the limit. Off by default.

**The car's data after a restart:** the car integrations (Tesla Fleet, Tesla Custom, Teslemetry, Tessie) can leave the car's entities unknown (or a "last update" of 1970) after Home Assistant restarts, until the car wakes up on its own. EV Ledger asks them for an update every 3 minutes in the first half hour after a start, every 15 minutes (8 times) while the car is on the charger without data, and when the car is online but its "last update" is more than half an hour old. An update never wakes the car. Charging itself does not depend on it: the plan uses the last known battery level and the charger is controlled directly.

A ready-by time with its own target (week plan day, one-time time) also sets the car's own charge limit to that target while the car is on the charger, since the car would otherwise stop at its old limit; afterwards the car gets the normal target back. The plan messages say "(ugeplan)" or "(engangstid)" and the target.

```yaml
service: evledger.set_week_plan
data:
  entity_id: select.my_car_charge_mode   # any EV Ledger entity of the car
  enabled: true
  days:
    mon: {time: "06:30"}
    tue: {time: "06:30"}
    wed: null                            # free day
    thu: {time: "06:30"}
    fri: {time: "06:00", target: 90}
    sat: null
    sun: {time: "10:00"}
---
service: evledger.set_ready_by_once
data:
  entity_id: select.my_car_charge_mode
  at: "2026-10-14 05:30"
  target: 95                             # leave out for the normal target
```
| `switch.<car>_precondition_the_car_for_ready_by`, `number.<car>_precondition_minutes_before` | 20 minutes (by default) before the ready-by time or a trip's departure, while the car is home, the phones are asked **Forvarm / Spring over**; the climate is only turned on after **Forvarm** (no answer, nothing happens). If the car is still plugged in 30 minutes after, the climate is turned off again. Off by default; needs car control. |

Prices without a published value yet (e.g. tomorrow's before 13:00) are estimated from the same
time on earlier days (also when Strømligning shows a forecast for tomorrow until the prices are out). A start waits until the plan has wanted charging for 15 s; a stop the
charger did not act on is repeated after 45 s. Right after a restart the last battery level is used at once, and until the prices are
loaded again the charging periods planned before the restart are followed, so a restart at the
planned start does not lose the charge. A charger that is offline for 10 minutes while the plan
wants to charge is reported on the phones at once; the charge starts as soon as it answers again. A car that stops at its own charge limit is done:
it is neither started again nor reported as stopped from outside. Settings, the chosen plan, a temporary plan, an
open phone question and the periods charged so far (for the done message) all survive a restart. Messages that only tell what happens (the active plan,
charging started, charge done, plug in soon) arrive without sound at night: `switch.<car>_silent_messages_at_night`
(on by default) with `time.<car>_silent_from` / `time.<car>_silent_until` (22:00–07:00 by default; the window may
cross midnight). iOS gets the "passive" interruption level (into the notification centre without sound or lighting
the screen), Android the low-importance channel "EV Ledger lydløs". Questions and warnings keep their sound. The
[`th-tesla-dashboard-card`](https://github.com/MRDonnii/ha-smart-home-cards/tree/main/src/cards/th-tesla-dashboard-card)
shows and controls all of it with `smart_charge: select.<car>_charge_mode`.

### Several cars on one charger

Each car has its own EV Ledger entry (its own model, battery, target, plans and ledger) and smart charging on the
same charger. The car that says it is plugged in (its own plug sensor: Tesla Fleet, Teslemetry or Tessie "charge
cable", Tesla Custom "charger", or the one chosen in the setup) and is at home takes the charger with its own plan;
right after plugging in, before the car has reported, the only car at home is taken. The other cars show "another
car" and never send the charger a command, also not in Charge now, and only the car on the charger records the charge
in its ledger. When no car can be told (several at home, none reporting yet – or a guest car), nothing starts until
**Charge now** is pressed on the car that is plugged in (also from the phone message); that choice holds until the
cable comes out. With one car on the charger nothing changes.

### Trips from a calendar

With a **trip calendar**, EV Ledger reads the next 36 hours every 15 minutes. The first event with
the **keyword** in its title or description (or, without a keyword, the first event with an
address) becomes the temporary plan: its address is the destination, and the car leaves so it is
there at the event's start (the drive time plus 10 minutes, or 45 minutes before when there is no
address). Moving or deleting the event moves or clears the plan. A trip set by hand is never
replaced, and a calendar trip cleared by hand is not added again (also after a restart). All-day events are skipped.

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
ledger entry: kWh, price, start and end, battery before and after) for your own automations. It shows in the
logbook too ("Bil finished charging at home: 7.5 kWh for 12.30 DKK"), as does a tyre change
(`evledger_tyres_changed`).

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

What changed in each version: [CHANGELOG.md](CHANGELOG.md). A new version in `manifest.json` on `main` is published as
a GitHub release by itself (`.github/workflows/release.yml`), with its CHANGELOG section as the notes.

## License

[MIT](LICENSE)
