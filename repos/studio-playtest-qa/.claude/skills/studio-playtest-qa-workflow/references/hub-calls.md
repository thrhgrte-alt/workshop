# Hub calls (argument names are schema_unverified)

| step | hub tool | what to pass / save |
|---|---|---|
| list the open places | `list_roblox_studios` | pass its output as `studios` |
| start / stop play | `roblox_studio_start_stop_play` | start before the session's scripts, stop after the console was read |
| run a script | `execute_luau` | the `luau` of `generate_check_script(dry_run=false)`; save the raw answer to the file the plan names |
| read the console | `get_console_output` | save the text; pass it as `console_log` |
| look at the screen | `screen_capture` | only when an assertion failed or the console shows an error; save the image and pass its PATH as `screenshots` (the tool never reads the image) |

No real capture of any of these exists in this repository yet. `samples/README.md` says which call to run and where to save the answer so the parsers can be checked against a real one.
