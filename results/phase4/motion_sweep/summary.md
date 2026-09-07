# Phase 4 Matched-Budget Sweep Engine

- Evidence class: exploratory development evidence
- Frozen subset: 25 genuine AV2 development scenarios
- Parameter points: 39
- Scenario/configuration executions: 975
- Failures: 0
- Verified second-pass checkpoint reuses: 975
- Pilot/test cohorts accessed: no
- Final method selected: no

## Explored Ranges

| Family | Byte ratio range | Keyframe ratio range | Position mean range (m) |
|---|---:|---:|---:|
| `exact_adjacent` | 2.540895-2.540895 | 1.000000-1.000000 | 0.000000-0.000000 |
| `fixed_interval_linear` | 0.518874-1.420393 | 0.123716-0.514483 | 0.005245-0.106049 |
| `position_bounded_linear` | 0.451208-0.892510 | 0.092569-0.278976 | 0.009689-0.082209 |
| `position_velocity_hybrid` | 0.436146-0.686427 | 0.085630-0.190370 | 0.015700-0.058716 |
| `raw_samples` | 1.000000-1.000000 | 1.000000-1.000000 | 0.000000-0.000000 |
| `rdp_linear` | 0.316543-0.470828 | 0.039791-0.101194 | 0.260372-1.304637 |
| `unconstrained_hermite` | 0.426222-0.833073 | 0.081573-0.252360 | 0.007706-0.058304 |
| `uniform_hermite` | 0.518626-1.421155 | 0.123716-0.514483 | 0.005498-0.100625 |
| `uniform_linear` | 0.518525-1.420336 | 0.123716-0.514483 | 0.005245-0.106049 |

Budget matching uses only actual representation size or complexity.
No metric interpolation, final selection, or confirmatory claim is made.
