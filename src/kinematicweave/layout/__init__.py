"""Trajectory-derived spatial layout inference and indexing."""

from kinematicweave.layout.shared_motion import (
    MapEvaluation,
    RouteCandidate,
    RouteCompatibility,
    SharedMotionConfig,
    build_route_candidates,
    build_shared_motion_model,
    classify_motion_category,
    classify_semantic_motion_track,
    cluster_scenario,
    compute_route_compatibility,
    evaluate_shared_motion_map,
    event_signature_json,
    resample_arc_length,
    shared_motion_configuration_identity,
)

__all__ = [
    "MapEvaluation",
    "RouteCandidate",
    "RouteCompatibility",
    "SharedMotionConfig",
    "build_route_candidates",
    "build_shared_motion_model",
    "classify_motion_category",
    "classify_semantic_motion_track",
    "cluster_scenario",
    "compute_route_compatibility",
    "evaluate_shared_motion_map",
    "event_signature_json",
    "resample_arc_length",
    "shared_motion_configuration_identity",
]
