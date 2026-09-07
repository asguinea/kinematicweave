from __future__ import annotations

from collections.abc import Mapping
from dataclasses import FrozenInstanceError
import importlib.util
import json
from pathlib import Path

import pytest

from kinematicweave.canonical import canonical_json_text
from kinematicweave.errors import ValidationError
from kinematicweave.experiments.motion_sweep import (
    BudgetDimension,
    ConfigurationBudgetRecord,
)
from kinematicweave.experiments.protocol_freeze import (
    PRIMARY_FAMILIES,
    CampaignMethod,
    CampaignRole,
    ComparisonPriority,
    FinalCampaignMatrix,
    FrozenBudgetTarget,
    ResourceProjection,
    SupplementalGrid,
    SupplementalGridPoint,
    freeze_budget_target,
    reconstruct_budget_ranges,
)


def _hash(index: int) -> str:
    return f"{index:064x}"


def _records() -> tuple[ConfigurationBudgetRecord, ...]:
    records: list[ConfigurationBudgetRecord] = []
    for index, family in enumerate(PRIMARY_FAMILIES, start=1):
        records.append(
            ConfigurationBudgetRecord(
                family,
                f"{family}-primary",
                _hash(index),
                0.46 + index * 0.005,
                0.105 + index * 0.002,
            )
        )
        records.append(
            ConfigurationBudgetRecord(
                family,
                f"{family}-diagnostic",
                _hash(index + 10),
                0.70 + index * 0.001,
                0.22 + index * 0.001,
            )
        )
    return tuple(records)


def _target(
    dimension: BudgetDimension = BudgetDimension.BYTE_RATIO,
) -> FrozenBudgetTarget:
    return freeze_budget_target(
        _records(),
        dimension,
        0.50 if dimension is BudgetDimension.BYTE_RATIO else 0.13,
        PRIMARY_FAMILIES,
        0.05,
        ComparisonPriority.PRIMARY,
    )


def _method(
    index: int,
    family: str,
    role: CampaignRole,
) -> CampaignMethod:
    return CampaignMethod(
        f"method-{index}",
        family,
        _hash(index + 30),
        canonical_json_text({"method": family, "index": index}),
        role,
        "required by the frozen comparison contract",
    )


def _matrix() -> FinalCampaignMatrix:
    methods = [
        _method(1, "raw_samples", CampaignRole.REFERENCE),
        _method(2, "exact_adjacent", CampaignRole.REFERENCE),
    ]
    methods.extend(
        _method(index + 3, family, CampaignRole.PRIMARY)
        for index, family in enumerate(PRIMARY_FAMILIES)
    )
    return FinalCampaignMatrix(
        tuple(methods),
        (
            _target(BudgetDimension.BYTE_RATIO),
            _target(BudgetDimension.KEYFRAME_RATIO),
        ),
        _hash(60),
        _hash(61),
        ("position_error_m",),
        ("event_f1",),
    )


def test_range_reconstruction_is_sorted_and_exact() -> None:
    ranges = reconstruct_budget_ranges(tuple(reversed(_records())))
    assert [value.family for value in ranges] == sorted(PRIMARY_FAMILIES)
    first = ranges[0]
    family_records = [row for row in _records() if row.family == first.family]
    assert first.byte_ratio_minimum == min(row.byte_ratio for row in family_records)
    assert first.byte_ratio_maximum == max(row.byte_ratio for row in family_records)
    assert first.configuration_count == 2


def test_budget_freeze_uses_budget_only_and_is_deterministic() -> None:
    first = _target(BudgetDimension.BYTE_RATIO)
    second = freeze_budget_target(
        tuple(reversed(_records())),
        BudgetDimension.BYTE_RATIO,
        0.50,
        tuple(reversed(PRIMARY_FAMILIES)),
        0.05,
        ComparisonPriority.PRIMARY,
    )
    assert first.identity == second.identity
    assert all(selection.actual_budget <= 0.50 for selection in first.selections)
    serialized = json.dumps(first.to_dict())
    assert all(
        outcome_field not in serialized
        for outcome_field in ("position_error", "semantic_f1", "runtime_seconds")
    )
    assert first.to_dict()["selection_input"] == "relevant_budget_only"


def test_budget_coverage_rejects_excess_mismatch_and_missing_family() -> None:
    with pytest.raises(ValidationError, match="target coverage"):
        freeze_budget_target(
            _records(),
            BudgetDimension.BYTE_RATIO,
            0.20,
            PRIMARY_FAMILIES,
            0.01,
            ComparisonPriority.PRIMARY,
        )
    with pytest.raises(ValidationError, match="lacks"):
        freeze_budget_target(
            _records(),
            BudgetDimension.BYTE_RATIO,
            0.50,
            (*PRIMARY_FAMILIES, "missing"),
            1.0,
            ComparisonPriority.PRIMARY,
        )


def test_empty_supplemental_grid_is_frozen_and_deterministic() -> None:
    accepted = tuple(_hash(index) for index in range(1, 5))
    first = SupplementalGrid(
        accepted,
        (),
        True,
        "Accepted development points already satisfy coverage.",
    )
    second = SupplementalGrid(
        accepted,
        (),
        True,
        "Accepted development points already satisfy coverage.",
    )
    assert first.identity == second.identity
    assert first.to_dict()["points"] == []
    with pytest.raises(FrozenInstanceError):
        first.points = ()  # type: ignore[misc]


def test_supplemental_grid_rejects_accepted_and_internal_duplicates() -> None:
    point = SupplementalGridPoint(
        "new-point",
        "uniform_linear",
        canonical_json_text({"method": "uniform_linear", "stride": 12}),
        _hash(10),
        "Bracket the byte target without using an outcome.",
    )
    with pytest.raises(ValidationError, match="accepted"):
        SupplementalGrid(
            (_hash(10),),
            (point,),
            True,
        )
    with pytest.raises(ValidationError, match="duplicate supplemental"):
        SupplementalGrid(
            (_hash(11),),
            (point, point),
            True,
        )


def test_final_matrix_identity_and_required_families() -> None:
    matrix = _matrix()
    assert matrix.identity == _matrix().identity
    payload = matrix.to_dict()
    assert payload["pilot_scenario_count"] == 50
    assert payload["test_scenario_count"] == 300
    assert payload["final_method_selected"] is False
    prohibited = payload["pilot_decisions_prohibited"]
    assert isinstance(prohibited, list)
    assert "test-matrix changes after any pilot execution" in prohibited
    with pytest.raises(ValidationError, match="required family"):
        FinalCampaignMatrix(
            matrix.methods[:-1],
            matrix.budget_targets,
            matrix.cohort_identity,
            matrix.development_validation_identity,
            matrix.motion_metric_names,
            matrix.semantic_metric_names,
        )


def test_resource_projection_scales_counts_and_enforces_feasibility() -> None:
    projection = ResourceProjection(
        150,
        18,
        2_700.0,
        525_000_000,
        138_000_000,
        3_200_000_000,
        1_000_000_000_000,
        800_000_000_000,
        20.0,
        5_400,
    )
    assert projection.pilot_runtime_seconds == 900.0
    assert projection.test_runtime_seconds == 5_400.0
    payload = projection.to_dict()
    pilot = payload["pilot"]
    test = payload["test"]
    assert isinstance(pilot, Mapping)
    assert isinstance(test, Mapping)
    assert pilot["checkpoint_count"] == 900
    assert test["checkpoint_count"] == 5_400
    assert projection.required_free_disk_reserve_bytes == 150_000_000_000
    assert projection.feasible is True


def test_protocol_runner_is_import_safe() -> None:
    script = Path(__file__).parents[1] / "scripts" / "run_protocol_freeze.py"
    specification = importlib.util.spec_from_file_location(
        "run_protocol_freeze_import_test",
        script,
    )
    assert specification is not None and specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    assert callable(module.main)
