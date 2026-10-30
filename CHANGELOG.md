# Changelog

What changed in each version of EV Ledger. A new version in `manifest.json` on `main` is published as a GitHub release
with its section below as the release notes. Versions before 0.7.2: see the
[releases](https://github.com/MRDonnii/evledger/releases).

## [0.15.1] - 2026-10-30

### Changed

- **The car's climate (preconditioning) goes over Bluetooth at home.** Tesla BLE's climate comes first while it reaches
  the car; out of Bluetooth range it is unavailable and Tesla Fleet, Teslemetry or Tessie take the command, Tesla Custom
  last. When the command fails, the next one is tried. Keeping the battery at the target while heating reads the climate
  from the same one. A climate chosen in the setup is still the only one used.

## [0.15.0] - 2026-10-30

### Added

- **Tesla BLE (the car over local Bluetooth) as one more of the car's sources.** With
  [Tesla BLE](https://github.com/yoziru/ha-tesla-ble) and an ESPHome Bluetooth proxy at home, its device ("Tesla" and
  the VIN's last 6 characters) is found by the VIN as the same car; nothing to set up. Battery, charging, lock, outside
  temperature and the plug sensor are read from it like from the other integrations: the one that reported last wins,
  which at home is Tesla BLE (every 10–60 s within range), and out of range the cloud integrations take over by
  themselves. Not its tracker (no position) and not its "charge energy added" (a total over all charges).
- **The charge limit goes over Bluetooth first**, then through Tesla Fleet, Teslemetry or Tessie, Tesla Custom last.
  When one of them fails (the car just left Bluetooth range, the cloud is down), the next one is tried.

### Changed

- **The car's other devices are also found by the VIN** (a device identifier or serial number), not only by the same
  name: plug sensors, charge limit, climate, "full at" time, the car refresh and the sources.

## [0.14.1] - 2026-10-28

### Fixed

- **A price of 0 in an operator's AFIR data is no price**, not free charging (a Verdo site wrote 0 kr/kWh "ad hoc", last
  updated in 2024). Free charging is still set with a zone or a named place.
- **Operator prices match whole words**: "OK" is OK a.m.b.a.'s price, not that of any operator with "ok" in its name.

## [0.14.0] - 2026-10-28

### Added

- **The operators' own stations and prices (EU AFIR data).** EV Ledger reads the Danish AFIR feeds: Spirii's (Spirii,
  PowerGo, Aura, Verdo, EDF, Looad, Shell Recharge …), OK's, EV-EL's and TotalEnergies' without a key, Monta's (EWII,
  Norlys, go'energi, NRGi, Uno-X …) with your own Monta Public API application, and Eco-Movement's (Clever, Circle K,
  Q8, Ionity …) with a token from Eco-Movement. A public charge at such a station gets the operator's price per kWh
  when it started (DC above 22 kW average, else AC), plus a start fee and a price per minute from the first minute.
  Price order: named place, zone, the operator's own price, an operator price set here, the default price. Charges
  keep `station_id`.
- **Options:** Monta client id and secret, Eco-Movement token (redacted in diagnostics).
- **`evledger/chargers`** returns these stations with their AC and DC price now, start fee, minute fees, the day's
  price range and the feed, plus the feeds' status (`afir`). An Open Charge Map station within 60 m of one is left out.

### Changed

- The feeds are fetched only when a map or a public charge needs them, at most every 6 hours, in the background, one
  site at a time; each tariff is kept once (Spirii's 654 were 105 different ones).

## [0.13.0] - 2026-10-27

### Added

- **"Which charger?"** When the car starts charging away from home with several operators within 150 m and none
  clearly nearest, the phones are asked which one, with a button per station ("CLEVER · 150 kW", "Circle K · 22 kW").
  The nearest within 40 m with the next twice as far counts as clearly nearest. The answer sets the place, operator and
  power, and a charge already priced by itself is priced again. Without an answer the nearest stays.
- **Fast charger prices.** `evledger.set_operator_price` takes `fast_price`. A station of 50 kW and up (its power comes
  from Open Charge Map or the Supercharger list) uses it. Charges keep `station_kw`.
- **Correct a charge's price per kWh.** `evledger.update_charge` takes `price_per_kwh` (the total becomes kWh × it),
  `entity_id` (any EV Ledger entity of the car) instead of `entry_id`, and `remember` (`place` or `operator`) to use the
  price for the next charges at that place, or as the operator's price (its fast price at 50 kW and up). A price set
  with `update_charge` is marked as typed in and is never replaced by an automatic price; a correction that is refused
  leaves the charge as it was. Correcting only the kWh of a charge priced by itself keeps its price per kWh.
- **"Ret kr/kWh" on the phone.** The message after a public charge priced by itself (a place's, the zone's, the
  operator's or the default price) says which price was used and has a "Ret kr/kWh" button: the answer "3,95" puts the
  price per kWh right, "Clever 3,95" also names and remembers the place.
- **`evledger/chargers`** (WebSocket): the stations around a point for a map. It returns Superchargers, Open Charge
  Map's stations (with the ledger's own key, which stays in Home Assistant), the named places and the zones with a
  price. Each has its price per kWh, its source, the operator's normal and fast price, power, connectors and the
  number of points.

### Fixed

- **Questions about charging away reached no phone with "only when home".** The phones are away when the car charges
  away. These messages now go to the phones within 2 km of the car, else to all chosen phones.

## [0.12.0] - 2026-10-27

### Added

- **Open Charge Map for public charging stations.** Put your own free API key in the options (sign in at
  openchargemap.org, My Profile → My Apps → register an application). A public charge that is not in a zone or at a
  Supercharger then gets the nearest station's name and operator within 300 m, e.g. "CLEVER – Bilka Herning". In
  Denmark the database names most stations, but the operator for only some (PowerGo, Tesla, Clever, Circle K, Ionity…;
  Norlys hardly). The key is redacted in diagnostics.
- **Operator prices, `evledger.set_operator_price`** (operator, price per kWh; part of the name is enough). A charge at
  that operator's station gets its price.
- **Named places.** When a public charge is given a name with its price (the phone answer "Clever 3,95", the dashboard
  form's place, or `log_public_charge` with `location_name`), the ledger remembers the place: a charge within 100 m
  later gets that name and price per kWh by itself. `evledger.forget_place` removes one. The order a public charge is
  priced in: a named place, the zone, the operator, the default price, else the phones are asked.
- Public charges keep their position (`latitude`/`longitude`, redacted in diagnostics) and `operator`. The default
  price number shows `operator_prices` and `places` (names and prices, no positions).
- A phone answer with a name and one number up to 15 (or "kr/kWh") is a price per kWh. Above that it is the charge's
  total. After a restart the answer uses the ledger's own kWh estimate.

## [0.11.0] - 2026-10-27

### Added

- **A default price for public charging, `number.<car>_default_public_price_per_kwh`** ("Standardpris ude pr. kWh"; 0 = off,
  the default). Tesla's Superchargers have one price around the clock. With the price set, a public charge is priced
  when it ends and counts in the monthly and yearly cost straight away. The kWh comes from the car's own "energy added"
  counter (Tesla Fleet `charge_energy_added`, Tesla Custom `energy_added`, found on the battery sensor's device) when it
  changed during this charge and agrees with the battery's gain within a factor of two. Otherwise the battery's gain
  divided by 0.95 is used, as before. Any price given later replaces it: the phone's answer, `log_public_charge` or the
  dashboard form within 12 hours. The phone message says the charge was saved with the default price.
- **The place of a public charge.** When it starts (or ends, if the car had no position), the charge gets the name of
  the Home Assistant zone the car is in, else of a Tesla Supercharger within 300 m ("Supercharger Søndervig"). The
  Supercharger sites come from supercharge.info's public list: fetched when a public charge first needs it, at most
  once a week, and kept in `.storage`.
- **Zone prices, `evledger.set_zone_price`** (zone, price per kWh; no price removes it). A public charge in such a
  zone gets the zone's price instead of the default price, e.g. 0 where charging is free at family. They are shown in
  the default price number's `zone_prices` attribute.
- Charges record where their price came from (`price_source`: `default`, `zone` or `manual`) and their `zone`.

### Fixed

- **A hanging car integration no longer keeps a charge running.** On 27 Oct 2026 Tesla Custom stopped updating at a
  Supercharger. It said "charging" (and the position there) for over an hour after the charge ended and the car had
  left, while Tesla Fleet already said "disconnected". The ledger kept the public charge open and the plan showed it
  charging. For the charging state, the battery, the position, the lock and the outside temperature, the ledger now
  reads every one of the car's integrations: Tesla Custom, Tesla Fleet, Teslemetry or Tessie devices with the car's
  name. It uses the one that reported last (`last_reported`). The odometer stays with the configured entity, because
  two integrations' counters differ a little and switching would make trips out of nothing. The car's "energy added"
  counter for a public charge is chosen the same way.

## [0.10.4] - 2026-10-19

### Added

- **`binary_sensor.<car>_plugged_in`** ("Sat til"): whether the car is plugged in, the way the plan sees it. Where EV
  Ledger has a charger, the charger decides, together with whether this car is the one on it. Otherwise the car's own
  plug sensors decide, and any one of them saying plugged in is enough. On 19 Oct 2026 Tesla Custom's charger sensor
  stayed "off" for a day while the car was plugged in. EV Ledger itself saw the car, but the dashboard cards that read
  that one sensor said "Ikke sat til". Dashboards and automations can now use this sensor. Attributes: `charger`,
  `car_says_plugged`, `car_plugged_since` and `car_sensors`.

## [0.10.3] - 2026-10-18

### Fixed

- **No "Laderen lader sig ikke stoppe" for a full car.** On 18 Oct 2026 the car reached 100 % at 04:58 and the plan
  stopped the charger. Zaptec's cloud then said "charging" (with the old power) for 6.8 minutes. A few more minutes
  would have sent a false alert at five in the morning. A car at 100 % or at its own charge limit cannot charge on,
  so it gets no alert. The stop is still repeated until the charger says it has stopped.

## [0.10.2] - 2026-10-17

### Fixed

- **A running charge was stopped for a few øre when there were two ready-by times.** On 17 Oct 2026 the week plan
  wanted 100 % on Saturday at 08:00 and the usual weekday time 06:45 kept the minimum level. A ready-by time like
  that, which the battery already met, switched off three things: the single charging window, the check that a split
  plan really pays, and the guard that keeps a running charge going. The plan then wavered every few minutes between
  one period and two while the car charged. It was planned to stop at 04:45 and finish at 05:45 for 1.6 øre (the car
  was full first). The brother's car really stopped at 05:00 and started again at 05:30. A ready-by time the battery
  already meets now changes nothing. With several ready-by times that each still need energy, the running charge also
  goes on as long as that meets all of them.
- **A running charge is only paused when that saves at least 2 % and 0.25 (kr).** A stop costs a wake-up at the next
  start, and stopping a charge for øre is not wanted. The old limit of 0.5 % or 0.02 still holds for a start that is
  planned but has not begun.

### Tests

- Replays of both cars' nights of 17 Oct with the real quarter prices and the week plan (both fail on 0.10.1).
- The random nights now also run with the week plan and two ready-by times.

## [0.10.1] - 2026-10-16

### Fixed

- **A battery sensor at `nan` or `inf` stopped EV Ledger from starting.** Home Assistant then showed the entry as
  failed. Numbers from other integrations are now read in one way: `nan`, `inf`, text and numbers too large count as
  unknown, just like `unknown`. This covers the battery level, range, the car's charge limit, its position, the
  charger's power and session energy, the price now, the prices in the list, and Monta's cost.
- **A charger power of `nan`** made the home charging power sensor fail on every update.
- **A price with an end time years ahead** (a broken price list) was split into millions of quarters at every new
  plan, which would freeze Home Assistant. One price now covers one day at most.
- **`nan` set on an EV Ledger number** (from a script or an automation) broke the plan. The numbers now turn it away
  with a message, and so does the plan itself. The actions `log_public_charge`, `add_charge`, `update_charge` and the
  tyre actions only take kWh, prices and kilometres within sensible limits.
- **Failing services of other integrations** were logged as errors. Now they are logged as warnings, whatever the
  error: a phone that cannot be reached, a car that does not answer an update, a climate or calendar call that times
  out. A calendar answer of an odd shape no longer raises an error, and a failed message is not sent again.
- **A price answer from the phone with absurd numbers** (hundreds of digits, 0 kWh or more than 300 kWh) is asked
  again, not saved.

### Tests

- New tests feed EV Ledger broken values on every input it reads, and services of other integrations that fail in
  every way.
- A chaos test sets all inputs to random good and broken values for 70 steps per run, while the user picks plans,
  sets numbers, toggles switches and presses buttons. No error may be logged, and no entity may show `nan` or
  infinity.
- Property tests (Hypothesis) check that the charge plan is sane for thousands of random inputs, and that the price
  parser survives price lists of any shape.
- New tests cover the charger control through a plain switch.

## [0.10.0] - 2026-10-16

### Added

- **The car's wake-up time is measured and planned for.** EV Ledger measures how long the car takes from the first
  start to drawing power (the last 10 starts, kept across restarts). The plan counts that time as lost at the start of
  every period that is not already charging: 5 minutes until three starts are measured, then the third quartile of
  the measurements (0.5–20 minutes). Periods are made long enough to reach the target anyway. A plan in several
  periods is compared with the single period with the wake-ups counted, so it only splits when that still pays. A few
  missing minutes make a period longer rather than opening a new one: a new period must give at least 15 minutes.
  The charge mode select shows `start_delays` and `wake_minutes`. (14 Oct 2026: the car took ten minutes.)
- **Ready ahead of time** (`number.<car>_ready_ahead_of_time`, 15 minutes by default): the plan is done this long
  before each ready-by time when the slots before that can hold the target. When the time is too short, it charges
  up to the ready-by time as before.
- **Home Assistant away in the night.** The time Home Assistant was last alive is saved with its restore data (every
  15 minutes and when it stops; no extra recorder rows). When a planned period should have started while it was away
  for more than 20 minutes, the plan is made again at once with the time left and catches up. The phones are told
  what the new plan reaches by the ready-by time, without sound at night.
- **A charger that is offline** for 10 minutes is now also reported before the plan's next period while the car says
  it is plugged in, not only once the period has started.
- **A stop that does not take:** when the charger still charges 10 minutes after the plan stopped it (repeated every
  45 s meanwhile), the phones are told.

### Changed

- Forecast prices for a later period count as 10 % dearer when working out whether waiting with the dear rest pays,
  and up to which price tonight's quarters are charged. Forecasts miss, and waiting must still pay when they do.

### Tests

- New unit tests cover the wake-up model, the margin, the forecast margin, the offline and stop alerts, and an outage
  with catch-up. Each new feature's test fails without the feature.
- The random nights now also give every car its own wake-up time (half the nights with earlier measurements), take
  Home Assistant away for 30–150 minutes on a quarter of them, and require the target by 06:40, inside the margin.
  The rules hold through every night: never Charge now, no charging the plan does not want, with *Lad kun til* never
  on to the target, and dear charging only for what the cheap hours cannot hold. While Home Assistant is away, the
  charger's own doing is not counted.

## [0.9.7] - 2026-10-16

### Fixed

- **The night the clocks go back (25 Oct 2026).** The plan's quarters were kept in local time. In the hour that comes
  twice, Python compares and sorts local times by the clock alone, so 02:15 the second time equalled 02:15 the first.
  The plan made a charging period of no length. The quarters are now kept in UTC and only shown in local time.
  Tested on both nights the clocks change (25 Oct 2026 and 28 Mar 2027) with random cloud lag. Before, every autumn
  night failed.
- **No plug-in reminder at night.** The reminder came half an hour before the plan's cheapest start, also at midnight
  and 04:30 (the brother's phone). Inside the silent window (22–07 by default, also when silent messages are off) none
  is sent; the evening reminder covers a night start. A reminder is kept across restarts, so it is not sent again.
- A charger command that fails with an error of any kind is logged, and the controller sends it again.

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
