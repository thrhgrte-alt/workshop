"""Summarize a ``.sbs`` file so the library knows HOW a material was built, not just how it looks.

``.sbs`` files are XML. This reader is deliberately tolerant: it collects what it can recognise
(graph identifiers, node counts, atomic filter names, instanced library graphs, exposed parameters)
and returns ``warnings`` for anything unexpected instead of failing. The element names used here
(``graph``/``identifier``, ``compNode``, ``filter``, ``path``, ``paramsArrayCells``) follow the
structure of typical files but were written without a large corpus to test on - if your files differ,
the summary will simply be thinner and say so in ``warnings``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path


def _v(elem: ET.Element | None) -> str | None:
    return None if elem is None else elem.attrib.get("v")


def summarize_sbs(path: str | Path) -> dict:
    path = Path(path)
    warnings: list[str] = []
    try:
        # sbs files come from the user's own library, but never resolve external entities anyway
        parser = ET.XMLParser()
        root = ET.parse(path, parser=parser).getroot()
    except (ET.ParseError, OSError) as exc:
        return {"file": str(path), "ok": False, "warnings": [f"could not parse as XML: {exc}"], "graphs": []}

    graphs = []
    for graph in root.iter("graph"):
        ident = _v(graph.find("identifier")) or "<unnamed>"
        filters, instances = Counter(), Counter()
        node_count = 0
        for node in graph.iter("compNode"):
            node_count += 1
            f = _v(node.find(".//compFilter/filter")) or _v(node.find(".//filter"))
            inst = _v(node.find(".//compInstance/path"))
            if inst:
                instances[inst.split("?")[0].replace("pkg:///", "").replace("pkg://", "")] += 1
            elif f:
                filters[f] += 1
        outputs = [_v(o.find("identifier")) for o in graph.iter("compOutput")]
        exposed = [_v(p.find("identifier")) for p in graph.iter("paramInput")]
        if node_count == 0:
            warnings.append(f"graph '{ident}': no compNode elements found (non-compositing graph or unfamiliar layout)")
        graphs.append({
            "identifier": ident,
            "node_count": node_count,
            "atomic_filters": dict(sorted(filters.items())),
            "library_instances": dict(sorted(instances.items())),
            "outputs": [o for o in outputs if o],
            "exposed_parameters": [p for p in exposed if p],
        })
    if not graphs:
        warnings.append("no <graph> elements found")
    return {"file": str(path), "ok": bool(graphs), "graphs": graphs, "warnings": warnings}


def summary_to_manifest_fields(summary: dict) -> dict:
    """Turn a summary into the manifest ``domain`` + searchable tags (the node names become tags)."""
    tags: set[str] = set()
    outputs: set[str] = set()
    exposed: list[str] = []
    total_nodes = 0
    for g in summary.get("graphs", []):
        tags.update(f"node:{name.split('::')[-1]}" for name in g["atomic_filters"])
        tags.update(f"node:{name.rsplit('/', 1)[-1]}" for name in g["library_instances"])
        outputs.update(g["outputs"])
        exposed.extend(g["exposed_parameters"])
        total_nodes += g["node_count"]
    return {"tags": sorted(tags), "domain": {"graph_summary": {"nodes": total_nodes, "graphs": len(summary.get("graphs", []))},
                                              "exposed_parameters": exposed}}
