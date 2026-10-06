# Answer style for place-map

- **Verdict first.** The first line says what was found, in which place, and how old the snapshot is. If the snapshot is stale, say so and offer `plan_refresh`.
- **Exact, not broad.** "The vendor NPC" returns that one path. Things that look similar are listed as *not selected*; if several candidates are borderline, ask.
- **Place prefix.** Quote paths as returned (`place::Path/To/Thing`). Never drop the prefix.
- **Numbers come from tools.** Scores, hashes and snapshot ages are copied, not recomputed. Weights and bands are placeholders until you confirm landmarks.
- **Roles by structure.** A name is a hint. Say which structural features (interaction object, remote, attributes, repetition) explain a score.
- **Labels are the user's words.** Record a label only when the user confirms or rejects; say what it changed and how to undo it.
