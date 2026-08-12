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
| Charger (cost) | [Monta](https://github.com/erlendsellie/monta_ha) | actual cost of the last completed session |
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

The config flow asks for:

1. **Vehicle name** and **currency** (e.g. DKK, EUR, USD).
2. **Vehicle entities** — battery %, odometer, device tracker, "is charging"
   binary sensor, and optionally a lock entity, all picked from your existing
   vehicle integration.
3. **Charger providers** — tick whichever of Zaptec / Monta / manual entry
   apply to you.
4. Entity pickers for whichever providers you ticked.

You can revisit all of this later from the integration's **Configure** button.

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

These are plain sensors with list-valued attributes — build whatever
`custom:button-card` / `auto-entities` / markdown-table dashboard you like on
top. No bundled dashboard is shipped, on purpose — everyone's taste in "nerdy
dashboard" differs.

## Roadmap

- [ ] MQTT export of ledger data (for anyone who wants to build a standalone
      app or dashboard outside Home Assistant)
- [ ] More vehicle providers (official Tesla integration, other EVs)
- [ ] More charger providers (Easee, Wallbox, generic price-sensor + energy-sensor pairing)
- [ ] Statistics/long-term-stats integration for native HA energy dashboard support

## Contributing

Issues and PRs welcome. The codebase is deliberately small — `coordinator.py`
does trip/charge detection, `providers/` holds the pluggable data sources,
`store.py` persists everything as a Home Assistant `Store`.

## License

[MIT](LICENSE)
