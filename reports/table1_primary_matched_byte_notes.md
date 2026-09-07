# Table 1 - Primary Matched-Storage Test Results

## Column definitions

- Method and configuration: frozen family and selected configuration identity.
- Bytes/raw: serialized representation bytes divided by raw canonical bytes.
- P95 position: frozen source-timestamp p95 error in metres.
- Mean and maximum velocity: frozen velocity-vector errors in metres per second.
- Semantic F1: frozen overall event-preservation F1.
- Guarantee: method-specific explicit guarantee, if any.

RDP's 0.05 m value is a geometric path tolerance only and is not a
time-indexed replay guarantee.

The compact displayed table intentionally omits byte-target mismatch,
keyframes/source, and mean position error. These fields remain at full
precision in
`results/benchmark_figures/figure2_rate_distortion/primary_table_values.json`.

## Selection

All 7 approximation families are selected
by the accepted achieved-byte matching contract at target 0.48. No metric
outcome is used for matching and achieved ratios are not claimed to be exact.
Full-precision values and record identities are in
`results/benchmark_figures/figure2_rate_distortion/primary_table_values.json`.

## Principal confirmatory contrasts

- A: position-bounded linear versus uniform linear stride 10
- B: position-bounded linear versus RDP linear 0.05 m
- C: position/velocity hybrid versus unconstrained Hermite 0.10 m

Each support record preserves the accepted natural-unit effect, 95% confidence
interval, corrected p-value, rank-biserial correlation, standardized paired
effect, practical magnitude, and pilot direction agreement. No new statistical
procedure was run.

## Caption

Primary matched-storage results from the frozen 300-scenario test cohort. Configuration selection used achieved storage only, and achieved ratios are close but not identical. RDP provides only a geometric path tolerance; full complexity and statistical fields remain in the accepted evidence.

## Output checksums

- `tables/benchmark/table1_primary_matched_byte.tex`: `8a903a0af8c0b1c2b60d2c2bb3585e1b80557e09617dda0e213d9c25f1dc92e5`
- `tables/benchmark/table1_primary_matched_byte.csv`: `1aa37ce460461399f6efc602191cecc291b709360212594ba31d83f667a88eda`
- `tables/benchmark/table1_primary_matched_byte.md`: `dd11334751fd18da20c1a403367c636c871dec9adb719d4a34e968169dc3cc3b`
