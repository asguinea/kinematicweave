# Phase 4 Qualitative Motion Evidence

## Scope

This evidence uses the frozen 300-scenario Batch 4.6 test cohort and the accepted
Batch 4.7-4.8 statistical and ablation artifacts. It does not rerun a codec,
change a threshold, rematch a budget, redetect an event, or alter an inferential
procedure. The 21 published examples were selected by fixed
machine-readable ranks before rendering.

## Visual interpretation

Adaptive segmentation changes where retained timestamps fall. The selected
overlays show position-bounded linear replay spending keyframes around temporal
motion changes, while uniform spacing can spend the same broad resource class
on low-change intervals. The adverse and median cases remain visible, so this
is not a favorable-only gallery.

RDP can trace a spatially plausible route while placing an agent incorrectly in
time because its geometric simplification objective does not bound
time-indexed interpolation error. The spatial path and synchronized error trace
therefore answer different questions.

Unconstrained Hermite can keep positions visually close while endpoint
derivatives create distorted speed, acceleration, braking, or event timing
between keyframes. The velocity-constrained hybrid repairs the largest recorded
velocity inflation by adding refinement where the frozen bound requires it.
That stronger guarantee can cost storage and can preserve semantics differently;
the evidence reports those outcomes separately.

The two principal KinematicWeave codecs have different operating roles.
Position-bounded linear is the direct temporal position-fidelity method.
The position/velocity hybrid is the stronger dynamics-control method when
velocity fidelity or a worst-case guarantee matters. Neither is declared a
universal winner.

## Failures and exceptions

The evidence includes smallest/adverse adaptive improvement, little hybrid
advantage, low semantic preservation, class coverage, and two accepted
opposite-direction city strata. These cases qualify the aggregate conclusions
without changing the frozen confirmatory analysis. Mechanism statements are
limited to recorded paths, primitives, errors, and event correspondence.

## Outputs

Eight release figures are committed in SVG and PNG form. A deterministic
8-figure manifest records labels, source artifacts, safe
example identifiers, sizes, and checksums. The additional replay compares
source, uniform linear, RDP, position-bounded linear, unconstrained Hermite, and
the hybrid over synchronized time. Because no system encoder was available, the
verified numbered PNG frames remain ignored while their assembly manifest and a
representative preview are committed.

## Decision

`qualitative_decision = "completed"`

Phase 4 qualitative motion evidence completed on deterministic frozen-test examples.
