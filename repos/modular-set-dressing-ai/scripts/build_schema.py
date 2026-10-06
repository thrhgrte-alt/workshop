#!/usr/bin/env python3
"""Regenerate library/manifest.schema.json from the domain definition. Use --check in CI."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from setdress import KINDS, project
from setdress.core.manifest import base_schema
from setdress.domain.schema import DOMAIN_SCHEMA

target = project().schema_file
text = json.dumps(base_schema(KINDS, DOMAIN_SCHEMA), indent=2) + "\n"
if "--check" in sys.argv:
    sys.exit(0 if target.exists() and target.read_text(encoding="utf-8") == text else 1)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(text, encoding="utf-8")
print(f"wrote {target}")
