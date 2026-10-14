# Changelog

What changed in each version of EV Ledger. A new version in `manifest.json` on `main` is published as a GitHub release
with its section below as the release notes. Versions before 0.7.2: see the
[releases](https://github.com/MRDonnii/evledger/releases).

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
