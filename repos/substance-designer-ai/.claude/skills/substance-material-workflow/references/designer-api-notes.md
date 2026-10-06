# Designer API notes: what is verified and what is not

This repository was written **without access to a Substance 3D Designer installation**. Be exact about that.

## What is known to be true (from Adobe's documentation, confirmed when written)
- Designer has a Python scripting API (`import sd`), documented in Adobe's scripting API reference.
- Nodes are created with `graph.newNode('<definition>')`, for example `sbs::compositing::bitmap`.
- `SDValue*` classes wrap values (for example `SDValueFloat4`), and connections are created through
  `SDNode` methods (`newPropertyConnection...`).

## What is assumed (generated scripts follow this shape; unverified)
- `sd.getContext().getSDApplication().getPackageMgr()`, `.newUserPackage()`, `.savePackageAs(...)`
- `SDSBSCompGraph.sNew(package)`, `graph.setIdentifier`, `graph.newInstanceNode(resource)`
- `node.getPropertyFromId`, `node.setInputPropertyValueFromId`, `node.newPropertyConnectionFromId`
- `node.setAnnotationPropertyValueFromId('identifier', ...)` for output identifiers (output *usages* are not set)
- `app.getQtForPythonUIMgr().getCurrentGraph()` for the open graph
- Every node definition, slot id and parameter id in `recipes/node_catalog.yaml`

## How to turn assumptions into facts (the probe)
1. `python -m sdai probe-script > probe_designer.py`
2. Run it inside Designer (Python console / script runner). It reports the version, which API members exist,
   and for each catalog node the real input, parameter and output ids.
3. `python -m sdai catalog-diff <result.json>` lists mismatches and what is available instead.
4. Fix `recipes/node_catalog.yaml`, set `verified: true` only for entries the diff confirms, repeat until clean.
5. `inspect_environment` then reports `probe_clean: true`.

## When a generated script fails
The script writes its result JSON even on failure: `errors` has the message, `traceback` the stack. Typical
causes: a wrong id (fix the catalog), a missing library graph (load Designer's library packages), or an API
call that differs on this version (adjust `sdai/domain/scriptgen.py` and re-run `python -m pytest`).
Report the exact error to the user; do not retry with guessed ids.

## What the fake Designer in `tests/fake_designer.py` proves
That the generated script's logic is internally coherent against the *assumed* API. It does not prove the
assumed API is real.
