# Workflow cheat sheet

| Step | Tool | Notes |
|---|---|---|
| scope | `get_style_brief`, `find_past_corrections` | both need project_id and place_id |
| import | `emit_import_luau` -> Studio `execute_luau` -> `normalise_import` | needs `studios`; draft spec with assumptions |
| check | `validate_economy_spec`, `check_economy` | one finding at a time |
| drill | `time_to_upgrade_table`, `flow_report`, `find_dominant_strategies`, `find_dead_options`, `monetisation_check` | `detail=true` only if needed |
| explain | `sensitivity` | one value, absolute or percent variants |
| fix | `propose_rebalance` | dry run first; smallest 1-2 changes; locked never changed |
| compare | `compare_specs` | allowed across places; analysis only |
| hand over | `export_values_luau` | new module, place guard, never overwrites |
| remember | `record_run`, `record_decision`, `suggest_band_adjustments`, `promote_run` | per place; `is_global` shares |

Budget: results are one summary line plus ranked findings. A full `check_economy` is about a thousand characters. If you need rows, ask for them; do not call five tools to build one table.

Tool groups: tools whose description starts `[rare]` can be disabled by the client (`ECONBAL_DISABLE_GROUPS=rare`); the daily loop (check, table, flow, propose, record) is core.
