# samples/: hub outputs this repository parses

**No real capture exists in this environment.** Every file below is **SYNTHETIC**: written by `scripts/make_examples.py` from the mock world, in the shape this repository ASSUMES. Every parser that reads them is **`schema_unverified`**
(stated in the README and in tool output). Replace each synthetic file with a real capture, then run `python -m pytest tests/test_tools_and_hygiene.py -k samples`: a parser that cannot read the real shape fails there, and that is the point.

| hub output the repo parses | hub call to run | save the result as | parsed by |
|---|---|---|---|
| the answer of a generated script | `execute_luau` with the `luau` of `generate_check_script(check_id="boot", studios=..., dry_run=false)` in the open place, during play | `samples/execute_luau/boot-real.json` (the WHOLE answer exactly as the hub returned it, including any wrapper) | `playqa/domain/schema.py: extract_result` |
| the same, when the script's place guard stops it | run the same script with another place open (expect the hub's error text) | `samples/execute_luau/place-guard-real.json` | `extract_result` (verdict REFUSED) |
| the console | `get_console_output` right after the checks of one play session, before `roblox_studio_start_stop_play` stop | `samples/get_console_output/session-real.txt` (or `.json` if the hub returns structured entries) | `playqa/domain/console.py: parse` |
| the list of open places | `list_roblox_studios` | `samples/list_roblox_studios/open-real.json` | `guide_core.scope.match_open_studio` (accepts name/placeName/title, place_id/placeId/PlaceId, studio_id/studioId/id) |
| a screenshot | `screen_capture` after a failing check | the image anywhere you like; only its PATH is used (nothing reads the pixels) | `explain_failure(screenshots=[path])` |
| play start / stop | `roblox_studio_start_stop_play` | nothing to parse: the plan only names the call and its order | `playqa/domain/plan.py` |

Synthetic files shipped here: `execute_luau/boot-clean.json` (assumed shape: a `content` list with one text block), `execute_luau/boot-error.json` (assumed: a `result` string), `execute_luau/place-guard-refusal.json` (assumed: `isError` + `error` text),
`get_console_output/boot-error.txt` (Studio-like text lines with a time stamp and NO level tag: levels are inferred from the message), `get_console_output/boot-error-entries.json` (the LogService `{message, messageType, timestamp}` shape),
`list_roblox_studios/open-places.json`, `screen_capture/README.txt`.

What a real capture would settle: whether `execute_luau` returns the script's return value or only its printed output (the scripts do both: they `return` the JSON and `print` it after `PLAYQA_RESULT`), how the console marks levels and time stamps, what `list_roblox_studios` calls its keys, and in which context (server or client) `execute_luau` runs. Until then a wrong guess shows up as INCONCLUSIVE, not as a pass.
