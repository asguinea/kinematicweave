"""Project-owned exception hierarchy for KinematicWeave."""

__all__ = [
    "ArtifactError",
    "ConfigurationError",
    "ExperimentError",
    "KinematicWeaveError",
    "ResourceLimitError",
    "SchemaError",
    "ValidationError",
]


class KinematicWeaveError(Exception):
    """Base exception for project-owned failures."""


class ConfigurationError(KinematicWeaveError, ValueError):
    """Configuration input or resolution is invalid."""


class ValidationError(KinematicWeaveError, ValueError):
    """Project data or API input fails validation."""


class SchemaError(ValidationError):
    """Structured data does not satisfy its declared schema."""


class ResourceLimitError(KinematicWeaveError):
    """A bounded project resource limit would be exceeded."""


class ExperimentError(KinematicWeaveError):
    """Experiment setup or execution fails."""


class ArtifactError(KinematicWeaveError):
    """Artifact creation, validation, or access fails."""
