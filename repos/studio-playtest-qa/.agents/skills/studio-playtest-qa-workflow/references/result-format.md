# playqa.result/1 (what a generated script returns)

```json
{"schema": "playqa.result/1", "check_id": "boot", "kind": "boot", "repeat_no": 1, "place": {"place_id": 0, "name": "..."},
 "refused": null, "assertions": [{"id": "services_exist", "subject": null, "ok": true, "measured": 4, "expected": 4, "detail": "all present"}],
 "measures": {}}
```

The script also prints `PLAYQA_RESULT <json>` and `PLAYQA_MARK <check> <repeat> start|end` (and `PLAYQA_PROBE <remote|economy|data> <label>`) so the console can be cut per check and per probe. An empty Luau table encodes as `[]`; readers treat `[]` as `{}`.
Script assertions are taken as reported; tool assertions are derived from `measures` and the thresholds in force. A script that declines to run sets `refused`.

What the tool accepts from the hub for an `execute_luau` answer (all `schema_unverified`): the result mapping itself; a JSON string; a mapping holding either under `result|output|returns|return|value|content|text|stdout|message`; a list of `{type: text, text: ...}` blocks;
a text containing a `PLAYQA_RESULT <json>` line. Anything else is an unreadable answer: the verdict is INCONCLUSIVE, never a failure of the game. The console may be text lines (optional `HH:MM:SS(.mmm)` stamp and level tag) or a list of `{message, messageType|type|level, timestamp|time|t}`.
