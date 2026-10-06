# The simulation model

Everything is in `econbal/domain/sim.py` (read its docstring). Summary:

1. Time is **active minutes**. A day is `sessions_per_day x session_minutes` active minutes. No offline time.
2. Income of a source = `(per_minute + sum of add effects) x product of mult effects x rebirth multiplier`, times the archetype's `efficiency`. A source with `unlock` earns nothing until that upgrade is owned.
3. `tax` removes a fraction of gross income; `upkeep` removes a fixed amount per minute but never more than the balance.
4. The player picks ONE target by policy and **saves for it**. `cheapest_payback`: payback = cost / (rise of net income per minute the upgrade causes). Ties break on cost, then spec order. Upgrades with no effect rank last. A rebirth ranks after income-improving upgrades.
5. A purchase happens at the end of the first whole minute whose balance covers the cost; leftovers carry over.
6. Boosts owned by the archetype apply from minute 0. `boost_mode` none/all gives the free and fully boosted runs.
7. Rebirth: when every track in `resets.tracks` is complete, the rebirth becomes a target; buying it pays the cost, removes the upgrades of those tracks, resets the listed currencies to their start value (leftover counted as the sink `rebirth_reset`) and raises the multiplier.
8. `variance` > 0 multiplies each session by `1 + variance x (2u - 1)`, u from `random.Random("<seed>:<archetype>")`. With 0, nothing random exists.
9. Time to next upgrade = minutes since the previous purchase (any track). Steps not finished in the horizon are projected from current income.

Limits: relative pacing only; archetypes are assumptions; no events, trading, other players, backpack limits or luck.
