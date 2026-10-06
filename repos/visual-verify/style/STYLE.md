# Style: visual verification

This style is a set of **measurements and limits**, not an art direction. Every number is a PLACEHOLDER until you replace it.

- `style/style.yaml`: traits, rules for how results are worded, the correction dimensions you can use when you accept or reject a result.
- `style/thresholds.yaml`: every pass/fail limit and measurement setting, with its default, allowed range and largest single step. Each is a named, versioned parameter
  (`python -m visualverify learn list`); your accept/reject decisions can move them one bounded step at a time, only through propose, gate and your explicit approval.
- `projects/<project_id>/style.yaml` (optional): a per-project overlay of traits, constraints and exclusions on top of this file.

What these checks cannot tell you: whether an image is well drawn, anatomically right, in correct perspective, lit believably, or the thing that was asked for.
