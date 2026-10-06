"""Tools every repository exposes: library search, style brief, feedback, curation."""

from __future__ import annotations

from typing import Any

from . import feedback as fb
from . import retrieval
from .embeddings import load_embedder
from .manifest import LibraryStore
from .mcpkit import ToolSpec
from .project import Project
from .style import load_style, style_brief


def default_embedder(project: Project):
    name = project.env("EMBEDDER") or ("hashing-512" if project.embeddings_file.exists() else None)
    return load_embedder(name)


def common_tools(project: Project, correction_dimensions: list[str] | None = None) -> list[ToolSpec]:
    store = LibraryStore(project)

    def search_library(
        query: str = "",
        kind: str | None = None,
        tags: list[str] | None = None,
        traits: list[str] | None = None,
        palette: list[str] | None = None,
        k: int = 5,
        include_candidates: bool = False,
    ) -> dict[str, Any]:
        """Find the most relevant curated reference examples (and 'avoid' examples) for a task.
        Combines exact filters, tags, text search, palette similarity and optional embeddings.
        Returns small summaries with the reason each was retrieved; load files only for the few you need."""
        filters = {"kind": kind} if kind else None
        return retrieval.search(store, query, filters=filters, tags=tags or (), traits=traits or (),
                                palette=palette or (), k=max(1, min(k, 20)), embedder=default_embedder(project),
                                include_candidates=include_candidates)

    def get_style_brief(focus: list[str] | None = None, max_chars: int = 1800) -> dict[str, Any]:
        """Return the compact style specification (traits, must/should rules, things to avoid).
        Pass 'focus' keywords (e.g. ['palette', 'edge']) to list the relevant traits first."""
        style = load_style(project)
        return {"brief": style_brief(style, focus or (), max_chars), "ranges": style.get("ranges", {}),
                "correction_dimensions": style.get("correction_dimensions", [])}

    def find_past_corrections(request: str, k: int = 5) -> dict[str, Any]:
        """Past user corrections most relevant to a new request. Read these before planning."""
        return {"corrections": fb.corrections_for(project, request, k)}

    def record_run(request: str, constraints: dict | None = None, retrieved: list[str] | None = None,
                   tools: list[str] | None = None, recipes: list[str] | None = None,
                   outputs: list[str] | None = None, preview: str | None = None,
                   model: str | None = None) -> dict[str, Any]:
        """Save what was requested, retrieved, used and produced. Returns a run_id for later feedback."""
        return {"run_id": fb.record_run(project, request=request, constraints=constraints, retrieved=retrieved,
                                        tools=tools, recipes=recipes, outputs=outputs, preview=preview, model=model)}

    def record_decision(run_id: str, decision: str, reason: str = "", corrections: list[dict] | None = None,
                        rating: int | None = None, promote_requested: bool = False) -> dict[str, Any]:
        """Store the user's verdict (accept/reject/revise) with structured corrections:
        [{"dimension": ..., "direction": "more|less|change", "note": ...}]. Only call with the user's actual words."""
        return fb.record_decision(project, run_id, decision, reason=reason, corrections=corrections, rating=rating,
                                  promote_requested=promote_requested, allowed_dimensions=correction_dimensions)

    def promote_run(run_id: str, kind: str, title: str, description: str, tags: list[str], path: str,
                    polarity: str = "positive", confirm: bool = False) -> dict[str, Any]:
        """Add a reviewed run to the library. Without confirm=true it only creates an unreviewed *candidate*,
        which default searches ignore. Ask the user before setting confirm=true."""
        asset = fb.promote_run(project, run_id, kind=kind, title=title, description=description, tags=tags,
                               path=path, polarity=polarity, confirm=confirm)
        return {"asset_id": asset["id"], "status": asset["status"]}

    return [
        ToolSpec("search_library", search_library, search_library.__doc__),
        ToolSpec("get_style_brief", get_style_brief, get_style_brief.__doc__),
        ToolSpec("find_past_corrections", find_past_corrections, find_past_corrections.__doc__),
        ToolSpec("record_run", record_run, record_run.__doc__, read_only=False, idempotent=False),
        ToolSpec("record_decision", record_decision, record_decision.__doc__, read_only=False, idempotent=False),
        ToolSpec("promote_run", promote_run, promote_run.__doc__, read_only=False, idempotent=True),
    ]
