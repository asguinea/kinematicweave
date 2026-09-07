"""Integration verification for tracked Phase 4 qualitative evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results/phase4/qualitative_motion"
UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


def _json(name: str) -> dict[str, Any]:
    value = json.loads((RESULTS / name).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_decision_and_required_selection_coverage() -> None:
    evidence = _json("evidence.json")
    selected = _json("selected_examples.json")
    assert evidence["qualitative_decision"] == "completed"
    assert evidence["scenario_count"] == 300
    assert selected["manual_cherry_pick"] is False
    categories = {row["category"] for row in selected["examples"]}
    assert {
        "adaptive_largest_position_improvement",
        "adaptive_median_position_improvement",
        "adaptive_smallest_or_adverse_improvement",
        "rdp_largest_temporal_failure",
        "rdp_median_temporal_case",
        "hermite_largest_velocity_inflation",
        "hermite_largest_semantic_loss",
        "hybrid_largest_velocity_recovery",
        "hybrid_largest_acceleration_or_braking_recovery",
        "hybrid_largest_semantic_tradeoff",
        "hybrid_little_advantage",
        "semantic_stop",
        "semantic_turn",
        "semantic_acceleration",
        "semantic_braking",
        "semantic_overlapping_events",
        "agent_vehicle",
        "agent_pedestrian",
        "agent_cyclist",
        "robustness_city_exception_1",
        "robustness_city_exception_2",
    } <= categories


def test_unfavorable_and_exception_cases_are_explicit() -> None:
    selected = _json("selected_examples.json")["examples"]
    indexed = {row["category"]: row for row in selected}
    assert (
        indexed["adaptive_smallest_or_adverse_improvement"]["selection_quantile"]
        == "smallest"
    )
    assert indexed["hybrid_little_advantage"]["selection_direction"] == "ascending"
    for index in (1, 2):
        row = indexed[f"robustness_city_exception_{index}"]
        assert row["metrics"]["direction_agrees_with_aggregate"] is False


def test_figures_have_consistent_vector_and_raster_outputs() -> None:
    manifest = _json("figure_manifest.json")
    assert manifest["figure_count"] == 8
    assert set(manifest["visual_language"]) == {
        "source",
        "uniform",
        "RDP",
        "position_bounded",
        "unconstrained_Hermite",
        "position_velocity_hybrid",
    }
    for figure in manifest["figures"]:
        assert figure["source_data"]
        formats = {output["format"] for output in figure["outputs"]}
        assert formats == {"svg", "png"}
        for output in figure["outputs"]:
            path = ROOT / output["path"]
            assert path.is_file()
            assert path.stat().st_size == output["size_bytes"]
            assert _sha256(path) == output["sha256"]


def test_replay_frame_ordering_preview_and_checksums() -> None:
    manifest = _json("replay_sequence_manifest.json")
    assert manifest["comparison_order"] == [
        "source",
        "uniform_linear_stride_10",
        "rdp_linear_0_05_m",
        "position_bounded_linear_0_10_m",
        "unconstrained_hermite_0_10_m",
        "position_velocity_hybrid_0_10_m_1_00_mps",
    ]
    assert manifest["ordering_verified"]
    assert manifest["frame_checksums_verified"]
    assert [row["frame"] for row in manifest["frames"]] == list(
        range(manifest["frame_count"])
    )
    if not (ROOT / manifest["frames"][0]["path"]).is_file():
        pytest.skip("source replay frames are intentionally excluded from Git")
    for row in manifest["frames"]:
        assert _sha256(ROOT / row["path"]) == row["sha256"]
    preview = manifest["representative_preview"]
    assert _sha256(ROOT / preview["path"]) == preview["sha256"]
    assert manifest["encoder"]["assembly_command"].startswith("ffmpeg ")
    assert manifest["encoder"]["available"] is True
    assert manifest["encoder"]["codec"] == "libx264"
    assert manifest["encoder"]["repeat_encode_byte_identical"] is True
    video = manifest["video"]
    video_path = ROOT / video["path"]
    assert video_path.stat().st_size == video["size_bytes"] == 70_505
    assert _sha256(video_path) == video["sha256"]
    assert video["codec_name"] == "h264"
    assert video["pixel_format"] == "yuv420p"
    assert video["frame_rate"] == "12/1"
    assert (video["width"], video["height"]) == (1280, 720)
    assert video["duration_seconds"] == 3.0
    assert video["frame_count"] == manifest["frame_count"] == 36


def test_evidence_checksums_and_safe_release() -> None:
    evidence = _json("evidence.json")
    for name, expected in evidence["evidence_file_sha256"].items():
        assert _sha256(RESULTS / name) == expected
    tracked_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in RESULTS.rglob("*")
        if path.is_file() and path.suffix in {".json", ".md", ".svg"}
    )
    assert UUID_PATTERN.search(tracked_text) is None
    assert "/home/" not in tracked_text
    assert "C:\\Users\\" not in tracked_text
    assert "example-user" not in tracked_text.lower()
    selected = _json("selected_examples.json")
    assert all(
        re.fullmatch(r"(scenario|trajectory)-[0-9a-f]{16}", row["safe_record_id"])
        for row in selected["examples"]
    )


def test_private_selection_map_remains_ignored() -> None:
    private = ROOT / "cache/phase4_qualitative_motion/private_selection.json"
    pytest.importorskip("subprocess")
    import subprocess

    result = subprocess.run(
        ["git", "check-ignore", "--quiet", str(private)],
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0
