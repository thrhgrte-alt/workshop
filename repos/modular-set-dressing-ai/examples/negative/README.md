# Negative examples (avoid these)

Each is stored with `"polarity": "negative"` and correction notes; search returns them as avoid-examples.

| Map | Why it is bad | What fixes it |
|---|---|---|
| `tavern_bare.png` | Empty: coverage far below range, no hierarchy | Place hero + supports, then dress accents |
| `tavern_crowded.png` | Overcrowded: coverage above range, no free space, collisions | Remove pieces; keep an open rectangle |
| `tavern_all_barrels.png` | One module copied everywhere | Vary modules; cap any one module's share |
| `tavern_blocked_path.png` | Props on the entrance path and across the bar sightline | Move them out of the corridors |
