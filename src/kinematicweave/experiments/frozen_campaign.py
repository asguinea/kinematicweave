"""Frozen pilot-before-test controls for the Phase 4 motion campaign."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any, cast

from kinematicweave.canonical import canonical_json_text, canonical_sha256
from kinematicweave.errors import ArtifactError, SchemaError, ValidationError
from kinematicweave.experiments.motion_cohort import CohortRole, MotionCohortUnit
from kinematicweave.experiments.motion_sweep import read_ranked_cohort_role

SCHEMA_VERSION = "1.0"
FROZEN_MATRIX_IDENTITY = (
    "c87a88e2f6a6a443a2654f5785cec16ab15e8ad8e2b1fef2d6d2bde8d5d59599"
)
FROZEN_COHORT_IDENTITY = (
    "dde0528d20bd942a504378aa48ace2f5386d555a2441eb076f9b804e7b040166"
)
PILOT_SCENARIO_COUNT = 50
TEST_SCENARIO_COUNT = 300
CONFIGURATION_COUNT = 18


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError(f"{field_name} must be nonempty text")
    return value


def _sha256(value: object, field_name: str) -> str:
    text = _text(value, field_name)
    if len(text) != 64 or any(
        character not in "0123456789abcdef" for character in text
    ):
        raise ValidationError(f"{field_name} must be lowercase SHA-256")
    return text


def _positive_int(value: object, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValidationError(f"{field_name} must be a positive integer")
    return value


@dataclass(frozen=True, slots=True)
class PilotCompletion:
    """Immutable evidence that permits the frozen test cohort to be opened."""

    cohort_identity: str
    matrix_identity: str
    pilot_scenario_identity: str
    trajectory_membership_identity: str
    cache_snapshot_identity: str
    checkpoint_output_identity: str
    scenario_count: int
    configuration_count: int
    scenario_configuration_count: int
    failure_count: int
    protocol_changed: bool
    resource_feasible: bool
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise ValidationError("pilot completion schema_version differs")
        for field_name in (
            "cohort_identity",
            "matrix_identity",
            "pilot_scenario_identity",
            "trajectory_membership_identity",
            "cache_snapshot_identity",
            "checkpoint_output_identity",
        ):
            object.__setattr__(
                self,
                field_name,
                _sha256(getattr(self, field_name), field_name),
            )
        for field_name in (
            "scenario_count",
            "configuration_count",
            "scenario_configuration_count",
        ):
            object.__setattr__(
                self,
                field_name,
                _positive_int(getattr(self, field_name), field_name),
            )
        if (
            not isinstance(self.failure_count, int)
            or isinstance(self.failure_count, bool)
            or self.failure_count < 0
        ):
            raise ValidationError("failure_count must be nonnegative")
        if not isinstance(self.protocol_changed, bool) or not isinstance(
            self.resource_feasible, bool
        ):
            raise ValidationError("pilot gate flags must be Boolean")

    @property
    def passed(self) -> bool:
        """Return whether every frozen pilot gate is satisfied."""
        return (
            self.cohort_identity == FROZEN_COHORT_IDENTITY
            and self.matrix_identity == FROZEN_MATRIX_IDENTITY
            and self.scenario_count == PILOT_SCENARIO_COUNT
            and self.configuration_count == CONFIGURATION_COUNT
            and self.scenario_configuration_count
            == PILOT_SCENARIO_COUNT * CONFIGURATION_COUNT
            and self.failure_count == 0
            and not self.protocol_changed
            and self.resource_feasible
        )

    def to_dict(self) -> dict[str, object]:
        """Return the canonical completion record."""
        return {
            "schema_version": self.schema_version,
            "cohort_identity": self.cohort_identity,
            "matrix_identity": self.matrix_identity,
            "pilot_scenario_identity": self.pilot_scenario_identity,
            "trajectory_membership_identity": self.trajectory_membership_identity,
            "cache_snapshot_identity": self.cache_snapshot_identity,
            "checkpoint_output_identity": self.checkpoint_output_identity,
            "scenario_count": self.scenario_count,
            "configuration_count": self.configuration_count,
            "scenario_configuration_count": self.scenario_configuration_count,
            "failure_count": self.failure_count,
            "protocol_changed": self.protocol_changed,
            "resource_feasible": self.resource_feasible,
            "pilot_gate_passed": self.passed,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> PilotCompletion:
        """Strictly parse a pilot completion record."""
        fields = {
            "schema_version",
            "cohort_identity",
            "matrix_identity",
            "pilot_scenario_identity",
            "trajectory_membership_identity",
            "cache_snapshot_identity",
            "checkpoint_output_identity",
            "scenario_count",
            "configuration_count",
            "scenario_configuration_count",
            "failure_count",
            "protocol_changed",
            "resource_feasible",
            "pilot_gate_passed",
        }
        if set(value) != fields:
            raise SchemaError("pilot completion fields differ")
        try:
            completion = cls(
                schema_version=cast(Any, value["schema_version"]),
                cohort_identity=cast(Any, value["cohort_identity"]),
                matrix_identity=cast(Any, value["matrix_identity"]),
                pilot_scenario_identity=cast(Any, value["pilot_scenario_identity"]),
                trajectory_membership_identity=cast(
                    Any, value["trajectory_membership_identity"]
                ),
                cache_snapshot_identity=cast(Any, value["cache_snapshot_identity"]),
                checkpoint_output_identity=cast(
                    Any, value["checkpoint_output_identity"]
                ),
                scenario_count=cast(Any, value["scenario_count"]),
                configuration_count=cast(Any, value["configuration_count"]),
                scenario_configuration_count=cast(
                    Any, value["scenario_configuration_count"]
                ),
                failure_count=cast(Any, value["failure_count"]),
                protocol_changed=cast(Any, value["protocol_changed"]),
                resource_feasible=cast(Any, value["resource_feasible"]),
            )
        except (TypeError, ValidationError) as error:
            raise SchemaError(str(error)) from None
        if value["pilot_gate_passed"] is not completion.passed:
            raise SchemaError("pilot gate result differs")
        return completion


def pilot_scenario_identity(units: Sequence[MotionCohortUnit]) -> str:
    """Return the exact ordered identity of the frozen pilot membership."""
    return canonical_sha256(
        "phase4-frozen-pilot-scenarios-v1",
        [
            {
                "source_scenario_id": unit.source_scenario_id,
                "selection_rank": unit.selection_rank,
            }
            for unit in units
        ],
    )


def test_scenario_identity(units: Sequence[MotionCohortUnit]) -> str:
    """Return the exact ordered identity of the frozen test membership."""
    return canonical_sha256(
        "phase4-frozen-test-scenarios-v1",
        [
            {
                "source_scenario_id": unit.source_scenario_id,
                "selection_rank": unit.selection_rank,
            }
            for unit in units
        ],
    )


def write_pilot_completion(path: Path, completion: PilotCompletion) -> None:
    """Atomically install a passing pilot completion without replacement."""
    if not isinstance(completion, PilotCompletion) or not completion.passed:
        raise ValidationError("pilot completion does not pass the frozen gate")
    if path.exists() or path.is_symlink():
        existing = load_pilot_completion(path)
        if existing != completion:
            raise ArtifactError("pilot completion is immutable")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        canonical_json_text(completion.to_dict()),
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def load_pilot_completion(path: Path) -> PilotCompletion:
    """Load and verify the immutable pilot completion gate."""
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("passing pilot completion is required before test access")
    try:
        text = path.read_text(encoding="utf-8")
        value = json.loads(text)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise ArtifactError("pilot completion is unreadable") from error
    if not isinstance(value, Mapping):
        raise ArtifactError("pilot completion root must be a mapping")
    try:
        completion = PilotCompletion.from_dict(cast(Mapping[str, object], value))
    except SchemaError as error:
        raise ArtifactError(str(error)) from error
    if text != canonical_json_text(completion.to_dict()):
        raise ArtifactError("pilot completion is not canonical")
    if not completion.passed:
        raise ArtifactError("pilot completion does not pass the frozen gate")
    return completion


def read_pilot_units(path: Path) -> tuple[MotionCohortUnit, ...]:
    """Read exactly the pilot role and stop before test metadata."""
    return read_ranked_cohort_role(path, CohortRole.PILOT, PILOT_SCENARIO_COUNT)


def read_test_units(
    path: Path,
    pilot_completion_path: Path,
) -> tuple[MotionCohortUnit, ...]:
    """Open the test role only after verifying the immutable pilot gate."""
    load_pilot_completion(pilot_completion_path)
    return read_ranked_cohort_role(path, CohortRole.TEST, TEST_SCENARIO_COUNT)


def checkpoint_output_identity(checkpoint_hashes: Sequence[str], role: str) -> str:
    """Return the complete ordered identity over one role's checkpoints."""
    normalized = tuple(_sha256(value, "checkpoint hash") for value in checkpoint_hashes)
    expected = (
        PILOT_SCENARIO_COUNT if role == CohortRole.PILOT.value else TEST_SCENARIO_COUNT
    ) * CONFIGURATION_COUNT
    if len(normalized) != expected:
        raise ValidationError("checkpoint hash count differs from frozen role matrix")
    return canonical_sha256(f"phase4-frozen-{role}-checkpoints-v1", normalized)


def file_snapshot_identity(paths: Sequence[Path], root: Path, role: str) -> str:
    """Hash an ordered set of immutable source or cache files."""
    records: list[dict[str, object]] = []
    for path in sorted(paths):
        if path.is_symlink() or not path.is_file() or not path.is_relative_to(root):
            raise ArtifactError("snapshot path is not a regular repository file")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        records.append(
            {
                "path": path.relative_to(root).as_posix(),
                "size_bytes": path.stat().st_size,
                "sha256": digest,
            }
        )
    if not records:
        raise ValidationError("file snapshot cannot be empty")
    return canonical_sha256(f"phase4-frozen-{role}-input-files-v1", records)
