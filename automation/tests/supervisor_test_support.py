from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock


def isolate_supervisor_output(test_case: unittest.TestCase, root: Path) -> Path:
    """Keep synthetic supervisor sessions inside a per-test temporary root."""

    temporary_root = tempfile.TemporaryDirectory(dir=root)
    test_case.addCleanup(temporary_root.cleanup)
    output_root = Path(temporary_root.name).relative_to(root) / "supervisor_sessions"
    patcher = mock.patch(
        "automation.responses_runner_v2.supervisor_artifacts.SUPERVISOR_OUTPUT_ROOT",
        output_root.as_posix(),
    )
    patcher.start()
    test_case.addCleanup(patcher.stop)
    return output_root
