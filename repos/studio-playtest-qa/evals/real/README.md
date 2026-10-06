# evals/real/: examples that were NOT written by the builder

This folder ships EMPTY on purpose. The evals in `evals/tasks/` were written by the builder next to the code: they only show that the tool agrees with itself. They are reported as **self-written**, never as real-world precision.
Anything placed here is reported **separately** (`python -m playqa eval-report`), and an empty folder is reported as **0 real cases**, not as a pass.

## How to add a real example

1. Run a real playtest in Studio through the hub (see `samples/README.md`). Save the raw `execute_luau` answers, the console text and, if any, the screenshot path.
2. Write one YAML file here (any name ending `.yaml`) with tasks in the same format as `evals/tasks/*.yaml`, using the `judge_docs` op so that YOUR files are the input:

```yaml
- id: real-boot-error-seen-in-my-place          # unique; must not clash with a self-written id
  tags: [real]
  input:
    op: judge_docs
    place: demo_mine/main                       # a place registered in projects.yaml (use your own registry via PLAYQA_PROJECTS)
    check: boot
    results: [ ...paste the raw answer here (JSON), or one string... ]
    console: |
      ...paste the console text here...
  checks:                                       # what a person who watched the playtest knows to be true
    - {type: equals, path: verdict, value: fail}
    - {type: list_contains, path: failed, items: [console_no_errors]}
```

3. Run `python -m playqa eval-report --label mine`. The report lists self-written and real counts apart. A real example that fails is the most useful thing this repository can learn from: it means a parser or a threshold disagrees with what really happened.

The expectations must come from what actually happened in the game, not from running this tool and pasting its output.
