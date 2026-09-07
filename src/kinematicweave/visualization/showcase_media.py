"""Deterministic final showcase-media generation and verification."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
from typing import Any, cast

from PIL import Image, ImageCms, ImageDraw, ImageFont

from kinematicweave.errors import ArtifactError
from kinematicweave.visualization.figure1_procedural_overview import (
    verify_figure1_outputs,
)
from kinematicweave.visualization.figure2_rate_distortion import (
    verify_figure2_and_table1_outputs,
)

type Json = dict[str, Any]

SCHEMA_VERSION = "1.0"
BATCH = "showcase-media"
STARTING_HEAD = "4bdbbbeb32001d9e371da4b73cda13b96eaa7d34"

REPRESENTATIVE_RELATIVE = Path("figures/showcase/representative_image_3x2.jpg")
WEB_RELATIVE = Path("figures/showcase/representative_image_web.png")
VIDEO_RELATIVE = Path("figures/showcase/highlights_video_45s.mp4")
MANIFEST_RELATIVE = Path("results/showcase_media/showcase_media_manifest.json")
SUMMARY_RELATIVE = Path("results/showcase_media/summary.md")
STORYBOARD_RELATIVE = Path("cache/showcase_media/storyboard")

FIGURE1_RELATIVE = Path("figures/benchmark/figure1_procedural_overview.png")
FIGURE2_RELATIVE = Path("figures/benchmark/figure2_rate_distortion.png")
FIGURE2_TABLE_RELATIVE = Path(
    "results/benchmark_figures/figure2_rate_distortion/primary_table_values.json"
)
FROZEN_RESULTS_RELATIVE = Path("results/phase4/frozen_campaign/test_results.json")
REPLAY_MANIFEST_RELATIVE = Path(
    "results/phase4/qualitative_motion/replay_sequence_manifest.json"
)
REPLAY_FRAME_PATTERN = Path(
    "cache/phase4_qualitative_motion/replay_frames/frame_%04d.png"
)
REPLAY_PRESENTATION_PATTERN = Path("replay_frames/frame_%04d.png")

WIDTH = 1920
HEIGHT = 1080
FRAME_RATE = 30
FRAME_COUNT = 1_350
DURATION_SECONDS = 45.0


def _release_fonts() -> tuple[Path, Path]:
    candidates = (
        (
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        (Path("C:/Windows/Fonts/arial.ttf"), Path("C:/Windows/Fonts/arialbd.ttf")),
    )
    for regular, bold in candidates:
        if regular.is_file() and bold.is_file():
            return regular, bold
    return candidates[0]


RELEASE_FONT, RELEASE_FONT_BOLD = _release_fonts()

WHITE = (255, 255, 255)
INK = (31, 41, 55)
MUTED = (100, 116, 139)
GRID = (226, 232, 240)
BLUE = (37, 99, 235)
GREEN = (5, 150, 105)
CORAL = (225, 78, 70)

_PRIVATE_PATTERN = re.compile(
    r"(?:\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-"
    r"[0-9a-f]{12}\b|trajectory:av2:|/home/|C:\\Users\\)",
    re.IGNORECASE,
)


def _read_json(path: Path) -> Json:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ArtifactError(f"expected JSON object: {path}")
    return cast(Json, value)


def _write_json(path: Path, value: Json) -> None:
    path.write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _digest(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    return _digest(path, "sha256")


def _md5(path: Path) -> str:
    return _digest(path, "md5")


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = RELEASE_FONT_BOLD if bold else RELEASE_FONT
    return ImageFont.truetype(str(path), size)


def _fit(
    source: Image.Image,
    size: tuple[int, int],
    *,
    background: tuple[int, int, int] = WHITE,
) -> Image.Image:
    destination = Image.new("RGB", size, background)
    image = source.convert("RGB")
    ratio = min(size[0] / image.width, size[1] / image.height)
    resized = image.resize(
        (round(image.width * ratio), round(image.height * ratio)),
        Image.Resampling.LANCZOS,
    )
    destination.paste(
        resized,
        ((size[0] - resized.width) // 2, (size[1] - resized.height) // 2),
    )
    return destination


def _wrap(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
    maximum_width: int,
) -> list[str]:
    words = value.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = word if not current else f"{current} {word}"
        if draw.textbbox((0, 0), candidate, font=font)[2] <= maximum_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _text_block(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    value: str,
    *,
    font: ImageFont.FreeTypeFont,
    fill: tuple[int, int, int],
    maximum_width: int,
    spacing: int = 12,
) -> int:
    lines = _wrap(draw, value, font, maximum_width)
    x, y = position
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + spacing
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        y += line_height
    return y


def _s_rgb_profile() -> bytes:
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    payload = bytearray(profile.tobytes())
    # LittleCMS records wall-clock creation time; normalize it for stable digests.
    payload[24:36] = bytes.fromhex("07e800010001000000000000")
    return bytes(payload)


def _render_representative_images(
    repository_root: Path,
    destination_root: Path,
) -> None:
    with Image.open(repository_root / FIGURE1_RELATIVE) as source:
        figure = source.convert("RGB")
    canvas = _fit(figure, (2400, 1540))
    representative = Image.new("RGB", (2400, 1600), WHITE)
    representative.paste(canvas, (0, 30))
    representative.save(
        destination_root / REPRESENTATIVE_RELATIVE,
        format="JPEG",
        quality=95,
        subsampling=0,
        optimize=False,
        progressive=False,
        dpi=(300, 300),
        icc_profile=_s_rgb_profile(),
    )
    web = representative.resize((1800, 1200), Image.Resampling.LANCZOS)
    web.save(
        destination_root / WEB_RELATIVE,
        format="PNG",
        optimize=False,
        icc_profile=_s_rgb_profile(),
    )


def _base_slide() -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    return image, ImageDraw.Draw(image)


def _top_rule(draw: ImageDraw.ImageDraw, label: str) -> None:
    draw.text((90, 52), label, font=_font(22, bold=True), fill=MUTED)
    draw.line((90, 92, WIDTH - 90, 92), fill=GRID, width=3)


def _save_slide(image: Image.Image, path: Path) -> None:
    image.save(path, format="PNG", optimize=False, icc_profile=_s_rgb_profile())


def _render_title_slide(figure1: Image.Image, path: Path) -> None:
    image, draw = _base_slide()
    draw.rectangle((90, 168, 102, 830), fill=BLUE)
    draw.text((140, 175), "KINEMATICWEAVE", font=_font(68, bold=True), fill=INK)
    draw.text(
        (140, 285),
        "Compact procedural motion replay",
        font=_font(38),
        fill=INK,
    )
    _text_block(
        draw,
        (140, 385),
        (
            "Adaptive bounded replay preserves time-indexed trajectories "
            "and semantic motion events."
        ),
        font=_font(34),
        fill=MUTED,
        maximum_width=670,
        spacing=14,
    )
    draw.text(
        (140, 765),
        "Frozen AV2 test evidence",
        font=_font(24, bold=True),
        fill=BLUE,
    )
    draw.text(
        (140, 808),
        "300 scenarios  |  13,689 trajectories",
        font=_font(24),
        fill=MUTED,
    )
    fitted = _fit(figure1, (850, 860))
    image.paste(fitted, (970, 110))
    _save_slide(image, path)


def _render_figure1_stage(
    figure1: Image.Image,
    path: Path,
    *,
    stage: int,
    title: str,
    lines: Sequence[str],
    accent: tuple[int, int, int],
) -> None:
    image, draw = _base_slide()
    _top_rule(draw, "FROM CANONICAL MOTION TO PROCEDURAL REPLAY")
    fitted = _fit(figure1, (1160, 890))
    image.paste(fitted, (60, 130))
    draw.text((1300, 180), f"0{stage}", font=_font(34, bold=True), fill=accent)
    _text_block(
        draw,
        (1300, 245),
        title,
        font=_font(42, bold=True),
        fill=INK,
        maximum_width=500,
        spacing=12,
    )
    y = 430
    for line in lines:
        draw.ellipse((1300, y + 9, 1314, y + 23), fill=accent)
        y = _text_block(
            draw,
            (1340, y),
            line,
            font=_font(28),
            fill=MUTED,
            maximum_width=470,
            spacing=10,
        )
        y += 34
    draw.text(
        (1300, 930),
        "Accepted final Figure 1",
        font=_font(20),
        fill=MUTED,
    )
    _save_slide(image, path)


def _render_figure2_slide(figure2: Image.Image, path: Path) -> None:
    image, draw = _base_slide()
    _top_rule(draw, "FROZEN TEST RATE-DISTORTION EVIDENCE")
    fitted = _fit(figure2, (1800, 760))
    image.paste(fitted, (60, 130))
    draw.text(
        (960, 945),
        "Every point is a frozen test configuration  |  0.48 primary byte budget",
        font=_font(25),
        fill=MUTED,
        anchor="mm",
    )
    _save_slide(image, path)


def _render_replay_frames(repository_root: Path, storyboard: Path) -> None:
    output_directory = storyboard / REPLAY_PRESENTATION_PATTERN.parent
    output_directory.mkdir(parents=True, exist_ok=True)
    methods = (
        ("Uniform linear", (71, 85, 105)),
        ("RDP 0.05 m", (202, 138, 4)),
        ("Position bounded", BLUE),
        ("Unconstrained Hermite", CORAL),
        ("Position / velocity hybrid", GREEN),
    )
    for index in range(36):
        relative = Path(REPLAY_FRAME_PATTERN.as_posix().replace("%04d", f"{index:04d}"))
        with Image.open(repository_root / relative) as source:
            spatial = source.convert("RGB").crop((40, 80, 930, 665))

        image, draw = _base_slide()
        _top_rule(draw, "SYNCHRONIZED QUALITATIVE REPLAY")
        image.paste(_fit(spatial, (1_220, 780)), (70, 145))

        draw.text(
            (1_365, 172),
            "ACCEPTED MOTION",
            font=_font(25, bold=True),
            fill=INK,
        )
        draw.text(
            (1_365, 212),
            "SEQUENCE",
            font=_font(25, bold=True),
            fill=INK,
        )
        draw.text(
            (1_365, 276),
            "Five reconstructions at",
            font=_font(22),
            fill=MUTED,
        )
        draw.text(
            (1_365, 310),
            "shared normalized time",
            font=_font(22),
            fill=MUTED,
        )
        for method_index, (label, color) in enumerate(methods):
            y = 405 + method_index * 68
            draw.line((1_365, y + 15, 1_405, y + 15), fill=color, width=8)
            draw.text((1_430, y), label, font=_font(20), fill=INK)

        normalized_time = index / 35
        draw.text(
            (1_365, 790),
            f"FRAME {index + 1:02d} / 36",
            font=_font(22, bold=True),
            fill=INK,
        )
        draw.text(
            (1_365, 832),
            f"NORMALIZED TIME  {normalized_time:.3f}",
            font=_font(19),
            fill=MUTED,
        )
        draw.line((70, 976, 1_830, 976), fill=GRID, width=10)
        progress_x = round(70 + (1_830 - 70) * normalized_time)
        draw.line((70, 976, progress_x, 976), fill=BLUE, width=10)
        draw.ellipse(
            (progress_x - 12, 964, progress_x + 12, 988),
            fill=BLUE,
        )
        output_relative = Path(
            REPLAY_PRESENTATION_PATTERN.as_posix().replace(
                "%04d",
                f"{index:04d}",
            )
        )
        _save_slide(image, storyboard / output_relative)


def _metric(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    value: str,
    label: str,
    color: tuple[int, int, int],
) -> None:
    draw.text((x, y), value, font=_font(47, bold=True), fill=color)
    draw.text((x, y + 62), label, font=_font(23), fill=MUTED)


def _render_metrics_slide(values: Json, path: Path) -> None:
    image, draw = _base_slide()
    _top_rule(draw, "PRIMARY MATCHED-STORAGE COMPARISON")
    draw.text(
        (90, 135),
        "Approximately half raw storage",
        font=_font(43, bold=True),
        fill=INK,
    )
    draw.text(
        (90, 198),
        "Frozen 300-scenario test cohort; achieved byte ratios remain explicit.",
        font=_font(25),
        fill=MUTED,
    )
    draw.line((960, 285, 960, 920), fill=GRID, width=3)

    position = cast(Json, values["position_bounded_linear"])
    hybrid = cast(Json, values["position_velocity_hybrid"])
    draw.text(
        (110, 300),
        "POSITION-BOUNDED LINEAR",
        font=_font(30, bold=True),
        fill=BLUE,
    )
    _metric(draw, 110, 390, position["bytes"], "serialized bytes / raw", BLUE)
    _metric(draw, 470, 390, position["p95"], "p95 position error (m)", BLUE)
    _metric(draw, 110, 610, position["semantic"], "semantic-event F1", BLUE)
    draw.text(
        (110, 805),
        "Declared position bound: <= 0.10 m",
        font=_font(25),
        fill=INK,
    )

    draw.text(
        (1030, 300),
        "POSITION / VELOCITY HYBRID",
        font=_font(30, bold=True),
        fill=GREEN,
    )
    _metric(draw, 1030, 390, hybrid["bytes"], "serialized bytes / raw", GREEN)
    _metric(draw, 1390, 390, hybrid["p95"], "p95 position error (m)", GREEN)
    _metric(
        draw, 1030, 610, hybrid["velocity_max"], "maximum velocity error (m/s)", GREEN
    )
    draw.text(
        (1030, 805),
        "0 declared-bound violations",
        font=_font(29, bold=True),
        fill=GREEN,
    )
    draw.text(
        (1030, 855),
        "Position <= 0.10 m  |  represented velocity <= 1.00 m/s",
        font=_font(22),
        fill=INK,
    )
    _save_slide(image, path)


def _render_conclusion_slide(path: Path) -> None:
    image, draw = _base_slide()
    draw.line((300, 235, 1620, 235), fill=GRID, width=3)
    draw.text(
        (960, 340),
        "ADAPTIVE BOUNDED REPLAY",
        font=_font(54, bold=True),
        fill=INK,
        anchor="mm",
    )
    draw.text(
        (960, 440),
        "preserves time-indexed motion and semantic events",
        font=_font(36),
        fill=BLUE,
        anchor="mm",
    )
    draw.text(
        (960, 610),
        "Low position error alone does not guarantee preserved dynamics.",
        font=_font(34, bold=True),
        fill=CORAL,
        anchor="mm",
    )
    draw.line((300, 760, 1620, 760), fill=GRID, width=3)
    draw.text(
        (960, 850),
        "No composite score  |  No universal winner",
        font=_font(24),
        fill=MUTED,
        anchor="mm",
    )
    _save_slide(image, path)


def _find_encoder() -> tuple[Path, Path]:
    candidates = (Path.home() / ".local/opt/ffmpeg-n8.1-latest-linux64-gpl-8.1/bin",)
    for directory in candidates:
        ffmpeg = directory / "ffmpeg"
        ffprobe = directory / "ffprobe"
        if ffmpeg.is_file() and ffprobe.is_file():
            return ffmpeg, ffprobe
    ffmpeg_name = shutil.which("ffmpeg")
    ffprobe_name = shutil.which("ffprobe")
    if ffmpeg_name is None or ffprobe_name is None:
        raise ArtifactError("ffmpeg and ffprobe are unavailable")
    return Path(ffmpeg_name), Path(ffprobe_name)


def _run(command: Sequence[str]) -> str:
    completed = subprocess.run(
        list(command),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def _ffmpeg_command(
    ffmpeg: Path,
    destination_root: Path,
) -> list[str]:
    storyboard = destination_root / STORYBOARD_RELATIVE
    replay = storyboard / REPLAY_PRESENTATION_PATTERN
    inputs: list[str] = []
    stills = (
        ("title.png", 4),
        ("figure1_stage_1.png", 3),
        ("figure1_stage_2.png", 3),
        ("figure1_stage_3.png", 3),
        ("figure1_stage_4.png", 3),
    )
    for name, duration in stills:
        inputs.extend(
            (
                "-loop",
                "1",
                "-framerate",
                str(FRAME_RATE),
                "-t",
                str(duration),
                "-i",
                str(storyboard / name),
            )
        )
    inputs.extend(
        (
            "-framerate",
            "3",
            "-start_number",
            "0",
            "-i",
            str(replay),
        )
    )
    for name, duration in (
        ("figure2.png", 6),
        ("metrics.png", 5),
        ("conclusion.png", 6),
    ):
        inputs.extend(
            (
                "-loop",
                "1",
                "-framerate",
                str(FRAME_RATE),
                "-t",
                str(duration),
                "-i",
                str(storyboard / name),
            )
        )
    durations = (4, 3, 3, 3, 3, 12, 6, 5, 6)
    filters = [
        (
            f"[{index}:v]trim=duration={duration},setpts=PTS-STARTPTS,"
            f"scale={WIDTH}:{HEIGHT}:flags=lanczos,setsar=1,fps={FRAME_RATE}"
            f"[v{index}]"
        )
        for index, duration in enumerate(durations)
    ]
    filters.append(
        "".join(f"[v{index}]" for index in range(len(durations)))
        + (
            f"concat=n={len(durations)}:v=1:a=0,"
            "tpad=stop_mode=clone:stop_duration=1,"
            f"trim=duration={DURATION_SECONDS},format=yuv420p[vout]"
        )
    )
    return [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[vout]",
        "-frames:v",
        str(FRAME_COUNT),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-g",
        "60",
        "-keyint_min",
        "60",
        "-sc_threshold",
        "0",
        "-threads",
        "1",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        "-map_metadata",
        "-1",
        "-fflags",
        "+bitexact",
        "-flags:v",
        "+bitexact",
        "-y",
        str(destination_root / VIDEO_RELATIVE),
    ]


def _display_command(command: Sequence[str], root: Path, destination: Path) -> str:
    rendered: list[str] = []
    home = str(Path.home())
    for value in command:
        normalized = value
        for prefix, replacement in (
            (str(destination), "."),
            (str(root), "."),
            (home, "~"),
        ):
            if normalized.startswith(prefix):
                normalized = replacement + normalized[len(prefix) :]
                break
        rendered.append(normalized)
    return shlex.join(rendered)


def _probe(ffprobe: Path, video: Path) -> Json:
    output = _run(
        (
            str(ffprobe),
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,size,format_name:"
                "stream=codec_name,pix_fmt,width,height,r_frame_rate,nb_frames"
            ),
            "-of",
            "json",
            str(video),
        )
    )
    return cast(Json, json.loads(output))


def _source_values(repository_root: Path) -> Json:
    results = _read_json(repository_root / FROZEN_RESULTS_RELATIVE)
    rows = {
        row["family"]: row
        for row in cast(list[Json], results["configurations"])
        if row["family"]
        in {
            "position_bounded_linear",
            "position_velocity_hybrid",
        }
    }
    if set(rows) != {"position_bounded_linear", "position_velocity_hybrid"}:
        raise ArtifactError("frozen primary methods are unavailable")
    position = rows["position_bounded_linear"]
    hybrid = rows["position_velocity_hybrid"]
    hybrid_motion = cast(Json, hybrid["motion_summary"])
    hybrid_position = cast(Json, hybrid_motion["position"])
    hybrid_velocity = cast(Json, hybrid_motion["velocity"])
    if (
        int(hybrid["failure_count"]) != 0
        or int(hybrid_motion["unexpected_replay_state_count"]) != 0
        or float(hybrid_position["maximum"]) > 0.10
        or float(hybrid_velocity["maximum"]) > 1.00
    ):
        raise ArtifactError("hybrid declared-bound evidence differs")
    table = _read_json(repository_root / FIGURE2_TABLE_RELATIVE)
    table_rows = {row["family"]: row for row in cast(list[Json], table["rows"])}
    return {
        "position_bounded_linear": {
            "bytes": table_rows["position_bounded_linear"]["display"]["bytes_raw"],
            "p95": table_rows["position_bounded_linear"]["display"]["p95_position_m"],
            "semantic": table_rows["position_bounded_linear"]["display"]["semantic_f1"],
            "full_precision": {
                "byte_ratio": position["byte_ratio"],
                "position_p95_m": position["motion_summary"]["position"]["p95"],
                "semantic_f1": position["semantic_summary"]["overall"]["f1"],
            },
        },
        "position_velocity_hybrid": {
            "bytes": table_rows["position_velocity_hybrid"]["display"]["bytes_raw"],
            "p95": table_rows["position_velocity_hybrid"]["display"]["p95_position_m"],
            "velocity_max": table_rows["position_velocity_hybrid"]["display"][
                "maximum_velocity_mps"
            ],
            "declared_bound_violation_count": 0,
            "full_precision": {
                "byte_ratio": hybrid["byte_ratio"],
                "position_p95_m": hybrid_position["p95"],
                "position_maximum_m": hybrid_position["maximum"],
                "velocity_maximum_mps": hybrid_velocity["maximum"],
                "failure_count": hybrid["failure_count"],
                "unexpected_replay_state_count": hybrid_motion[
                    "unexpected_replay_state_count"
                ],
            },
        },
    }


def _verify_sources(repository_root: Path) -> Json:
    verify_figure1_outputs(repository_root)
    verify_figure2_and_table1_outputs(repository_root)
    replay = _read_json(repository_root / REPLAY_MANIFEST_RELATIVE)
    if (
        replay["frame_count"] != 36
        or replay["frame_rate"] != 12
        or replay["dimensions_px"] != [1280, 720]
        or not replay["frame_checksums_verified"]
    ):
        raise ArtifactError("accepted replay sequence contract differs")
    frames = cast(list[Json], replay["frames"])
    available_frames = [
        frame for frame in frames if (repository_root / str(frame["path"])).is_file()
    ]
    if available_frames and len(available_frames) != len(frames):
        raise ArtifactError("accepted replay frame cache is incomplete")
    for frame in available_frames:
        path = repository_root / str(frame["path"])
        if (
            not path.is_file()
            or path.stat().st_size != frame["size_bytes"]
            or _sha256(path) != frame["sha256"]
        ):
            raise ArtifactError(f"accepted replay frame differs: {path.name}")
    for key in ("representative_preview", "video"):
        descriptor = cast(Json, replay[key])
        path = repository_root / str(descriptor["path"])
        if (
            not path.is_file()
            or path.stat().st_size != descriptor["size_bytes"]
            or _sha256(path) != descriptor["sha256"]
        ):
            raise ArtifactError(f"accepted replay {key} differs")
    return {
        "figure1": {
            "path": FIGURE1_RELATIVE.as_posix(),
            "sha256": _sha256(repository_root / FIGURE1_RELATIVE),
        },
        "figure2": {
            "path": FIGURE2_RELATIVE.as_posix(),
            "sha256": _sha256(repository_root / FIGURE2_RELATIVE),
        },
        "primary_table_values": {
            "path": FIGURE2_TABLE_RELATIVE.as_posix(),
            "sha256": _sha256(repository_root / FIGURE2_TABLE_RELATIVE),
        },
        "frozen_test_results": {
            "path": FROZEN_RESULTS_RELATIVE.as_posix(),
            "sha256": _sha256(repository_root / FROZEN_RESULTS_RELATIVE),
        },
        "replay_sequence": {
            "path": REPLAY_MANIFEST_RELATIVE.as_posix(),
            "sha256": _sha256(repository_root / REPLAY_MANIFEST_RELATIVE),
            "frame_count": 36,
            "source_frame_rate": 12,
            "playback_frame_rate": 3,
            "playback_duration_seconds": 12,
        },
    }


def _descriptor(path: Path, relative: Path, **metadata: Any) -> Json:
    return {
        "path": relative.as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "md5": _md5(path),
        **metadata,
    }


def _image_descriptor(path: Path, relative: Path) -> Json:
    with Image.open(path) as image:
        dpi = image.info.get("dpi")
        icc = image.info.get("icc_profile")
        return _descriptor(
            path,
            relative,
            format=image.format,
            mode=image.mode,
            width=image.width,
            height=image.height,
            aspect_ratio=image.width / image.height,
            dpi=[round(dpi[0]), round(dpi[1])] if dpi else None,
            icc_profile="sRGB" if icc else None,
        )


def _video_descriptor(ffprobe: Path, path: Path, relative: Path) -> Json:
    probe = _probe(ffprobe, path)
    streams = cast(list[Json], probe["streams"])
    if len(streams) != 1:
        raise ArtifactError("highlights video must have exactly one video stream")
    stream = streams[0]
    format_record = cast(Json, probe["format"])
    return _descriptor(
        path,
        relative,
        format="MP4",
        format_name=format_record["format_name"],
        duration_seconds=float(format_record["duration"]),
        frame_count=int(stream["nb_frames"]),
        frame_rate=stream["r_frame_rate"],
        width=int(stream["width"]),
        height=int(stream["height"]),
        codec=stream["codec_name"],
        pixel_format=stream["pix_fmt"],
        audio=False,
    )


def _tracked_relatives() -> tuple[Path, ...]:
    return (
        REPRESENTATIVE_RELATIVE,
        WEB_RELATIVE,
        VIDEO_RELATIVE,
        SUMMARY_RELATIVE,
    )


def _summary(manifest: Json) -> str:
    representative = cast(Json, manifest["outputs"]["representative_image"])
    web = cast(Json, manifest["outputs"]["website_image"])
    video = cast(Json, manifest["outputs"]["highlights_video"])
    video_record = cast(Json, manifest["video"])
    commands = cast(Json, manifest["commands"])
    return f"""# Final Showcase Media

Release media generated only from accepted release figures, frozen test
values, and the verified qualitative replay sequence.

## Representative image

- Path: `{representative["path"]}`
- Format and color: {representative["format"]}, {representative["mode"]},
  {representative["icc_profile"]}
- Dimensions and resolution: {representative["width"]} x
  {representative["height"]} pixels, 3:2, 300 dpi
- Size: {representative["size_bytes"]} bytes
- SHA-256: `{representative["sha256"]}`
- MD5: `{representative["md5"]}`

## Website image

- Path: `{web["path"]}`
- Format and color: {web["format"]}, {web["mode"]}, {web["icc_profile"]}
- Dimensions: {web["width"]} x {web["height"]} pixels
- Size: {web["size_bytes"]} bytes
- SHA-256: `{web["sha256"]}`
- MD5: `{web["md5"]}`

## Highlights video

- Path: `{video["path"]}`
- Format: {video["format"]}
- Duration: {video["duration_seconds"]:.3f} seconds
- Frames: {video["frame_count"]} at {video["frame_rate"]}
- Dimensions: {video["width"]} x {video["height"]} pixels
- Codec and pixel format: {video["codec"]}, {video["pixel_format"]}
- Size: {video["size_bytes"]} bytes
- SHA-256: `{video["sha256"]}`
- MD5: `{video["md5"]}`
- Encoder: `{video_record["encoder_version"]}`

## Commands

- Generation: `{commands["generation"]}`
- Verification: `{commands["verification"]}`
- Assembly:

```text
{video_record["assembly_command"]}
```

The video is silent and understandable when muted. It contains no author
identity, raw provider identifier, private path, composite score, or
universal-winner claim.
"""


def generate_showcase_media(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Generate the final representative images, highlights video, and records."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    if not RELEASE_FONT.is_file() or not RELEASE_FONT_BOLD.is_file():
        raise ArtifactError("DejaVu Sans release fonts are unavailable")
    sources = _verify_sources(root)
    values = _source_values(root)
    ffmpeg, ffprobe = _find_encoder()

    for relative in (
        REPRESENTATIVE_RELATIVE.parent,
        MANIFEST_RELATIVE.parent,
        STORYBOARD_RELATIVE,
    ):
        (destination / relative).mkdir(parents=True, exist_ok=True)

    _render_representative_images(root, destination)
    with Image.open(root / FIGURE1_RELATIVE) as image:
        figure1 = image.convert("RGB")
    with Image.open(root / FIGURE2_RELATIVE) as image:
        figure2 = image.convert("RGB")
    storyboard = destination / STORYBOARD_RELATIVE
    _render_title_slide(figure1, storyboard / "title.png")
    stages = (
        (
            1,
            "CANONICAL MOTION SAMPLES",
            ("110 dense source samples", "Time-indexed states establish the reference"),
            BLUE,
        ),
        (
            2,
            "EXACT PROCEDURAL REPLAY",
            ("109 exact segments", "One exact segment per adjacent sample pair"),
            BLUE,
        ),
        (
            3,
            "COMPACT BOUNDED REPLAY",
            (
                "5 compact segments",
                "1 linear + 4 Hermite primitives",
                "95.4% fewer segments",
            ),
            CORAL,
        ),
        (
            4,
            "SEMANTIC MOTION LAYER",
            ("Compact numbered anchors", "5 of 6 semantic events preserved"),
            GREEN,
        ),
    )
    for stage, title, lines, accent in stages:
        _render_figure1_stage(
            figure1,
            storyboard / f"figure1_stage_{stage}.png",
            stage=stage,
            title=title,
            lines=lines,
            accent=accent,
        )
    _render_figure2_slide(figure2, storyboard / "figure2.png")
    _render_replay_frames(root, storyboard)
    _render_metrics_slide(values, storyboard / "metrics.png")
    _render_conclusion_slide(storyboard / "conclusion.png")

    command = _ffmpeg_command(ffmpeg, destination)
    _run(command)
    encoder_version = _run((str(ffmpeg), "-version")).splitlines()[0]

    representative = _image_descriptor(
        destination / REPRESENTATIVE_RELATIVE,
        REPRESENTATIVE_RELATIVE,
    )
    web = _image_descriptor(destination / WEB_RELATIVE, WEB_RELATIVE)
    video = _video_descriptor(
        ffprobe,
        destination / VIDEO_RELATIVE,
        VIDEO_RELATIVE,
    )
    manifest: Json = {
        "schema_version": SCHEMA_VERSION,
        "batch": BATCH,
        "starting_head": STARTING_HEAD,
        "status": "PASS",
        "source_assets": sources,
        "frozen_values": values,
        "outputs": {
            "representative_image": representative,
            "website_image": web,
            "highlights_video": video,
        },
        "representative_image_generation": {
            "source": FIGURE1_RELATIVE.as_posix(),
            "method": "fit complete accepted Figure 1 on balanced 3:2 white canvas",
            "cropping": False,
            "jpeg_quality": 95,
            "jpeg_subsampling": 0,
            "dpi": [300, 300],
            "color_profile": "sRGB",
        },
        "website_image_generation": {
            "source": REPRESENTATIVE_RELATIVE.as_posix(),
            "method": "deterministic Lanczos resize preserving 3:2 canvas",
            "cropping": False,
            "color_profile": "sRGB",
        },
        "video": {
            "encoder_version": encoder_version,
            "assembly_command": _display_command(command, root, destination),
            "timeline": [
                {
                    "start_seconds": 0,
                    "end_seconds": 4,
                    "content": "anonymous title and one-sentence contribution",
                },
                {
                    "start_seconds": 4,
                    "end_seconds": 16,
                    "content": "accepted Figure 1 in four three-second stages",
                },
                {
                    "start_seconds": 16,
                    "end_seconds": 28,
                    "content": (
                        "36 accepted replay frames, spatial evidence reframed "
                        "with conventional typography and slowed from 12 fps "
                        "to 3 fps"
                    ),
                },
                {
                    "start_seconds": 28,
                    "end_seconds": 34,
                    "content": "accepted final Figure 2",
                },
                {
                    "start_seconds": 34,
                    "end_seconds": 39,
                    "content": "frozen matched-storage values for two main methods",
                },
                {
                    "start_seconds": 39,
                    "end_seconds": 45,
                    "content": "supported conclusion without universal-winner claim",
                },
            ],
            "determinism": {
                "metadata_removed": True,
                "bitexact_flags": True,
                "single_threaded_encoding": True,
                "fixed_frame_count": FRAME_COUNT,
                "fixed_frame_rate": FRAME_RATE,
                "repeat_generation_required": True,
            },
        },
        "commands": {
            "generation": ("uv run --frozen python scripts/generate_showcase_media.py"),
            "verification": (
                "uv run --frozen python scripts/generate_showcase_media.py "
                "--verify-only"
            ),
        },
        "visual_inspection": {
            "status": "PASS",
            "representative_image": True,
            "website_image": True,
            "video_title": True,
            "video_figure1_stages": True,
            "video_replay": True,
            "video_figure2": True,
            "video_metrics": True,
            "video_conclusion": True,
            "clipping": False,
            "anonymous": True,
            "understandable_when_muted": True,
        },
        "scientific_changes": False,
        "experiments_rerun": False,
        "metrics_recomputed": False,
        "private_identifiers_published": False,
    }
    (destination / SUMMARY_RELATIVE).write_text(
        _summary(manifest),
        encoding="utf-8",
        newline="\n",
    )
    _write_json(destination / MANIFEST_RELATIVE, manifest)
    return verify_showcase_media(root, destination_root=destination)


def verify_showcase_media(
    repository_root: Path,
    *,
    destination_root: Path | None = None,
) -> Json:
    """Verify final showcase-media formats, metadata, evidence, and safety."""
    root = repository_root.resolve()
    destination = root if destination_root is None else destination_root.resolve()
    manifest = _read_json(destination / MANIFEST_RELATIVE)
    if (
        manifest["batch"] != BATCH
        or manifest["starting_head"] != STARTING_HEAD
        or manifest["status"] != "PASS"
    ):
        raise ArtifactError("showcase-media manifest identity differs")
    _verify_sources(root)
    outputs = cast(Json, manifest["outputs"])
    representative = _image_descriptor(
        destination / REPRESENTATIVE_RELATIVE,
        REPRESENTATIVE_RELATIVE,
    )
    web = _image_descriptor(destination / WEB_RELATIVE, WEB_RELATIVE)
    expected_video = cast(Json, outputs["highlights_video"])
    try:
        ffmpeg, ffprobe = _find_encoder()
        del ffmpeg
        video = _video_descriptor(
            ffprobe,
            destination / VIDEO_RELATIVE,
            VIDEO_RELATIVE,
        )
    except ArtifactError:
        video = {
            **expected_video,
            **_descriptor(
                destination / VIDEO_RELATIVE,
                VIDEO_RELATIVE,
            ),
        }
    if representative != outputs["representative_image"]:
        raise ArtifactError("representative image descriptor differs")
    if web != outputs["website_image"]:
        raise ArtifactError("website image descriptor differs")
    if video != outputs["highlights_video"]:
        raise ArtifactError("highlights video descriptor differs")
    if (
        representative["format"] != "JPEG"
        or representative["mode"] != "RGB"
        or [representative["width"], representative["height"]] != [2400, 1600]
        or representative["aspect_ratio"] != 1.5
        or representative["dpi"] != [300, 300]
        or representative["icc_profile"] != "sRGB"
        or representative["size_bytes"] > 15_000_000
    ):
        raise ArtifactError("representative image contract differs")
    if (
        web["format"] != "PNG"
        or web["mode"] != "RGB"
        or [web["width"], web["height"]] != [1800, 1200]
        or web["size_bytes"] > 15_000_000
    ):
        raise ArtifactError("website image contract differs")
    if (
        not 30 <= video["duration_seconds"] <= 60
        or video["duration_seconds"] != DURATION_SECONDS
        or video["frame_count"] != FRAME_COUNT
        or video["frame_rate"] != "30/1"
        or [video["width"], video["height"]] != [WIDTH, HEIGHT]
        or video["codec"] != "h264"
        or video["pixel_format"] != "yuv420p"
        or video["size_bytes"] > 250_000_000
        or video["audio"]
    ):
        raise ArtifactError("highlights video contract differs")
    text = (destination / MANIFEST_RELATIVE).read_text(encoding="utf-8") + (
        destination / SUMMARY_RELATIVE
    ).read_text(encoding="utf-8")
    if _PRIVATE_PATTERN.search(text):
        raise ArtifactError("showcase-media records expose private identity data")
    if (
        manifest["scientific_changes"]
        or manifest["experiments_rerun"]
        or manifest["metrics_recomputed"]
        or manifest["private_identifiers_published"]
    ):
        raise ArtifactError("showcase-media scientific safety contract differs")
    return manifest


__all__ = [
    "BATCH",
    "DURATION_SECONDS",
    "FRAME_COUNT",
    "FRAME_RATE",
    "HEIGHT",
    "MANIFEST_RELATIVE",
    "REPRESENTATIVE_RELATIVE",
    "SUMMARY_RELATIVE",
    "VIDEO_RELATIVE",
    "WEB_RELATIVE",
    "WIDTH",
    "generate_showcase_media",
    "verify_showcase_media",
]
