"""Canonical domain objects, identifiers, value types, and validation."""

from kinematicweave.domain.procedural import (
    ProceduralPrimitiveType,
    ProceduralSegment,
    ProceduralTape,
    ProceduralTrack,
    ReplayState,
)
from kinematicweave.domain.semantic import (
    MotionEvent,
    MotionEventType,
    SemanticMotionTape,
    SemanticMotionTrack,
    SemanticWaypoint,
    SemanticWaypointRole,
)

__all__ = [
    "MotionEvent",
    "MotionEventType",
    "ProceduralPrimitiveType",
    "ProceduralSegment",
    "ProceduralTape",
    "ProceduralTrack",
    "ReplayState",
    "SemanticMotionTape",
    "SemanticMotionTrack",
    "SemanticWaypoint",
    "SemanticWaypointRole",
]
