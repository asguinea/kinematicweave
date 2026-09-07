"""Focused validation for the final showcase-media package."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
import re
from typing import Any

from PIL import Image

from kinematicweave.visualization import showcase_media

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_PATTERN = re.compile(
    r"(?:\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}\b|trajectory:av2:|/home/|C:\\Users\\)",
    re.IGNORECASE,
)


def _manifest() -> dict[str, Any]:
    value = json.loads(
        (ROOT / showcase_media.MANIFEST_RELATIVE).read_text(encoding="utf-8")
    )
    assert isinstance(value, dict)
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def test_representative_image_contract_and_hashes() -> None:
    manifest = _manifest()
    descriptor = manifest["outputs"]["representative_image"]
    path = ROOT / showcase_media.REPRESENTATIVE_RELATIVE
    with Image.open(path) as image:
        assert image.format == "JPEG"
        assert image.mode == "RGB"
        assert image.size == (2400, 1600)
        assert image.info["dpi"] == (300, 300)
        assert image.info["icc_profile"]
    assert descriptor["aspect_ratio"] == 1.5
    assert descriptor["size_bytes"] <= 15_000_000
    assert descriptor["sha256"] == _sha256(path)
    assert descriptor["md5"] == _md5(path)


def test_website_image_contract_and_hashes() -> None:
    manifest = _manifest()
    descriptor = manifest["outputs"]["website_image"]
    path = ROOT / showcase_media.WEB_RELATIVE
    with Image.open(path) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (1800, 1200)
        assert image.info["icc_profile"]
    assert descriptor["size_bytes"] <= 15_000_000
    assert descriptor["sha256"] == _sha256(path)
    assert descriptor["md5"] == _md5(path)


def test_video_ffprobe_contract_and_hashes() -> None:
    manifest = showcase_media.verify_showcase_media(ROOT)
    descriptor = manifest["outputs"]["highlights_video"]
    path = ROOT / showcase_media.VIDEO_RELATIVE
    assert descriptor["duration_seconds"] == 45.0
    assert descriptor["frame_count"] == 1_350
    assert descriptor["frame_rate"] == "30/1"
    assert descriptor["width"] == 1920
    assert descriptor["height"] == 1080
    assert descriptor["codec"] == "h264"
    assert descriptor["pixel_format"] == "yuv420p"
    assert descriptor["audio"] is False
    assert descriptor["size_bytes"] <= 250_000_000
    assert descriptor["sha256"] == _sha256(path)
    assert descriptor["md5"] == _md5(path)


def test_storyboard_and_frozen_values_are_traceable() -> None:
    manifest = _manifest()
    assert manifest["starting_head"] == showcase_media.STARTING_HEAD
    assert manifest["scientific_changes"] is False
    assert manifest["experiments_rerun"] is False
    assert manifest["metrics_recomputed"] is False
    assert [row["end_seconds"] for row in manifest["video"]["timeline"]] == [
        4,
        16,
        28,
        34,
        39,
        45,
    ]
    values = manifest["frozen_values"]
    assert values["position_bounded_linear"]["p95"] == "0.0918"
    assert values["position_bounded_linear"]["semantic"] == "0.9121"
    assert values["position_velocity_hybrid"]["p95"] == "0.0806"
    assert values["position_velocity_hybrid"]["declared_bound_violation_count"] == 0


def test_records_are_anonymous_and_contain_both_hash_algorithms() -> None:
    manifest = _manifest()
    text = (ROOT / showcase_media.MANIFEST_RELATIVE).read_text(encoding="utf-8") + (
        ROOT / showcase_media.SUMMARY_RELATIVE
    ).read_text(encoding="utf-8")
    assert PRIVATE_PATTERN.search(text) is None
    assert manifest["private_identifiers_published"] is False
    for descriptor in manifest["outputs"].values():
        assert re.fullmatch(r"[0-9a-f]{64}", descriptor["sha256"])
        assert re.fullmatch(r"[0-9a-f]{32}", descriptor["md5"])


def test_generator_modules_have_no_import_time_io() -> None:
    module = importlib.import_module("kinematicweave.visualization.showcase_media")
    script_path = ROOT / "scripts/generate_showcase_media.py"
    specification = importlib.util.spec_from_file_location(
        "showcase_media_generator_script",
        script_path,
    )
    assert specification is not None
    assert specification.loader is not None
    script = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(script)
    assert callable(module.generate_showcase_media)
    assert callable(script.main)
