from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation.responses_runner_v2 import supervisor_agents, supervisor_artifacts
from automation.responses_runner_v2.contracts import relpath, runner_now, sha256_file


ROOT = Path(__file__).resolve().parents[2]


def _model_output() -> dict:
    return {
        "status": "succeeded",
        "approval_decision": "approve",
        "summary": "Frozen evidence satisfies the review objective.",
        "reviewed_artifacts": [],
        "missing_artifacts": [],
        "blocking_issues": [],
        "non_blocking_improvements": [],
        "recommendations": [],
        "unsupported_claims": [],
        "evidence": [{"source": "review", "quote_or_summary": "Evidence is complete."}],
        "validation_errors": [],
        "next_action": "proceed_to_consolidation",
    }


class ReviewerIntegrityTests(unittest.TestCase):
    def test_operator_sandbox_is_write_enabled_only_for_declared_paths(self) -> None:
        calls: list[list[str]] = []

        def runner(argv, **_kwargs):
            calls.append(list(argv))
            return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

        with tempfile.TemporaryDirectory(dir=ROOT / ".local") as tmp:
            base = Path(tmp)
            common = {
                "root": ROOT,
                "review_kind": "recovery",
                "supervisor_session_id": "sup_operator_sandbox",
                "runner": runner,
            }
            supervisor_agents.invoke_operator_codex(
                **common,
                review_cycle_id="cycle_operator_read_only",
                job={"review_job_id": "read-only"},
                output_dir=relpath(ROOT, base / "read_only"),
            )
            supervisor_agents.invoke_operator_codex(
                **common,
                review_cycle_id="cycle_operator_write",
                job={
                    "review_job_id": "write",
                    "allowed_write_paths": [relpath(ROOT, base / "declared.txt")],
                },
                output_dir=relpath(ROOT, base / "write"),
            )

            self.assertEqual(calls[0][calls[0].index("--sandbox") + 1], "read-only")
            self.assertEqual(calls[1][calls[1].index("--sandbox") + 1], "workspace-write")

    def test_independent_reviewers_consume_one_shared_cycle_input(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

        local_dir = ROOT / ".local"
        local_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local_dir) as tmp:
            base = Path(tmp)
            reviewed = base / "reviewed.md"
            reviewed.write_text("reviewed\n", encoding="utf-8")
            job_payload = {"review_job_id": "shared", "reviewed_artifacts": [relpath(ROOT, reviewed)]}
            job = base / "frozen_job.json"
            job.write_text(json.dumps(job_payload, indent=2) + "\n", encoding="utf-8")
            manifest_payload = {
                "schema_version": "responses_runner_v2.reviewed_artifact_manifest.v1",
                "artifacts": [{"path": relpath(ROOT, reviewed), "sha256": sha256_file(reviewed), "bytes": reviewed.stat().st_size}],
                "aggregate_sha256": "0" * 64,
            }
            manifest = base / "reviewed_artifacts.manifest.json"
            manifest.write_text(json.dumps(manifest_payload, indent=2) + "\n", encoding="utf-8")
            review_input_payload = {
                "schema_version": "responses_runner_v2.review_input.v2",
                "created_at": runner_now().isoformat(),
                "supervisor_session_id": "sup_shared",
                "review_cycle_id": "cycle_shared",
                "review_kind": "scaffold",
                "frozen_job_path": relpath(ROOT, job),
                "frozen_job_sha256": sha256_file(job),
                "frozen_job_bytes": job.stat().st_size,
                "job": job_payload,
                "reviewed_artifact_manifest_path": relpath(ROOT, manifest),
                "reviewed_artifact_manifest_sha256": sha256_file(manifest),
                "reviewed_artifact_manifest_bytes": manifest.stat().st_size,
                "reviewed_artifacts": manifest_payload["artifacts"],
            }
            review_input = base / "review_input.json"
            review_input.write_text(json.dumps(review_input_payload, indent=2) + "\n", encoding="utf-8")
            common = {
                "root": ROOT,
                "review_kind": "scaffold",
                "review_cycle_id": "cycle_shared",
                "supervisor_session_id": "sup_shared",
                "job": relpath(ROOT, job),
                "review_input": relpath(ROOT, review_input),
                "runner": runner,
            }
            codex = supervisor_agents.invoke_codex_review_agent(
                **common,
                output_dir=relpath(ROOT, base / "codex"),
            )
            claude = supervisor_agents.invoke_claude_review_agent(
                **common,
                output_dir=relpath(ROOT, base / "claude"),
            )

            self.assertEqual(codex.command["review_input_path"], claude.command["review_input_path"])
            self.assertEqual(codex.command["review_input_sha256"], sha256_file(review_input))
            self.assertEqual(claude.command["review_input_sha256"], sha256_file(review_input))
            self.assertEqual(codex.command["job_sha256"], sha256_file(job))
            self.assertEqual(claude.command["job_bytes"], job.stat().st_size)
            self.assertEqual(
                codex.command["reviewed_artifact_manifest_sha256"],
                claude.command["reviewed_artifact_manifest_sha256"],
            )
            self.assertNotEqual(
                codex.command["composed_prompt_sha256"],
                claude.command["composed_prompt_sha256"],
            )

            def mutating_runner(_argv, **_kwargs):
                reviewed.write_text("changed by reviewer\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

            mutation = supervisor_agents.invoke_codex_review_agent(
                **{**common, "runner": mutating_runner},
                output_dir=relpath(ROOT, base / "codex_mutation"),
            )
            self.assertEqual(mutation.status, "read_only_violation")
            self.assertEqual(len(mutation.read_only_check["changed_paths"]), 1)
            self.assertEqual(mutation.read_only_check["changed_paths"][0]["path"], relpath(ROOT, reviewed))
            self.assertEqual(mutation.read_only_check["changed_paths"][0]["status"], "modified")

    def test_codex_reviewer_uses_stdin_read_only_ephemeral_and_small_schema(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="stage_output",
                review_cycle_id="cycle_integrity",
                supervisor_session_id="sup_integrity",
                job={
                    "review_job_id": "secret-job-body",
                    "workflow_id": "workflow-bound",
                    "run_id": "run-bound",
                    "stage_id": "stage-bound",
                },
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )

            argv, kwargs = calls[0]
            self.assertEqual(argv[:2], ["codex", "exec"])
            self.assertEqual(argv[-1], "-")
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
            self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-sol")
            self.assertIn("--ephemeral", argv)
            self.assertIn("--ignore-user-config", argv)
            self.assertIn("--output-schema", argv)
            self.assertNotIn("secret-job-body", " ".join(argv))
            self.assertIn("secret-job-body", kwargs["input"])
            self.assertEqual(result.status, "succeeded")
            self.assertNotIn("job_keys", result.command)
            self.assertRegex(result.command["job_sha256"], r"^[a-f0-9]{64}$")
            self.assertGreater(result.command["job_bytes"], 0)

            usage_attempt = json.loads(
                (ROOT / result.usage_attempt_path).read_text(encoding="utf-8")
            )
            self.assertEqual(usage_attempt["attempt_id"], result.command_id)
            self.assertEqual(usage_attempt["lane"], "reviewer")
            self.assertEqual(usage_attempt["model"], "gpt-5.6-sol")
            self.assertEqual(usage_attempt["status"], "succeeded")
            self.assertGreaterEqual(usage_attempt["duration_ms"], 0)
            self.assertEqual(usage_attempt["retry_count"], 0)
            self.assertEqual(usage_attempt["uploaded_files"], 0)
            self.assertEqual(usage_attempt["uploaded_bytes"], 0)
            self.assertTrue(all(value is None for key, value in usage_attempt["usage"].items() if key != "schema_version"))

            payload = json.loads((ROOT / result.decision_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["decision_id"], result.command_id)
            self.assertEqual(payload["supervisor_session_id"], "sup_integrity")
            self.assertEqual(payload["review_cycle_id"], "cycle_integrity")
            self.assertEqual(payload["actor_role"], "codex_review_agent")
            self.assertEqual(payload["workflow_id"], "workflow-bound")
            self.assertEqual(payload["run_id"], "run-bound")
            self.assertEqual(payload["stage_id"], "stage-bound")
            for key in ("composed_prompt_path", "review_input_path"):
                mode = stat.S_IMODE((ROOT / result.command[key]).stat().st_mode)
                self.assertEqual(mode, 0o600)

    def test_claude_uses_subscription_safe_read_only_tools_and_no_bare(self) -> None:
        calls: list[tuple[list[str], dict]] = []

        def runner(argv, **kwargs):
            calls.append((list(argv), kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            result = supervisor_agents.invoke_claude_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle_claude_integrity",
                supervisor_session_id="sup_integrity",
                job={"review_job_id": "job"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )

            argv, kwargs = calls[0]
            self.assertEqual(argv[:2], ["claude", "-p"])
            self.assertNotIn("--bare", argv)
            self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
            self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
            self.assertIn("--no-session-persistence", argv)
            self.assertNotIn("ANTHROPIC_API_KEY", kwargs["env"])
            self.assertIn("review_input.v1", kwargs["input"])
            self.assertEqual(result.status, "succeeded")

    def test_exit_zero_malformed_output_gets_exactly_one_format_repair(self) -> None:
        calls: list[str] = []

        def runner(_argv, **kwargs):
            calls.append(kwargs["input"])
            if len(calls) == 1:
                return SimpleNamespace(returncode=0, stdout="not-json", stderr="")
            return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle_repair",
                supervisor_session_id="sup_integrity",
                job={"review_job_id": "job"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )
            self.assertEqual(len(calls), 2)
            self.assertIn("Single Format Repair", calls[1])
            self.assertEqual(result.status, "succeeded")
            self.assertEqual(result.command["repair_attempt"]["status"], "succeeded")
            usage_attempt = json.loads(
                (ROOT / result.usage_attempt_path).read_text(encoding="utf-8")
            )
            self.assertEqual(usage_attempt["retry_count"], 1)

    def test_explicit_ignored_review_artifact_is_hashed_and_protected(self) -> None:
        local_dir = ROOT / ".local"
        local_dir.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=local_dir) as tmp:
            reviewed = Path(tmp) / "reviewed.json"
            reviewed.write_text('{"before": true}\n', encoding="utf-8")

            def runner(_argv, **_kwargs):
                reviewed.write_text('{"after": true}\n', encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout=json.dumps(_model_output()), stderr="")

            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="stage_output",
                review_cycle_id="cycle_explicit_local",
                supervisor_session_id="sup_integrity",
                job={
                    "review_job_id": "job",
                    "reviewed_artifacts": [{"path": reviewed.relative_to(ROOT).as_posix()}],
                },
                output_dir=(Path(tmp) / "output").relative_to(ROOT),
                runner=runner,
            )
            self.assertEqual(result.status, "read_only_violation")
            self.assertEqual(result.read_only_check["changed_paths"][0]["path"], reviewed.relative_to(ROOT).as_posix())

    def test_session_write_is_owner_only_atomic_and_rejects_stale_revision(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            session_path = Path(tmp) / "session"
            session_path.mkdir(mode=0o700)
            payload = {"schema_version": "responses_runner_v2.supervisor_session.v1"}
            with mock.patch.object(supervisor_artifacts, "validate_against_schema", return_value=None):
                first = supervisor_artifacts.write_session(ROOT, session_path, payload)
                loaded = supervisor_artifacts.load_session(ROOT, session_path)
                stale = dict(loaded)
                current = supervisor_artifacts.write_session(ROOT, session_path, loaded)
                self.assertEqual(current["_revision"], first["_revision"] + 1)
                with self.assertRaisesRegex(SystemExit, "revision conflict"):
                    supervisor_artifacts.write_session(ROOT, session_path, stale)

            manifest = supervisor_artifacts.session_manifest_path(session_path)
            self.assertEqual(stat.S_IMODE(manifest.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE((session_path / ".supervisor_session.revision").stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
