# Workflow notes

- Start from the hero piece, then supports, then accents. `dress_region` is for accents: give it `density`, `include_tags`/`exclude_tags`, `weights`, optional `cluster`
  `{around, radius}`, `orient` (`random90`, `face_center`, ...) and a `seed`. Same seed + same scene = same plan; try several seeds and keep the best by metrics and by eye.
- `explain` says why each piece went where; `rejected` counts why candidates failed. If `attempts` hits the limit, relax `spacing` or `density`.
- Plans are small and reviewable. Prefer several small saved versions over one large one; `undo_last_change` always works and never deletes history.
- Debugging a refusal: the finding code names the cause (`collision`, `blocks_path`, `blocks_sightline`, `outside_region`, `in_exclusion`, `scale_out_of_range`, `unknown_module`).
- After the first Studio placement of a new kit, check orientation visually: a chair with `yaw 0` should face the same way its model's front faces.
