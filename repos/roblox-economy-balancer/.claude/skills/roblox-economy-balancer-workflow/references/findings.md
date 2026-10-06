# Finding codes

Severity and wording live in `rules/findings.yaml`; thresholds in `style/style.yaml` (`ranges`, `target_bands`). A verdict is `pass` when there is no error and no warning (info does not fail).

| Code | Meaning |
|---|---|
| `over_band`, `under_band` | step slower or faster than its band for the reference archetype |
| `wall` | step slower than `wall_factor` x band maximum (or `max_idle_minutes` where no band applies); also projected steps |
| `gap_cliff`, `cost_cliff` | a step takes, or costs, more than the allowed multiple of the previous step |
| `slow_for_archetype` | calendar days for one step above `max_days_per_step` for any archetype |
| `not_reached` | tier not reached in the horizon (info) |
| `dead_option`, `no_effect` | payback above `max_payback_minutes` for every archetype; upgrade without income effect |
| `zero_effect_gate` | a zero-effect upgrade gates later tiers (the policy cannot represent that honestly) |
| `dominant_option`, `dominated_option`, `dominant_strategy` | the choice is not a choice |
| `content_exhausted`, `runaway_inflation`, `stalled` | content runs out before `min_content_days`; unspent income in the tail after exhaustion; nothing affordable is reachable |
| `pay_to_win`, `free_cliff`, `boost_trivialises` | boost speeds a tier more than `max_boost_speedup`; free players slower than the band allows while boosted play is fine; boost makes a step trivial (info) |
