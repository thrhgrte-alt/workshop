# economy.yaml (format econbal-economy/1)

```yaml
format: econbal-economy/1
id: my_place
simulation: {days: 14, seed: 0, policy: cheapest_payback}     # policy: cheapest_payback | cheapest_cost | spec_order
currencies: {coins: {start: 0}}
sources:  [{id: mining, currency: coins, per_minute: 10, unlock: null}]       # unlock: an upgrade id that switches the source on
sinks:    [{id: fuel, currency: coins, kind: upkeep, value: 1.0}]             # upkeep = per active minute; tax = fraction (0-1) of income
upgrades:                                                                     # same track + same tier = ALTERNATIVES (buying one forecloses the others)
  - {id: drill_1, track: drill, tier: 1, currency: coins, cost: 100, effects: [{source: mining, add: 10}], requires: [], min_rebirths: 0, strategy: speed}
boosts:   [{id: double, kind: gamepass, price_robux: 399, effects: [{source: "*", mult: 2}]}]
archetypes:
  regular: {session_minutes: 20, sessions_per_day: 3, efficiency: 1.0, variance: 0.0, boosts: [], rebirth: ladder_complete}   # casual and grinder too
rebirths: [{id: rebirth, currency: coins, cost: 1000000, resets: {tracks: [drill], currencies: [coins]}, bonus: {source: "*", mult_per_rebirth: 0.5}, max: 2}]
locked: ["upgrades.drill_1.cost", "sources.*", "rebirths.rebirth"]
```

- **Effects**: exactly one of `add` (flat, on that source, applied first) or `mult`. `source: "*"` is all sources and only allows `mult`. A rebirth bonus is `mult_per_rebirth` (1 + k n) or `mult_compound` (k^n).
- **Tiers** are contiguous from 1 within a track. **Ids** match `[A-Za-z][A-Za-z0-9_]{0,39}` (they end up in Luau).
- **Locked**: fnmatch globs over value paths, an entity prefix (`upgrades.drill_2` locks all its values), or `locked: true` on an entity. A pattern that matches nothing is an error (typo guard).
- **Value paths**: `upgrades.drill_1.cost`, `upgrades.drill_1.effects.0.add`, `sources.mining.per_minute`, `sinks.fuel.value`, `boosts.double.effects.0.mult`, `rebirths.rebirth.cost`, `archetypes.regular.efficiency`, ...
- Quote any YAML text that contains `: `.
