# Phase 4 Representation Ablations and Contribution Attribution

Phase 4 representation contributions attributed on the frozen AV2 pilot and test cohorts.

The scenario-level attribution reuses the frozen Batch 4.6 and 4.7 artifacts without codec execution, metric changes, interpolation, or budget rematching. Test results are confirmatory and pilot results remain separate replication evidence.

Adaptive linear segmentation helps when a time-indexed position guarantee matters because it places breakpoints in response to replay error rather than at a fixed cadence. In the primary-byte context, position-bounded linear 0.10 m had the lower test mean position error. This is attribution at retained achieved budgets, not a claim of invented exact budget equality.

Geometric path simplification is insufficient for temporal replay because RDP controls perpendicular path geometry rather than the state reconstructed at each timestamp. The test velocity comparison favored position-bounded linear 0.10 m, while the position-bounded method alone carries the measured time-indexed 0.10 m replay guarantee.

Adding unconstrained Hermite primitives changes interpolation between selected endpoints: test position favored unconstrained Hermite 0.10 m, but velocity favored position-bounded linear 0.10 m. Position preservation therefore does not imply dynamics preservation.

The explicit velocity constraint adds a verified 1.00 m/s worst-case bound with zero pilot or test violations. Mean position was effectively unchanged between unconstrained and constrained Hermite, while the hybrid reduced mean velocity error by 0.164776 m/s and worst-case velocity error by 9.231274 m/s. The constraint cost 0.022136 byte-ratio units, while position/velocity hybrid 0.10 m and 1.00 m/s encoded faster; replay runtime did not show the same practical advantage.

The simpler position-bounded linear method and the hybrid serve different requirements. Against linear, the hybrid reduced byte ratio by 0.017375, segment ratio by 0.008307, mean position error by 0.010939 m, and worst-case velocity error by 3.456534 m/s. Linear reduced mean velocity error by 0.034328 m/s, raised overall semantic F1 by 0.025162, and encoded 0.394503 s faster. Hybrid is preferable when the explicit velocity guarantee dominates; linear is preferable when semantic preservation, average velocity error, runtime, and primitive simplicity dominate. Neither wins every outcome.

Exact adjacent-sample proceduralization preserves replay correctness but does not imply compression. Across test scenarios its exact-to-raw byte ratio had mean 2.547859; tracks, segments, manifests, metadata, and Parquet framing add physical storage beyond the raw sample table.

Robustness reporting retained 7 city-level and 29 class or motion-category direction disagreements. These exceptions remain descriptive and are not filtered from the evidence. No universally best method is declared.
