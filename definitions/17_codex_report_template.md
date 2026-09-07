# Codex Batch Report Template

Copy this template into the final response for every implementation batch.

---

# Batch `<number>` — `<name>`

## Status

`PASS | PARTIAL | BLOCKED | FAIL`

## Objective

Briefly restate the approved objective of this batch.

## Scope completed

Describe the work completed within the approved scope.

## Files added

- `path/to/file`
- `path/to/file`

Write `None` when applicable.

## Files modified

- `path/to/file`
- `path/to/file`

Write `None` when applicable.

## Files deleted

- `path/to/file`

Write `None` when applicable.

## Implementation details

Summarize the important implementation choices.

Include:

- public interfaces;
- data-flow changes;
- validation behavior;
- deterministic behavior;
- configuration changes;
- serialization or schema effects;
- error handling.

## Dependencies added or changed

List each dependency and why it was needed.

Write `None` when applicable.

## Tests added or updated

List:

- test files;
- tested behaviors;
- boundary and failure cases;
- deterministic or regression checks.

## Commands executed

```text
command 1
command 2
command 3
```

## Test and check results

Report the result of each executed check.

Example:

```text
Formatting: PASS
Lint: PASS
Type checking: PASS
Unit tests: 84 passed
Integration tests: 6 passed
Smoke test: PASS
```

Include failures, skips, and unavailable checks.

## Generated artifacts

List any generated fixtures, manifests, reports, figures, or result files.

Write `None` when applicable.

## Resource observations

Report relevant measurements or state:

`Not measured; not required for this batch.`

Include runtime, RAM, VRAM, disk, workers, or batch size when relevant.

## Definition files consulted

- `definitions/...`
- `definitions/...`

## Definition conformance

State whether the implementation conforms to the consulted definitions.

## Deviations

Describe every deviation from the definitions.

Write `None` when applicable.

## Known limitations

List limitations that remain within the implemented behavior.

Write `None` when applicable.

## Outstanding issues

List unresolved issues, failed checks, blockers, or follow-up work required before accepting the batch.

Write `None` when applicable.

## Suggested next action

State one of:

- ready for human review;
- fix the listed issues under the same batch;
- resolve the blocker before continuing.

---

## Reporting rules

- Do not omit failed tests.
- Do not mark the batch PASS when required work is incomplete.
- Do not include work from the next batch.
- Do not rewrite the approved objective.
- Keep file paths repository-relative.
- Be specific enough that the report can be reviewed without inspecting every diff.
