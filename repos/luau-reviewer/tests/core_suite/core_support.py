"""Builds a tiny throwaway repository so the shared core can be tested without any domain code."""

from __future__ import annotations

import json
from pathlib import Path

from luaurev.core.manifest import base_schema
from luaurev.core.project import Project

KINDS = ("material", "reference")

EXAMPLES = [
    {"id": "stone-wall-a", "kind": "material", "title": "Chunky stone wall", "path": "examples/a.sbs",
     "description": "Chunky low-poly style stone wall with rounded bevels and cool grey palette",
     "license": {"owner": "user"}, "status": "curated", "polarity": "positive", "rating": 5,
     "tags": ["stone", "wall", "stylized"],
     "style": {"traits": ["chunky", "rounded-bevels"], "palette": ["#5c6670", "#8a96a3", "#2b3036"],
               "detail_density": "low"}},
    {"id": "wood-planks-b", "kind": "material", "title": "Warm wood planks", "path": "examples/b.sbs",
     "description": "Hand painted wood planks with warm brown tones",
     "license": {"owner": "user"}, "status": "curated", "polarity": "positive", "rating": 4,
     "tags": ["wood", "planks", "stylized"],
     "style": {"traits": ["hand-painted"], "palette": ["#8b5a2b", "#c68642", "#4a2f17"], "detail_density": "medium"}},
    {"id": "noisy-realistic-c", "kind": "material", "title": "Noisy photoreal rock", "path": "examples/c.sbs",
     "description": "Too noisy and too realistic for the target style",
     "license": {"owner": "user"}, "status": "curated", "polarity": "negative",
     "tags": ["stone", "photoreal", "noisy"], "correction_notes": ["reduce micro detail", "less realistic"]},
    {"id": "unreviewed-d", "kind": "material", "title": "Unreviewed stone import", "path": "examples/d.sbs",
     "license": {"owner": "unknown"}, "status": "candidate", "tags": ["stone"]},
]

STYLE = """\
version: 1
name: Toy style
summary: Chunky, readable, low-detail stylization.
traits:
  shape_language: [chunky, rounded]
  palette_range: cool greys and warm browns
  detail: low to medium
ranges:
  contrast:
    min: 0.2
    max: 0.9
    note: keep values readable
constraints:
  - {id: C1, severity: must, rule: Keep silhouettes simple.}
  - {id: C2, severity: should, rule: Prefer rounded bevels.}
exclusions: [photoreal noise]
correction_dimensions: [edge_wear, palette, detail_density]
"""

SKILL = """\
---
name: toy-skill
description: Toy skill used by tests. Use when testing skill validation.
---
# Toy skill
Do the toy thing.
"""


def make_project(root: Path) -> Project:
    (root / "library").mkdir(parents=True)
    (root / "library" / "manifest.schema.json").write_text(json.dumps(base_schema(KINDS)), encoding="utf-8")
    (root / "style").mkdir()
    (root / "style" / "style.yaml").write_text(STYLE, encoding="utf-8")
    (root / "AGENTS.md").write_text("# Toy agents file\nFollow the skill.\n", encoding="utf-8")
    (root / "skills" / "toy-skill").mkdir(parents=True)
    (root / "skills" / "toy-skill" / "SKILL.md").write_text(SKILL, encoding="utf-8")
    ex = root / "examples" / "library"
    ex.mkdir(parents=True)
    (ex / "assets.jsonl").write_text("\n".join(json.dumps(a) for a in EXAMPLES) + "\n", encoding="utf-8")
    return Project(name="toy-repo", package="toy", env_prefix="TOYTEST", root=root, domain="toy", asset_kinds=KINDS)
