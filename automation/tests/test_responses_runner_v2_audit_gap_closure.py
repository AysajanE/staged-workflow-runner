from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from automation import run_responses_supervisor_v2, run_responses_v2
from automation.responses_runner_v2 import (
    data_lifecycle,
    supervisor,
    supervisor_artifacts,
    telemetry,
)
from automation.responses_runner_v2.contracts import relpath, sha256_file


ROOT = Path(__file__).resolve().parents[2]


class AuditGapClosureTests(unittest.TestCase):
    def test_v2_revision_is_atomic_manifest_state_and_v1_sidecar_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            base = Path(temporary_directory)
            v2_path = base / "v2"
            v2_path.mkdir(mode=0o700)
            with mock.patch.object(supervisor_artifacts, "validate_against_schema", return_value=None):
                first = supervisor_artifacts.write_session(
                    ROOT,
                    v2_path,
                    {"schema_version": "responses_runner_v2.supervisor_session.v2"},
                )
                manifest_path = supervisor_artifacts.session_manifest_path(v2_path)
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                self.assertEqual(manifest["revision"], 1)
                self.assertEqual(first["_revision"], 1)
                self.assertFalse((v2_path / ".supervisor_session.revision").exists())

                loaded = supervisor_artifacts.load_session(ROOT, v2_path)
                stale = dict(loaded)
                current = supervisor_artifacts.write_session(ROOT, v2_path, loaded)
                self.assertEqual(current["revision"], 2)
                self.assertEqual(current["_revision"], 2)
                with self.assertRaisesRegex(SystemExit, "revision conflict"):
                    supervisor_artifacts.write_session(ROOT, v2_path, stale)

                v1_path = base / "v1"
                v1_path.mkdir(mode=0o700)
                supervisor_artifacts.write_session(
                    ROOT,
                    v1_path,
                    {"schema_version": "responses_runner_v2.supervisor_session.v1"},
                )
                self.assertTrue((v1_path / ".supervisor_session.revision").is_file())
                self.assertNotIn(
                    "revision",
                    json.loads(
                        supervisor_artifacts.session_manifest_path(v1_path).read_text(
                            encoding="utf-8"
                        )
                    ),
                )

    def test_reviewer_output_discovery_matches_current_cycle_layout(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            target = Path(temporary_directory)
            relative_targets = (
                "review_cycles/cycle/operator/operator.json",
                "review_cycles/cycle/reviewers/codex/codex.json",
                "review_cycles/cycle/reviewers/claude/claude.json",
                "review_cycles/cycle/revision/operator/revision.json",
            )
            for relative in relative_targets:
                path = target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")
            legacy = target / "review_cycles/cycle/codex_review_agent/legacy.json"
            legacy.parent.mkdir(parents=True)
            legacy.write_text("{}\n", encoding="utf-8")

            discovered = {
                path.relative_to(target).as_posix()
                for category, path in data_lifecycle._targets(target, ["reviewer_output"])
                if category == "reviewer_output"
            }
            self.assertEqual(discovered, set(relative_targets))

    def test_usage_help_and_shared_prompt_describe_actual_contracts(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            run_responses_v2.parse_args(["--help"])
        help_text = output.getvalue()
        self.assertIn("Build normalized primary and sidecar usage totals", help_text)
        self.assertNotIn("primary, sidecar, and reviewer", help_text)

        output = io.StringIO()
        with redirect_stdout(output), self.assertRaises(SystemExit):
            run_responses_supervisor_v2.parse_args(["--help"])
        self.assertIn(
            "Build reviewer-attempt usage totals for a supervisor",
            output.getvalue(),
        )

        shared = (
            ROOT
            / "automation/task_packs/responses_runner_v2_supervisor_internal/shared_instructions.md"
        ).read_text(encoding="utf-8")
        required = "`status=succeeded`, `approval_decision=blocked`, and `next_action=blocked`"
        self.assertGreaterEqual(shared.count(required), 2)
        self.assertNotIn("`status=blocked`", shared)

    def test_create_bundle_accepts_cycle_driven_minimal_arguments(self) -> None:
        args = run_responses_supervisor_v2.parse_args(
            [
                "create-bundle",
                "--session",
                "session",
                "--review-cycle",
                "stage_output_001",
            ]
        )
        self.assertEqual(args.review_cycle, "stage_output_001")
        self.assertIsNone(args.workflow_id)
        self.assertIsNone(args.acceptance_record)

    def test_review_cycle_accepts_derived_stage_inputs(self) -> None:
        args = run_responses_supervisor_v2.parse_args(
            [
                "review-cycle",
                "--session",
                "session",
                "--review-cycle",
                "stage_output_001",
                "--run-dir",
                "runs/example",
                "--stage",
                "draft",
            ]
        )
        self.assertEqual(args.run_dir, Path("runs/example"))
        self.assertEqual(args.stage, "draft")
        self.assertIsNone(args.job_json)
        self.assertIsNone(args.review_kind)

    def test_accept_can_create_the_derived_bundle_in_one_command(self) -> None:
        args = run_responses_supervisor_v2.parse_args(
            [
                "accept",
                "--session",
                "session",
                "--review-cycle",
                "stage_output_001",
                "--then-bundle",
                "--then-launch",
            ]
        )
        self.assertTrue(args.then_bundle)
        self.assertTrue(args.then_launch)
        self.assertIsNone(args.bundle_output)

    def test_supervisor_usage_report_aggregates_only_reviewer_attempts(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as temporary_directory:
            session_path = Path(temporary_directory)
            attempt_path = (
                session_path
                / "review_cycles/cycle/reviewers/codex/cmd.reviewer_usage_attempt.json"
            )
            attempt_path.parent.mkdir(parents=True)
            attempt = telemetry.build_usage_report(
                [
                    {
                        "attempt_id": "cmd",
                        "lane": "reviewer",
                        "model": "gpt-5.6-sol",
                        "status": "succeeded",
                        "duration_ms": 8,
                        "retry_count": 0,
                        "upload_count": 0,
                        "uploaded_bytes": 0,
                        "usage": None,
                    }
                ]
            )["attempts"][0]
            attempt_path.write_text(json.dumps(attempt) + "\n", encoding="utf-8")
            orphan_path = attempt_path.with_name("orphan.reviewer_usage_attempt.json")
            orphan_path.write_text(json.dumps({**attempt, "attempt_id": "orphan"}) + "\n", encoding="utf-8")

            with mock.patch.object(
                supervisor_artifacts,
                "load_session",
                return_value={
                    "supervisor_session_id": "sup_usage",
                    "review_agent_invocations": [{
                        "command_id": "cmd",
                        "usage_attempt_path": relpath(ROOT, attempt_path),
                        "usage_attempt_sha256": sha256_file(attempt_path),
                    }],
                },
            ):
                result = telemetry.write_supervisor_usage_report(
                    root=ROOT,
                    session_ref=session_path,
                )

            report = json.loads(
                (ROOT / result["usage_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(result["attempt_count"], 1)
            self.assertEqual(set(report["by_lane"]), {"reviewer"})
            self.assertEqual(report["by_lane"]["reviewer"]["attempt_count"], 1)
            self.assertIsNone(report["by_lane"]["reviewer"]["total_tokens"])

    def test_supervisor_stage_scoped_bindings_match_engine_contract(self) -> None:
        binding_file = Path(
            "automation/examples/responses_runner_v2_evidence_synthesis/"
            "runtime_input_bindings.example.json"
        )
        workflow_file = Path(
            "automation/examples/responses_runner_v2_evidence_synthesis/workflows/"
            "document_evidence_synthesis.workflow.json"
        )
        bindings = supervisor._supervisor_input_bindings(
            root=ROOT,
            workflow_file=workflow_file,
            input_binding_file=binding_file,
        )
        self.assertTrue(bindings)
        self.assertTrue(any(binding.stage_ids for binding in bindings))

        for command, required in (
            ("dry-run-scaffold", ("--session", "session", "--workflow-file", str(workflow_file))),
            ("launch", ("--session", "session", "--workflow-file", str(workflow_file))),
            (
                "rerun-archived",
                (
                    "--session",
                    "session",
                    "--archive-manifest",
                    "archive.json",
                    "--workflow-file",
                    str(workflow_file),
                ),
            ),
        ):
            args = run_responses_supervisor_v2.parse_args(
                [command, *required, "--input-binding-file", str(binding_file)]
            )
            self.assertEqual(args.input_binding_file, binding_file)

        with mock.patch.object(
            run_responses_supervisor_v2.supervisor,
            "launch_scaffold",
            return_value={"status": "in_progress"},
        ) as launch:
            with redirect_stdout(io.StringIO()):
                result = run_responses_supervisor_v2.main(
                    [
                        "launch",
                        "--root",
                        str(ROOT),
                        "--session",
                        "session",
                        "--workflow-file",
                        str(workflow_file),
                        "--skip-token-count",
                    ]
                )
        self.assertEqual(result, 0)
        self.assertTrue(launch.call_args.kwargs["skip_token_count"])


if __name__ == "__main__":
    unittest.main()
