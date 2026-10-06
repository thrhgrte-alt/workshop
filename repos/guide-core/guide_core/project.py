"""Project context: where a repository keeps its public and private data."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Project:
    name: str  # repository name, e.g. "substance-designer-ai"
    package: str  # python package, e.g. "sdai"
    env_prefix: str  # environment variable prefix, e.g. "SDAI"
    root: Path  # repository root
    domain: str  # short domain label
    asset_kinds: tuple = ()

    def env(self, key: str, default: str | None = None) -> str | None:
        return os.environ.get(f"{self.env_prefix}_{key}", default)

    # --- private, local, git-ignored data -------------------------------
    @property
    def workspace(self) -> Path:
        """Private working data (user library, feedback, versions, output)."""
        return Path(self.env("WORKSPACE") or (self.root / "workspace")).expanduser()

    @property
    def library_file(self) -> Path:
        override = self.env("LIBRARY")
        return Path(override).expanduser() if override else self.workspace / "library" / "assets.jsonl"

    @property
    def embeddings_file(self) -> Path:
        return self.library_file.parent / "embeddings.jsonl"

    @property
    def feedback_dir(self) -> Path:
        return self.workspace / "feedback"

    @property
    def versions_dir(self) -> Path:
        return self.workspace / "versions"

    @property
    def output_dir(self) -> Path:
        return self.workspace / "output"

    # --- tracked, shareable data ----------------------------------------
    @property
    def examples_library_file(self) -> Path:
        return self.root / "examples" / "library" / "assets.jsonl"

    @property
    def schema_file(self) -> Path:
        return self.root / "library" / "manifest.schema.json"

    @property
    def style_file(self) -> Path:
        return self.root / "style" / "style.yaml"

    @property
    def asset_root(self) -> Path | None:
        value = self.env("ASSET_ROOT")
        return Path(value).expanduser() if value else None

    @property
    def allowed_roots(self) -> list[Path]:
        """Directories tools may write into. Override with <PREFIX>_ALLOWED_PATHS."""
        raw = self.env("ALLOWED_PATHS")
        if raw:
            return [Path(p).expanduser().resolve() for p in raw.split(os.pathsep) if p]
        return [self.workspace.resolve()]
