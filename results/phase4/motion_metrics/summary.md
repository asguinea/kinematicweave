# Phase 4 Motion and Semantic Evaluation Metrics

- Batch decision: `achieved`
- Cohort: 150 genuine AV2 development scenarios
- Included trajectories per method: 7,012
- Method/configurations: 18
- Evaluation failures: 0
- Pilot/test outcomes accessed: no

## Metric Summary

| Method | Position mean (m) | Position p95 (m) | Event records |
|---|---:|---:|---:|
| `raw_samples` | 0.0 | 0.0 | 42072 |
| `uniform_linear-stride-2` | 0.005087565112660008 | 0.029620557960343713 | 42072 |
| `uniform_linear-stride-5` | 0.03176023359812594 | 0.16007715836995975 | 42072 |
| `uniform_linear-stride-10` | 0.10168339115334918 | 0.4890746063229892 | 42072 |
| `uniform_hermite-stride-2` | 0.00534895338626553 | 0.031112466578799774 | 42072 |
| `uniform_hermite-stride-5` | 0.03494003109452138 | 0.17246809644389993 | 42072 |
| `uniform_hermite-stride-10` | 0.09712048705585585 | 0.4725334344012778 | 42072 |
| `rdp_linear-error-0p05` | 0.21052072307147116 | 0.9274108531780538 | 42072 |
| `rdp_linear-error-0p1` | 0.36948371133141694 | 1.5870121355928744 | 42072 |
| `rdp_linear-error-0p25` | 0.6746178452917313 | 3.052633489487832 | 42072 |
| `rdp_linear-error-0p5` | 0.8956731673393078 | 4.223510180372897 | 42072 |
| `fixed_interval_linear-interval-200000000` | 0.005087565112660008 | 0.029620557960343713 | 42072 |
| `fixed_interval_linear-interval-500000000` | 0.03176023359812594 | 0.16007715836995975 | 42072 |
| `fixed_interval_linear-interval-1000000000` | 0.10168339115334918 | 0.4890746063229892 | 42072 |
| `exact_adjacent_sample` | 0.0 | 0.0 | 42072 |
| `position_bounded_linear-error-0p1` | 0.0420759601963108 | 0.09172948513052268 | 42072 |
| `unconstrained_hermite-error-0p1` | 0.0315582711065467 | 0.08035077728162009 | 42072 |
| `position_velocity_bounded_hybrid-error-0p1-velocity-1p0` | 0.03153879904314948 | 0.08027386222124319 | 42072 |

These are development results. No ranking, significance, Pareto, matched-budget, or final-method claim is made.
