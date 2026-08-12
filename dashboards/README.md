# EV Ledger dashboard examples

Ready-to-copy Lovelace cards, built entirely from **native Home Assistant
card types** — no extra HACS cards needed.

## [`example-view.yaml`](example-view.yaml)

A full example view: glance tiles (charging status, cost/km, totals), last
trip / last charge tiles, an efficiency tile, a nag banner that only appears
when a public charge needs a price, native HA Statistics-graph month-over-
month cost/distance charts, and Jinja-templated recent-trips / recent-charges
tables.

**To use it:**

1. Find your vehicle's entity slug — Settings → Devices & services → EV
   Ledger → your vehicle → any entity's ID is `sensor.<slug>_something`.
2. Copy the file's contents, replace every `energitte` with your slug.
3. Either paste the whole thing as a masonry view's `cards:` list (dashboard
   → Edit → raw configuration editor), or copy individual cards into an
   existing view.

Want a fancier version with `custom:button-card` or similar? These are
deliberately kept to built-in cards so they work for everyone out of the
box — but the entity list and attribute shapes (see the main README's
"Dashboard sensors" table) are exactly what you'd template against for a
richer version. PRs adding alternate styles are welcome.
