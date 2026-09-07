# Phase 2 AV2 Provider-Data Evidence

## 1. Evidence decision

M2 is achieved with genuine AV2 provider-data evidence.

## 2. Official source and partition

- Source: `s3://argoverse/datasets/av2/motion-forecasting/`
- Partition: `val`
- Dataset version: `official-s3-3009ac419e1e`

## 3. Deterministic selection

- Selected scenarios: 10
- `f624753f-3238-4395-9c41-e37c03a9d1d6`
- `c1dbf4cf-1d11-4609-adec-b8139330ca7d`
- `3bf2e49d-0659-4c36-9e22-aa444e38a02b`
- `6f009d62-876c-4449-80bd-f279580400d7`
- `e6b27316-a539-4024-92a5-dff70935a4a9`
- `4cb7d121-c7f3-4ea2-8895-a0ad8d482e61`
- `1b4c7d12-baa8-4997-95cc-9a91343edd47`
- `3470404c-0388-4a99-bd1d-19802eb10c5a`
- `104f98cd-443c-42fe-9d76-c47a8ea838cc`
- `656cee35-5ab3-4758-a076-f84abf2b2e55`

## 4. Downloaded provider data

- Backend: `s5cmd` `v2.3.0-991c9fb`
- Candidate scenarios: 24988
- Selected remote bytes: 2086085
- Downloaded bytes: 0
- Reused download bytes: 2086085
- Listing seconds: 12.128049
- Download seconds: 0.015215
- Failed-attempt provider files: 20 files, 2086085 bytes; stage timing was not persisted.

## 5. First-run materialization

- Materialized scenarios: 10
- Reused scenarios: 0
- Materialization seconds: 2.155060
- Validation seconds: 1.175245
- Total seconds: 3.495135
- Scenarios per second: 2.861120

## 6. Second-run cache reuse

- Materialized scenarios: 0
- Reused scenarios: 10
- Reuse seconds: 0.053732
- Validation seconds: 1.188989
- Total seconds: 1.416773

## 7. Canonical data counts

- Source scenarios: 10
- Source coordinate_frames: 10
- Source agents: 428
- Source trajectories: 428
- Source trajectory_samples: 23068
- Source vector_map_elements: 1498
- Included scenarios: 10
- Included agents: 418
- Included trajectories: 418
- Included map_elements: 1498

## 8. Eligibility and exclusions

- Exclusions: 10
- `adapter_failure`: 0
- `invalid_timestamps`: 0
- `insufficient_samples`: 0
- `insufficient_duration`: 10
- `unsupported_agent_class`: 0
- `no_usable_map`: 0
- `insufficient_trajectory_coverage`: 0
- `coordinate_frame_mismatch`: 0
- `duplicate`: 0
- `geographic_leakage_conflict`: 0
- `invalid_geometry`: 0
- `resource_limit_exclusion`: 0
- `other_documented_reason`: 0

## 9. Runtime, memory, and disk measurements

- Peak process memory bytes: 189689856
- Peak-memory method: `windows-psapi-peak-working-set`
- Disk free before acquisition: 600074760192
- Disk free after acquisition: 600074166272
- Disk free after materialization: 600074174464
- CPU-only pipeline: yes
- GPU use: 0

## 10. Hardware used

- Manufacturer: `ASUSTeK COMPUTER INC.`
- Model: `ROG Zephyrus G14 GA403UP_GA403UP`
- Processor: `AMD64 Family 25 Model 117 Stepping 2, AuthenticAMD`
- Logical CPUs: 16
- Total RAM bytes: 33568325632
- Operating system: `{'system': 'Windows', 'release': '11', 'version': '10.0.26200', 'machine': 'AMD64'}`
- Python: `3.12.13`
- uv: `uv 0.11.32 (3010295ae 2026-07-23 x86_64-pc-windows-msvc)`

## 11. Reproduction command

```text
uv run --frozen python scripts/run_av2_provider_pilot.py --repository-root . --scenario-count 10 --data-root data/external/av2_motion --cache-root cache/av2_provider_pilot --evidence-root results/phase2/av2_provider_pilot --backend auto
```

## 12. Evidence-file checksums

- `acquisition_plan.json`: `b07277107641ef8d349387dd98f0c4cb0aadafb8c51290c31c53a3a44f19b7f3`
- `acquisition_report.json`: `5be4e1d50189b2e996c09880627bbe087070ea045769773bd231b18a385fd0a7`
- `pilot_plan.json`: `98a6af62640a1f5f6a4891c7dce2c4af4268be9cd79df03a782cda7516628830`
- `pilot_report_first_run.json`: `c19b3866e626608bc0649911e07a41c2828397a4a5ab1f89e09c2d5676d40ec9`
- `pilot_report_reuse_run.json`: `52144dfdb9da2295adc4ba4e8af6a90333de9d9f5f8edca66885bbbdd23b7ef3`
- `source_manifest.json`: `05c1eb1051ea19747222bfc7a53bba50ef3757410fb06792682acb4e4dce44f0`
- `validation_report.json`: `3eb46a0dd2cc7b89fa7e670a19bb78d7afb3e5271a5d69b9d351a3877678edd2`

The complete cross-file checksum set, including this summary, is recorded in `evidence.json`.

## 13. Provider incompatibilities corrected

- Official motion Parquet stores `start_timestamp` and `end_timestamp` as `double`; the adapter now accepts only finite, exactly integral float64 values that losslessly normalize to signed int64 nanoseconds without rounding.
- Official scenario-local maps can reference lanes outside the local crop; the adapter now omits those references from canonical topology, records the exact omitted IDs in semantic provenance, and adds relation-specific quality flags without fabricating geometry or nodes.
- Omitted external lane references across the ten scenarios: 158
- Canonical unresolved lane references: 0
- Fabricated canonical lane nodes: 0

## 14. Remaining limitations

- This is a deterministic ten-scenario provider compatibility and laptop evidence pass, not a final statistical campaign.
- Stage timings are measured observations and are not claimed to be deterministic.
