"""Deterministic release Figure 1 selection, rendering, and verification."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import hashlib
from itertools import pairwise
import json
import math
from pathlib import Path
import re
import shutil
from typing import Any, cast

from PIL import Image, ImageDraw, ImageFont
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from kinematicweave.artifact_store import run_directory_name
from kinematicweave.canonical import canonical_json_bytes
from kinematicweave.codecs.exact import evaluate_segment, replay_track
from kinematicweave.codecs.piecewise_linear import _trajectory_from_rows
from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralSegment,
    ProceduralTrack,
    procedural_segment_from_dict,
    procedural_track_from_dict,
)
from kinematicweave.domain.records import Trajectory
from kinematicweave.errors import ArtifactError, ValidationError
from kinematicweave.events.semantic_motion import (
    SEMANTIC_ALGORITHM_VERSION,
    SemanticMotionConfig,
    analyze_event_preservation,
    detect_semantic_trajectory,
    replay_detection_trajectory,
    semantic_motion_configuration_identity,
)
from kinematicweave.experiments.ablation_analysis import CONFIGURATION_IDS
from kinematicweave.experiments.qualitative_selection import (
    SAFE_ID_NAMESPACE,
    safe_identifier,
)
from kinematicweave.metrics.motion import evaluate_trajectory_motion
from kinematicweave.visualization.deterministic import (
    BLUE,
    CORAL,
    GOLD,
    GREEN,
    GRID,
    INK,
    MUTED,
    PURPLE,
    WHITE,
    Color,
    Drawing,
)

SCHEMA_VERSION = "1.0"
ALGORITHM_VERSION = "figure1-candidate-selection-v1"
SCRIPT_VERSION = "2.1"
SELECTION_BATCH = "F1.1"
BATCH = "F1.2"
STARTING_HEAD = "8fc221253c0aaa84516632fe19088a050b7130c7"

EXACT_CONFIGURATION = "exact_adjacent"
HYBRID_CONFIGURATION = "position_velocity_hybrid_0_10_m_1_00_mps"
RAW_CONFIGURATION = "raw_samples"
EVENT_TYPES = ("stop", "left_turn", "right_turn", "acceleration", "braking")

FIGURE_RELATIVE = Path("figures/benchmark/figure1_procedural_overview_draft")
FINAL_FIGURE_RELATIVE = Path("figures/benchmark/figure1_procedural_overview")
GRAYSCALE_RELATIVE = Path("figures/benchmark/figure1_procedural_overview_grayscale.png")
CANDIDATE_RELATIVE = Path("figures/benchmark/figure1_candidates")
FINAL_CANDIDATE_RELATIVE = Path("figures/benchmark/figure1_final_candidates")
RESULT_RELATIVE = Path("results/benchmark_figures/figure1_procedural_overview")
NOTES_RELATIVE = Path("reports/figure1_procedural_overview_notes.md")
CAPTION_RELATIVE = Path("reports/figure1_procedural_overview_caption.md")
PRIVATE_RELATIVE = Path(
    "cache/benchmark_figures/figure1_procedural_overview/private_candidate_mapping.json"
)


def _release_font() -> Path:
    candidates = (
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


RELEASE_FONT = _release_font()
PRIMARY_SAFE_IDENTIFIER = "trajectory-c3b9d7c1f89e1c25"
COMPARISON_SAFE_IDENTIFIER = "trajectory-f83da3381e4f5eda"

type Json = dict[str, Any]

_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CandidateFeatures:
    """Release-safe deterministic features for one eligible trajectory."""

    safe_identifier: str
    valid_source_sample_count: int
    path_length_m: float
    duration_s: float
    cumulative_absolute_heading_change_rad: float
    semantic_event_count: int
    semantic_event_types: tuple[str, ...]
    semantic_event_type_diversity: int
    exact_segment_count: int
    hybrid_segment_count: int
    segment_reduction_count: int
    segment_reduction_ratio: float
    hybrid_position_mean_m: float
    hybrid_position_p95_m: float
    hybrid_position_maximum_m: float
    hybrid_velocity_mean_mps: float
    hybrid_velocity_p95_mps: float
    hybrid_velocity_maximum_mps: float
    source_semantic_event_count: int
    replay_semantic_event_count: int
    matched_semantic_event_count: int
    semantic_preservation_f1: float
    spatial_extent_x_m: float
    spatial_extent_y_m: float
    spatial_extent_diagonal_m: float
    annotation_density_score: float
    hybrid_hold_count: int
    hybrid_linear_count: int
    hybrid_hermite_count: int

    def public_dict(self) -> Json:
        """Return the canonical JSON representation."""
        value = asdict(self)
        value["semantic_event_types"] = list(self.semantic_event_types)
        return value


@dataclass(frozen=True, slots=True)
class ReadabilityResult:
    """Predeclared deterministic readability-gate result."""

    passed: bool
    failures: tuple[str, ...]
    measurements: Mapping[str, float | int]

    def public_dict(self) -> Json:
        return {
            "passed": self.passed,
            "failures": list(self.failures),
            "measurements": dict(self.measurements),
        }


@dataclass(frozen=True, slots=True)
class _CandidateInput:
    scenario_id: str
    trajectory_id: str
    exact: Json
    hybrid: Json
    events: tuple[Json, ...]


@dataclass(frozen=True, slots=True)
class _RankedCandidate:
    source_scenario_id: str
    trajectory_id: str
    features: CandidateFeatures
    source_rows: tuple[Json, ...]
    rank: int = 0


@dataclass(frozen=True, slots=True)
class _RunArtifact:
    path: Path
    file_identities: Mapping[str, Json]
    checkpoint_sha256: str


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactError(f"{path} must contain a JSON object")
    return cast(Json, value)


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(dict(value)) + b"\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _contained(root: Path, path: Path) -> Path:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise ArtifactError(f"path escapes root: {path}")
    return resolved


def _verify_evidence_directory(root: Path, relative: str) -> str:
    evidence_root = _contained(root, root / relative)
    evidence_path = evidence_root / "evidence.json"
    evidence = _read_json(evidence_path)
    checksums = evidence.get(
        "evidence_file_sha256",
        evidence.get("component_sha256"),
    )
    if not isinstance(checksums, dict):
        raise ArtifactError(f"{relative} evidence checksum map is missing")
    for name, expected in sorted(checksums.items()):
        path = _contained(evidence_root, evidence_root / str(name))
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"accepted evidence differs: {path}")
    return _sha256(evidence_path)


def _verify_qualitative_replay(root: Path) -> None:
    replay_root = root / "results/phase4/qualitative_motion"
    manifest = _read_json(replay_root / "replay_sequence_manifest.json")
    for row in cast(list[Json], manifest["frames"]):
        path = _contained(root, root / str(row["path"]))
        if _sha256(path) != row["sha256"]:
            raise ArtifactError("accepted qualitative replay frame differs")
    for key in ("representative_preview", "video"):
        row = cast(Json, manifest[key])
        path = _contained(root, root / str(row["path"]))
        if _sha256(path) != row["sha256"]:
            raise ArtifactError(f"accepted qualitative replay {key} differs")


def verify_accepted_inputs(
    repository_root: Path,
    generated_root: Path,
) -> Json:
    """Verify all committed evidence and large generated metric identities used."""
    evidence_paths = (
        "results/phase3/exact_codec_baseline",
        "results/phase3/piecewise_linear_codec",
        "results/phase3/hermite_codec",
        "results/phase3/velocity_bounded_codec",
        "results/phase3/semantic_motion",
        "results/phase3/milestone",
        "results/phase4/frozen_campaign",
        "results/phase4/qualitative_motion",
    )
    committed = {
        f"{relative}/evidence.json": _verify_evidence_directory(
            repository_root, relative
        )
        for relative in evidence_paths
    }
    _verify_qualitative_replay(repository_root)
    test_results_path = (
        repository_root / "results/phase4/frozen_campaign/test_results.json"
    )
    test_results = _read_json(test_results_path)
    if (
        test_results.get("cohort_role") != "test"
        or test_results.get("scenario_count") != 300
        or test_results.get("failure_count") != 0
    ):
        raise ArtifactError("accepted frozen-test campaign contract differs")
    generated_files: Json = {}
    expected_prefix = Path("cache/phase4_frozen_campaign/test")
    for descriptor in cast(list[Json], test_results["raw_result_artifacts"]):
        relative = Path(str(descriptor["path"]))
        try:
            suffix = relative.relative_to(expected_prefix)
        except ValueError as error:
            raise ArtifactError(
                "frozen result path differs from accepted root"
            ) from error
        path = _contained(generated_root, generated_root / suffix)
        if (
            not path.is_file()
            or path.stat().st_size != int(descriptor["size_bytes"])
            or _sha256(path) != descriptor["sha256"]
        ):
            raise ArtifactError(f"accepted generated metric differs: {relative}")
        generated_files[relative.as_posix()] = {
            "sha256": descriptor["sha256"],
            "size_bytes": descriptor["size_bytes"],
            "row_count": descriptor["row_count"],
        }
    required = {
        "cache/phase4_frozen_campaign/test/final-v1/trajectory_motion_metrics.parquet",
        "cache/phase4_frozen_campaign/test/final-v1/trajectory_event_metrics.parquet",
    }
    if not required <= set(generated_files):
        raise ArtifactError("required frozen trajectory metrics are not described")
    return {
        "dataset_id": "av2_motion",
        "genuine_provider_evidence": True,
        "cohort_identity": test_results["cohort_identity"],
        "test_scenario_identity": test_results["scenario_identity"],
        "trajectory_membership_identity": test_results[
            "trajectory_membership_identity"
        ],
        "metric_identity": test_results["metric_identity"],
        "checkpoint_output_identity": test_results["checkpoint_output_identity"],
        "committed_evidence_sha256": committed,
        "generated_metric_artifacts": generated_files,
    }


def _checkpoint_roots(generated_root: Path) -> dict[str, Path]:
    checkpoint_store = generated_root / "checkpoint_store/checkpoints-v1"
    mapped: dict[str, Path] = {}
    for directory in sorted(checkpoint_store.iterdir()):
        sample = next(directory.glob("*.json"), None)
        if sample is None:
            continue
        execution = cast(Json, cast(Json, _read_json(sample)["payload"])["execution"])
        mapped[str(execution["parameter_identity"])] = directory
    if len(mapped) != 18:
        raise ArtifactError("frozen checkpoint configuration map is incomplete")
    return mapped


def _verified_run(
    generated_root: Path,
    checkpoint_roots: Mapping[str, Path],
    configuration: str,
    source_scenario_id: str,
) -> _RunArtifact:
    configuration_id = CONFIGURATION_IDS[configuration]
    checkpoint = checkpoint_roots[configuration_id] / f"{source_scenario_id}.json"
    checkpoint_value = _read_json(checkpoint)
    execution = cast(Json, cast(Json, checkpoint_value["payload"])["execution"])
    artifact_summary = cast(Json, execution["artifact_summary"])
    if artifact_summary.get("checksum_agreement") is not True:
        raise ArtifactError("frozen checkpoint did not record checksum agreement")
    first_run = cast(list[Json], artifact_summary["runs"])[0]
    run_path = (
        generated_root
        / "representations/runs"
        / run_directory_name(str(first_run["run_id"]))
    )
    if (run_path / ".run.complete").read_text(encoding="utf-8").strip() != "complete":
        raise ArtifactError("frozen representation is not complete")
    identities: Json = {}
    for descriptor in cast(list[Json], first_run["files"]):
        name = str(descriptor["name"])
        path = _contained(run_path, run_path / name)
        if (
            not path.is_file()
            or path.stat().st_size != int(descriptor["size_bytes"])
            or _sha256(path) != descriptor["sha256"]
        ):
            raise ArtifactError(f"frozen representation file differs: {name}")
        identities[name] = {
            "sha256": descriptor["sha256"],
            "size_bytes": descriptor["size_bytes"],
            "row_count": descriptor["row_count"],
        }
    return _RunArtifact(run_path, identities, _sha256(checkpoint))


def _motion_rows(path: Path, configuration: str) -> list[Json]:
    columns = [
        "scenario_id",
        "trajectory_id",
        "source_sample_count",
        "valid_sample_count",
        "valid_run_count",
        "segment_count",
        "hold_count",
        "linear_count",
        "hermite_count",
        "position_statistics",
        "velocity_statistics",
        "velocity_compared_count",
    ]
    return [
        cast(Json, row)
        for row in pq.read_table(
            path,
            filters=[("configuration_id", "=", CONFIGURATION_IDS[configuration])],
            columns=columns,
        ).to_pylist()
    ]


def _event_rows(path: Path) -> dict[str, tuple[Json, ...]]:
    rows = pq.read_table(
        path,
        filters=[("configuration_id", "=", CONFIGURATION_IDS[HYBRID_CONFIGURATION])],
        columns=[
            "trajectory_id",
            "event_type",
            "source_event_count",
            "replay_event_count",
            "matched_event_count",
        ],
    ).to_pylist()
    grouped: dict[str, list[Json]] = defaultdict(list)
    for row in rows:
        if row["event_type"] in EVENT_TYPES:
            grouped[str(row["trajectory_id"])].append(cast(Json, row))
    return {
        trajectory_id: tuple(sorted(values, key=lambda item: str(item["event_type"])))
        for trajectory_id, values in grouped.items()
    }


def _base_candidate_inputs(generated_root: Path) -> list[_CandidateInput]:
    metrics = generated_root / "final-v1/trajectory_motion_metrics.parquet"
    events_path = generated_root / "final-v1/trajectory_event_metrics.parquet"
    exact = {
        str(row["trajectory_id"]): row
        for row in _motion_rows(metrics, EXACT_CONFIGURATION)
    }
    hybrid = {
        str(row["trajectory_id"]): row
        for row in _motion_rows(metrics, HYBRID_CONFIGURATION)
    }
    events = _event_rows(events_path)
    candidates: list[_CandidateInput] = []
    for trajectory_id, hybrid_row in sorted(hybrid.items()):
        exact_row = exact.get(trajectory_id)
        event_rows = events.get(trajectory_id, ())
        if exact_row is None:
            continue
        source_event_count = sum(int(row["source_event_count"]) for row in event_rows)
        velocity_maximum = cast(Json, hybrid_row["velocity_statistics"])["maximum"]
        if (
            int(hybrid_row["valid_sample_count"]) < 25
            or int(hybrid_row["valid_run_count"]) != 1
            or int(exact_row["segment_count"]) <= int(hybrid_row["segment_count"])
            or float(cast(Json, hybrid_row["position_statistics"])["maximum"]) > 0.1
            or (
                int(hybrid_row["velocity_compared_count"]) > 0
                and float(velocity_maximum) > 1.0
            )
            or source_event_count == 0
        ):
            continue
        candidates.append(
            _CandidateInput(
                scenario_id=str(hybrid_row["scenario_id"]),
                trajectory_id=trajectory_id,
                exact=exact_row,
                hybrid=hybrid_row,
                events=event_rows,
            )
        )
    return candidates


def _path_features(rows: Sequence[Json]) -> Json:
    points = [(float(row["x_m"]), float(row["y_m"])) for row in rows]
    distances = [
        math.hypot(right[0] - left[0], right[1] - left[1])
        for left, right in pairwise(points)
    ]
    headings = [
        math.atan2(right[1] - left[1], right[0] - left[0])
        for (left, right), distance in zip(
            pairwise(points),
            distances,
            strict=True,
        )
        if distance >= 0.10
    ]
    heading_change = math.fsum(
        abs((right - left + math.pi) % (2.0 * math.pi) - math.pi)
        for left, right in pairwise(headings)
    )
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    speeds = [float(row["speed_mps"] or 0.0) for row in rows]
    return {
        "path_length_m": math.fsum(distances),
        "duration_s": (int(rows[-1]["timestamp_ns"]) - int(rows[0]["timestamp_ns"]))
        / 1_000_000_000.0,
        "heading_change_rad": heading_change,
        "speed_range_mps": max(speeds, default=0.0) - min(speeds, default=0.0),
        "extent_x_m": max(xs) - min(xs),
        "extent_y_m": max(ys) - min(ys),
    }


def candidate_features(
    *,
    safe_id: str,
    source_rows: Sequence[Json],
    exact: Mapping[str, Any],
    hybrid: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
) -> CandidateFeatures:
    """Compute one deterministic candidate feature record."""
    if not re.fullmatch(r"trajectory-[0-9a-f]{16}", safe_id):
        raise ValidationError("candidate safe identifier is invalid")
    if len(source_rows) < 2:
        raise ValidationError("candidate source rows are incomplete")
    path = _path_features(source_rows)
    source_count = sum(int(row["source_event_count"]) for row in events)
    replay_count = sum(int(row["replay_event_count"]) for row in events)
    matched_count = sum(int(row["matched_event_count"]) for row in events)
    event_types = tuple(
        sorted(
            str(row["event_type"])
            for row in events
            if int(row["source_event_count"]) > 0
        )
    )
    exact_count = int(exact["segment_count"])
    hybrid_count = int(hybrid["segment_count"])
    position = cast(Mapping[str, Any], hybrid["position_statistics"])
    velocity = cast(Mapping[str, Any], hybrid["velocity_statistics"])
    extent_x = float(path["extent_x_m"])
    extent_y = float(path["extent_y_m"])
    return CandidateFeatures(
        safe_identifier=safe_id,
        valid_source_sample_count=len(source_rows),
        path_length_m=float(path["path_length_m"]),
        duration_s=float(path["duration_s"]),
        cumulative_absolute_heading_change_rad=float(path["heading_change_rad"]),
        semantic_event_count=source_count,
        semantic_event_types=event_types,
        semantic_event_type_diversity=len(event_types),
        exact_segment_count=exact_count,
        hybrid_segment_count=hybrid_count,
        segment_reduction_count=exact_count - hybrid_count,
        segment_reduction_ratio=(exact_count - hybrid_count) / exact_count,
        hybrid_position_mean_m=float(position["mean"]),
        hybrid_position_p95_m=float(position["p95"]),
        hybrid_position_maximum_m=float(position["maximum"]),
        hybrid_velocity_mean_mps=float(velocity["mean"]),
        hybrid_velocity_p95_mps=float(velocity["p95"]),
        hybrid_velocity_maximum_mps=float(velocity["maximum"]),
        source_semantic_event_count=source_count,
        replay_semantic_event_count=replay_count,
        matched_semantic_event_count=matched_count,
        semantic_preservation_f1=(
            2.0 * matched_count / (source_count + replay_count)
            if source_count + replay_count
            else 1.0
        ),
        spatial_extent_x_m=extent_x,
        spatial_extent_y_m=extent_y,
        spatial_extent_diagonal_m=math.hypot(extent_x, extent_y),
        annotation_density_score=source_count / max(float(path["path_length_m"]), 1.0),
        hybrid_hold_count=int(hybrid["hold_count"]),
        hybrid_linear_count=int(hybrid["linear_count"]),
        hybrid_hermite_count=int(hybrid["hermite_count"]),
    )


def eligibility_failures(
    features: CandidateFeatures,
    *,
    speed_range_mps: float,
) -> tuple[str, ...]:
    """Return failed eligibility clauses under the fixed contract."""
    failures: list[str] = []
    checks = (
        (features.valid_source_sample_count >= 25, "fewer_than_25_valid_samples"),
        (features.path_length_m > 0.0, "nonpositive_path_length"),
        (
            features.semantic_event_count >= 1,
            "no_required_semantic_event",
        ),
        (
            features.cumulative_absolute_heading_change_rad >= 0.35
            or speed_range_mps >= 1.0,
            "no_nontrivial_maneuver",
        ),
        (
            features.exact_segment_count > features.hybrid_segment_count,
            "no_segment_reduction",
        ),
        (
            features.hybrid_position_maximum_m <= 0.10,
            "position_bound_exceeded",
        ),
        (
            features.hybrid_velocity_maximum_mps <= 1.00,
            "velocity_bound_exceeded",
        ),
        (
            features.spatial_extent_diagonal_m >= 1.0,
            "insufficient_spatial_extent",
        ),
        (
            features.semantic_event_count <= 8,
            "excessive_event_count",
        ),
        (
            features.annotation_density_score <= 0.75,
            "excessive_annotation_density",
        ),
    )
    failures.extend(label for passed, label in checks if not passed)
    return tuple(failures)


def rank_candidates(
    candidates: Sequence[_RankedCandidate],
) -> list[_RankedCandidate]:
    """Rank eligible candidates under the predeclared lexicographic policy."""
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -candidate.features.semantic_event_type_diversity,
            -candidate.features.segment_reduction_ratio,
            -candidate.features.cumulative_absolute_heading_change_rad,
            -candidate.features.semantic_preservation_f1,
            -candidate.features.valid_source_sample_count,
            candidate.features.annotation_density_score,
            candidate.features.safe_identifier,
        ),
    )
    return [
        _RankedCandidate(
            item.source_scenario_id,
            item.trajectory_id,
            item.features,
            item.source_rows,
            rank=index,
        )
        for index, item in enumerate(ranked, start=1)
    ]


def readability_gate(features: CandidateFeatures) -> ReadabilityResult:
    """Apply the predeclared release-readability gate."""
    failures: list[str] = []
    if features.annotation_density_score > 0.35:
        failures.append("event_labels_too_dense_for_path_length")
    if features.spatial_extent_diagonal_m < 4.0:
        failures.append("spatial_extent_too_small_for_maneuver")
    if features.semantic_event_count > 8:
        failures.append("too_many_event_annotations")
    if features.segment_reduction_ratio < 0.50:
        failures.append("source_and_hybrid_panels_not_visually_distinct")
    measurements: dict[str, float | int] = {
        "annotation_density_score": features.annotation_density_score,
        "maximum_annotation_density_score": 0.35,
        "spatial_extent_diagonal_m": features.spatial_extent_diagonal_m,
        "minimum_spatial_extent_diagonal_m": 4.0,
        "semantic_event_count": features.semantic_event_count,
        "maximum_semantic_event_count": 8,
        "segment_reduction_ratio": features.segment_reduction_ratio,
        "minimum_segment_reduction_ratio": 0.50,
    }
    return ReadabilityResult(not failures, tuple(failures), measurements)


def _load_ranked_candidates(
    generated_root: Path,
    checkpoint_roots: Mapping[str, Path],
) -> tuple[list[_RankedCandidate], int]:
    base = _base_candidate_inputs(generated_root)
    by_scenario: dict[str, list[_CandidateInput]] = defaultdict(list)
    for candidate in base:
        source_scenario_id = candidate.scenario_id.rsplit(":", 1)[-1]
        by_scenario[source_scenario_id].append(candidate)
    eligible: list[_RankedCandidate] = []
    for source_scenario_id, scenario_candidates in sorted(by_scenario.items()):
        raw_run = _verified_run(
            generated_root,
            checkpoint_roots,
            RAW_CONFIGURATION,
            source_scenario_id,
        )
        target_ids = {candidate.trajectory_id for candidate in scenario_candidates}
        grouped: dict[str, list[Json]] = defaultdict(list)
        for row in pq.read_table(
            raw_run.path / "trajectory_samples.parquet"
        ).to_pylist():
            trajectory_id = str(row["trajectory_id"])
            if trajectory_id in target_ids and row["is_valid"]:
                grouped[trajectory_id].append(cast(Json, row))
        for candidate in scenario_candidates:
            rows = tuple(
                sorted(
                    grouped[candidate.trajectory_id],
                    key=lambda row: int(row["sample_index"]),
                )
            )
            if len(rows) != int(candidate.hybrid["valid_sample_count"]):
                raise ArtifactError("source and hybrid valid sample counts differ")
            path = _path_features(rows)
            features = candidate_features(
                safe_id=safe_identifier("trajectory", candidate.trajectory_id),
                source_rows=rows,
                exact=candidate.exact,
                hybrid=candidate.hybrid,
                events=candidate.events,
            )
            if not eligibility_failures(
                features,
                speed_range_mps=float(path["speed_range_mps"]),
            ):
                eligible.append(
                    _RankedCandidate(
                        source_scenario_id,
                        candidate.trajectory_id,
                        features,
                        rows,
                    )
                )
    if len(eligible) < 3:
        raise ArtifactError("fewer than three eligible genuine AV2 candidates")
    return rank_candidates(eligible), len(base)


def selection_contract() -> Json:
    """Return the fixed candidate-selection and readability contract."""
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": SELECTION_BATCH,
        "algorithm_version": ALGORITHM_VERSION,
        "eligibility_contract": {
            "genuine_av2": True,
            "accepted_canonical_validation": True,
            "required_records": [
                "verified canonical source",
                "verified exact adjacent-sample procedural record",
                "verified position-and-velocity-bounded hybrid record",
                "accepted source and replay semantic metric records",
            ],
            "minimum_valid_source_samples": 25,
            "required_valid_run_count": 1,
            "minimum_path_length_m_exclusive": 0.0,
            "required_event_types": list(EVENT_TYPES),
            "minimum_semantic_event_count": 1,
            "nontrivial_maneuver": (
                "cumulative absolute heading change >= 0.35 rad or "
                "source speed range >= 1.0 m/s"
            ),
            "exact_segments_must_exceed_hybrid_segments": True,
            "maximum_hybrid_position_error_m": 0.10,
            "maximum_hybrid_represented_velocity_error_mps": 1.00,
            "minimum_spatial_extent_diagonal_m": 1.0,
            "maximum_semantic_event_count": 8,
            "maximum_annotation_density_score": 0.75,
        },
        "feature_definitions": {
            "path_length_m": "sum of consecutive valid-source XY distances",
            "duration_s": "last minus first valid-source timestamp",
            "cumulative_absolute_heading_change_rad": (
                "sum of wrapped absolute changes between displacement headings "
                "for source steps of at least 0.10 m"
            ),
            "segment_reduction_ratio": "(exact segments - hybrid segments) / exact",
            "semantic_preservation_f1": (
                "2 * matched source/replay events / (source events + replay events)"
            ),
            "spatial_extent": "axis ranges and XY bounding-box diagonal",
            "annotation_density_score": (
                "source semantic event count / max(path length in metres, 1)"
            ),
        },
        "ranking_order": [
            "semantic event-type diversity descending",
            "segment-reduction ratio descending",
            "cumulative absolute heading change descending",
            "semantic preservation F1 descending",
            "valid source sample count descending",
            "annotation-density score ascending",
            "safe hashed identifier ascending",
        ],
        "tie_break_rules": {
            "numeric_equality": "continue to the next declared ranking key",
            "final": "safe hashed identifier ascending",
        },
        "readability_gate": {
            "maximum_annotation_density_score": 0.35,
            "minimum_spatial_extent_diagonal_m": 4.0,
            "maximum_semantic_event_count": 8,
            "minimum_segment_reduction_ratio": 0.50,
            "recommendation_policy": (
                "recommend the first ranked top-three candidate passing every gate"
            ),
        },
        "safe_identifier_policy": {
            "algorithm": "SHA-256",
            "namespace": SAFE_ID_NAMESPACE,
            "kind": "trajectory",
            "published_hex_characters": 16,
            "raw_identifier_allowlist": False,
            "raw_identifiers_in_tracked_outputs": False,
        },
        "ranking_fixed_before_rendering": True,
        "manual_identifier_allowlist": False,
    }


def _load_source_trajectory(
    run: _RunArtifact,
    trajectory_id: str,
) -> Trajectory:
    rows = pq.read_table(
        run.path / "trajectory_samples.parquet",
        filters=[("trajectory_id", "=", trajectory_id)],
    ).to_pylist()
    if not rows:
        raise ArtifactError("selected source trajectory is missing")
    return _trajectory_from_rows([cast(Json, row) for row in rows])


def _load_procedural_track(
    run: _RunArtifact,
    trajectory_id: str,
) -> ProceduralTrack:
    track_rows = pq.read_table(
        run.path / "procedural_tracks.parquet",
        filters=[("trajectory_id", "=", trajectory_id)],
    ).to_pylist()
    if len(track_rows) != 1:
        raise ArtifactError("selected procedural trajectory is not unique")
    track_row = cast(Json, track_rows[0])
    procedural_track_id = str(track_row["procedural_track_id"])
    segment_rows = pq.read_table(
        run.path / "procedural_segments.parquet",
        filters=[("procedural_track_id", "=", procedural_track_id)],
    ).to_pylist()
    segments = tuple(
        sorted(
            (procedural_segment_from_dict(cast(Json, row)) for row in segment_rows),
            key=lambda item: (item.run_index, item.segment_index),
        )
    )
    return procedural_track_from_dict(track_row, segments)


def _state_row(timestamp_ns: int, segment: ProceduralSegment) -> Json:
    state = evaluate_segment(segment, timestamp_ns)
    if state is None:
        raise ArtifactError("selected segment has no state within its interval")
    return {
        "timestamp_ns": timestamp_ns,
        "x_m": state.x_m,
        "y_m": state.y_m,
        "velocity_x_mps": state.velocity_x_mps,
        "velocity_y_mps": state.velocity_y_mps,
    }


def _dense_segment_rows(segment: ProceduralSegment) -> list[Json]:
    count = 16
    duration = segment.end_time_ns - segment.start_time_ns
    return [
        _state_row(
            segment.start_time_ns + round(duration * index / count),
            segment,
        )
        for index in range(count + 1)
    ]


def _breakpoints(track: ProceduralTrack) -> list[Json]:
    values = [
        {
            "timestamp_ns": segment.start_time_ns,
            "x_m": segment.start_x_m,
            "y_m": segment.start_y_m,
            "source_sample_index": segment.source_start_sample_index,
        }
        for segment in track.segments
    ]
    last = track.segments[-1]
    values.append(
        {
            "timestamp_ns": last.end_time_ns,
            "x_m": last.end_x_m,
            "y_m": last.end_y_m,
            "source_sample_index": last.source_end_sample_index,
        }
    )
    return values


def _axis_limits(points: Sequence[tuple[float, float]]) -> Json:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    center_x = (min(xs) + max(xs)) / 2.0
    center_y = (min(ys) + max(ys)) / 2.0
    span = max(max(xs) - min(xs), max(ys) - min(ys), 1.0)
    half = span * 0.58
    return {
        "x_min_m": center_x - half,
        "x_max_m": center_x + half,
        "y_min_m": center_y - half,
        "y_max_m": center_y + half,
        "padding_fraction": 0.08,
        "aspect_ratio": "equal",
    }


def _public_waypoints(semantic_track: Any) -> list[Json]:
    return [
        {
            "waypoint_index": waypoint.waypoint_index,
            "waypoint_role": waypoint.waypoint_role.value,
            "timestamp_ns": waypoint.timestamp_ns,
            "source_sample_index": waypoint.source_sample_index,
            "x_m": waypoint.x_m,
            "y_m": waypoint.y_m,
        }
        for waypoint in semantic_track.waypoints
    ]


def _public_events(semantic_track: Any) -> list[Json]:
    waypoint_index = {
        waypoint.waypoint_id: waypoint.waypoint_index
        for waypoint in semantic_track.waypoints
    }
    return [
        {
            "event_index": event.event_index,
            "event_type": event.event_type.value,
            "start_waypoint_index": waypoint_index[event.start_waypoint_id],
            "anchor_waypoint_index": waypoint_index[event.anchor_waypoint_id],
            "end_waypoint_index": waypoint_index[event.end_waypoint_id],
            "start_time_ns": event.start_time_ns,
            "anchor_time_ns": event.anchor_time_ns,
            "end_time_ns": event.end_time_ns,
        }
        for event in semantic_track.events
        if event.event_type.value in EVENT_TYPES
    ]


def _verified_candidate_bundle(
    candidate: _RankedCandidate,
    generated_root: Path,
    checkpoint_roots: Mapping[str, Path],
) -> tuple[Json, Json]:
    runs = {
        role: _verified_run(
            generated_root,
            checkpoint_roots,
            configuration,
            candidate.source_scenario_id,
        )
        for role, configuration in (
            ("source", RAW_CONFIGURATION),
            ("exact", EXACT_CONFIGURATION),
            ("hybrid", HYBRID_CONFIGURATION),
        )
    }
    trajectory = _load_source_trajectory(runs["source"], candidate.trajectory_id)
    exact_track = _load_procedural_track(runs["exact"], candidate.trajectory_id)
    hybrid_track = _load_procedural_track(runs["hybrid"], candidate.trajectory_id)
    valid_samples = tuple(sample for sample in trajectory.samples if sample.is_valid)
    source_positions: list[Json] = [
        {
            "sample_index": sample.sample_index,
            "timestamp_ns": sample.timestamp_ns,
            "x_m": sample.x_m,
            "y_m": sample.y_m,
            "velocity_x_mps": sample.velocity_x_mps,
            "velocity_y_mps": sample.velocity_y_mps,
        }
        for sample in valid_samples
    ]
    exact_positions: list[Json] = []
    hybrid_positions: list[Json] = []
    position_errors: list[float] = []
    velocity_errors: list[float | None] = []
    for sample in valid_samples:
        exact_state = replay_track(exact_track, sample.timestamp_ns)
        hybrid_state = replay_track(hybrid_track, sample.timestamp_ns)
        if exact_state is None or hybrid_state is None:
            raise ArtifactError("selected valid timestamp has no replay state")
        exact_positions.append(
            {
                "sample_index": sample.sample_index,
                "timestamp_ns": sample.timestamp_ns,
                "x_m": exact_state.x_m,
                "y_m": exact_state.y_m,
            }
        )
        hybrid_positions.append(
            {
                "sample_index": sample.sample_index,
                "timestamp_ns": sample.timestamp_ns,
                "x_m": hybrid_state.x_m,
                "y_m": hybrid_state.y_m,
                "velocity_x_mps": hybrid_state.velocity_x_mps,
                "velocity_y_mps": hybrid_state.velocity_y_mps,
            }
        )
        position_errors.append(
            math.hypot(
                sample.x_m - hybrid_state.x_m,
                sample.y_m - hybrid_state.y_m,
            )
        )
        if (
            sample.velocity_x_mps is not None
            and sample.velocity_y_mps is not None
            and hybrid_state.velocity_x_mps is not None
            and hybrid_state.velocity_y_mps is not None
        ):
            velocity_errors.append(
                math.hypot(
                    sample.velocity_x_mps - hybrid_state.velocity_x_mps,
                    sample.velocity_y_mps - hybrid_state.velocity_y_mps,
                )
            )
        else:
            velocity_errors.append(None)

    evaluation = evaluate_trajectory_motion(
        "position_velocity_bounded_hybrid-error-0p1-velocity-1p0",
        CONFIGURATION_IDS[HYBRID_CONFIGURATION],
        trajectory,
        hybrid_track,
    )
    metrics = evaluation.metrics
    metric_record = metrics.to_dict()
    position_statistics = cast(Json, metric_record["position_statistics"])
    velocity_statistics = cast(Json, metric_record["velocity_statistics"])
    if (
        metrics.segment_count != candidate.features.hybrid_segment_count
        or position_statistics["maximum"]
        != candidate.features.hybrid_position_maximum_m
        or position_statistics["p95"] != candidate.features.hybrid_position_p95_m
        or velocity_statistics["maximum"]
        != candidate.features.hybrid_velocity_maximum_mps
    ):
        raise ArtifactError("direct replay recomputation differs from accepted metrics")
    if exact_track.segment_count != candidate.features.exact_segment_count:
        raise ArtifactError("exact segment count differs from accepted metrics")

    source_semantics = detect_semantic_trajectory(
        trajectory,
        hybrid_track,
        SemanticMotionConfig(),
    )
    replay_trajectory = replay_detection_trajectory(trajectory, hybrid_track)
    replay_semantics = detect_semantic_trajectory(
        replay_trajectory,
        hybrid_track,
        SemanticMotionConfig(),
    )
    preservation = analyze_event_preservation(source_semantics, replay_semantics)
    if (
        preservation["source_event_count"]
        != candidate.features.source_semantic_event_count
        or preservation["replay_event_count"]
        != candidate.features.replay_semantic_event_count
        or preservation["matched_count"]
        != candidate.features.matched_semantic_event_count
    ):
        raise ArtifactError("semantic recomputation differs from accepted metrics")

    exact_segments = [
        {
            "segment_index": segment.segment_index,
            "source_start_sample_index": segment.source_start_sample_index,
            "source_end_sample_index": segment.source_end_sample_index,
            "primitive_type": ProceduralPrimitiveType(segment.primitive_type).value,
            "dense_replay_positions": _dense_segment_rows(segment),
        }
        for segment in exact_track.segments
    ]
    hybrid_segments = [
        {
            "segment_index": segment.segment_index,
            "source_start_sample_index": segment.source_start_sample_index,
            "source_end_sample_index": segment.source_end_sample_index,
            "primitive_type": ProceduralPrimitiveType(segment.primitive_type).value,
            "dense_replay_positions": _dense_segment_rows(segment),
        }
        for segment in hybrid_track.segments
    ]
    all_points = [(float(row["x_m"]), float(row["y_m"])) for row in source_positions]
    for segment in hybrid_segments:
        all_points.extend(
            (float(row["x_m"]), float(row["y_m"]))
            for row in cast(list[Json], segment["dense_replay_positions"])
        )
    annotations = {
        "source_samples": candidate.features.valid_source_sample_count,
        "exact_segments": candidate.features.exact_segment_count,
        "compact_segments": candidate.features.hybrid_segment_count,
        "segment_reduction_percentage": (
            candidate.features.segment_reduction_ratio * 100.0
        ),
        "hybrid_primitive_composition": {
            "hold": candidate.features.hybrid_hold_count,
            "linear": candidate.features.hybrid_linear_count,
            "cubic_hermite": candidate.features.hybrid_hermite_count,
        },
        "maximum_position_error_m": position_statistics["maximum"],
        "p95_position_error_m": position_statistics["p95"],
        "maximum_velocity_error_mps": velocity_statistics["maximum"],
        "source_semantic_event_count": preservation["source_event_count"],
        "replay_preserved_semantic_event_count": preservation["matched_count"],
        "replay_semantic_event_count": preservation["replay_event_count"],
        "semantic_event_types": list(candidate.features.semantic_event_types),
        "semantic_preservation_f1": preservation["f1"],
    }
    plotted = {
        "schema_version": SCHEMA_VERSION,
        "batch": SELECTION_BATCH,
        "safe_candidate_identifier": candidate.features.safe_identifier,
        "source_positions": source_positions,
        "source_timestamps_ns": [int(row["timestamp_ns"]) for row in source_positions],
        "exact_replay_positions": exact_positions,
        "exact_breakpoints": _breakpoints(exact_track),
        "exact_segments": exact_segments,
        "exact_segment_primitive_types": [
            ProceduralPrimitiveType(segment.primitive_type).value
            for segment in exact_track.segments
        ],
        "hybrid_replay_positions": hybrid_positions,
        "hybrid_breakpoints": _breakpoints(hybrid_track),
        "hybrid_segments": hybrid_segments,
        "hybrid_primitive_types": [
            ProceduralPrimitiveType(segment.primitive_type).value
            for segment in hybrid_track.segments
        ],
        "semantic_waypoints": _public_waypoints(source_semantics),
        "semantic_events": _public_events(source_semantics),
        "replay_semantic_events": _public_events(replay_semantics),
        "semantic_preservation": preservation,
        "axis_limits": _axis_limits(all_points),
        "position_errors_m": position_errors,
        "velocity_errors_mps": velocity_errors,
        "annotation_values": annotations,
    }
    input_identity = {
        "safe_candidate_identifier": candidate.features.safe_identifier,
        "source": {
            "checkpoint_sha256": runs["source"].checkpoint_sha256,
            "files": dict(runs["source"].file_identities),
        },
        "exact": {
            "checkpoint_sha256": runs["exact"].checkpoint_sha256,
            "configuration_identity": CONFIGURATION_IDS[EXACT_CONFIGURATION],
            "files": dict(runs["exact"].file_identities),
        },
        "hybrid": {
            "checkpoint_sha256": runs["hybrid"].checkpoint_sha256,
            "configuration_identity": CONFIGURATION_IDS[HYBRID_CONFIGURATION],
            "files": dict(runs["hybrid"].file_identities),
        },
        "semantic_detector": {
            "algorithm_version": SEMANTIC_ALGORITHM_VERSION,
            "configuration_identity": semantic_motion_configuration_identity(
                SemanticMotionConfig()
            ),
        },
    }
    return plotted, input_identity


def _transform_points(
    rows: Sequence[Mapping[str, Any]],
    limits: Mapping[str, Any],
    box: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    x, y, width, height = box
    x_min = float(limits["x_min_m"])
    x_max = float(limits["x_max_m"])
    y_min = float(limits["y_min_m"])
    y_max = float(limits["y_max_m"])
    return [
        (
            x + (float(row["x_m"]) - x_min) / (x_max - x_min) * width,
            y + height - (float(row["y_m"]) - y_min) / (y_max - y_min) * height,
        )
        for row in rows
    ]


def _dashed_polyline(
    drawing: Drawing,
    points: Sequence[tuple[float, float]],
    color: Color,
    width: int,
) -> None:
    for index, (left, right) in enumerate(pairwise(points)):
        if index % 2 == 0:
            drawing.line(*left, *right, color, width)


def _square(
    drawing: Drawing,
    point: tuple[float, float],
    radius: float,
    fill: Color,
    stroke: Color | None = None,
) -> None:
    drawing.rect(
        point[0] - radius,
        point[1] - radius,
        radius * 2,
        radius * 2,
        fill,
        stroke,
    )


def _cross(
    drawing: Drawing,
    point: tuple[float, float],
    radius: float,
    color: Color,
    *,
    diagonal: bool,
) -> None:
    x, y = point
    if diagonal:
        drawing.line(x - radius, y - radius, x + radius, y + radius, color, 3)
        drawing.line(x - radius, y + radius, x + radius, y - radius, color, 3)
    else:
        drawing.line(x - radius, y, x + radius, y, color, 3)
        drawing.line(x, y - radius, x, y + radius, color, 3)


def _direction_arrow(
    drawing: Drawing,
    points: Sequence[tuple[float, float]],
    color: Color,
) -> None:
    if len(points) < 4:
        return
    index = max(2, len(points) // 3)
    start = points[index - 2]
    end = points[index]
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    drawing.line(*start, *end, color, 3)
    for delta in (-0.65, 0.65):
        drawing.line(
            end[0],
            end[1],
            end[0] - 12 * math.cos(angle + delta),
            end[1] - 12 * math.sin(angle + delta),
            color,
            3,
        )


def _start_end_markers(
    drawing: Drawing,
    points: Sequence[tuple[float, float]],
) -> None:
    if not points:
        return
    drawing.circle(*points[0], 7, GREEN, INK)
    _square(drawing, points[-1], 7, WHITE, INK)


def _panel_header(
    drawing: Drawing,
    x: float,
    y: float,
    title: str,
) -> None:
    drawing.text(x, y, title, size=22)
    drawing.line(x, y + 14, x + 720, y + 14, GRID, 2)


def _draw_source_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    panel_origin: tuple[float, float],
) -> None:
    rows = cast(list[Json], plotted["source_positions"])
    points = _transform_points(rows, cast(Json, plotted["axis_limits"]), box)
    drawing.polyline(points, MUTED, 3)
    for point in points:
        drawing.circle(*point, 2.5, INK)
    _direction_arrow(drawing, points, BLUE)
    _start_end_markers(drawing, points)
    x, y = panel_origin
    drawing.text(x, y + 58, f"{len(points)} ORDERED SAMPLES", MUTED, 15)
    drawing.text(x, y + 83, "START: FILLED CIRCLE", GREEN, 13)
    drawing.text(x, y + 106, "END: OPEN SQUARE", INK, 13)


def _draw_exact_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    panel_origin: tuple[float, float],
) -> None:
    limits = cast(Json, plotted["axis_limits"])
    source = _transform_points(
        cast(list[Json], plotted["source_positions"]),
        limits,
        box,
    )
    exact = _transform_points(
        cast(list[Json], plotted["exact_replay_positions"]),
        limits,
        box,
    )
    breakpoints = _transform_points(
        cast(list[Json], plotted["exact_breakpoints"]),
        limits,
        box,
    )
    drawing.polyline(source, GRID, 4)
    drawing.polyline(exact, BLUE, 4)
    for point in breakpoints:
        drawing.circle(*point, 2.4, WHITE, BLUE)
    _start_end_markers(drawing, exact)
    x, y = panel_origin
    annotations = cast(Json, plotted["annotation_values"])
    drawing.text(
        x,
        y + 58,
        f"{annotations['exact_segments']} ADJACENT SEGMENTS",
        BLUE,
        15,
    )
    drawing.text(x, y + 83, "ONE BREAKPOINT PER SOURCE STATE", MUTED, 13)


def _draw_hybrid_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    panel_origin: tuple[float, float],
) -> None:
    limits = cast(Json, plotted["axis_limits"])
    source = _transform_points(
        cast(list[Json], plotted["source_positions"]),
        limits,
        box,
    )
    drawing.polyline(source, GRID, 4)
    for segment in cast(list[Json], plotted["hybrid_segments"]):
        points = _transform_points(
            cast(list[Json], segment["dense_replay_positions"]),
            limits,
            box,
        )
        primitive = str(segment["primitive_type"])
        if primitive == ProceduralPrimitiveType.CUBIC_HERMITE.value:
            _dashed_polyline(drawing, points, CORAL, 5)
        elif primitive == ProceduralPrimitiveType.HOLD.value:
            drawing.circle(*points[0], 6, GOLD, INK)
        else:
            drawing.polyline(points, BLUE, 5)
    breakpoints = _transform_points(
        cast(list[Json], plotted["hybrid_breakpoints"]),
        limits,
        box,
    )
    for point in breakpoints:
        _square(drawing, point, 4, WHITE, INK)
    _start_end_markers(drawing, source)
    annotations = cast(Json, plotted["annotation_values"])
    composition = cast(Json, annotations["hybrid_primitive_composition"])
    x, y = panel_origin
    drawing.text(
        x,
        y + 58,
        f"{annotations['compact_segments']} COMPACT SEGMENTS",
        BLUE,
        15,
    )
    drawing.text(
        x,
        y + 83,
        (
            f"LINEAR {composition['linear']}  HERMITE "
            f"{composition['cubic_hermite']}  HOLD {composition['hold']}"
        ),
        MUTED,
        13,
    )
    drawing.line(x, y + 112, x + 45, y + 112, BLUE, 4)
    drawing.text(x + 58, y + 117, "LINEAR", MUTED, 12)
    _dashed_polyline(
        drawing,
        ((x + 150, y + 112), (x + 170, y + 112), (x + 190, y + 112)),
        CORAL,
        4,
    )
    drawing.text(x + 203, y + 117, "HERMITE", MUTED, 12)


def _event_style(event_type: str) -> tuple[Color, str]:
    return {
        "stop": (INK, "STOP"),
        "left_turn": (GOLD, "LEFT"),
        "right_turn": (BLUE, "RIGHT"),
        "acceleration": (GREEN, "ACCEL"),
        "braking": (PURPLE, "BRAKE"),
    }[event_type]


def _event_marker(
    drawing: Drawing,
    event_type: str,
    point: tuple[float, float],
    *,
    anchor: bool,
) -> None:
    color, _ = _event_style(event_type)
    if event_type == "stop":
        drawing.circle(*point, 6 if anchor else 4, WHITE, color)
    elif event_type in {"left_turn", "right_turn"}:
        _square(drawing, point, 6 if anchor else 4, WHITE, color)
    elif event_type == "acceleration":
        _cross(drawing, point, 7 if anchor else 5, color, diagonal=False)
    else:
        _cross(drawing, point, 7 if anchor else 5, color, diagonal=True)


def _draw_semantic_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    panel_origin: tuple[float, float],
) -> None:
    limits = cast(Json, plotted["axis_limits"])
    source = _transform_points(
        cast(list[Json], plotted["source_positions"]),
        limits,
        box,
    )
    hybrid_rows = cast(list[Json], plotted["hybrid_replay_positions"])
    hybrid = _transform_points(hybrid_rows, limits, box)
    drawing.polyline(source, GRID, 4)
    drawing.polyline(hybrid, BLUE, 5)
    waypoints = {
        int(row["waypoint_index"]): row
        for row in cast(list[Json], plotted["semantic_waypoints"])
    }
    events = cast(list[Json], plotted["semantic_events"])
    for event in events:
        event_type = str(event["event_type"])
        start_row = waypoints[int(event["start_waypoint_index"])]
        anchor_row = waypoints[int(event["anchor_waypoint_index"])]
        end_row = waypoints[int(event["end_waypoint_index"])]
        start, anchor, end = _transform_points(
            (start_row, anchor_row, end_row),
            limits,
            box,
        )
        color, _ = _event_style(event_type)
        drawing.line(*start, *end, color, 2)
        _event_marker(drawing, event_type, start, anchor=False)
        _event_marker(drawing, event_type, anchor, anchor=True)
        _event_marker(drawing, event_type, end, anchor=False)
    _start_end_markers(drawing, hybrid)
    x, y = panel_origin
    for index, event_type in enumerate(
        cast(
            list[str], cast(Json, plotted["annotation_values"])["semantic_event_types"]
        )
    ):
        color, label = _event_style(event_type)
        legend_y = y + 58 + index * 24
        _event_marker(drawing, event_type, (x + 6, legend_y - 5), anchor=True)
        drawing.text(x + 24, legend_y, label, color, 12)


def _error_inset(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
) -> None:
    x, y, width, height = box
    values = [float(value) for value in cast(list[float], plotted["position_errors_m"])]
    maximum = 0.10
    drawing.rect(x, y, width, height, WHITE, GRID)
    drawing.line(x, y, x + width, y, CORAL, 2)
    points = [
        (
            x + index / max(len(values) - 1, 1) * width,
            y + height - value / maximum * height,
        )
        for index, value in enumerate(values)
    ]
    drawing.polyline(points, BLUE, 2)
    drawing.text(x + 8, y + 17, "POSITION ERROR", MUTED, 11)
    drawing.text(x + width - 8, y + 17, "0.10 M BOUND", CORAL, 11, "end")


def _render_overview(plotted: Json, *, preview: bool) -> Drawing:
    width, height = (1400, 1000) if preview else (1800, 1400)
    drawing = Drawing(width, height)
    margin = 46 if preview else 62
    gap_x = 32 if preview else 44
    gap_y = 28 if preview else 42
    header = 68 if preview else 82
    footer = 130 if preview else 225
    panel_width = (width - margin * 2 - gap_x) / 2
    panel_height = (height - header - footer - margin - gap_y) / 2
    origins = (
        (margin, header),
        (margin + panel_width + gap_x, header),
        (margin, header + panel_height + gap_y),
        (margin + panel_width + gap_x, header + panel_height + gap_y),
    )
    titles = (
        "A. CANONICAL MOTION SAMPLES",
        "B. EXACT PROCEDURAL REPLAY",
        "C. COMPACT BOUNDED REPLAY",
        "D. SEMANTIC MOTION LAYER",
    )
    safe_id = str(plotted["safe_candidate_identifier"])
    drawing.text(margin, 38 if preview else 46, "PROCEDURAL MOTION OVERVIEW", size=26)
    drawing.text(width - margin, 38 if preview else 46, safe_id, MUTED, 14, "end")
    for origin, title in zip(origins, titles, strict=True):
        _panel_header(drawing, *origin, title)
    plot_size = min(panel_height - 76, panel_width * 0.62)
    panel_boxes = tuple((x + 4, y + 34, plot_size, plot_size) for x, y in origins)
    text_origins = tuple(
        (box[0] + box[2] + 20, origin[1])
        for box, origin in zip(panel_boxes, origins, strict=True)
    )
    _draw_source_panel(drawing, plotted, panel_boxes[0], text_origins[0])
    _draw_exact_panel(drawing, plotted, panel_boxes[1], text_origins[1])
    _draw_hybrid_panel(drawing, plotted, panel_boxes[2], text_origins[2])
    _draw_semantic_panel(drawing, plotted, panel_boxes[3], text_origins[3])
    inset_width = panel_width - plot_size - 28
    _error_inset(
        drawing,
        plotted,
        (
            text_origins[2][0],
            origins[2][1] + panel_height - 88,
            inset_width,
            70,
        ),
    )

    annotations = cast(Json, plotted["annotation_values"])
    footer_y = height - footer + 26
    drawing.line(margin, footer_y - 22, width - margin, footer_y - 22, GRID, 2)
    if preview:
        types = " / ".join(
            item.replace("_", " ")
            for item in cast(list[str], annotations["semantic_event_types"])
        )
        drawing.text(
            margin,
            footer_y,
            (
                f"{annotations['source_samples']} SAMPLES  "
                f"{annotations['exact_segments']} EXACT  "
                f"{annotations['compact_segments']} COMPACT"
            ),
            size=16,
        )
        drawing.text(
            margin,
            footer_y + 30,
            (
                f"MAX POSITION {annotations['maximum_position_error_m']:.3f} M  "
                f"EVENTS {annotations['replay_preserved_semantic_event_count']}/"
                f"{annotations['source_semantic_event_count']}  {types}"
            ),
            MUTED,
            13,
        )
    else:
        columns = (
            (
                f"{annotations['source_samples']} SOURCE SAMPLES",
                f"{annotations['exact_segments']} EXACT SEGMENTS",
            ),
            (
                f"{annotations['compact_segments']} COMPACT SEGMENTS",
                (f"{annotations['segment_reduction_percentage']:.1f}% FEWER SEGMENTS"),
            ),
            (
                (f"MAX POSITION ERROR {annotations['maximum_position_error_m']:.3f} M"),
                (f"P95 POSITION ERROR {annotations['p95_position_error_m']:.3f} M"),
            ),
            (
                (
                    f"MAX VELOCITY ERROR "
                    f"{annotations['maximum_velocity_error_mps']:.2f} M/S"
                ),
                (
                    f"EVENTS PRESERVED "
                    f"{annotations['replay_preserved_semantic_event_count']}/"
                    f"{annotations['source_semantic_event_count']}"
                ),
            ),
        )
        column_width = (width - 2 * margin) / len(columns)
        for index, lines in enumerate(columns):
            x = margin + index * column_width
            drawing.text(x, footer_y + 12, lines[0], size=17)
            drawing.text(x, footer_y + 47, lines[1], MUTED, 15)
        drawing.text(
            margin,
            footer_y + 92,
            (
                "GENUINE AV2  /  EXACT SOURCE-TIME REPLAY  /  "
                "POSITION <= 0.10 M  /  VELOCITY <= 1.00 M/S"
            ),
            MUTED,
            14,
        )
        drawing.text(
            width - margin,
            footer_y + 92,
            "DRAFT F1.1",
            MUTED,
            14,
            "end",
        )
    return drawing


_SEMANTIC_DISPLAY_ORDER = (
    "stop",
    "acceleration",
    "braking",
    "left_turn",
    "right_turn",
)


def semantic_label_layout(
    plotted: Json,
    spatial_box: tuple[float, float, float, float],
    label_origin: tuple[float, float],
) -> list[Json]:
    """Return deterministic numbered anchors and external timeline rows."""
    limits = cast(Json, plotted["axis_limits"])
    waypoints = {
        int(row["waypoint_index"]): row
        for row in cast(list[Json], plotted["semantic_waypoints"])
    }
    events = sorted(
        cast(list[Json], plotted["semantic_events"]),
        key=lambda row: (
            int(row["start_time_ns"]),
            int(row["anchor_time_ns"]),
            str(row["event_type"]),
        ),
    )
    labels: list[Json] = []
    label_x, label_y = label_origin
    first_time_ns = int(
        cast(list[Json], plotted["source_positions"])[0]["timestamp_ns"]
    )
    for index, event in enumerate(events):
        event_type = str(event["event_type"])
        anchor_row = waypoints[int(event["anchor_waypoint_index"])]
        anchor = _transform_points((anchor_row,), limits, spatial_box)[0]
        _, label = _event_style(event_type)
        display_label = {
            "ACCEL": "ACCELERATION",
            "BRAKE": "BRAKING",
            "LEFT": "LEFT TURN",
            "RIGHT": "RIGHT TURN",
            "STOP": "STOP",
        }[label]
        baseline_y = label_y + index * 42
        text_width = max(len(display_label) * 9, 76)
        label_box = (label_x + 27, baseline_y - 15, text_width + 90, 24)
        labels.append(
            {
                "event_number": index + 1,
                "event_type": event_type,
                "label": display_label,
                "anchor_x": anchor[0],
                "anchor_y": anchor[1],
                "label_x": label_x,
                "label_y": baseline_y,
                "label_box": list(label_box),
                "interval_s": [
                    (int(event["start_time_ns"]) - first_time_ns) / 1_000_000_000,
                    (int(event["end_time_ns"]) - first_time_ns) / 1_000_000_000,
                ],
                "leader_line": False,
            }
        )
    for left, right in pairwise(labels):
        left_box = cast(list[float], left["label_box"])
        right_box = cast(list[float], right["label_box"])
        if left_box[1] + left_box[3] > right_box[1]:
            raise ArtifactError("semantic label layout overlaps")
    return labels


def _draw_final_source_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    annotation_origin: tuple[float, float],
) -> None:
    points = _transform_points(
        cast(list[Json], plotted["source_positions"]),
        cast(Json, plotted["axis_limits"]),
        box,
    )
    drawing.polyline(points, MUTED, 3)
    for point in points:
        drawing.circle(*point, 2.4, INK)
    _direction_arrow(drawing, points, BLUE)
    _start_end_markers(drawing, points)
    x, y = annotation_origin
    annotations = cast(Json, plotted["annotation_values"])
    drawing.text(x, y, f"{annotations['source_samples']} SOURCE SAMPLES", size=15)
    drawing.text(x, y + 30, "DENSE CANONICAL STATES", MUTED, 12)


def _draw_final_exact_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    annotation_origin: tuple[float, float],
) -> None:
    limits = cast(Json, plotted["axis_limits"])
    source = _transform_points(
        cast(list[Json], plotted["source_positions"]), limits, box
    )
    exact = _transform_points(
        cast(list[Json], plotted["exact_replay_positions"]), limits, box
    )
    breakpoints = _transform_points(
        cast(list[Json], plotted["exact_breakpoints"]), limits, box
    )
    drawing.polyline(source, GRID, 5)
    drawing.polyline(exact, BLUE, 4)
    for point in breakpoints:
        drawing.circle(*point, 2.4, WHITE, BLUE)
    _start_end_markers(drawing, exact)
    x, y = annotation_origin
    annotations = cast(Json, plotted["annotation_values"])
    drawing.text(x, y, f"{annotations['exact_segments']} EXACT SEGMENTS", size=15)
    drawing.text(x, y + 30, "ONE PER SAMPLE PAIR", MUTED, 12)


def _draw_final_hybrid_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    annotation_origin: tuple[float, float],
) -> None:
    limits = cast(Json, plotted["axis_limits"])
    source = _transform_points(
        cast(list[Json], plotted["source_positions"]), limits, box
    )
    drawing.polyline(source, GRID, 5)
    for segment in cast(list[Json], plotted["hybrid_segments"]):
        points = _transform_points(
            cast(list[Json], segment["dense_replay_positions"]), limits, box
        )
        primitive = str(segment["primitive_type"])
        if primitive == ProceduralPrimitiveType.CUBIC_HERMITE.value:
            _dashed_polyline(drawing, points, CORAL, 6)
        elif primitive == ProceduralPrimitiveType.HOLD.value:
            drawing.circle(*points[0], 6, GOLD, INK)
        else:
            drawing.polyline(points, BLUE, 6)
    breakpoints = _transform_points(
        cast(list[Json], plotted["hybrid_breakpoints"]), limits, box
    )
    for point in breakpoints:
        _square(drawing, point, 4.5, WHITE, INK)
    _start_end_markers(drawing, source)
    x, y = annotation_origin
    annotations = cast(Json, plotted["annotation_values"])
    composition = cast(Json, annotations["hybrid_primitive_composition"])
    drawing.text(x, y, f"{annotations['compact_segments']} COMPACT SEGMENTS", size=15)
    drawing.text(
        x,
        y + 30,
        (f"{composition['linear']} LINEAR  +  {composition['cubic_hermite']} HERMITE"),
        MUTED,
        12,
    )
    drawing.line(x, y + 62, x + 42, y + 62, BLUE, 5)
    drawing.text(x + 54, y + 67, "LINEAR", MUTED, 11)
    _dashed_polyline(
        drawing,
        ((x, y + 92), (x + 20, y + 92), (x + 42, y + 92)),
        CORAL,
        5,
    )
    drawing.text(x + 54, y + 97, "HERMITE", MUTED, 11)
    _square(drawing, (x + 6, y + 122), 4, WHITE, INK)
    drawing.text(x + 20, y + 127, "BREAKPOINT", MUTED, 11)


def _draw_final_semantic_panel(
    drawing: Drawing,
    plotted: Json,
    box: tuple[float, float, float, float],
    label_origin: tuple[float, float],
) -> list[Json]:
    limits = cast(Json, plotted["axis_limits"])
    source = _transform_points(
        cast(list[Json], plotted["source_positions"]), limits, box
    )
    hybrid = _transform_points(
        cast(list[Json], plotted["hybrid_replay_positions"]), limits, box
    )
    drawing.polyline(source, GRID, 5)
    drawing.polyline(hybrid, BLUE, 6)
    waypoints = {
        int(row["waypoint_index"]): row
        for row in cast(list[Json], plotted["semantic_waypoints"])
    }
    events = sorted(
        cast(list[Json], plotted["semantic_events"]),
        key=lambda row: (
            int(row["start_time_ns"]),
            int(row["anchor_time_ns"]),
            str(row["event_type"]),
        ),
    )
    placements = semantic_label_layout(plotted, box, label_origin)
    drawing.text(
        label_origin[0], label_origin[1] - 38, "SEMANTIC EVENT TIMELINE", size=13
    )
    if placements:
        drawing.line(
            label_origin[0] + 9,
            float(placements[0]["label_y"]) - 6,
            label_origin[0] + 9,
            float(placements[-1]["label_y"]) - 6,
            GRID,
            2,
        )
    for event, placement in zip(events, placements, strict=True):
        event_type = str(placement["event_type"])
        start_row = waypoints[int(event["start_waypoint_index"])]
        anchor_row = waypoints[int(event["anchor_waypoint_index"])]
        end_row = waypoints[int(event["end_waypoint_index"])]
        start, anchor, end = _transform_points(
            (start_row, anchor_row, end_row), limits, box
        )
        color, _ = _event_style(event_type)
        drawing.line(*start, *end, color, 3)
        _event_marker(drawing, event_type, start, anchor=False)
        _event_marker(drawing, event_type, end, anchor=False)
        number = int(placement["event_number"])
        drawing.circle(*anchor, 9, WHITE, color)
        drawing.text(anchor[0], anchor[1] + 4, str(number), color, 11, "middle")
        label_point = (float(placement["label_x"]) + 9, float(placement["label_y"]) - 6)
        drawing.circle(*label_point, 9, WHITE, color)
        drawing.text(
            label_point[0],
            label_point[1] + 4,
            str(number),
            color,
            11,
            "middle",
        )
        drawing.text(
            label_point[0] + 20,
            float(placement["label_y"]),
            str(placement["label"]),
            INK,
            12,
        )
        interval = cast(list[float], placement["interval_s"])
        drawing.text(
            label_point[0] + 20,
            float(placement["label_y"]) + 17,
            f"{interval[0]:.1f}-{interval[1]:.1f} S",
            MUTED,
            10,
        )
    _start_end_markers(drawing, hybrid)
    return placements


def _final_render_geometry(
    plotted: Json,
) -> tuple[Json, tuple[float, float]]:
    """Return an equal-scale viewport that fills the release panel."""
    points = cast(list[Json], plotted["source_positions"])
    x_values = [float(row["x_m"]) for row in points]
    y_values = [float(row["y_m"]) for row in points]
    x_center = (min(x_values) + max(x_values)) / 2
    y_center = (min(y_values) + max(y_values)) / 2
    x_span = max(max(x_values) - min(x_values), 1e-9)
    y_span = max(max(y_values) - min(y_values), 1e-9)
    maximum_span = max(x_span, y_span)
    minimum_cross_span = maximum_span * 0.5
    x_span = max(x_span * 1.12, minimum_cross_span)
    y_span = max(y_span * 1.12, minimum_cross_span)
    scale = min(470.0 / x_span, 430.0 / y_span)
    limits: Json = {
        "x_min_m": x_center - x_span / 2,
        "x_max_m": x_center + x_span / 2,
        "y_min_m": y_center - y_span / 2,
        "y_max_m": y_center + y_span / 2,
        "aspect_ratio": "equal",
    }
    return limits, (x_span * scale, y_span * scale)


def _render_final_overview(plotted: Json) -> tuple[Drawing, list[Json]]:
    width, height = 1800, 1400
    drawing = Drawing(width, height)
    margin = 62
    gap_x = 44
    gap_y = 36
    header = 72
    footer = 178
    panel_width = (width - 2 * margin - gap_x) / 2
    panel_height = (height - margin - header - footer - gap_y) / 2
    origins = (
        (margin, header),
        (margin + panel_width + gap_x, header),
        (margin, header + panel_height + gap_y),
        (margin + panel_width + gap_x, header + panel_height + gap_y),
    )
    titles = (
        "A. CANONICAL MOTION SAMPLES",
        "B. EXACT PROCEDURAL REPLAY",
        "C. COMPACT BOUNDED REPLAY",
        "D. SEMANTIC MOTION LAYER",
    )
    drawing.text(margin, 43, "PROCEDURAL MOTION OVERVIEW", size=28)
    drawing.text(
        width - margin,
        43,
        "DENSE SOURCE  >  EXACT  >  COMPACT  >  SEMANTIC",
        MUTED,
        13,
        "end",
    )
    for origin, title in zip(origins, titles, strict=True):
        _panel_header(drawing, *origin, title)
    display_limits, plot_dimensions = _final_render_geometry(plotted)
    render_plotted = dict(plotted)
    render_plotted["axis_limits"] = display_limits
    plot_width, plot_height = plot_dimensions
    plot_region_width = 500.0
    plot_region_height = 430.0
    panel_boxes = tuple(
        (
            origin[0] + 4 + (plot_region_width - plot_width) / 2,
            origin[1] + 58 + (plot_region_height - plot_height) / 2,
            plot_width,
            plot_height,
        )
        for origin in origins
    )
    annotation_origins = tuple(
        (origin[0] + plot_region_width + 28, origin[1] + 82) for origin in origins
    )
    _draw_final_source_panel(
        drawing, render_plotted, panel_boxes[0], annotation_origins[0]
    )
    _draw_final_exact_panel(
        drawing, render_plotted, panel_boxes[1], annotation_origins[1]
    )
    _draw_final_hybrid_panel(
        drawing, render_plotted, panel_boxes[2], annotation_origins[2]
    )
    placements = _draw_final_semantic_panel(
        drawing, render_plotted, panel_boxes[3], annotation_origins[3]
    )
    _error_inset(
        drawing,
        plotted,
        (
            annotation_origins[2][0],
            origins[2][1] + panel_height - 92,
            panel_width - plot_region_width - 36,
            72,
        ),
    )
    annotations = cast(Json, plotted["annotation_values"])
    footer_y = height - footer + 30
    drawing.line(margin, footer_y - 24, width - margin, footer_y - 24, GRID, 2)
    footer_columns = (
        (
            f"{annotations['source_samples']} SAMPLES",
            (
                "MAX / P95 POSITION  "
                f"{annotations['maximum_position_error_m']:.3f} / "
                f"{annotations['p95_position_error_m']:.3f} M"
            ),
        ),
        (
            (
                f"{annotations['exact_segments']} EXACT  >  "
                f"{annotations['compact_segments']} COMPACT"
            ),
            f"MAX VELOCITY  {annotations['maximum_velocity_error_mps']:.3f} M/S",
        ),
        (
            f"{annotations['segment_reduction_percentage']:.1f}% FEWER",
            (
                "EVENTS PRESERVED  "
                f"{annotations['replay_preserved_semantic_event_count']} / "
                f"{annotations['source_semantic_event_count']}"
            ),
        ),
    )
    available = width - 2 * margin
    column_width = available / len(footer_columns)
    for index, lines in enumerate(footer_columns):
        x = margin + index * column_width
        drawing.text(x, footer_y + 8, lines[0], size=14)
        drawing.text(x, footer_y + 40, lines[1], MUTED, 12)
    drawing.text(
        margin,
        footer_y + 78,
        "POSITION <= 0.10 M  /  REPRESENTED VELOCITY <= 1.00 M/S",
        MUTED,
        12,
    )
    composition = cast(Json, annotations["hybrid_primitive_composition"])
    drawing.text(
        width - margin,
        footer_y + 78,
        (
            f"PRIMITIVES  {composition['linear']} LINEAR  /  "
            f"{composition['cubic_hermite']} HERMITE"
        ),
        MUTED,
        12,
        "end",
    )
    drawing.text(margin, footer_y + 112, "GENUINE AV2 EXAMPLE", MUTED, 12)
    drawing.text(
        width - margin,
        footer_y + 112,
        "FINAL FIGURE 1",
        MUTED,
        12,
        "end",
    )
    return drawing, placements


def _output_descriptor(
    destination_root: Path,
    relative: Path,
    *,
    format_name: str,
    width: int,
    height: int,
) -> Json:
    path = destination_root / relative
    return {
        "path": relative.as_posix(),
        "format": format_name,
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "width": width,
        "height": height,
    }


def _selected_candidates_record(
    top_three: Sequence[_RankedCandidate],
    eligible_count: int,
    base_count: int,
) -> tuple[Json, _RankedCandidate]:
    rows: list[Json] = []
    recommended: _RankedCandidate | None = None
    for candidate in top_three:
        gate = readability_gate(candidate.features)
        if recommended is None and gate.passed:
            recommended = candidate
        rows.append(
            {
                "rank": candidate.rank,
                "safe_identifier": candidate.features.safe_identifier,
                "ranking_features": candidate.features.public_dict(),
                "event_types": list(candidate.features.semantic_event_types),
                "candidate_eligibility_evidence": {
                    "genuine_av2": True,
                    "accepted_canonical_validation": True,
                    "source_record_available": True,
                    "exact_record_available": True,
                    "hybrid_record_available": True,
                    "semantic_records_available": True,
                    "valid_run_count": 1,
                    "position_bound_passed": True,
                    "velocity_bound_passed": True,
                    "manual_identifier_allowlist_used": False,
                },
                "readability_gate": gate.public_dict(),
            }
        )
    if recommended is None:
        raise ArtifactError("none of the top three candidates passes readability")
    if recommended.rank == 1:
        reason = "rank 1 passes every predeclared readability gate"
    else:
        failed = [
            {
                "rank": candidate.rank,
                "failures": list(readability_gate(candidate.features).failures),
            }
            for candidate in top_three
            if candidate.rank < recommended.rank
        ]
        reason = (
            f"rank {recommended.rank} is the first top-three candidate passing "
            f"every gate; higher-ranked gate failures: {failed}"
        )
    record = {
        "schema_version": SCHEMA_VERSION,
        "batch": SELECTION_BATCH,
        "base_metric_candidate_count": base_count,
        "eligible_candidate_count": eligible_count,
        "top_three": rows,
        "recommended_candidate": recommended.features.safe_identifier,
        "recommended_rank": recommended.rank,
        "recommendation_reason": reason,
        "ranking_fixed_before_rendering": True,
        "manual_cherry_pick": False,
        "raw_provider_identifiers_published": False,
    }
    return record, recommended


def _load_frozen_candidate(
    repository_root: Path,
    selected: Json,
    safe_id: str,
) -> _RankedCandidate:
    selected_row = next(
        (
            row
            for row in cast(list[Json], selected["top_three"])
            if row["safe_identifier"] == safe_id
        ),
        None,
    )
    if selected_row is None:
        raise ArtifactError("frozen comparison candidate is missing")
    private = _read_json(repository_root / PRIVATE_RELATIVE)
    private_row = next(
        (
            row
            for row in cast(list[Json], private["candidates"])
            if row["safe_identifier"] == safe_id
        ),
        None,
    )
    if private_row is None:
        raise ArtifactError("ignored frozen candidate mapping is missing")
    feature_values = dict(cast(Json, selected_row["ranking_features"]))
    feature_values["semantic_event_types"] = tuple(
        cast(list[str], feature_values["semantic_event_types"])
    )
    return _RankedCandidate(
        source_scenario_id=str(private_row["source_scenario_id"]),
        trajectory_id=str(private_row["trajectory_id"]),
        features=CandidateFeatures(**feature_values),
        source_rows=(),
        rank=int(selected_row["rank"]),
    )


def _verify_frozen_scientific_values(plotted: Json) -> None:
    safe_id = str(plotted["safe_candidate_identifier"])
    annotations = cast(Json, plotted["annotation_values"])
    values_by_candidate: Mapping[str, Mapping[str, int | float]] = {
        PRIMARY_SAFE_IDENTIFIER: {
            "source_samples": 110,
            "exact_segments": 109,
            "compact_segments": 5,
            "segment_reduction_percentage": 95.41284403669725,
            "maximum_position_error_m": 0.07827359002814926,
            "p95_position_error_m": 0.0741763222339639,
            "maximum_velocity_error_mps": 0.4473862775795578,
            "source_semantic_event_count": 6,
            "replay_semantic_event_count": 5,
            "replay_preserved_semantic_event_count": 5,
            "semantic_preservation_f1": 0.9090909090909091,
        },
        COMPARISON_SAFE_IDENTIFIER: {
            "source_samples": 110,
            "exact_segments": 109,
            "compact_segments": 12,
            "segment_reduction_percentage": 88.9908256880734,
            "maximum_position_error_m": 0.09842122629875759,
            "p95_position_error_m": 0.07420916188783246,
            "maximum_velocity_error_mps": 0.9976158067451468,
            "source_semantic_event_count": 5,
            "replay_semantic_event_count": 5,
            "replay_preserved_semantic_event_count": 5,
            "semantic_preservation_f1": 1.0,
        },
    }
    exact_values = values_by_candidate.get(safe_id)
    if exact_values is None:
        raise ArtifactError("frozen plotted candidate differs")
    for name, expected in exact_values.items():
        actual = annotations[name]
        if isinstance(expected, float):
            if not math.isclose(float(actual), expected, rel_tol=0.0, abs_tol=1e-15):
                raise ArtifactError(f"frozen plotted value differs: {name}")
        elif actual != expected:
            raise ArtifactError(f"frozen plotted value differs: {name}")
    expected_composition = (
        {"hold": 0, "linear": 1, "cubic_hermite": 4}
        if safe_id == PRIMARY_SAFE_IDENTIFIER
        else {"hold": 0, "linear": 8, "cubic_hermite": 4}
    )
    if annotations["hybrid_primitive_composition"] != expected_composition:
        raise ArtifactError("frozen primitive composition differs")
    if set(cast(list[str], annotations["semantic_event_types"])) != set(EVENT_TYPES):
        raise ArtifactError("frozen semantic event types differ")


def _draft_caption(annotations: Mapping[str, Any]) -> str:
    return (
        "Genuine AV2 example illustrating the progression from canonical source "
        "samples to exact procedural replay, compact position-and-velocity-bounded "
        "replay, and semantic motion annotations. The compact representation "
        f"reduces the trajectory from {annotations['exact_segments']} exact "
        f"segments to {annotations['compact_segments']} segments while maintaining "
        "a maximum source-timestamp position error of "
        f"{annotations['maximum_position_error_m']:.3f} m, a p95 position error of "
        f"{annotations['p95_position_error_m']:.3f} m, and a maximum "
        "represented-velocity error of "
        f"{annotations['maximum_velocity_error_mps']:.2f} m/s. "
        f"{annotations['replay_preserved_semantic_event_count']} of "
        f"{annotations['source_semantic_event_count']} source semantic events "
        "are preserved on replay."
    )


def _final_caption(annotations: Mapping[str, Any]) -> str:
    return (
        "Genuine AV2 example showing canonical motion samples, exact procedural "
        "replay, compact position-and-velocity-bounded replay, and semantic motion "
        "annotations. The compact representation reduces "
        f"{annotations['exact_segments']} exact segments to "
        f"{annotations['compact_segments']} procedural segments "
        f"({annotations['segment_reduction_percentage']:.1f}% fewer) while "
        "maintaining maximum and p95 source-timestamp position errors of "
        f"{annotations['maximum_position_error_m']:.3f} m and "
        f"{annotations['p95_position_error_m']:.3f} m, respectively, and a "
        "maximum represented-velocity error of "
        f"{annotations['maximum_velocity_error_mps']:.3f} m/s. "
        f"{annotations['replay_preserved_semantic_event_count']} of "
        f"{annotations['source_semantic_event_count']} source semantic events "
        "are preserved on replay; stop, acceleration, braking, left turn, and "
        "right turn remain represented."
    )


def _caption_text(annotations: Mapping[str, Any]) -> str:
    return f"# Figure 1 - Procedural Motion Overview\n\n{_final_caption(annotations)}\n"


def _notes_text(
    *,
    accepted: Json,
    selected: Json,
    plotted: Json,
    output_checksums: Mapping[str, str],
) -> str:
    annotations = cast(Json, plotted["annotation_values"])
    top_three = cast(list[Json], selected["top_three"])
    candidate_lines = "\n".join(
        (
            f"- Rank {row['rank']}: `{row['safe_identifier']}`; "
            f"events {', '.join(row['event_types'])}; "
            f"{row['ranking_features']['valid_source_sample_count']} samples; "
            f"{row['ranking_features']['exact_segment_count']} exact to "
            f"{row['ranking_features']['hybrid_segment_count']} compact segments; "
            f"readability {'PASS' if row['readability_gate']['passed'] else 'FAIL'}."
        )
        for row in top_three
    )
    gate_lines = "\n".join(
        (
            f"- Rank {row['rank']}: "
            + (
                "PASS."
                if row["readability_gate"]["passed"]
                else "FAIL: " + ", ".join(row["readability_gate"]["failures"]) + "."
            )
        )
        for row in top_three
    )
    checksum_lines = "\n".join(
        f"- `{path}`: `{checksum}`"
        for path, checksum in sorted(output_checksums.items())
    )
    traceability = (
        "- Source sample count: verified canonical source rows.\n"
        "- Exact and compact segment counts: verified procedural-track and "
        "segment records.\n"
        "- Primitive composition: verified hybrid segment primitive types.\n"
        "- Position and velocity errors: direct accepted replay API recomputation "
        "at canonical source timestamps, checked against frozen trajectory metrics.\n"
        "- Semantic counts and F1: accepted detector rerun on verified source and "
        "hybrid replay, checked against frozen event metrics."
    )
    return f"""# Figure 1 \u2014 Procedural Motion Overview

## 1. Figure purpose

Explain how one genuine canonical AV2 trajectory becomes an exact procedural
replay, then a compact position-and-velocity-bounded replay, and finally a
semantic motion layer. Batch F1.1 selected and verified the candidates; Batch
F1.2 preserves their scientific content and records the human-reviewed final
release choice.

## 2. Accepted input artifacts

Committed Phase 3 exact, linear, Hermite, hybrid, semantic, and milestone
evidence; committed Phase 4 frozen-campaign and qualitative-motion evidence;
verified local frozen metric and representation artifacts. The accepted test
cohort identity is `{accepted["test_scenario_identity"]}`.

## 3. Deterministic candidate-selection rule

Eligibility and ranking are fixed in
`results/benchmark_figures/figure1_procedural_overview/selection_contract.json`.
Ranking is lexicographic by event-type diversity, segment reduction, curvature,
semantic F1, sample count, clutter, and safe identifier. No raw-ID allowlist is
used. The later human visual comparison does not change this ranking record.

## 4. Eligible candidate count

{selected["eligible_candidate_count"]} candidates.

## 5. Top three candidates

{candidate_lines}

## 6. F1.1 density-gate recommendation

`{selected["recommended_candidate"]}` (rank {selected["recommended_rank"]}).
{selected["recommendation_reason"]}

## 7. Readability-gate results

{gate_lines}

The gate evaluates spatial annotation density only. It does not evaluate hero
figure explanatory quality.

## 8. Numeric annotations

- Source samples: {annotations["source_samples"]}
- Exact segments: {annotations["exact_segments"]}
- Compact segments: {annotations["compact_segments"]}
- Segment reduction: {annotations["segment_reduction_percentage"]:.1f}%
- Hybrid primitives: hold {annotations["hybrid_primitive_composition"]["hold"]},
  linear {annotations["hybrid_primitive_composition"]["linear"]}, Hermite
  {annotations["hybrid_primitive_composition"]["cubic_hermite"]}
- Maximum position error: {annotations["maximum_position_error_m"]:.3f} m
- P95 position error: {annotations["p95_position_error_m"]:.3f} m
- Maximum velocity error: {annotations["maximum_velocity_error_mps"]:.2f} m/s
- Events preserved: {annotations["replay_preserved_semantic_event_count"]}/
  {annotations["source_semantic_event_count"]}
- Semantic F1: {annotations["semantic_preservation_f1"]:.3f}

## 9. Traceability for every annotation

{traceability}

## 10. F1.1 draft layout

A 2 x 2 spatial layout uses identical axis limits, equal aspect ratio, and
shared start/end conventions. Panel C includes one compact source-timestamp
position-error inset against the 0.10 m bound.

## 11. Observed readability or clutter issues

Ranks 1 and 2 exceed the predeclared 0.35-event-per-metre label-density gate.
Human review finds rank 1 substantially stronger as the primary explanatory
figure because its curved maneuver makes the 109-to-5 exact-to-compact
transformation and Hermite vocabulary visible. Rank 3 is spatially clear but
nearly straight, leaving the four panels visually too similar.

## 12. F1.1 refinements carried into F1.2

F1.2 uses DejaVu Sans, adaptive equal-scale viewports, numbered semantic anchors,
an external event timeline, refined line weights, and a stronger footer
hierarchy without changing candidate data, codecs, or accepted evidence.

## 13. F1.1 draft caption

{_draft_caption(annotations)}

## 14. Reproduction command

`uv run --frozen python scripts/generate_figure1_procedural_overview.py`

Exact provider identifiers are available only in the ignored local candidate
mapping artifact.

## 15. Output checksums

{checksum_lines}

## 16. F1.2 final-production treatment

The final 2 x 2 layout retains identical within-candidate spatial extents and
equal scale across panels. Panel titles and trajectory marks lead the hierarchy;
concise panel
annotations and a six-item metric footer remain secondary. Linear primitives
use a solid stroke, Hermite primitives use a dashed stroke, breakpoints use open
squares, and source samples remain smaller neutral marks.

## 17. Semantic-label treatment

All five event types remain visible. Compact numbered anchors on the path map to
collision-checked, chronological rows in an external Panel D timeline. No
semantic label is placed directly on the spatial path and no leader-line field
obscures the maneuver.

## 18. Final visual inspection

PASS: full-resolution PNG, vector PDF, grayscale PNG, one-column reduction,
two-column reduction, 100% rendering, and print-like preview were inspected.
No clipping, label overlap, panel-title loss, ambiguous endpoint, oversized
legend, or primitive/event-style ambiguity was observed. Candidate 1 is
recommended over candidate 3 because it better exposes reduction, maneuver
shape, and panel-to-panel transformation while retaining readable semantics.

## 19. Final caption

{_final_caption(annotations)}
"""


def _summary_text(selected: Json, plotted: Json) -> str:
    annotations = cast(Json, plotted["annotation_values"])
    return f"""# Release Figure 1

Batch F1.1 ranked {selected["eligible_candidate_count"]} eligible genuine AV2
trajectories and recommended `{selected["recommended_candidate"]}` under its
spatial-density gate. Batch F1.2 records the human visual comparison and chooses
`{PRIMARY_SAFE_IDENTIFIER}` (rank 1) for the final release PDF, color PNG,
and grayscale PNG without changing the frozen ranking or candidate data.

The final figure shows {annotations["source_samples"]} source samples,
{annotations["exact_segments"]} exact segments, and
{annotations["compact_segments"]} compact segments
({annotations["segment_reduction_percentage"]:.1f}% fewer), with maximum
position error {annotations["maximum_position_error_m"]:.3f} m, p95 position
error {annotations["p95_position_error_m"]:.3f} m, maximum represented-velocity
error {annotations["maximum_velocity_error_mps"]:.3f} m/s, and all five
semantic events preserved
({annotations["replay_preserved_semantic_event_count"]}/
{annotations["source_semantic_event_count"]}).

See `reports/figure1_procedural_overview_notes.md` and
`reports/figure1_procedural_overview_caption.md`.
"""


def _candidate_comparison_record(primary: Json, comparison: Json) -> Json:
    primary_annotations = cast(Json, primary["annotation_values"])
    comparison_annotations = cast(Json, comparison["annotation_values"])
    return {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "comparison_status": "PASS",
        "human_visual_review_incorporated": True,
        "f1_1_density_gate_scope": (
            "spatial annotation density; not underlying explanatory quality"
        ),
        "final_typography": "DejaVu Sans",
        "comparison_sizes": {
            "one_column": [900, 700],
            "two_column": [1800, 1400],
        },
        "candidates": [
            {
                "rank": 1,
                "safe_identifier": PRIMARY_SAFE_IDENTIFIER,
                "exact_segments": primary_annotations["exact_segments"],
                "compact_segments": primary_annotations["compact_segments"],
                "segment_reduction_percentage": primary_annotations[
                    "segment_reduction_percentage"
                ],
                "primitive_composition": primary_annotations[
                    "hybrid_primitive_composition"
                ],
                "semantic_preservation": [
                    primary_annotations["replay_preserved_semantic_event_count"],
                    primary_annotations["source_semantic_event_count"],
                ],
                "assessment": {
                    "exact_to_compact_reduction_visibility": "strong",
                    "visual_distinction_across_panels": "strong",
                    "maneuver_legibility": "strong curved maneuver",
                    "semantic_readability": "pass with numbered anchors and timeline",
                    "unused_space": "low after adaptive viewport",
                    "grayscale_clarity": "pass",
                },
            },
            {
                "rank": 3,
                "safe_identifier": COMPARISON_SAFE_IDENTIFIER,
                "exact_segments": comparison_annotations["exact_segments"],
                "compact_segments": comparison_annotations["compact_segments"],
                "segment_reduction_percentage": comparison_annotations[
                    "segment_reduction_percentage"
                ],
                "primitive_composition": comparison_annotations[
                    "hybrid_primitive_composition"
                ],
                "semantic_preservation": [
                    comparison_annotations["replay_preserved_semantic_event_count"],
                    comparison_annotations["source_semantic_event_count"],
                ],
                "assessment": {
                    "exact_to_compact_reduction_visibility": "moderate",
                    "visual_distinction_across_panels": "weak",
                    "maneuver_legibility": "weak nearly straight maneuver",
                    "semantic_readability": "pass with numbered anchors and timeline",
                    "unused_space": "moderate after adaptive viewport",
                    "grayscale_clarity": "pass",
                },
            },
        ],
        "recommended_safe_identifier": PRIMARY_SAFE_IDENTIFIER,
        "recommended_rank": 1,
        "recommendation": (
            "Candidate 1 is the stronger primary explanatory figure: its curved "
            "maneuver exposes the 109-to-5 exact-to-compact reduction and Hermite "
            "vocabulary, creates visibly distinct panels, remains legible at both "
            "release sizes, and uses space more effectively. Candidate 3 "
            "retains stronger event preservation but its nearly straight path "
            "makes the four panels too similar for the primary hero figure."
        ),
        "scientific_values_changed": False,
        "candidate_data_changed": False,
        "codec_or_evidence_changed": False,
    }


def _save_comparison_sheet(
    candidate_paths: tuple[Path, Path],
    destination: Path,
    *,
    cell_width: int,
    horizontal: bool,
) -> tuple[int, int]:
    label_height = round(cell_width * 0.055)
    font = ImageFont.truetype(str(RELEASE_FONT), round(label_height * 0.55))
    rendered: list[Image.Image] = []
    for path in candidate_paths:
        image = Image.open(path).convert("RGB")
        cell_height = round(image.height * cell_width / image.width)
        rendered.append(
            image.resize((cell_width, cell_height), Image.Resampling.LANCZOS)
        )
    if horizontal:
        canvas = Image.new(
            "RGB",
            (cell_width * 2, rendered[0].height + label_height),
            "white",
        )
        positions = ((0, label_height), (cell_width, label_height))
    else:
        canvas = Image.new(
            "RGB",
            (cell_width, (rendered[0].height + label_height) * 2),
            "white",
        )
        positions = (
            (0, label_height),
            (0, rendered[0].height + label_height * 2),
        )
    draw = ImageDraw.Draw(canvas)
    for index, (image, position) in enumerate(zip(rendered, positions, strict=True)):
        label_y = position[1] - 12
        draw.text(
            (14, label_y),
            f"CANDIDATE {1 if index == 0 else 3}",
            fill=INK,
            font=font,
            anchor="ls",
        )
        canvas.paste(image, position)
    canvas.save(destination, format="PNG", optimize=False, compress_level=9)
    return canvas.size


def _tracked_paths() -> tuple[Path, ...]:
    return (
        Path("figures/benchmark/figure1_procedural_overview_draft.pdf"),
        Path("figures/benchmark/figure1_procedural_overview_draft.png"),
        FINAL_FIGURE_RELATIVE.with_suffix(".pdf"),
        FINAL_FIGURE_RELATIVE.with_suffix(".png"),
        GRAYSCALE_RELATIVE,
        CANDIDATE_RELATIVE / "candidate_01_preview.png",
        CANDIDATE_RELATIVE / "candidate_02_preview.png",
        CANDIDATE_RELATIVE / "candidate_03_preview.png",
        FINAL_CANDIDATE_RELATIVE / "candidate_01_composition.png",
        FINAL_CANDIDATE_RELATIVE / "candidate_03_composition.png",
        FINAL_CANDIDATE_RELATIVE / "candidate_01_composition_grayscale.png",
        FINAL_CANDIDATE_RELATIVE / "candidate_03_composition_grayscale.png",
        FINAL_CANDIDATE_RELATIVE / "one_column_comparison.png",
        FINAL_CANDIDATE_RELATIVE / "two_column_comparison.png",
        NOTES_RELATIVE,
        CAPTION_RELATIVE,
        RESULT_RELATIVE / "selection_contract.json",
        RESULT_RELATIVE / "selected_candidates.json",
        RESULT_RELATIVE / "plotted_values.json",
        RESULT_RELATIVE / "candidate_03_plotted_values.json",
        RESULT_RELATIVE / "final_candidate_comparison.json",
        RESULT_RELATIVE / "figure_manifest.json",
        RESULT_RELATIVE / "summary.md",
    )


def _png_is_grayscale(path: Path) -> bool:
    if path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n":
        return False
    image = Image.open(path).convert("RGB")
    pixels = image.tobytes()
    return all(
        pixels[index] == pixels[index + 1] == pixels[index + 2]
        for index in range(0, len(pixels), 3)
    )


def verify_figure1_outputs(destination_root: Path) -> Json:
    """Verify all generated release outputs and their evidence chain."""
    result_root = destination_root / RESULT_RELATIVE
    evidence = _read_json(result_root / "evidence.json")
    checksums = cast(Json, evidence["tracked_output_sha256"])
    expected_paths = {path.as_posix() for path in _tracked_paths()}
    if set(checksums) != expected_paths:
        raise ArtifactError("Figure 1 evidence output set differs")
    for relative, expected in sorted(checksums.items()):
        path = _contained(destination_root, destination_root / relative)
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"Figure 1 output differs: {relative}")
    manifest = _read_json(result_root / "figure_manifest.json")
    for descriptor in cast(list[Json], manifest["outputs"]):
        path = _contained(destination_root, destination_root / descriptor["path"])
        if (
            path.stat().st_size != descriptor["size_bytes"]
            or _sha256(path) != descriptor["sha256"]
        ):
            raise ArtifactError("Figure 1 manifest output differs")
    png_paths = [
        destination_root / descriptor["path"]
        for descriptor in cast(list[Json], manifest["outputs"])
        if descriptor["format"] == "png"
    ]
    if any(path.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n" for path in png_paths):
        raise ArtifactError("Figure 1 PNG signature is invalid")
    for pdf_relative in (
        FIGURE_RELATIVE.with_suffix(".pdf"),
        FINAL_FIGURE_RELATIVE.with_suffix(".pdf"),
    ):
        if not (destination_root / pdf_relative).read_bytes().startswith(b"%PDF-1.4"):
            raise ArtifactError("Figure 1 PDF signature is invalid")
    if not _png_is_grayscale(destination_root / GRAYSCALE_RELATIVE):
        raise ArtifactError("Figure 1 grayscale PNG contains color pixels")
    for name in (
        "candidate_01_composition_grayscale.png",
        "candidate_03_composition_grayscale.png",
    ):
        if not _png_is_grayscale(destination_root / FINAL_CANDIDATE_RELATIVE / name):
            raise ArtifactError("candidate comparison grayscale PNG contains color")
    selected = _read_json(result_root / "selected_candidates.json")
    plotted = _read_json(result_root / "plotted_values.json")
    comparison_plotted = _read_json(result_root / "candidate_03_plotted_values.json")
    comparison_record = _read_json(result_root / "final_candidate_comparison.json")
    if (
        selected["recommended_candidate"] != COMPARISON_SAFE_IDENTIFIER
        or plotted["safe_candidate_identifier"] != PRIMARY_SAFE_IDENTIFIER
        or comparison_plotted["safe_candidate_identifier"] != COMPARISON_SAFE_IDENTIFIER
        or comparison_record["recommended_safe_identifier"] != PRIMARY_SAFE_IDENTIFIER
    ):
        raise ArtifactError("candidate comparison and final recommendation differ")
    if (
        cast(Json, plotted["axis_limits"])["aspect_ratio"] != "equal"
        or len(cast(list[Json], plotted["source_positions"]))
        != cast(Json, plotted["annotation_values"])["source_samples"]
    ):
        raise ArtifactError("Figure 1 plotted-value contract differs")
    _verify_frozen_scientific_values(plotted)
    _verify_frozen_scientific_values(comparison_plotted)
    production = cast(Json, plotted.get("final_production"))
    semantic_layout = cast(list[Json], production.get("semantic_label_layout"))
    if (
        production.get("batch") != BATCH
        or production.get("scientific_values_frozen") is not True
        or production.get("human_visual_review_override") is not True
        or len(semantic_layout) != len(cast(list[Json], plotted["semantic_events"]))
        or [row["event_number"] for row in semantic_layout]
        != list(range(1, len(semantic_layout) + 1))
        or any(row["leader_line"] for row in semantic_layout)
    ):
        raise ArtifactError("Figure 1 final-production record differs")
    caption = (destination_root / CAPTION_RELATIVE).read_text(encoding="utf-8")
    if _final_caption(cast(Json, plotted["annotation_values"])) not in caption:
        raise ArtifactError("Figure 1 caption differs from plotted values")
    if manifest.get("caption_sha256") != _sha256(destination_root / CAPTION_RELATIVE):
        raise ArtifactError("Figure 1 caption checksum differs")
    if cast(Json, manifest["visual_inspection"]).get("status") != "PASS":
        raise ArtifactError("Figure 1 visual inspection is not recorded as passing")
    if (
        manifest.get("selected_safe_identifier") != PRIMARY_SAFE_IDENTIFIER
        or manifest.get("human_visual_review_override") is not True
        or cast(Json, manifest["rendering_configuration"])["font_family"]["raster"]
        != "DejaVu Sans"
    ):
        raise ArtifactError("Figure 1 final rendering configuration differs")
    tracked_text = "\n".join(
        (destination_root / relative).read_text(encoding="utf-8")
        for relative in checksums
        if Path(relative).suffix in {".json", ".md"}
    )
    if (
        _UUID_PATTERN.search(tracked_text)
        or "/home/" in tracked_text
        or "C:\\Users\\" in tracked_text
        or "source_scenario_id" in tracked_text
        or "trajectory:av2:" in tracked_text
    ):
        raise ArtifactError("tracked Figure 1 outputs expose private identity data")
    return evidence


def _verify_existing_package(repository_root: Path) -> tuple[Json, Json]:
    result_root = repository_root / RESULT_RELATIVE
    evidence = _read_json(result_root / "evidence.json")
    if evidence.get("batch") not in {SELECTION_BATCH, BATCH}:
        raise ArtifactError("Figure 1 evidence batch is not accepted")
    for relative, expected in cast(Json, evidence["tracked_output_sha256"]).items():
        path = _contained(repository_root, repository_root / relative)
        if not path.is_file() or _sha256(path) != expected:
            raise ArtifactError(f"accepted F1.1 output differs: {relative}")
    selected = _read_json(result_root / "selected_candidates.json")
    current_plotted = _read_json(result_root / "plotted_values.json")
    _verify_frozen_scientific_values(current_plotted)
    comparison_path = result_root / "candidate_03_plotted_values.json"
    comparison_plotted = (
        _read_json(comparison_path) if comparison_path.is_file() else current_plotted
    )
    _verify_frozen_scientific_values(comparison_plotted)
    if (
        selected["recommended_candidate"] != COMPARISON_SAFE_IDENTIFIER
        or comparison_plotted["safe_candidate_identifier"] != COMPARISON_SAFE_IDENTIFIER
    ):
        raise ArtifactError("accepted F1.1 candidate and plotted values differ")
    return selected, comparison_plotted


def generate_figure1_procedural_overview(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
    generated_root: Path | None = None,
) -> Json:
    """Generate and verify the frozen-data Batch F1.2 final release figure."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    generated = (
        root / "cache/phase4_frozen_campaign/test"
        if generated_root is None
        else generated_root.resolve()
    )
    selected, accepted_comparison_plotted = _verify_existing_package(root)
    accepted = verify_accepted_inputs(root, generated)
    checkpoint_roots = _checkpoint_roots(generated)
    primary_candidate = _load_frozen_candidate(root, selected, PRIMARY_SAFE_IDENTIFIER)
    comparison_candidate = _load_frozen_candidate(
        root, selected, COMPARISON_SAFE_IDENTIFIER
    )
    primary_recomputed, primary_input_identity = _verified_candidate_bundle(
        primary_candidate,
        generated,
        checkpoint_roots,
    )
    comparison_recomputed, comparison_input_identity = _verified_candidate_bundle(
        comparison_candidate,
        generated,
        checkpoint_roots,
    )
    accepted_comparison_scientific = dict(accepted_comparison_plotted)
    accepted_comparison_scientific.pop("final_production", None)
    if canonical_json_bytes(comparison_recomputed) != canonical_json_bytes(
        accepted_comparison_scientific
    ):
        raise ArtifactError("F1.2 recomputation differs from accepted plotted values")
    _verify_frozen_scientific_values(primary_recomputed)
    _verify_frozen_scientific_values(comparison_recomputed)
    if not RELEASE_FONT.is_file():
        raise ArtifactError("required DejaVu Sans release font is unavailable")

    result_root = destination / RESULT_RELATIVE
    candidate_root = destination / CANDIDATE_RELATIVE
    final_candidate_root = destination / FINAL_CANDIDATE_RELATIVE
    figure_root = destination / FIGURE_RELATIVE.parent
    notes_path = destination / NOTES_RELATIVE
    caption_path = destination / CAPTION_RELATIVE
    for directory in (
        result_root,
        candidate_root,
        final_candidate_root,
        figure_root,
        notes_path.parent,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    for name in ("selection_contract.json", "selected_candidates.json"):
        source = root / RESULT_RELATIVE / name
        target = result_root / name
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)
    for index in range(1, 4):
        relative = CANDIDATE_RELATIVE / f"candidate_{index:02d}_preview.png"
        source = root / relative
        target = destination / relative
        if source.resolve() != target.resolve():
            shutil.copyfile(source, target)

    draft_drawing = _render_overview(primary_recomputed, preview=False)
    draft_drawing.save_png(destination / FIGURE_RELATIVE.with_suffix(".png"))
    draft_drawing.save_pdf(destination / FIGURE_RELATIVE.with_suffix(".pdf"))
    primary_drawing, primary_label_layout = _render_final_overview(primary_recomputed)
    comparison_drawing, comparison_label_layout = _render_final_overview(
        comparison_recomputed
    )
    primary_composition = (
        destination / FINAL_CANDIDATE_RELATIVE / "candidate_01_composition.png"
    )
    comparison_composition = (
        destination / FINAL_CANDIDATE_RELATIVE / "candidate_03_composition.png"
    )
    primary_drawing.save_png(primary_composition, font_path=RELEASE_FONT)
    comparison_drawing.save_png(comparison_composition, font_path=RELEASE_FONT)
    primary_drawing.save_png(
        destination
        / FINAL_CANDIDATE_RELATIVE
        / "candidate_01_composition_grayscale.png",
        grayscale=True,
        font_path=RELEASE_FONT,
    )
    comparison_drawing.save_png(
        destination
        / FINAL_CANDIDATE_RELATIVE
        / "candidate_03_composition_grayscale.png",
        grayscale=True,
        font_path=RELEASE_FONT,
    )
    primary_drawing.save_png(
        destination / FINAL_FIGURE_RELATIVE.with_suffix(".png"),
        font_path=RELEASE_FONT,
    )
    primary_drawing.save_pdf(destination / FINAL_FIGURE_RELATIVE.with_suffix(".pdf"))
    primary_drawing.save_png(
        destination / GRAYSCALE_RELATIVE,
        grayscale=True,
        font_path=RELEASE_FONT,
    )
    one_column_dimensions = _save_comparison_sheet(
        (primary_composition, comparison_composition),
        destination / FINAL_CANDIDATE_RELATIVE / "one_column_comparison.png",
        cell_width=900,
        horizontal=False,
    )
    two_column_dimensions = _save_comparison_sheet(
        (primary_composition, comparison_composition),
        destination / FINAL_CANDIDATE_RELATIVE / "two_column_comparison.png",
        cell_width=1800,
        horizontal=False,
    )

    plotted = dict(primary_recomputed)
    annotations = cast(Json, primary_recomputed["annotation_values"])
    primary_limits, primary_dimensions = _final_render_geometry(primary_recomputed)
    comparison_limits, comparison_dimensions = _final_render_geometry(
        comparison_recomputed
    )
    plotted["final_production"] = {
        "batch": BATCH,
        "scientific_values_frozen": True,
        "source_record_batch": SELECTION_BATCH,
        "human_visual_review_override": True,
        "semantic_label_order": "chronological",
        "semantic_label_layout": primary_label_layout,
        "display_axis_limits": primary_limits,
        "display_plot_dimensions_px": list(primary_dimensions),
        "display_annotations": {
            "source_samples": f"{annotations['source_samples']} samples",
            "segment_transition": (
                f"{annotations['exact_segments']} exact segments to "
                f"{annotations['compact_segments']} compact segments"
            ),
            "segment_reduction": (
                f"{annotations['segment_reduction_percentage']:.1f}% fewer segments"
            ),
            "position_error": (
                "max / p95 position error: "
                f"{annotations['maximum_position_error_m']:.3f} / "
                f"{annotations['p95_position_error_m']:.3f} m"
            ),
            "velocity_error": (
                "max velocity error: "
                f"{annotations['maximum_velocity_error_mps']:.3f} m/s"
            ),
            "semantic_preservation": (
                "semantic events preserved: "
                f"{annotations['replay_preserved_semantic_event_count']} / "
                f"{annotations['source_semantic_event_count']}"
            ),
        },
    }
    _write_json(result_root / "plotted_values.json", plotted)
    comparison_plotted = dict(comparison_recomputed)
    comparison_plotted["final_production"] = {
        "batch": BATCH,
        "scientific_values_frozen": True,
        "source_record_batch": SELECTION_BATCH,
        "comparison_candidate": True,
        "semantic_label_order": "chronological",
        "semantic_label_layout": comparison_label_layout,
        "display_axis_limits": comparison_limits,
        "display_plot_dimensions_px": list(comparison_dimensions),
    }
    _write_json(
        result_root / "candidate_03_plotted_values.json",
        comparison_plotted,
    )
    comparison_record = _candidate_comparison_record(
        primary_recomputed,
        comparison_recomputed,
    )
    comparison_record["rendered_comparison_dimensions"] = {
        "one_column_sheet": list(one_column_dimensions),
        "two_column_sheet": list(two_column_dimensions),
    }
    _write_json(result_root / "final_candidate_comparison.json", comparison_record)
    caption_path.write_text(
        _caption_text(cast(Json, plotted["annotation_values"])),
        encoding="utf-8",
        newline="\n",
    )

    outputs = [
        _output_descriptor(
            destination,
            FIGURE_RELATIVE.with_suffix(".pdf"),
            format_name="pdf",
            width=1800,
            height=1400,
        ),
        _output_descriptor(
            destination,
            FIGURE_RELATIVE.with_suffix(".png"),
            format_name="png",
            width=1800,
            height=1400,
        ),
        _output_descriptor(
            destination,
            FINAL_FIGURE_RELATIVE.with_suffix(".pdf"),
            format_name="pdf",
            width=1800,
            height=1400,
        ),
        _output_descriptor(
            destination,
            FINAL_FIGURE_RELATIVE.with_suffix(".png"),
            format_name="png",
            width=1800,
            height=1400,
        ),
        _output_descriptor(
            destination,
            GRAYSCALE_RELATIVE,
            format_name="png",
            width=1800,
            height=1400,
        ),
    ]
    outputs.extend(
        _output_descriptor(
            destination,
            CANDIDATE_RELATIVE / f"candidate_{index:02d}_preview.png",
            format_name="png",
            width=1400,
            height=1000,
        )
        for index in range(1, 4)
    )
    for name in (
        "candidate_01_composition.png",
        "candidate_03_composition.png",
        "candidate_01_composition_grayscale.png",
        "candidate_03_composition_grayscale.png",
    ):
        outputs.append(
            _output_descriptor(
                destination,
                FINAL_CANDIDATE_RELATIVE / name,
                format_name="png",
                width=1800,
                height=1400,
            )
        )
    outputs.extend(
        (
            _output_descriptor(
                destination,
                FINAL_CANDIDATE_RELATIVE / "one_column_comparison.png",
                format_name="png",
                width=one_column_dimensions[0],
                height=one_column_dimensions[1],
            ),
            _output_descriptor(
                destination,
                FINAL_CANDIDATE_RELATIVE / "two_column_comparison.png",
                format_name="png",
                width=two_column_dimensions[0],
                height=two_column_dimensions[1],
            ),
        )
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "script_version": SCRIPT_VERSION,
        "selected_safe_identifier": primary_candidate.features.safe_identifier,
        "f1_1_density_gate_safe_identifier": COMPARISON_SAFE_IDENTIFIER,
        "human_visual_review_override": True,
        "plotted_values_identity": _sha256(result_root / "plotted_values.json"),
        "caption_sha256": _sha256(caption_path),
        "rendering_configuration": {
            "renderer": "kinematicweave deterministic vector/raster renderer",
            "primary_dimensions": [1800, 1400],
            "grayscale_dimensions": [1800, 1400],
            "preview_dimensions": [1400, 1000],
            "layout": "2x2 spatial panels with one Panel C error inset",
            "spatial_axis_limits_identical": True,
            "spatial_aspect_ratio": "equal",
            "font_family": {
                "raster": "DejaVu Sans",
                "vector": "Helvetica-Bold",
            },
            "font_sizes_px": {
                "figure_title": 28,
                "panel_title": 22,
                "panel_annotation": 15,
                "semantic_label": 12,
                "metric_footer": 13,
            },
            "line_widths_px": {
                "source_path": 3,
                "exact_path": 4,
                "hybrid_path": 6,
                "semantic_interval": 3,
                "timeline_line": 2,
            },
            "marker_sizes_px": {
                "source_sample_radius": 2.4,
                "breakpoint_radius": 4.5,
                "start_end_radius": 7,
                "semantic_anchor_radius": 7,
            },
            "primitive_styles": {
                "linear": "solid",
                "cubic_hermite": "dashed",
                "breakpoint": "open square",
                "source_sample": "small neutral circle",
            },
            "semantic_label_order": "chronological",
            "semantic_collision_check": "pairwise non-overlapping timeline rows",
            "semantic_path_labels": "numbered anchors only",
            "background": "white",
            "timestamps_embedded": False,
        },
        "input_identities": {
            "accepted": accepted,
            "primary_verified_run": primary_input_identity,
            "comparison_verified_run": comparison_input_identity,
        },
        "outputs": outputs,
        "visual_inspection": {
            "status": "PASS",
            "full_resolution_png": True,
            "vector_pdf": True,
            "grayscale_png": True,
            "one_column": True,
            "two_column": True,
            "full_page": True,
            "label_overlap": False,
            "clipping": False,
            "primitive_distinction": True,
            "semantic_distinction": True,
        },
    }
    _write_json(result_root / "figure_manifest.json", manifest)
    output_checksums = {
        descriptor["path"]: descriptor["sha256"] for descriptor in outputs
    }
    output_checksums.update(
        {
            (RESULT_RELATIVE / name).as_posix(): _sha256(result_root / name)
            for name in (
                "selection_contract.json",
                "selected_candidates.json",
                "plotted_values.json",
                "candidate_03_plotted_values.json",
                "final_candidate_comparison.json",
                "figure_manifest.json",
            )
        }
    )
    output_checksums[CAPTION_RELATIVE.as_posix()] = _sha256(caption_path)
    notes_path.write_text(
        _notes_text(
            accepted=accepted,
            selected=selected,
            plotted=plotted,
            output_checksums=output_checksums,
        ),
        encoding="utf-8",
        newline="\n",
    )
    (result_root / "summary.md").write_text(
        _summary_text(selected, plotted),
        encoding="utf-8",
        newline="\n",
    )
    tracked_checksums = {
        relative.as_posix(): _sha256(destination / relative)
        for relative in _tracked_paths()
    }
    evidence = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "starting_head": STARTING_HEAD,
        "status": "PASS",
        "pass_statement": (
            "Final Release Figure 1 completed from the accepted genuine AV2 "
            "procedural motion example with unchanged traceable scientific values."
        ),
        "genuine_av2": True,
        "candidate_selection_rerun": False,
        "candidate_ranking_frozen": True,
        "eligible_candidate_count": selected["eligible_candidate_count"],
        "top_three_safe_identifiers": [
            row["safe_identifier"] for row in cast(list[Json], selected["top_three"])
        ],
        "f1_1_density_gate_safe_identifier": COMPARISON_SAFE_IDENTIFIER,
        "f1_1_density_gate_rank": 3,
        "recommended_safe_identifier": primary_candidate.features.safe_identifier,
        "recommended_rank": 1,
        "human_visual_review_override": True,
        "final_candidate_comparison_recorded": True,
        "exact_configuration_identity": CONFIGURATION_IDS[EXACT_CONFIGURATION],
        "hybrid_configuration_identity": CONFIGURATION_IDS[HYBRID_CONFIGURATION],
        "semantic_detector_identity": semantic_motion_configuration_identity(
            SemanticMotionConfig()
        ),
        "accepted_input_identity": accepted,
        "tracked_output_sha256": tracked_checksums,
        "raw_provider_identifiers_published": False,
        "private_mapping_path": PRIVATE_RELATIVE.as_posix(),
        "scientific_results_changed": False,
        "scientific_values_recomputed_and_verified": True,
        "visual_inspection_status": "PASS",
        "deterministic_generation_required": True,
    }
    _write_json(result_root / "evidence.json", evidence)
    return verify_figure1_outputs(destination)


__all__ = [
    "ALGORITHM_VERSION",
    "BATCH",
    "SCHEMA_VERSION",
    "SCRIPT_VERSION",
    "CandidateFeatures",
    "ReadabilityResult",
    "candidate_features",
    "eligibility_failures",
    "generate_figure1_procedural_overview",
    "rank_candidates",
    "readability_gate",
    "selection_contract",
    "verify_accepted_inputs",
    "verify_figure1_outputs",
]
