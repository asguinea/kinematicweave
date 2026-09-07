"""Package installation and structure smoke tests."""

import importlib
from importlib.metadata import version
import sys

import pytest

import kinematicweave

APPROVED_PACKAGES: tuple[str, ...] = (
    "kinematicweave.data",
    "kinematicweave.domain",
    "kinematicweave.trajectory",
    "kinematicweave.events",
    "kinematicweave.codecs",
    "kinematicweave.grammar",
    "kinematicweave.layout",
    "kinematicweave.runtime",
    "kinematicweave.editing",
    "kinematicweave.metrics",
    "kinematicweave.experiments",
    "kinematicweave.statistics",
    "kinematicweave.visualization",
    "kinematicweave.reporting",
    "kinematicweave.native",
)
FORBIDDEN_DEPENDENCY_ROOTS: frozenset[str] = frozenset(
    {
        "cupy",
        "networkx",
        "numpy",
        "polars",
        "pyarrow",
        "pybind11",
        "rerun",
        "scipy",
        "shapely",
        "sklearn",
        "torch",
    }
)


def test_version_api_matches_distribution_metadata() -> None:
    """The public version API has one metadata-backed symbol."""
    assert kinematicweave.__version__ == version("kinematicweave")
    assert kinematicweave.__all__ == ["__version__"]


@pytest.mark.parametrize("package_name", APPROVED_PACKAGES)
def test_package_import_has_no_sibling_side_effects(package_name: str) -> None:
    """Each approved package imports without importing sibling packages."""
    for approved_package in APPROVED_PACKAGES:
        sys.modules.pop(approved_package, None)

    imported_package = importlib.import_module(package_name)

    assert imported_package.__name__ == package_name
    loaded_packages = tuple(
        approved_package
        for approved_package in APPROVED_PACKAGES
        if approved_package in sys.modules
    )
    assert loaded_packages == (package_name,)


def test_hierarchy_imports_without_optional_dependencies() -> None:
    """The complete package hierarchy requires no optional dependency."""
    for approved_package in APPROVED_PACKAGES:
        sys.modules.pop(approved_package, None)
    modules_before = set(sys.modules)

    for approved_package in APPROVED_PACKAGES:
        importlib.import_module(approved_package)

    newly_loaded_roots = {
        module_name.partition(".")[0]
        for module_name in set(sys.modules).difference(modules_before)
    }
    assert newly_loaded_roots.isdisjoint(FORBIDDEN_DEPENDENCY_ROOTS)
