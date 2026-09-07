# Phase 4 Comparable Motion Baselines

All values below are observations from the frozen 150-scenario development cohort.
They are development-grid evidence, not a final ranking or release selection.

| Method/configuration | Keyframes | Segments | Bytes | Raw ratio | Position mean / p95 / max (m) |
|---|---:|---:|---:|---:|---:|
| raw_samples | 388,862 | 0 | 18,587,086 | 1.000000 | 0 / 0 / 0 |
| uniform_linear-stride-2 | 200,044 | 193,032 | 25,940,355 | 1.395612 | 0.00508757 / 0.0296206 / 0.567891 |
| uniform_linear-stride-5 | 85,861 | 78,849 | 13,079,690 | 0.703698 | 0.0317602 / 0.160077 / 1.40466 |
| uniform_linear-stride-10 | 47,925 | 40,913 | 8,883,322 | 0.477930 | 0.101683 / 0.489075 / 3.84599 |
| uniform_hermite-stride-2 | 200,044 | 193,032 | 25,944,906 | 1.395857 | 0.00534895 / 0.0311125 / 0.728093 |
| uniform_hermite-stride-5 | 85,861 | 78,849 | 13,084,487 | 0.703956 | 0.03494 / 0.172468 / 1.75312 |
| uniform_hermite-stride-10 | 47,925 | 40,913 | 8,888,439 | 0.478205 | 0.0971205 / 0.472533 / 3.8517 |
| rdp_linear-error-0p05 | 40,038 | 33,026 | 8,104,605 | 0.436034 | 0.210521 / 0.927411 / 11.9504 |
| rdp_linear-error-0p1 | 29,169 | 22,157 | 6,836,673 | 0.367818 | 0.369484 / 1.58701 / 20.6994 |
| rdp_linear-error-0p25 | 20,505 | 13,493 | 5,790,081 | 0.311511 | 0.674618 / 3.05263 / 25.3867 |
| rdp_linear-error-0p5 | 17,131 | 10,119 | 5,366,625 | 0.288729 | 0.895673 / 4.22351 / 25.3867 |
| fixed_interval_linear-interval-200000000 | 200,044 | 193,032 | 25,940,757 | 1.395633 | 0.00508757 / 0.0296206 / 0.567891 |
| fixed_interval_linear-interval-500000000 | 85,861 | 78,849 | 13,083,494 | 0.703902 | 0.0317602 / 0.160077 / 1.40466 |
| fixed_interval_linear-interval-1000000000 | 47,925 | 40,913 | 8,889,147 | 0.478243 | 0.101683 / 0.489075 / 3.84599 |
| exact_adjacent_sample | 388,862 | 381,850 | 46,938,333 | 2.525320 | 0 / 0 / 0 |
| position_bounded_linear-error-0p1 | 49,753 | 42,741 | 9,242,053 | 0.497230 | 0.042076 / 0.0917295 / 0.0999997 |
| unconstrained_hermite-error-0p1 | 43,722 | 36,710 | 8,575,617 | 0.461375 | 0.0315583 / 0.0803508 / 0.0999974 |
| position_velocity_bounded_hybrid-error-0p1-velocity-1p0 | 47,315 | 40,303 | 8,990,393 | 0.483690 | 0.0315388 / 0.0802739 / 0.0999992 |

The table includes the raw storage reference, uniform keyframe baselines,
the RDP geometric baseline, fixed-time baselines, and the four accepted
procedural comparison methods. No pilot or test outcome was inspected.

Phase 4 comparable motion baselines completed on all 150 genuine AV2 development scenarios.
