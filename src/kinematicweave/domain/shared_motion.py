"""Immutable shared-motion category, template, and membership records."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
import json
import math
from typing import cast

from kinematicweave.canonical import canonical_json_text
from kinematicweave.domain.map_records import geometry_from_canonical_wkb
from kinematicweave.domain.records import AgentClass, OriginType
from kinematicweave.errors import ValidationError
from kinematicweave.identifiers import validate_identifier

__all__ = [
    "MotionCategory",
    "MotionCategoryLabel",
    "RouteTemplate",
    "RouteTemplateMembership",
    "SharedMotionModel",
    "motion_category_from_dict",
    "motion_category_to_dict",
    "route_template_from_dict",
    "route_template_membership_from_dict",
    "route_template_membership_to_dict",
    "route_template_to_dict",
]

_INT32_MAX = 2**31 - 1
_INT64_MAX = 2**63 - 1


class MotionCategoryLabel(StrEnum):
    """Fixed shared-motion category vocabulary and canonical order."""

    STATIONARY = "stationary"
    STRAIGHT = "straight"
    LEFT_TURN = "left_turn"
    RIGHT_TURN = "right_turn"
    STOP_AND_GO = "stop_and_go"
    ACCELERATING = "accelerating"
    BRAKING = "braking"
    MIXED = "mixed"
    GAP_AFFECTED = "gap_affected"


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} must be a nonempty string")
    return value.strip()


def _integer(value: object, field_name: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be a non-Boolean integer")
    minimum = 1 if positive else 0
    maximum = _INT64_MAX if field_name == "duration_ns" else _INT32_MAX
    if not minimum <= value <= maximum:
        raise ValidationError(f"{field_name} is outside its valid range")
    return value


def _finite(value: object, field_name: str, *, positive: bool = False) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValidationError(f"{field_name} must be numeric")
    normalized = float(value)
    if not math.isfinite(normalized) or (positive and normalized <= 0.0):
        qualifier = "finite and positive" if positive else "finite"
        raise ValidationError(f"{field_name} must be {qualifier}")
    return normalized


def _enum[EnumT: StrEnum](
    enum_type: type[EnumT], value: object, field_name: str
) -> EnumT:
    if isinstance(value, enum_type):
        return value
    try:
        return enum_type(cast(str, value))
    except (TypeError, ValueError):
        raise ValidationError(f"{field_name} has invalid value {value!r}") from None


def _inferred(value: object) -> OriginType:
    normalized = _enum(OriginType, value, "origin_type")
    if normalized is not OriginType.INFERRED:
        raise ValidationError("shared-motion records must use OriginType.INFERRED")
    return normalized


def _canonical_object(value: object, field_name: str) -> str:
    normalized = _text(value, field_name)
    try:
        decoded = json.loads(normalized)
    except json.JSONDecodeError as error:
        raise ValidationError(f"{field_name} must be valid JSON") from error
    if not isinstance(decoded, dict):
        raise ValidationError(f"{field_name} must contain an object")
    canonical = canonical_json_text(decoded, trailing_newline=False)
    if normalized != canonical:
        raise ValidationError(f"{field_name} must be canonical JSON")
    return canonical


def _flags(value: object) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValidationError("quality_flags must be a non-string sequence")
    normalized = tuple(_text(item, "quality_flags item") for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValidationError("quality_flags must not contain duplicates")
    if normalized != tuple(sorted(normalized)):
        raise ValidationError("quality_flags must be ordered")
    return normalized


@dataclass(frozen=True, slots=True)
class MotionCategory:
    category_id: str
    category_label: MotionCategoryLabel | str
    agent_class: AgentClass | str
    event_signature_json: str
    representative_template_id: str
    template_count: int
    track_count: int
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        for name in ("category_id", "representative_template_id"):
            object.__setattr__(self, name, validate_identifier(getattr(self, name)))
        object.__setattr__(
            self,
            "category_label",
            _enum(MotionCategoryLabel, self.category_label, "category_label"),
        )
        object.__setattr__(
            self, "agent_class", _enum(AgentClass, self.agent_class, "agent_class")
        )
        object.__setattr__(
            self,
            "event_signature_json",
            _canonical_object(self.event_signature_json, "event_signature_json"),
        )
        object.__setattr__(
            self,
            "template_count",
            _integer(self.template_count, "template_count", positive=True),
        )
        object.__setattr__(
            self,
            "track_count",
            _integer(self.track_count, "track_count", positive=True),
        )
        object.__setattr__(self, "origin_type", _inferred(self.origin_type))
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class RouteTemplate:
    template_id: str
    category_id: str
    scenario_id: str
    coordinate_frame_id: str
    representative_track_id: str
    member_count: int
    geometry_type: str
    geometry_wkb: bytes
    path_length_m: float
    duration_ns: int
    event_signature_json: str
    semantic_attributes_json: str
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        for name in (
            "template_id",
            "category_id",
            "scenario_id",
            "coordinate_frame_id",
            "representative_track_id",
        ):
            object.__setattr__(self, name, validate_identifier(getattr(self, name)))
        object.__setattr__(
            self,
            "member_count",
            _integer(self.member_count, "member_count", positive=True),
        )
        geometry_type = _text(self.geometry_type, "geometry_type")
        if geometry_type not in {"LineString", "MultiLineString"}:
            raise ValidationError("geometry_type must be LineString or MultiLineString")
        if not isinstance(self.geometry_wkb, bytes):
            raise ValidationError("geometry_wkb must be bytes")
        geometry = geometry_from_canonical_wkb(self.geometry_wkb)
        if geometry.geom_type != geometry_type or geometry.is_empty:
            raise ValidationError("template WKB differs from geometry_type")
        if not geometry.is_valid or not all(
            math.isfinite(value)
            for coordinate in geometry.coords
            for value in coordinate
        ):
            raise ValidationError("template geometry must be finite and valid")
        object.__setattr__(self, "geometry_type", geometry_type)
        object.__setattr__(self, "geometry_wkb", bytes(self.geometry_wkb))
        object.__setattr__(
            self,
            "path_length_m",
            _finite(self.path_length_m, "path_length_m", positive=True),
        )
        object.__setattr__(
            self,
            "duration_ns",
            _integer(self.duration_ns, "duration_ns", positive=True),
        )
        for name in ("event_signature_json", "semantic_attributes_json"):
            object.__setattr__(self, name, _canonical_object(getattr(self, name), name))
        object.__setattr__(self, "origin_type", _inferred(self.origin_type))
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class RouteTemplateMembership:
    template_id: str
    procedural_track_id: str
    membership_index: int
    scenario_id: str
    agent_id: str
    trajectory_id: str
    mean_path_error_m: float
    maximum_path_error_m: float
    start_distance_m: float
    end_distance_m: float
    path_length_ratio: float
    duration_ratio: float
    map_route_signature_json: str | None
    origin_type: OriginType | str
    quality_flags: Sequence[str]

    def __post_init__(self) -> None:
        for name in (
            "template_id",
            "procedural_track_id",
            "scenario_id",
            "agent_id",
            "trajectory_id",
        ):
            object.__setattr__(self, name, validate_identifier(getattr(self, name)))
        object.__setattr__(
            self,
            "membership_index",
            _integer(self.membership_index, "membership_index"),
        )
        for name in (
            "mean_path_error_m",
            "maximum_path_error_m",
            "start_distance_m",
            "end_distance_m",
        ):
            normalized = _finite(getattr(self, name), name)
            if normalized < 0.0:
                raise ValidationError(f"{name} must not be negative")
            object.__setattr__(self, name, normalized)
        for name in ("path_length_ratio", "duration_ratio"):
            object.__setattr__(
                self, name, _finite(getattr(self, name), name, positive=True)
            )
        if self.map_route_signature_json is not None:
            object.__setattr__(
                self,
                "map_route_signature_json",
                _canonical_object(
                    self.map_route_signature_json, "map_route_signature_json"
                ),
            )
        object.__setattr__(self, "origin_type", _inferred(self.origin_type))
        object.__setattr__(self, "quality_flags", _flags(self.quality_flags))


@dataclass(frozen=True, slots=True)
class SharedMotionModel:
    dataset_id: str
    dataset_version: str
    source_validation_identity: str
    procedural_codec_identity: str
    semantic_detector_identity: str
    grouping_configuration_identity: str
    categories: Sequence[MotionCategory]
    route_templates: Sequence[RouteTemplate]
    memberships: Sequence[RouteTemplateMembership]
    category_summary_metadata: Sequence[str] = ()

    def __post_init__(self) -> None:
        for name in (
            "dataset_id",
            "dataset_version",
            "source_validation_identity",
            "procedural_codec_identity",
            "semantic_detector_identity",
            "grouping_configuration_identity",
        ):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        typed = (
            ("categories", MotionCategory),
            ("route_templates", RouteTemplate),
            ("memberships", RouteTemplateMembership),
        )
        for name, expected_type in typed:
            value = getattr(self, name)
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValidationError(f"{name} must be a non-string sequence")
            copied = tuple(value)
            if any(not isinstance(item, expected_type) for item in copied):
                raise ValidationError(f"{name} contains an invalid record")
            object.__setattr__(self, name, copied)
        summaries = tuple(
            _canonical_object(item, "category_summary_metadata item")
            for item in self.category_summary_metadata
        )
        object.__setattr__(self, "category_summary_metadata", summaries)
        _validate_model(self)


def _validate_model(model: SharedMotionModel) -> None:
    categories = {item.category_id: item for item in model.categories}
    templates = {item.template_id: item for item in model.route_templates}
    membership_keys = {
        (item.template_id, item.procedural_track_id) for item in model.memberships
    }
    if len(categories) != len(model.categories):
        raise ValidationError("category identifiers must be unique")
    if len(templates) != len(model.route_templates):
        raise ValidationError("template identifiers must be unique")
    if len(membership_keys) != len(model.memberships):
        raise ValidationError("membership keys must be unique")
    if any(item.category_id not in categories for item in model.route_templates):
        raise ValidationError("template category reference does not resolve")
    if any(item.template_id not in templates for item in model.memberships):
        raise ValidationError("membership template reference does not resolve")
    for template in model.route_templates:
        members = tuple(
            item
            for item in model.memberships
            if item.template_id == template.template_id
        )
        if len(members) != template.member_count:
            raise ValidationError("template member_count differs")
        if tuple(item.membership_index for item in members) != tuple(
            range(len(members))
        ):
            raise ValidationError(
                "membership indices must be contiguous and zero-based"
            )
        if members[0].procedural_track_id != template.representative_track_id:
            raise ValidationError("representative track must be first member")
        if any(item.scenario_id != template.scenario_id for item in members):
            raise ValidationError("template and membership scenarios differ")
    for category in model.categories:
        category_templates = tuple(
            item
            for item in model.route_templates
            if item.category_id == category.category_id
        )
        category_members = tuple(
            item
            for item in model.memberships
            if templates[item.template_id].category_id == category.category_id
        )
        if len(category_templates) != category.template_count:
            raise ValidationError("category template_count differs")
        if category.representative_template_id not in {
            item.template_id for item in category_templates
        }:
            raise ValidationError("category representative template does not belong")
        zero_length_count = sum(
            len(decoded.get("zero_length_track_ids", ()))
            for item in model.category_summary_metadata
            if (decoded := json.loads(item)).get("category_id") == category.category_id
        )
        if len(
            {item.procedural_track_id for item in category_members}
        ) + zero_length_count != (category.track_count):
            raise ValidationError("category track_count differs")


def motion_category_to_dict(record: MotionCategory) -> dict[str, object]:
    return {
        "category_id": record.category_id,
        "category_label": _enum(
            MotionCategoryLabel, record.category_label, "category_label"
        ).value,
        "agent_class": _enum(AgentClass, record.agent_class, "agent_class").value,
        "event_signature_json": record.event_signature_json,
        "representative_template_id": record.representative_template_id,
        "template_count": record.template_count,
        "track_count": record.track_count,
        "origin_type": _enum(OriginType, record.origin_type, "origin_type").value,
        "quality_flags": list(record.quality_flags),
    }


def route_template_to_dict(record: RouteTemplate) -> dict[str, object]:
    return {
        "template_id": record.template_id,
        "category_id": record.category_id,
        "scenario_id": record.scenario_id,
        "coordinate_frame_id": record.coordinate_frame_id,
        "representative_track_id": record.representative_track_id,
        "member_count": record.member_count,
        "geometry_type": record.geometry_type,
        "geometry_wkb": record.geometry_wkb,
        "path_length_m": record.path_length_m,
        "duration_ns": record.duration_ns,
        "event_signature_json": record.event_signature_json,
        "semantic_attributes_json": record.semantic_attributes_json,
        "origin_type": _enum(OriginType, record.origin_type, "origin_type").value,
        "quality_flags": list(record.quality_flags),
    }


def route_template_membership_to_dict(
    record: RouteTemplateMembership,
) -> dict[str, object]:
    return {
        "template_id": record.template_id,
        "procedural_track_id": record.procedural_track_id,
        "membership_index": record.membership_index,
        "scenario_id": record.scenario_id,
        "agent_id": record.agent_id,
        "trajectory_id": record.trajectory_id,
        "mean_path_error_m": record.mean_path_error_m,
        "maximum_path_error_m": record.maximum_path_error_m,
        "start_distance_m": record.start_distance_m,
        "end_distance_m": record.end_distance_m,
        "path_length_ratio": record.path_length_ratio,
        "duration_ratio": record.duration_ratio,
        "map_route_signature_json": record.map_route_signature_json,
        "origin_type": _enum(OriginType, record.origin_type, "origin_type").value,
        "quality_flags": list(record.quality_flags),
    }


def motion_category_from_dict(value: Mapping[str, object]) -> MotionCategory:
    return MotionCategory(**value)  # type: ignore[arg-type]


def route_template_from_dict(value: Mapping[str, object]) -> RouteTemplate:
    return RouteTemplate(**value)  # type: ignore[arg-type]


def route_template_membership_from_dict(
    value: Mapping[str, object],
) -> RouteTemplateMembership:
    return RouteTemplateMembership(**value)  # type: ignore[arg-type]
