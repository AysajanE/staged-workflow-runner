"""Responses Runner v2 package."""

import sys

if sys.version_info < (3, 10):
    raise SystemExit("Responses Runner v2 requires Python 3.10 or newer.")

from .contracts import (
    AUTHORITY_ORDER,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_STRUCTURAL_MODEL,
    RUNNER_VERSION,
)

__all__ = [
    "AUTHORITY_ORDER",
    "DEFAULT_OUTPUT_ROOT",
    "DEFAULT_PRIMARY_MODEL",
    "DEFAULT_STRUCTURAL_MODEL",
    "RUNNER_VERSION",
]
