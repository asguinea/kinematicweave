# Empirical Evidence Gate

## 1. Purpose

This definition establishes the evidence required to accept project claims,
implementation phases, and milestones that depend on external systems or
empirical execution.

## 2. Required external evidence

When a project claim or phase depends on an external dataset, hardware run,
benchmark, experiment, or third-party system, obtaining and executing that
dependency is part of the phase.

Codex must actively attempt the required acquisition and execution. Data
acquisition, tool installation, storage consumption, and execution time are not
reasons to silently redefine required evidence as optional.

Resource constraints must first be handled through:

- bounded subsets;
- streaming;
- resumability;
- resource and disk-space preflight checks.

Codex must actively resolve ordinary acquisition, provider-data,
integration, tooling, and platform problems within the current batch. A failed
intermediate execution is diagnostic information, not automatically a human
checkpoint. Reasonable remediation attempts must be exhausted before a blocker
is declared.

## 3. Evidence classes

Reports must clearly distinguish:

- implementation evidence;
- fixture evidence;
- provider-data evidence;
- measured hardware evidence.

Synthetic data and project-created fixtures are supporting evidence only. They
cannot replace provider data when provider-data compatibility or real-world
results are required.

Provider incompatibilities must be investigated and corrected when they are
ordinary engineering problems. They are not a reason to substitute fixtures or
abandon required execution.

A phase or milestone cannot pass solely because its implementation exists.

## 4. Blockers and failure handling

Required evidence that cannot be obtained is a blocker.

When a required evidence gate is blocked:

1. the batch reports `FAIL`;
2. the exact missing requirement is stated;
3. attempted remedies and commands are reported;
4. later phases do not begin.

A blocker may be declared only when reasonable remediation attempts have
failed and completion requires a human decision or an unavailable external
resource. Final reports must enumerate intermediate problems and how each was
resolved.

`Not run`, `not configured`, and `not available` are never equivalent to
`PASS` for a required empirical gate.

## 5. Waivers

A requirement may be waived only by an explicit human decision recorded in the
decision log. Codex or the assistant may not create an implicit waiver.

## 6. Future batch prompts

Every future Codex implementation prompt must state:

- whether external data or execution is required;
- the exact evidence that gates completion.
