# evals/real: examples that were NOT written by the builder

Empty on purpose. The 146 tasks in `evals/tasks/` were written by the builder next to the code, on generated images with known properties. They show that the tool agrees with the values the generators
and hand calculations say it should produce; **they are not a measure of how well it serves real renders, screenshots or textures**. This folder is where real examples go, and it is reported separately
(`python -m visualverify eval-report`: "Real: 0 cases" until you add some; an empty set is never reported as a pass).

## How to add a real example

1. Put the image(s) somewhere inside a folder your project may read (`image_roots` in `projects.yaml`). Images stay on your machine: nothing in this repository uploads them. Do not commit private images.
2. Add a YAML file here (any name, `*.yaml`) holding a list of tasks in the same format as `evals/tasks/`, but with `source` left out (it is set to `real` on load) and an `op: tools_seq` input that points at your files by
   absolute path:

```yaml
- id: real-hero-silhouette-vs-approved-reference
  tags: [silhouette, real]
  input:
    op: tools_seq
    registry:                         # so the task reads your folder; the project id is yours
      version: 1
      projects:
        - {project_id: my-game, image_roots: ["/path/to/my/art"], places: [{place_id: main, studio_name: "My Game"}]}
    steps:
      - tool: silhouette_iou
        args: {candidate: "/path/to/my/art/hero_v3.png", reference: "/path/to/my/art/hero_approved.png", project_id: my-game}
  checks:
    - {type: step_ok, step: 0}
    - {type: approx, path: steps.0.result.measured.iou, value: 0.93, tol: 0.02}   # the number YOU checked another way
```

3. The expectation must be YOURS (a verdict you made, or a number you checked another way), not a copy of what the tool printed.
4. `python -m visualverify eval-report --label real1` runs both sets and reports them apart. `learn gate` also runs this folder, so a proposed threshold change that would flip one of your real cases is rejected.
