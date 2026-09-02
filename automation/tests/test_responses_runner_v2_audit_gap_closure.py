from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from automation import run_responses_v2


ROOT = Path(__file__).resolve().parents[2]


class EngineCliTests(unittest.TestCase):
    def test_dry_run_cli_surfaces_context_warning(self) -> None:
        stderr = io.StringIO()
        result = {
            "run_manifest_path": ".local/test/run_manifest.json",
            "warnings": [
                {
                    "code": "exact_token_preflight_not_executed_in_dry_run",
                    "message": "Live execution may block.",
                    "diagnostics_path": ".local/test/local_context_estimate.json",
                }
            ],
        }
        argv = [
            "run",
            "--root",
            str(ROOT),
            "--workflow-file",
            "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
            "--dry-run",
        ]
        with mock.patch.object(run_responses_v2, "run_workflow", return_value=result), redirect_stderr(stderr), redirect_stdout(io.StringIO()):
            exit_code = run_responses_v2.main(argv)

        self.assertEqual(exit_code, 0)
        self.assertIn("exact_token_preflight_not_executed_in_dry_run", stderr.getvalue())
        self.assertIn("local_context_estimate.json", stderr.getvalue())

    def test_run_and_resume_wait_by_default_and_accept_no_wait(self) -> None:
        run_args = run_responses_v2.parse_args(
            ["run", "--root", str(ROOT), "--workflow-file", "workflow.json"]
        )
        self.assertTrue(run_args.wait)
        self.assertEqual(run_args.poll_interval, 20.0)

        no_wait = run_responses_v2.parse_args(
            ["run", "--root", str(ROOT), "--workflow-file", "workflow.json", "--no-wait"]
        )
        self.assertFalse(no_wait.wait)

        resume_args = run_responses_v2.parse_args(
            ["resume", "--root", str(ROOT), "--run-dir", "run", "--stage", "stage"]
        )
        self.assertTrue(resume_args.wait)
        resume_no_wait = run_responses_v2.parse_args(
            ["resume", "--root", str(ROOT), "--run-dir", "run", "--stage", "stage", "--no-wait"]
        )
        self.assertFalse(resume_no_wait.wait)
