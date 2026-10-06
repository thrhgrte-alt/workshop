# Reading the rubric

`evals/rubric_environment.yaml` and `evals/rubric_prop.yaml` (props add `silhouette_clear`). Pass mark 0.7, and every `required` criterion must pass.

| Criterion | Method | Auto part | Person part |
|---|---|---|---|
| `not_degenerate` | auto, required | not flat, not tiny, real variation | - |
| `locks_respected` | auto, required | focal box contrast and edge-energy centre (1.0 when nothing is locked) | - |
| `style_cohesion` | hybrid, required | palette adherence to the direction's palette | one coherent style? |
| `readability` | hybrid, required | heuristic from value range, value spread, edge density | focal point clear? |
| `brief_adherence` | manual, required | - | does it show what was asked? |
| `novelty_vs_library` | auto | 1 - similarity to the nearest curated image (unscored if the library is empty) | - |
| `usability_as_reference` | manual | - | could a modeller build from it? |
| `silhouette_clear` (props) | hybrid | components and fill of the thresholded silhouette | reads in black? |

**Hybrid = the lower of metric and person.** A good metric cannot rescue a human "no", and a human "yes" cannot hide a bad metric. Scores are judgments, not ground truth: report them as
"scored X by <who>", never as measured quality. `complete: false` means something is unscored; do not report a final verdict then.
