"""Public package metadata for KinematicWeave."""

from importlib.metadata import version as _distribution_version

__version__: str = _distribution_version("kinematicweave")
__all__: list[str] = ["__version__"]
