<p align="center">
  <img src="logo.png" width="120" alt="EV Ledger logo">
</p>

<h1 align="center">EV Ledger</h1>

<p align="center">
  Track every trip and every charge — home or public — as one unified cost ledger in Home Assistant.
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

EV Ledger never talks to a vehicle or charger vendor's API directly — it only
reads entities that another integration already created. That's deliberate:
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

It's one page:

1. **Vehicle name** and **currency** (e.g. DKK, EUR, USD).
2. **Pick your vehicle** — a device picker scoped to the Tesla Custom
   Integration. If you have more than one car, this is how EV Ledger knows
   which one this entry is for. Its entities (battery, odometer, location,
   charging state, lock, outside temperature) are resolved automatically.
3. **Pick your charger(s)** — a device picker for Zaptec and one for Monta.
   Leave either empty and that provider is simply left out of the setup —
   there's no separate "which providers do you want" step. Their entities
   are resolved automatically from whichever device you picked.
4. **Electricity price sensor** (optional) — for home charging cost when
   Monta isn't configured or isn't fresh enough. Leave empty to skip.
5. **Efficiency comparison** (optional) — pick your model/trim from a built-in
   list of Tesla's published WLTP figures, or "Custom" and enter your own
   battery capacity + rated consumption right there on the same page.

Submit, and you're done — unless something couldn't be auto-detected from a
device you picked, in which case a second, much shorter page asks only for
the specific entity that's missing.

You can revisit all of this later from the integration's **Configure**
button — also one page, pre-filled with everything currently configured.
Clearing a Zaptec/Monta/price entity there drops that provider.

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
