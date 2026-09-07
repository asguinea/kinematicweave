"""Run Batch 5.1 blind layout-induction feasibility and evidence gates."""

from __future__ import annotations

import argparse
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
import sys
from typing import cast

from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.experiments.layout_feasibility import (
    ALLOWED_MOTION_FILENAMES,
    BATCH,
    DEFAULT_CONFIG,
    DEVELOPMENT_SCENARIO_COUNT,
    Json,
    MotionOnlyScenarioInput,
    _canonical_json,
    _read_json,
    _write_json,
    aggregation_analysis,
    av2_motion_results,
    feasibility_contract,
    layout_feasibility_configuration_identity,
    run_stage_a,
    safe_identifier,
    sha256_file,
    synthetic_feasibility_results,
    verify_stage_a_bundle,
)
from kinematicweave.experiments.layout_map_diagnostics import (
    MapScenarioInput,
    run_stage_b,
)

STARTING_HEAD = "db8fdda6db603e063c161b5daa7d6b500e5909fd"
COHORT_MANIFEST = Path("results/phase4/motion_cohort/cohort_manifest.json")
DEFAULT_GENERATED_ROOT = Path("cache/phase5_layout_feasibility")
DEFAULT_EVIDENCE_ROOT = Path("results/phase5/layout_feasibility")
REQUIRED_EVIDENCE_FILES = (
    "feasibility_contract.json",
    "synthetic_results.json",
    "av2_motion_results.json",
    "aggregation_analysis.json",
    "post_freeze_map_diagnostics.json",
    "claim_feasibility_matrix.json",
    "failure_report.json",
    "summary.md",
    "evidence.json",
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--generated-root",
        type=Path,
        default=DEFAULT_GENERATED_ROOT,
    )
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=DEFAULT_EVIDENCE_ROOT,
    )
    parser.add_argument("--probe-scenarios", type=int, default=None)
    parser.add_argument("--verify-only", action="store_true")
    return parser.parse_args()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ArtifactError(f"{label} must be a mapping")
    return cast(Mapping[str, object], value)


def _sequence(value: object, label: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ArtifactError(f"{label} must be a non-string sequence")
    return value


def _cohort_units(repository_root: Path) -> tuple[Json, tuple[Json, ...]]:
    path = repository_root / COHORT_MANIFEST
    manifest = _read_json(path)
    raw_units = _sequence(manifest.get("units"), "cohort units")
    development = tuple(
        cast(Json, dict(_mapping(raw, "cohort unit")))
        for raw in raw_units
        if _mapping(raw, "cohort unit").get("cohort_role") == "development"
    )
    if len(development) != DEVELOPMENT_SCENARIO_COUNT:
        raise ArtifactError("frozen development cohort does not contain 150 scenarios")
    roles = {str(_mapping(raw, "cohort unit").get("cohort_role")) for raw in raw_units}
    if roles != {"development", "pilot", "test"}:
        raise ArtifactError("frozen cohort roles differ")
    return manifest, tuple(
        sorted(development, key=lambda item: int(item["selection_rank"]))
    )


def _cache_entry(repository_root: Path, unit: Json) -> Path:
    key = unit.get("materialization_cache_key")
    if not isinstance(key, str) or len(key) != 64:
        raise ArtifactError("development unit cache key is invalid")
    path = repository_root / "cache/phase4_motion_cohort/cohort/entries" / key[:2] / key
    if not path.is_dir():
        raise ArtifactError(
            f"development cache entry is missing: rank {unit['selection_rank']}"
        )
    return path


def _safe_scenario(unit: Json) -> str:
    source_id = unit.get("source_scenario_id")
    if not isinstance(source_id, str):
        raise ArtifactError("development unit source scenario identifier is invalid")
    return safe_identifier("phase5-layout-feasibility-scenario", source_id)


def _motion_inputs(
    repository_root: Path,
    units: Sequence[Json],
) -> tuple[MotionOnlyScenarioInput, ...]:
    inputs: list[MotionOnlyScenarioInput] = []
    for unit in units:
        entry = _cache_entry(repository_root, unit)
        files = tuple(entry / filename for filename in ALLOWED_MOTION_FILENAMES)
        if any(not path.is_file() or path.is_symlink() for path in files):
            raise ArtifactError("development motion-only cache entry is incomplete")
        inputs.append(
            MotionOnlyScenarioInput(
                safe_scenario_id=_safe_scenario(unit),
                cohort_role=str(unit["cohort_role"]),
                selection_rank=int(unit["selection_rank"]),
                files=files,
                sha256_by_filename=tuple(
                    (path.name, sha256_file(path)) for path in files
                ),
            )
        )
    return tuple(inputs)


def _map_inputs(
    repository_root: Path,
    units: Sequence[Json],
) -> tuple[MapScenarioInput, ...]:
    inputs: list[MapScenarioInput] = []
    for unit in units:
        path = _cache_entry(repository_root, unit) / "vector_map_elements.parquet"
        if not path.is_file() or path.is_symlink():
            raise ArtifactError("development canonical map is incomplete")
        inputs.append(
            MapScenarioInput(
                safe_scenario_id=_safe_scenario(unit),
                cohort_role=str(unit["cohort_role"]),
                selection_rank=int(unit["selection_rank"]),
                vector_map_path=path,
                vector_map_sha256=sha256_file(path),
            )
        )
    return tuple(inputs)


def _claim(
    claim_id: str,
    category: str,
    classification: str,
    evidence: Sequence[str],
    qualification: str,
    unsupported: Sequence[str],
) -> Json:
    return {
        "claim_id": claim_id,
        "candidate_category": category,
        "classification": classification,
        "evidence_fields": list(evidence),
        "qualification": qualification,
        "unsupported_broader_interpretations": list(unsupported),
    }


def _claim_matrix(motion: Json, diagnostics: Json, aggregation: Json) -> Json:
    aggregate = cast(Json, motion["aggregate"])
    map_aggregate = cast(Json, diagnostics["aggregate"])
    repeated = int(aggregate["multi_track_supported_track_count"])
    transitions = int(aggregate["observed_transition_crossing_count"])
    merges = int(aggregate["potential_merge_count"])
    splits = int(aggregate["potential_split_count"])
    turn_count = sum(
        int(value)
        for key, value in cast(
            Mapping[str, int],
            aggregate["event_count_by_type"],
        ).items()
        if key in {"left_turn", "right_turn"}
    )
    endpoint_support = int(aggregate["repeated_endpoint_cell_count"])
    stop_count = int(
        cast(Mapping[str, int], aggregate["event_count_by_type"]).get("stop", 0)
    )
    crossing_count = int(aggregate["geometric_crossing_count"])
    claims = [
        _claim(
            "L1",
            "motion-supported corridors",
            ("feasible only with aggregation" if repeated else "unsupported"),
            [
                "av2_motion_results.json:aggregate.multi_track_supported_track_count",
                "post_freeze_map_diagnostics.json:aggregate.motion_point_within_support_fraction",
            ],
            (
                "Repeated motion can support scenario-local corridor candidates; "
                "no corridor geometry was inferred."
            ),
            ["complete corridor coverage", "physical road boundaries"],
        ),
        _claim(
            "L2",
            "directional centerlines",
            (
                "feasible only with aggregation"
                if int(aggregate["directional_agreement_pair_count"])
                else "unsupported"
            ),
            [
                "av2_motion_results.json:aggregate.directional_agreement_pair_count",
                "av2_motion_results.json:aggregate.opposing_direction_pair_count",
            ],
            "Travel direction is observed, but a persistent centerline requires aggregation.",
            ["lane identity", "complete bidirectionality"],
        ),
        _claim(
            "L3",
            "corridor width or uncertainty",
            "deferred",
            [
                "av2_motion_results.json:aggregate.repeated_spatial_cell_count",
                "post_freeze_map_diagnostics.json:aggregate.mapped_structure_supported_fraction",
            ],
            "Motion spread is observable, but physical width is not identifiable from center traces alone.",
            ["drivable boundary recovery", "lane-width estimation"],
        ),
        _claim(
            "L4",
            "observed route transitions",
            "feasible now",
            [
                "av2_motion_results.json:aggregate.observed_transition_crossing_count",
                "post_freeze_map_diagnostics.json:aggregate.map_consistent_observed_transition_fraction",
            ],
            "Only transitions actually traversed by development motion are supported.",
            ["unobserved connectivity", "routing completeness"],
        ),
        _claim(
            "L5",
            "turns",
            "feasible now" if turn_count else "unsupported",
            ["av2_motion_results.json:aggregate.event_count_by_type"],
            "Turns are accepted semantic motion events, not junction objects.",
            ["all permitted turns", "junction completeness"],
        ),
        _claim(
            "L6",
            "merges and splits",
            (
                "feasible only as partial motion-supported structure"
                if merges or splits
                else "unsupported"
            ),
            [
                "av2_motion_results.json:aggregate.potential_merge_count",
                "av2_motion_results.json:aggregate.potential_split_count",
            ],
            "Counts are endpoint-pattern evidence and remain potential structures.",
            ["topological merge nodes", "complete incident corridors"],
        ),
        _claim(
            "L7",
            "junction evidence",
            (
                "feasible only as partial motion-supported structure"
                if transitions or merges or splits
                else "unsupported"
            ),
            [
                "av2_motion_results.json:aggregate.observed_transition_crossing_count",
                "av2_motion_results.json:aggregate.potential_merge_count",
                "av2_motion_results.json:aggregate.potential_split_count",
            ],
            "Observed transitions support partial junction evidence only.",
            ["junction geometry", "junction graph reconstruction"],
        ),
        _claim(
            "L8",
            "connected versus disconnected crossings",
            (
                "feasible only as partial motion-supported structure"
                if crossing_count
                else "unsupported"
            ),
            [
                "av2_motion_results.json:aggregate.observed_transition_crossing_count",
                "av2_motion_results.json:aggregate.elevation_separated_crossing_count",
                "av2_motion_results.json:aggregate.ambiguous_geometric_crossing_count",
            ],
            (
                "Observed transitions or elevation can classify some crossings; "
                "remaining planar crossings stay ambiguous."
            ),
            ["connectivity from geometry alone", "all crossing semantics"],
        ),
        _claim(
            "L9",
            "stop zones",
            ("feasible only with aggregation" if stop_count else "unsupported"),
            [
                "av2_motion_results.json:aggregate.event_count_by_type",
                "av2_motion_results.json:aggregate.repeated_endpoint_cell_count",
            ],
            "Repeated stop events can support zones after aggregation, not from a single event.",
            ["traffic-control semantics", "parking classification"],
        ),
        _claim(
            "L10",
            "endpoint or access zones",
            (
                "feasible only as partial motion-supported structure"
                if endpoint_support
                else "unsupported"
            ),
            ["av2_motion_results.json:aggregate.repeated_endpoint_cell_count"],
            "Repeated endpoints indicate observed access patterns but not complete access geometry.",
            ["entrance type", "full access-zone boundary"],
        ),
        _claim(
            "L11",
            "complete local road-network reconstruction",
            "unsupported",
            [
                "post_freeze_map_diagnostics.json:aggregate.mapped_structure_supported_fraction",
                "aggregation_analysis.json:coordinates_comparable_across_scenarios",
            ],
            "Development motion is sparse relative to mapped local structure.",
            ["complete road graph", "unobserved roads"],
        ),
        _claim(
            "L12",
            "complete symbolic spatial grammar",
            "unsupported",
            [
                "claim_feasibility_matrix.json:claims",
                "aggregation_analysis.json:recommendation",
            ],
            "The batch measures evidence sufficiency and creates no grammar.",
            ["layout induction achieved", "editing or rerouting readiness"],
        ),
    ]
    return {
        "schema_version": "1.0",
        "batch": BATCH,
        "claim_count": len(claims),
        "claims": claims,
        "classification_counts": dict(
            sorted(Counter(str(item["classification"]) for item in claims).items())
        ),
        "frozen_before_map_diagnostics": True,
        "map_diagnostics_changed_classifications": False,
        "universal_layout_feasibility_claimed": False,
        "production_layout_induction_implemented": False,
        "descriptive_context": {
            "mapped_structure_supported_fraction": map_aggregate[
                "mapped_structure_supported_fraction"
            ],
            "cross_scenario_comparability": aggregation[
                "coordinates_comparable_across_scenarios"
            ],
        },
    }


def _failure_report(motion: Json, diagnostics: Json) -> Json:
    aggregate = cast(Json, motion["aggregate"])
    map_aggregate = cast(Json, diagnostics["aggregate"])
    zero_or_unfavorable = {
        key: aggregate[key]
        for key in (
            "elevation_separated_crossing_count",
            "observed_transition_crossing_count",
            "potential_merge_count",
            "potential_split_count",
        )
    }
    zero_or_unfavorable["scenario_without_mapped_structure_support_count"] = (
        map_aggregate["scenario_without_mapped_structure_support_count"]
    )
    return {
        "schema_version": "1.0",
        "batch": BATCH,
        "excluded_track_count": aggregate["excluded_track_count"],
        "zero_count_and_unfavorable_findings": zero_or_unfavorable,
        "all_zero_count_and_unfavorable_findings_retained": True,
        "ambiguous_geometric_crossing_count": aggregate[
            "ambiguous_geometric_crossing_count"
        ],
        "scenario_local_fragmentation": {
            "support_component_count": aggregate["support_component_count"],
            "scenario_count": aggregate["scenario_count"],
        },
        "stage_a_failure_count": 0,
        "stage_b_failure_count": 0,
        "scenario_replacement_count": 0,
        "production_layout_fallback_used": False,
    }


def _summary(
    motion: Json,
    aggregation: Json,
    diagnostics: Json,
    claims: Json,
    deterministic: bool,
) -> str:
    aggregate = cast(Json, motion["aggregate"])
    map_aggregate = cast(Json, diagnostics["aggregate"])
    return f"""# Batch 5.1 — Blind Layout-Induction Feasibility

## Decision

`feasibility_gate = "completed"`

Phase 5 layout-induction feasibility gate completed on synthetic and 150
genuine AV2 development scenarios with an enforced motion-only construction
boundary.

This is a descriptive feasibility decision. It does not claim that layout
induction has been achieved and it materializes no corridors, centerlines,
junctions, transition graphs, semantic zones, or spatial grammar.

## Frozen cohort and boundary

- Development scenarios analyzed: {motion["development_scenarios_analyzed"]}
- Pilot scenario access: {motion["pilot_scenario_access_count"]}
- Test scenario access: {motion["test_scenario_access_count"]}
- Stage A map files opened: {motion["map_file_open_count"]}
- Stage A completed before map access: true
- Stage A remained byte-identical after repeated Stage B execution: true

## Motion-only evidence

- Layout-eligible tracks: {aggregate["layout_eligible_track_count"]}
- Excluded tracks retained: {aggregate["excluded_track_count"]}
- Tracks with multi-track spatial support: {aggregate["multi_track_supported_track_count"]}
- Multi-track supported fraction: {aggregate["multi_track_supported_track_fraction"]:.6f}
- Directional/opposing support pairs: {aggregate["directional_agreement_pair_count"]} / {aggregate["opposing_direction_pair_count"]}
- Potential merges/splits: {aggregate["potential_merge_count"]} / {aggregate["potential_split_count"]}
- Geometric crossings: {aggregate["geometric_crossing_count"]}
- Transition-supported/elevation-separated/ambiguous crossings:
  {aggregate["observed_transition_crossing_count"]} /
  {aggregate["elevation_separated_crossing_count"]} /
  {aggregate["ambiguous_geometric_crossing_count"]}

## Aggregation

Coordinates comparable across scenarios:
`{str(aggregation["coordinates_comparable_across_scenarios"]).lower()}`.
Recommendation: {aggregation["recommendation"]}.

## Post-freeze diagnostics

- Motion points near mapped structure: {map_aggregate["motion_point_within_support_fraction"]:.6f}
- Mapped local structure supported by motion: {map_aggregate["mapped_structure_supported_fraction"]:.6f}
- Orientation agreement: {map_aggregate["orientation_agreement_fraction"]:.6f}
- Map-consistent observed transitions: {map_aggregate["map_consistent_observed_transition_fraction"]:.6f}

## Claim feasibility

{claims["classification_counts"]}

Complete local road-network reconstruction and complete symbolic spatial grammar
are unsupported. Physical corridor width and multi-scenario geographic
aggregation remain deferred. Sparse, zero-count, and ambiguous findings remain
in the evidence.

## Determinism

Two isolated full-cohort generations were byte-identical:
`{str(deterministic).lower()}`.
"""


def _write_evidence(
    evidence_root: Path,
    values: Mapping[str, Json | str],
    *,
    stage_a: Json,
    stage_b: Json,
    cohort_manifest_sha256: str,
) -> Json:
    evidence_root.mkdir(parents=True, exist_ok=True)
    for name, value in values.items():
        path = evidence_root / name
        if isinstance(value, str):
            path.write_text(value, encoding="utf-8", newline="\n")
        else:
            _write_json(path, value)
    hashes = {
        name: sha256_file(evidence_root / name)
        for name in REQUIRED_EVIDENCE_FILES
        if name != "evidence.json"
    }
    record = {
        "schema_version": "1.0",
        "batch": BATCH,
        "feasibility_gate": "completed",
        "starting_head": STARTING_HEAD,
        "development_scenarios_analyzed": DEVELOPMENT_SCENARIO_COUNT,
        "pilot_scenario_access_count": 0,
        "test_scenario_access_count": 0,
        "stage_a_completed_before_map_access": True,
        "stage_a_identity": cast(Json, stage_a["manifest"])["stage_a_identity"],
        "stage_a_bundle_sha256": cast(Json, stage_a["manifest"])["bundle_sha256"],
        "stage_a_snapshot_before_stage_b": stage_b["stage_a_snapshot_before"],
        "stage_a_snapshot_after_stage_b": stage_b["stage_a_snapshot_after"],
        "stage_a_checksums_unchanged_after_stage_b": True,
        "cohort_manifest_sha256": cohort_manifest_sha256,
        "configuration_identity": layout_feasibility_configuration_identity(),
        "isolated_full_generation_count": 2,
        "deterministic_isolated_regeneration_verified": True,
        "all_zero_count_and_unfavorable_findings_retained": True,
        "production_layout_induction_implemented": False,
        "phase4_evidence_modified": False,
        "evidence_file_sha256": hashes,
    }
    _write_json(evidence_root / "evidence.json", record)
    return record


def _generation(
    repository_root: Path,
    units: Sequence[Json],
    root: Path,
    *,
    cohort_identity: str,
    cohort_manifest_sha256: str,
    require_full: bool,
) -> tuple[Json, Json]:
    stage_a_root = root / "stage_a"
    stage_b_root = root / "stage_b"
    stage_a = run_stage_a(
        repository_root,
        _motion_inputs(repository_root, units),
        stage_a_root,
        cohort_identity=cohort_identity,
        cohort_manifest_sha256=cohort_manifest_sha256,
        require_full_cohort=require_full,
    )
    # Map paths are constructed only after Stage A has completed and verified.
    verify_stage_a_bundle(
        stage_a_root,
        expected_scenario_count=len(units),
    )
    stage_b = run_stage_b(
        repository_root,
        stage_a_root,
        _map_inputs(repository_root, units),
        stage_b_root,
        expected_scenario_count=len(units),
    )
    repeated = run_stage_b(
        repository_root,
        stage_a_root,
        _map_inputs(repository_root, units),
        stage_b_root,
        expected_scenario_count=len(units),
    )
    if stage_b != repeated:
        raise ArtifactError("repeated Stage B diagnostics differ")
    return stage_a, stage_b


def _verify_evidence(evidence_root: Path) -> Json:
    actual = tuple(
        sorted(path.name for path in evidence_root.iterdir() if path.is_file())
    )
    if actual != tuple(sorted(REQUIRED_EVIDENCE_FILES)):
        raise ArtifactError("Batch 5.1 tracked evidence file set differs")
    evidence = _read_json(evidence_root / "evidence.json")
    if evidence.get("feasibility_gate") != "completed":
        raise ArtifactError("Batch 5.1 feasibility gate is incomplete")
    if evidence.get("development_scenarios_analyzed") != 150:
        raise ArtifactError("Batch 5.1 did not analyze 150 development scenarios")
    if (
        evidence.get("pilot_scenario_access_count") != 0
        or evidence.get("test_scenario_access_count") != 0
    ):
        raise ArtifactError("Batch 5.1 accessed a forbidden cohort role")
    hashes = _mapping(evidence.get("evidence_file_sha256"), "evidence hashes")
    for name, expected in hashes.items():
        if sha256_file(evidence_root / str(name)) != expected:
            raise ArtifactError(f"Batch 5.1 evidence checksum differs: {name}")
    return evidence


def run(repository_root: Path, generated_root: Path, evidence_root: Path) -> Json:
    manifest, units = _cohort_units(repository_root)
    cohort_identity = str(manifest["cohort_identity"])
    manifest_sha = sha256_file(repository_root / COHORT_MANIFEST)
    contract = feasibility_contract(DEFAULT_CONFIG)
    synthetic = synthetic_feasibility_results(DEFAULT_CONFIG)
    first_stage_a, first_stage_b = _generation(
        repository_root,
        units,
        generated_root / "generation-a",
        cohort_identity=cohort_identity,
        cohort_manifest_sha256=manifest_sha,
        require_full=True,
    )
    second_stage_a, second_stage_b = _generation(
        repository_root,
        units,
        generated_root / "generation-b",
        cohort_identity=cohort_identity,
        cohort_manifest_sha256=manifest_sha,
        require_full=True,
    )
    if (
        first_stage_a["snapshot"] != second_stage_a["snapshot"]
        or first_stage_b["diagnostics"] != second_stage_b["diagnostics"]
        or first_stage_b["output_sha256"] != second_stage_b["output_sha256"]
    ):
        raise ArtifactError("isolated full-cohort generations differ")
    motion = av2_motion_results(first_stage_a)
    aggregation = aggregation_analysis(first_stage_a)
    diagnostics = cast(Json, first_stage_b["diagnostics"])
    claims = _claim_matrix(motion, diagnostics, aggregation)
    failures = _failure_report(motion, diagnostics)
    summary = _summary(motion, aggregation, diagnostics, claims, True)
    evidence = _write_evidence(
        evidence_root,
        {
            "feasibility_contract.json": contract,
            "synthetic_results.json": synthetic,
            "av2_motion_results.json": motion,
            "aggregation_analysis.json": aggregation,
            "post_freeze_map_diagnostics.json": diagnostics,
            "claim_feasibility_matrix.json": claims,
            "failure_report.json": failures,
            "summary.md": summary,
        },
        stage_a=first_stage_a,
        stage_b=first_stage_b,
        cohort_manifest_sha256=manifest_sha,
    )
    _verify_evidence(evidence_root)
    return evidence


def probe(
    repository_root: Path,
    generated_root: Path,
    scenario_count: int,
) -> Json:
    if not 1 <= scenario_count <= 25:
        raise ValidationError("probe scenario count must be in [1, 25]")
    manifest, units = _cohort_units(repository_root)
    selected = units[:scenario_count]
    stage_a, stage_b = _generation(
        repository_root,
        selected,
        generated_root / f"probe-{scenario_count}",
        cohort_identity=str(manifest["cohort_identity"]),
        cohort_manifest_sha256=sha256_file(repository_root / COHORT_MANIFEST),
        require_full=False,
    )
    return {
        "probe_scenario_count": scenario_count,
        "stage_a_identity": cast(Json, stage_a["manifest"])["stage_a_identity"],
        "stage_b_output_sha256": stage_b["output_sha256"],
        "pass_gate_eligible": False,
    }


def main() -> int:
    arguments = _arguments()
    repository_root = arguments.repository_root.resolve()
    generated_root = (
        arguments.generated_root
        if arguments.generated_root.is_absolute()
        else repository_root / arguments.generated_root
    )
    evidence_root = (
        arguments.evidence_root
        if arguments.evidence_root.is_absolute()
        else repository_root / arguments.evidence_root
    )
    if arguments.verify_only:
        _verify_evidence(evidence_root)
        return 0
    if arguments.probe_scenarios is not None:
        result = probe(
            repository_root,
            generated_root,
            arguments.probe_scenarios,
        )
        sys.stdout.write(_canonical_json(result))
        return 0
    run(repository_root, generated_root, evidence_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
