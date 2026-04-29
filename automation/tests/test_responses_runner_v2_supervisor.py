from __future__ import annotations

import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation.responses_runner_v2 import supervisor, supervisor_agents, supervisor_policies
from automation.responses_runner_v2.contracts import relpath, runner_now
from automation.responses_runner_v2.supervisor_artifacts import load_session


ROOT = Path(__file__).resolve().parents[2]


def _brief(tmp_path: Path) -> Path:
    path = tmp_path / "clarified_brief.md"
    path.write_text("# Clarified brief\n\nBuild the supervised lane.\n", encoding="utf-8")
    return path


def _create_session(tmp_path: Path) -> dict:
    return supervisor.create_session(
        root=ROOT,
        clarified_task_brief=_brief(tmp_path).relative_to(ROOT),
        summary="Build supervised lane.",
    )


def _recommendation(rec_id: str, *, evidence: bool = True, severity: str = "medium") -> dict:
    return {
        "recommendation_id": rec_id,
        "source_agent": "codex_review_agent",
        "severity": severity,
        "recommendation": f"Apply {rec_id}",
        "evidence": ([{"artifact_path": "artifact.md", "quote_or_summary": "Relevant evidence."}] if evidence else []),
        "affected_artifacts": ["artifact.md"],
        "exact_change_needed": f"Change requested by {rec_id}.",
    }


def _review_decision(
    *,
    actor_role: str,
    review_cycle_id: str,
    recommendations: list[dict] | None = None,
    blocking_issues: list[dict] | None = None,
    approval: str = "approve_with_conditions",
    review_kind: str = "scaffold",
) -> dict:
    return {
        "schema_version": "responses_runner_v2.review_decision.v1",
        "decision_id": f"{actor_role}_{review_cycle_id}",
        "created_at": runner_now().isoformat(),
        "supervisor_session_id": "sup_test",
        "workflow_id": "workflow",
        "run_id": None,
        "stage_id": None,
        "review_cycle_id": review_cycle_id,
        "review_kind": review_kind,
        "actor_role": actor_role,
        "agent_command_id": f"cmd_{actor_role}" if actor_role != "consolidation_pass" else None,
        "status": "succeeded",
        "approval_decision": approval,
        "summary": "Synthetic review decision.",
        "markdown_report_path": "placeholder.md",
        "json_report_path": "placeholder.json",
        "reviewed_artifacts": [],
        "missing_artifacts": [],
        "blocking_issues": blocking_issues or [],
        "non_blocking_improvements": [],
        "recommendations": recommendations or [],
        "unsupported_claims": [],
        "evidence": [{"source": actor_role, "quote_or_summary": "Synthetic evidence."}],
        "command": {
            "argv": ["fake"],
            "cwd": str(ROOT),
            "started_at": runner_now().isoformat(),
            "completed_at": runner_now().isoformat(),
            "exit_code": 0,
            "stdout_path": "stdout.txt",
            "stderr_path": "stderr.txt",
        } if actor_role != "consolidation_pass" else None,
        "read_only_check": {
            "method": "workspace_snapshot_excluding_local_artifacts",
            "before_hash": "before",
            "after_hash": "before",
            "diff_path": "diff.md",
            "status": "passed",
        } if actor_role in {"codex_review_agent", "claude_review_agent"} else None,
        "validation_errors": [],
        "next_action": "proceed_to_consolidation",
    }


def _fake_runner_with_stdout(payload: dict, calls: list[list[str]]):
    def runner(argv, **_kwargs):
        calls.append(list(argv))
        return SimpleNamespace(returncode=0, stdout=json.dumps(payload), stderr="")
    return runner


def _stage_scaffold(session_id: str, tmp_path: Path) -> dict:
    scaffold_dir = tmp_path / "scaffold"
    scaffold_dir.mkdir()
    (scaffold_dir / "README.md").write_text("# scaffold\n", encoding="utf-8")
    return supervisor.stage_scaffold(root=ROOT, session_ref=session_id, scaffold_path=scaffold_dir.relative_to(ROOT))


def _write_checkpoint(
    *,
    tmp_path: Path,
    response_status: str,
    stage_status: str,
    markdown_text: str | None,
    token_preflight_status: str = "succeeded",
    updated_at: str | None = None,
    incomplete_details: dict | None = None,
) -> tuple[Path, Path]:
    run_dir = tmp_path / "run"
    stage_dir = run_dir / "stages" / "01_stage"
    stage_dir.mkdir(parents=True)
    response_json_path = stage_dir / "response.final.json"
    markdown_path = stage_dir / "response.final.md"
    response_payload = {
        "id": "resp_stage",
        "status": response_status,
        "model": "gpt-5.5-pro",
        "background": True,
        "store": True,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": markdown_text or ""}]}],
    }
    if incomplete_details:
        response_payload["incomplete_details"] = incomplete_details
    response_json_path.write_text(json.dumps(response_payload, indent=2) + "\n", encoding="utf-8")
    if markdown_text is not None:
        markdown_path.write_text(markdown_text, encoding="utf-8")
    checkpoint = {
        "schema_version": "responses_runner_v2.stage_checkpoint.v1",
        "run_id": "run_test",
        "stage_id": "stage",
        "stage_number": 1,
        "updated_at": updated_at or runner_now().isoformat(),
        "status": stage_status,
        "terminal": stage_status in {"completed", "failed", "incomplete", "blocked"},
        "resume_mode": "fresh_submit",
        "request_payload_path": relpath(ROOT, stage_dir / "request_payload.json"),
        "input_manifest_json_path": relpath(ROOT, stage_dir / "input_manifest.json"),
        "input_manifest_markdown_path": relpath(ROOT, stage_dir / "input_manifest.md"),
        "token_preflight": {"status": token_preflight_status},
        "response": {"id": "resp_stage", "status": response_status, "model": "gpt-5.5-pro", "background": True, "store": True},
        "artifacts": {
            "stage_dir": relpath(ROOT, stage_dir),
            "response_final_json_path": relpath(ROOT, response_json_path),
            **({"response_final_markdown_path": relpath(ROOT, markdown_path)} if markdown_text is not None else {}),
        },
    }
    if incomplete_details:
        checkpoint["incomplete_details"] = incomplete_details
    checkpoint_path = stage_dir / "stage_checkpoint.json"
    checkpoint_path.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
    run_manifest = {
        "schema_version": "responses_runner_v2.run_manifest.v1",
        "run_id": "run_test",
        "run_name": "run_test",
        "workflow_id": "workflow",
        "workflow_manifest_path": relpath(ROOT, tmp_path / "workflow.json"),
        "workflow_manifest_sha256": "0" * 64,
        "run_dir": relpath(ROOT, run_dir),
        "started_at": runner_now().isoformat(),
        "updated_at": runner_now().isoformat(),
        "status": stage_status,
        "stage_order": ["stage"],
        "stages": [
            {
                "stage_id": "stage",
                "stage_number": 1,
                "gate": "review_required",
                "stage_dir": relpath(ROOT, stage_dir),
                "status": stage_status,
                "response_id": "resp_stage",
                "response_status": response_status,
                "checkpoint_path": relpath(ROOT, checkpoint_path),
            }
        ],
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8")
    (stage_dir / "request_payload.json").write_text('{"model":"gpt-5.5-pro","reasoning":{"effort":"xhigh"},"prompt_cache_retention":"24h"}\n', encoding="utf-8")
    (stage_dir / "input_manifest.json").write_text("{}\n", encoding="utf-8")
    (stage_dir / "input_manifest.md").write_text("# input\n", encoding="utf-8")
    (tmp_path / "workflow.json").write_text("{}\n", encoding="utf-8")
    return run_dir, checkpoint_path


class ResponsesRunnerV2SupervisorTests(unittest.TestCase):
    def test_session_creation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            session = _create_session(Path(tmp))
            self.assertTrue((ROOT / session["_manifest_path"]).exists())
            self.assertEqual(session["operator_boundary"], supervisor.OPERATOR_BOUNDARY)
            self.assertEqual(session["model_defaults"]["primary"], "gpt-5.5-pro")
            self.assertEqual(session["model_defaults"]["structural"], "gpt-5.5")

    def test_scaffold_staging(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            record = _stage_scaffold(session["supervisor_session_id"], tmp_path)
            self.assertEqual(record["approval_status"], "staged")
            self.assertTrue((ROOT / record["hash_manifest_path"]).exists())
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(updated["status"], "scaffold_staged")

    def test_missing_operator_provisional_blocks_reviewers(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            session = _create_session(Path(tmp))
            supervisor.create_review_cycle(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_missing", review_kind="scaffold")
            job = Path(tmp) / "job.json"
            job.write_text('{"review_job_id":"job"}\n', encoding="utf-8")
            with self.assertRaises(SystemExit):
                supervisor.invoke_reviewers(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_missing",
                    review_kind="scaffold",
                    job_json=job.relative_to(ROOT),
                    output_dir=Path(tmp).relative_to(ROOT),
                )

    def test_operator_invocation_records_provisional(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            decision = _review_decision(actor_role="operator_codex", review_cycle_id="cycle_op")
            job = tmp_path / "job.json"
            job.write_text('{"review_job_id":"job"}\n', encoding="utf-8")
            calls: list[list[str]] = []
            with mock.patch("automation.responses_runner_v2.supervisor_agents._run_subprocess", side_effect=_fake_runner_with_stdout(decision, calls)):
                result = supervisor.invoke_operator(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_op",
                    review_kind="scaffold",
                    job_json=job.relative_to(ROOT),
                    output_dir=tmp_path.relative_to(ROOT),
                )
            self.assertEqual(calls[0][0:2], ["codex", "exec"])
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = updated["review_cycles"][0]
            self.assertEqual(cycle["operator_provisional_record"], result["operator_review"])

    def test_scaffold_review_gating_blocks_launch_without_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            _stage_scaffold(session["supervisor_session_id"], tmp_path)
            with self.assertRaises(SystemExit):
                supervisor.assert_scaffold_launch_allowed(root=ROOT, session_ref=session["supervisor_session_id"])

    def test_codex_review_invocation_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            calls: list[list[str]] = []
            decision = _review_decision(actor_role="codex_review_agent", review_cycle_id="cycle1")
            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle1",
                supervisor_session_id="sup_test",
                job={"review_job_id": "job1"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=_fake_runner_with_stdout(decision, calls),
            )
            self.assertEqual(calls[0][0:2], ["codex", "exec"])
            self.assertEqual(result.status, "succeeded")
            self.assertTrue((ROOT / result.decision_path).exists())

    def test_review_agent_schema_invalid_json_is_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            invalid = {"schema_version": "responses_runner_v2.review_decision.v1", "actor_role": "codex_review_agent"}
            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle_schema_bad",
                supervisor_session_id="sup_test",
                job={"review_job_id": "job"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=_fake_runner_with_stdout(invalid, []),
            )
            self.assertEqual(result.status, "malformed_output")
            payload = json.loads((ROOT / result.decision_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["approval_decision"], "blocked")
            self.assertTrue(payload["validation_errors"])

    def test_review_agent_malformed_json_is_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            def runner(_argv, **_kwargs):
                return SimpleNamespace(returncode=0, stdout="not json", stderr="")
            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle_bad_json",
                supervisor_session_id="sup_test",
                job={"review_job_id": "job1"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )
            self.assertEqual(result.status, "malformed_output")

    def test_claude_review_invocation_contract(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            calls: list[list[str]] = []
            decision = _review_decision(actor_role="claude_review_agent", review_cycle_id="cycle2")

            def runner(argv, **_kwargs):
                calls.append(list(argv))
                if "--effort" in argv and argv[argv.index("--effort") + 1] == "max":
                    return SimpleNamespace(returncode=2, stdout="", stderr="unsupported effort max")
                return SimpleNamespace(returncode=0, stdout=json.dumps(decision), stderr="")

            result = supervisor_agents.invoke_claude_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle2",
                supervisor_session_id="sup_test",
                job={"review_job_id": "job2"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )
            self.assertEqual(calls[0][0:3], ["claude", "--bare", "-p"])
            self.assertEqual(calls[1][calls[1].index("--effort") + 1], "xhigh")
            self.assertTrue(result.fallback_used)
            self.assertEqual(result.status, "succeeded")

    def test_review_agent_read_only_violation_fails(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            watched = Path(tmp) / "watched.txt"
            watched.write_text("before\n", encoding="utf-8")
            decision = _review_decision(actor_role="codex_review_agent", review_cycle_id="cycle_ro")

            def runner(_argv, **_kwargs):
                watched.write_text("after\n", encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout=json.dumps(decision), stderr="")

            result = supervisor_agents.invoke_codex_review_agent(
                root=ROOT,
                review_kind="scaffold",
                review_cycle_id="cycle_ro",
                supervisor_session_id="sup_test",
                job={"review_job_id": "job_ro"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )
            self.assertEqual(result.status, "read_only_violation")

    def test_consolidation_preserves_recommendation_not_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            supervisor.create_review_cycle(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_con", review_kind="scaffold")
            operator_path = tmp_path / "operator.json"
            codex_path = tmp_path / "codex.json"
            claude_path = tmp_path / "claude.json"
            operator_path.write_text(json.dumps(_review_decision(actor_role="operator_codex", review_cycle_id="cycle_con"), indent=2) + "\n", encoding="utf-8")
            codex_path.write_text(json.dumps(_review_decision(actor_role="codex_review_agent", review_cycle_id="cycle_con", recommendations=[_recommendation("rec1")]), indent=2) + "\n", encoding="utf-8")
            claude_path.write_text(json.dumps(_review_decision(actor_role="claude_review_agent", review_cycle_id="cycle_con", recommendations=[_recommendation("rec2")]), indent=2) + "\n", encoding="utf-8")
            updated = load_session(ROOT, session["supervisor_session_id"])
            updated["review_cycles"][0]["operator_provisional_record"] = operator_path.relative_to(ROOT).as_posix()
            from automation.responses_runner_v2 import supervisor_artifacts
            supervisor_artifacts.write_session(ROOT, supervisor_artifacts.session_dir(ROOT, session["supervisor_session_id"]), updated)
            consolidated = supervisor.consolidate_reviews(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_con",
                codex_review=codex_path.relative_to(ROOT),
                claude_review=claude_path.relative_to(ROOT),
                output=(tmp_path / "consolidated.json").relative_to(ROOT),
            )
            for rec in consolidated["recommendations"]:
                self.assertIn("consolidation_recommendation", rec)
                self.assertNotIn("operator_decision", rec)

    def test_acceptance_without_applied_evidence_rejects_selected_recommendation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            consolidated = _review_decision(
                actor_role="consolidation_pass",
                review_cycle_id="cycle_accept",
                recommendations=[{**_recommendation("supported", evidence=True), "consolidation_recommendation": "accepted_for_operator_review"}],
                review_kind="consolidation",
            )
            consolidated["agent_command_id"] = None
            consolidated["command"] = None
            consolidated["read_only_check"] = None
            consolidated_path = tmp_path / "consolidated.json"
            consolidated_path.write_text(json.dumps(consolidated, indent=2) + "\n", encoding="utf-8")
            supervisor.create_review_cycle(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_accept", review_kind="scaffold")
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_accept",
                consolidated_review=consolidated_path.relative_to(ROOT),
                accepted_recommendation_ids=["supported"],
                output=(tmp_path / "acceptance.json").relative_to(ROOT),
            )
            rec = acceptance["recommendations"][0]
            self.assertEqual(rec["operator_decision"], "rejected")
            self.assertIn("Missing applied-change evidence", rec["rejected_reason"])

    def test_operator_accepts_only_with_applied_evidence(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            consolidated = _review_decision(
                actor_role="consolidation_pass",
                review_cycle_id="cycle_accept_ok",
                recommendations=[{**_recommendation("supported", evidence=True), "consolidation_recommendation": "accepted_for_operator_review"}],
                review_kind="consolidation",
            )
            consolidated["agent_command_id"] = None
            consolidated["command"] = None
            consolidated["read_only_check"] = None
            consolidated_path = tmp_path / "consolidated.json"
            consolidated_path.write_text(json.dumps(consolidated, indent=2) + "\n", encoding="utf-8")
            evidence_path = tmp_path / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "recommendations": {
                            "supported": {
                                "operator_rationale": "Applied because evidence supports it.",
                                "changes_applied": [
                                    {
                                        "path": "artifact.md",
                                        "summary": "Applied supported change.",
                                        "evidence": [{"source": "test", "quote_or_summary": "file changed"}],
                                    }
                                ],
                                "validation_evidence": [{"source": "pytest", "quote_or_summary": "passed"}],
                            }
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            supervisor.create_review_cycle(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_accept_ok", review_kind="scaffold")
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_accept_ok",
                consolidated_review=consolidated_path.relative_to(ROOT),
                accepted_recommendation_ids=["supported"],
                applied_change_evidence=evidence_path.relative_to(ROOT),
                output=(tmp_path / "acceptance.json").relative_to(ROOT),
            )
            rec = acceptance["recommendations"][0]
            self.assertEqual(rec["operator_decision"], "accepted")
            self.assertTrue(rec["changes_applied"])
            self.assertTrue(rec["validation_evidence"])

    def test_failed_complete_artifact_is_reviewable(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            _run_dir, checkpoint = _write_checkpoint(
                tmp_path=Path(tmp),
                response_status="failed",
                stage_status="failed",
                markdown_text="# Response\n\nThis failed response still contains a complete substantive artifact with required content.\n",
            )
            outcome = supervisor_policies.classify_stage_outcome(root=ROOT, checkpoint_path=checkpoint.relative_to(ROOT))
            self.assertEqual(outcome["classification"], "failed_complete_artifact")
            self.assertTrue(outcome["reviewable"])
            self.assertTrue(outcome["review_bundle_allowed"])

    def test_failed_no_artifact_requires_archive_before_rerun(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            _stage_scaffold(session["supervisor_session_id"], tmp_path)
            run_dir, checkpoint = _write_checkpoint(tmp_path=tmp_path, response_status="failed", stage_status="failed", markdown_text=None)
            outcome = supervisor_policies.classify_stage_outcome(root=ROOT, checkpoint_path=checkpoint.relative_to(ROOT))
            self.assertFalse(supervisor_policies.can_rerun_failed_no_artifact(outcome=outcome, archive_manifest=None))
            archive = supervisor.archive_attempt(root=ROOT, session_ref=session["supervisor_session_id"], run_dir=run_dir.relative_to(ROOT), stage_id="stage", reason="failed_no_artifact")
            self.assertTrue(supervisor_policies.can_rerun_failed_no_artifact(outcome=outcome, archive_manifest=archive))
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(updated["retry_budget"]["failed_no_artifact"], 0)

    def test_failed_no_artifact_rerun_blocks_on_archive_evidence_mismatch(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            _stage_scaffold(session["supervisor_session_id"], tmp_path)
            run_dir, checkpoint = _write_checkpoint(tmp_path=tmp_path, response_status="failed", stage_status="failed", markdown_text=None)
            outcome = supervisor_policies.classify_stage_outcome(root=ROOT, checkpoint_path=checkpoint.relative_to(ROOT))
            archive = supervisor.archive_attempt(root=ROOT, session_ref=session["supervisor_session_id"], run_dir=run_dir.relative_to(ROOT), stage_id="stage", reason="failed_no_artifact")
            self.assertFalse(
                supervisor_policies.can_rerun_failed_no_artifact(
                    outcome=outcome,
                    archive_manifest=archive,
                    current_request_hash="mismatched",
                    current_scaffold_hash=archive["scaffold_hash"],
                )
            )

    def test_incomplete_output_limit_blocks_progression(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            _run_dir, checkpoint = _write_checkpoint(
                tmp_path=Path(tmp),
                response_status="incomplete",
                stage_status="incomplete",
                markdown_text="# Partial\n\nThis is partial and must not advance as a normal bundle.\n",
                incomplete_details={"reason": "max_output_tokens"},
            )
            outcome = supervisor_policies.classify_stage_outcome(
                root=ROOT,
                checkpoint_path=checkpoint.relative_to(ROOT),
                human_pause_output=(Path(tmp) / "pause.json").relative_to(ROOT),
            )
            self.assertEqual(outcome["classification"], "incomplete_output_limit")
            self.assertFalse(outcome["review_bundle_allowed"])
            self.assertTrue(outcome["human_pause_required"])
            self.assertIsNotNone(outcome["human_pause"])

    def test_blocked_token_preflight_creates_human_pause(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            _run_dir, checkpoint = _write_checkpoint(
                tmp_path=Path(tmp),
                response_status="failed",
                stage_status="blocked",
                markdown_text=None,
                token_preflight_status="failed_closed",
            )
            outcome = supervisor_policies.classify_stage_outcome(
                root=ROOT,
                checkpoint_path=checkpoint.relative_to(ROOT),
                human_pause_output=(Path(tmp) / "preflight_pause.json").relative_to(ROOT),
            )
            pause = outcome["human_pause"]
            self.assertEqual(outcome["classification"], "blocked_token_preflight")
            self.assertTrue(pause["trigger"])
            self.assertTrue(pause["artifact_to_present"])
            self.assertTrue(pause["decision_required"])
            self.assertTrue(pause["safe_continuation_action"])
            self.assertTrue(pause["blocks_review_bundle_creation"])

    def test_monitoring_anomaly_does_not_duplicate_submit(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            stale_time = (runner_now() - timedelta(hours=8)).isoformat()
            run_dir, _checkpoint = _write_checkpoint(
                tmp_path=tmp_path,
                response_status="in_progress",
                stage_status="in_progress",
                markdown_text=None,
                updated_at=stale_time,
            )
            with mock.patch("automation.responses_runner_v2.supervisor.run_workflow") as run_mock:
                result = supervisor.monitor_stage(root=ROOT, session_ref=session["supervisor_session_id"], run_dir=run_dir.relative_to(ROOT), stage_id="stage", stale_after_seconds=60)
            run_mock.assert_not_called()
            self.assertEqual(result["classification"], "long_running_monitoring_anomaly")
            self.assertEqual(result["action"], "monitor_without_duplicate_submit")

    def test_final_bundle_schema_requires_inventory_and_reviews(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            session = _create_session(tmp_path)
            incomplete = {
                "packet_version": "draft",
                "summary": "draft",
                "file_inventory": [{"path": "AGENTS.md", "action": "create", "category": "config", "purpose": "root"}],
                "emitted_files": [{"path": "AGENTS.md", "action": "create", "content_kind": "full_file", "sha256": "0" * 64}],
                "validation_evidence": [{"check_id": "x", "phase": "green", "command_or_method": "pytest", "expected_result": "pass", "actual_result": "pass", "status": "passed"}],
                "agent_reviews": {
                    "operator_codex": {"json_path": "op.json", "markdown_path": "op.md", "decision": "approve"},
                    "codex_review_agent": {"json_path": "codex.json", "markdown_path": "codex.md", "decision": "approve"},
                    "claude_review_agent": {"json_path": "claude.json", "markdown_path": "claude.md", "decision": "approve"},
                },
                "operator_acceptance": {"json_path": "accept.json", "markdown_path": "accept.md", "decision": "approve"},
                "model_migration_summary": {
                    "primary_generation_model": "gpt-5.5-pro",
                    "structural_processing_model": "gpt-5.5",
                    "prompt_cache_retention": "24h",
                    "surfaces_updated": ["engine"],
                },
                "failure_policy_summary": [
                    {"case_id": "completed_complete_artifact", "action": "review"},
                    {"case_id": "failed_complete_artifact", "action": "review"},
                    {"case_id": "failed_no_artifact", "action": "archive"},
                    {"case_id": "incomplete_output_limit", "action": "block"},
                    {"case_id": "blocked_token_preflight", "action": "pause"},
                    {"case_id": "long_running_monitoring_anomaly", "action": "monitor"},
                ],
                "human_pause_summary": [],
                "rollout_instructions": "apply and test",
                "residual_risks": [],
            }
            with self.assertRaises(SystemExit):
                supervisor.create_final_implementation_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=incomplete, output=(tmp_path / "bundle.json").relative_to(ROOT))
            complete = dict(incomplete)
            complete["consolidation"] = {"json_path": "con.json", "markdown_path": "con.md", "decision": "approve_with_conditions"}
            bundle = supervisor.create_final_implementation_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=complete, output=(tmp_path / "bundle.json").relative_to(ROOT))
            self.assertEqual(bundle["schema_version"], "responses_runner_v2.final_implementation_bundle.v1")
            self.assertTrue((tmp_path / "bundle.json").exists())


if __name__ == "__main__":
    unittest.main()
