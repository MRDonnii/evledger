# Changelog

What changed in each version of EV Ledger. A new version in `manifest.json` on `main` is published as a GitHub release
with its section below as the release notes. Versions before 0.7.2: see the
[releases](https://github.com/MRDonnii/evledger/releases).

## [0.7.5] - 2026-10-15

### Added

- **The price limit asks instead of just waiting.** When *Price limit in cheapest* is on, the car is above *Always
  charge to* and part of the charge is dearer than *Cheapest max price*, the phones get one question per plug-in
  (and again when the limit settings change): the battery level, how far the car charges under the limit, and what
  charging everything anyway would cost, with the buttons *Charge anyway* (Charge now, which hands back to Cheapest
  at the target) and *Wait*. Without an answer the car waits.

## [0.7.4] - 2026-10-15

### Added

- **A price limit on Cheapest, with your own set points.** Switch *Price limit in cheapest* on, and Cheapest charges
  above *Always charge to* (20 % by default) only in quarters at or below *Cheapest max price* (2.00 kr/kWh by
  default). Below that level it still charges up to it in the cheapest quarters before the ready-by time, whatever
  the price. A night where every quarter is dearer leaves the car waiting (status *waiting*, not *target reached*),
  and the plan message says the price is above the limit; a temporary trip is always charged in full. The plan's
  sensor shows `price_limit`, `price_limit_floor`, `above_limit_kwh` and `limit_soc`.

## [0.7.3] - 2026-10-15

### Fixed

- **"The charger is connected, but it is not this car" no longer comes when it is this car.** A car from two
  integrations (Tesla Custom and Tesla Fleet) has more than one plug sensor, and a sleeping car leaves one of them on
  "disconnected" for hours. The car now counts as plugged in when any of its own plug sensors says so, and all of them
  are asked for fresh data when the cable goes in.
- **The message waits for the car to report.** "Another car is plugged in" is sent to the phones only when no sensor of
  the car has said plugged in within four minutes; a car that wakes up a little after the charger is not mistaken for
  another car. The plan on the phone (or the question about it) is sent when the car reports its plug after the charger.

## [0.7.2] - 2026-10-14

### Fixed

- **Smart charging with Strømligning plans with the whole price again.** With the price sensors left empty, the plan
  took every price list on Strømligning's device, so it could price today with the grid tariff alone and tomorrow with
  the spot price alone. It now uses the price sensor and Strømligning's own sensor for tomorrow, whichever integration
  loads first; tomorrow's forecast (before the prices are out) counts as an estimate. Recorded charging costs were not
  affected.
- **The public charge form is there for every car** (kWh, price, place and the save button); it was only created with
  smart charging on.
- **A price answered on the phone goes to the charge it was asked about.** With a home charge also waiting for a price
  it made a new public charge and left the asked one open. `log_public_charge` never fills a home charge.
- **No phantom public charge at home** when the car reports charging a little before the home charger does.
- **Monta**: a charge never gets the cost of an earlier charge that Monta still shows.
- **Home charging energy** counts the charge running also without a price, so the Energy dashboard gets it as it
  charges, not all at the end.
- **Configure** can clear the optional entities again (lock, outside temperature, Zaptec charging and completed energy,
  Monta wallet).
- Saved trips and charges with a field from a newer version still load.

### Added

- **Diagnostics download** (Settings → Devices & services → EV Ledger → ⋮), with the car's positions and the phones
  left out.
- **Repairs**: a message when an entity EV Ledger reads (car, charger, price) or a phone for the messages is gone, e.g.
  renamed by its integration. It goes away by itself when they are back.
- **Logbook**: finished home charges and tyre changes, in Danish when Home Assistant is set to Danish.

### Changed

- The list attributes of the trips, charges, pending and monthly performance sensors are no longer kept in the
  recorder's history (they are for cards).
- README and the setup form say which Strømligning sensor to pick.
