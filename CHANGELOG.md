# Changelog

What changed in each version of EV Ledger. A new version in `manifest.json` on `main` is published as a GitHub release
with its section below as the release notes. Versions before 0.7.2: see the
[releases](https://github.com/MRDonnii/evledger/releases).

## [0.9.6] - 2026-10-16

### Fixed

- **"Charge now" only for a start that is really yours.** A charge that EV Ledger did not start counts as a start from
  the app or the car only when it lasts 5 minutes. An old "charging" from the charger's cloud goes away before that.
  EV Ledger does not stop the charger while it confirms the start.
- **A car that wakes late is still ours.** On 14 Oct the car took 10 minutes to start. Up to 60 minutes after our own
  start, charging is ours, also after a stop in between and across a restart. The last command is kept with the plan.
  Before, a car that woke after its period had ended could switch the plan to Charge now.
- **The command is sent when the plan turns, whatever the charger says.**
  - Charge → pause after our start: the stop is sent even when the cloud says "stopped" but the car still charges.
  - Pause → charge within 5 minutes of our stop: the start is sent even when the cloud says "charging".
  - Not while Zaptec waits for authorisation, when its charging switch cannot be used.
- A "charging" we did not start that goes away again is no longer counted as a stop from outside. Two such stops
  would have left the car uncharged until the next period.
- After a charger reboot, a start is sent again at once instead of 3 minutes later.

### Tests

- Random whole nights against a simulated Zaptec charger and car:
  - the cloud lags 2–150 s behind the commands and flickers for up to 4 minutes
  - the car takes up to 12 minutes to wake
  - Zaptec asks for authorisation, reboots, and Home Assistant restarts
  - one to three cheap periods, and the real night of 16 Oct with "Lad kun til"
- Rules that must hold through every night:
  - never Charge now
  - no charging the plan does not want beyond 90 seconds
  - dear charging only for what the cheap hours cannot hold
  - with "Lad kun til", never on to the target
- Without the protections 80 of 80 nights fail; with them every night passes. 40 + 40 nights run with the tests, and
  more with `EVLEDGER_FUZZ_NIGHTS`.

## [0.9.5] - 2026-10-16

### Fixed

- **Pauses hold, also when the charger's cloud lags.**
  - Up to 5 minutes after our own stop, a "charging" from the charger is treated as old. It no longer starts Charge
    now, does not keep the plan charging through the pause, does not show as charging and does not send "Charging
    started". The stop is sent again.
  - On 16 Oct the stale "charging" also made the plan keep the charge going: continuing cost the same, so the plan
    started charging in the pause.
- **The answer "Lad kun til X %" is fixed for the plug-in.** It is kept as it was given and restored after a restart.
  Before, a restart before the car and the price forecast were back made the plan charge to the target again.
- New end-to-end tests play whole nights against a simulated Zaptec charger. They cover:
  - a cloud that lags behind the stop for a minute and for four minutes
  - flickers at every period end
  - an old "stopped" just after a start
  - restarts in the pause, also with an old "charging" at start-up
  - the waiting answer across a restart
  - a replay of the night of 16 Oct with its real quarter prices

  Four of them fail without the fix.

## [0.9.4] - 2026-10-16

### Fixed

- **A charger that is slow to report our stop no longer starts "Charge now".** At the end of a charging period the
  plan stopped the charger. Two seconds later Zaptec's cloud still reported "charging" for a minute, and the plan took
  that for a start from the app or the car. It switched to Charge now and charged to 100 % instead of the 68 % chosen
  with "Lad kun til". Up to 3 minutes after our own stop, a "charging" report is the charger catching up, and the stop
  is sent again. The same lag the other way, an old "stopped" just after our own start, is no longer taken for a stop
  from outside.

## [0.9.3] - 2026-10-16

### Fixed

- A fault in an extra can no longer stop smart charging. The extras are the price question, the phone messages,
  learned departures, the CO2 forecast, waiting for a cheaper day, the plan comparison, the tyre reminder and the
  entity updates. The plan still controls the charger, the ledger still records the charge, and the fault is logged
  once with its traceback instead of every minute.

## [0.9.2] - 2026-10-16

### Changed

- The answers to *billigere at vente?* say what they charge to: *Lad kun til 66 %* (or *Lad ikke i nat* when the car
  already has enough until the cheaper period) and *Lad til 80 %*, instead of *Vent med resten* / *Lad til målet*, so
  it is easy to judge whether waiting is enough. The message uses the same words.

## [0.9.1] - 2026-10-15

### Fixed

- The question *billigere at vente?* (and the one about going over the price cap) is asked once per plug-in. Before,
  every changed setting asked it again, also after it was answered, so a number stepped up and down sent it several
  times within seconds.
- *Message when charging starts* is sent once per plug-in plan. A plan split into several periods sent it again for
  every period that began more than half an hour after the last message.

## [0.9.0] - 2026-10-15

### Changed

- **Ask before dear charging, from what waiting saves and how much you drive.** With *Ask before dear charging* on
  (the old *Price limit in cheapest*), Cheapest still plans to the target. It now looks for the cheapest later period
  in the day and at night for each of the next three days after the ready-by time, using published prices and
  Strømligning's forecast. For each one it works out how far the battery must be charged tonight so that the daily
  driving (from the ledger's trips) still leaves *Always keep at least* (the old *Always charge to*) until then, and
  what charging only that much tonight and the rest later saves. The phones are asked once per plug-in when the best
  one saves at least *Ask when waiting saves at least* (new, 10 kr by default), with the level tonight, what the
  rest costs tonight, and the cheaper periods with their saving. *Wait with the rest* charges tonight up to that
  level and only in quarters no dearer than the later period; *Charge to the target* keeps the plan; no answer keeps
  the plan.
- *Fixed price limit* (the old *Cheapest max price*) is optional: 0 (the new default) means only the saving counts;
  above 0, quarters up to it are always charged tonight.
- The plan's sensor shows `price_limit` (the price above which waiting is cheaper), `limit_saving`, `limit_ask`,
  `daily_use_kwh` and each cheaper period's `need_soc`, `over_kwh` and `saving`.

### Added

- **Strømligning's forecast.** Its forecasts sensor is read next to today's and tomorrow's prices, so plans can look
  past tomorrow; those prices count as estimated. A cheaper later period is only taken from published prices or the
  forecast, never from a price borrowed from an earlier day.

## [0.8.2] - 2026-10-15

### Changed

- **Cheapest splits the charge for smaller savings.** A plan in several blocks is used when it saves at least 0.5 %
  (was 2 %) compared with one block, also when charging to the target past the price limit, and a running charge
  stops between the blocks when going on would cost more than that.

## [0.8.1] - 2026-10-15

### Fixed

- **Tomorrow's prices with a hand-picked Strømligning sensor.** When the smart charging step lists Strømligning's
  price sensor without its *tomorrow* binary sensor, the plan now reads that one too. Before, the night was planned on
  yesterday's prices (marked estimated), and with the price limit every estimated quarter counted as above it.

## [0.8.0] - 2026-10-15

### Changed

- **The price limit asks whether to wait, and the plan charges to the target meanwhile.** With *Price limit in
  cheapest* on, Cheapest plans to the target as without the limit. When part of that plan costs more than *Cheapest
  max price*, the car is above *Always charge to* and a later period after the ready-by time is cheaper (the best
  one in the day, 06–18, and the best one at night, each at or below the limit and saving at least 1 kr), the phones
  get one question per plug-in: how much energy is above the limit and at what price, how far the car gets under the
  limit, and when the cheaper periods are and what waiting saves. *Wait with the rest* charges only below the limit
  for this plug-in; *Charge to the target* keeps the plan. Without an answer it charges to the target. This replaces
  0.7.4's hard limit and 0.7.5's question.
- New switch *Wait above the price limit* (the answer, reset at the next plug-in). The plan's sensor shows
  `above_limit_kwh` (the energy above the limit), `above_limit_cost`, `limit_soc` (the level reached under the limit),
  `limit_wait` and `limit_cheaper` (the cheaper periods with price and saving).
- A plan split into several blocks is used when it saves at least 2 % (was 5 %) compared with one block.

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
