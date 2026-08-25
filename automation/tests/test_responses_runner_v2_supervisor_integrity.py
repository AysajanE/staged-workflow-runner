from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2 import (
    supervisor,
    supervisor_agents,
    supervisor_artifacts,
    telemetry,
    workflow as workflow_module,
)
from automation.responses_runner_v2.contracts import ASSURANCE_PROFILES, relpath, runner_now, sha256_file, sha256_text
from automation.responses_runner_v2.supervisor_artifacts import load_session, validate_against_schema
from automation.tests.supervisor_test_support import isolate_supervisor_output


ROOT = Path(__file__).resolve().parents[2]


class _BlockingLaunchClient:
    def __init__(self) -> None:
        self.create_started = threading.Event()
        self.release_create = threading.Event()
        self.create_requests: list[dict] = []
        self._upload_count = 0
        self._lock = threading.Lock()

    def upload_file(self, _path, purpose, file_expiration_policy=None):
        with self._lock:
            self._upload_count += 1
            upload_id = f"file_{self._upload_count}"
        response = {"id": upload_id, "purpose": purpose, "created_at": 1}
        if file_expiration_policy:
            response["expires_at"] = 2
        return response

    def count_input_tokens_once(self, _payload):
        return {"input_tokens": 123}

    def create_response(self, payload):
        with self._lock:
            self.create_requests.append(payload)
        self.create_started.set()
        if not self.release_create.wait(timeout=10):
            raise AssertionError("concurrent launch test did not release fake submission")
        return {
            "id": "resp_supervisor_launch",
            "status": "in_progress",
            "model": "gpt-5.6",
            "background": True,
            "store": True,
            "created_at": 1,
            "output": [],
        }


class _FailedLaunchClient(_BlockingLaunchClient):
    def create_response(self, payload):
        with self._lock:
            self.create_requests.append(payload)
        return {
            "id": "resp_supervisor_failed",
            "status": "failed",
            "model": "gpt-5.6",
            "background": True,
            "store": True,
            "created_at": 1,
            "error": {"code": "synthetic_failure", "message": "Synthetic failure"},
            "output": [],
        }


def _decision(*, root: Path, output_dir: Path, role: str, cycle: str, review_kind: str, status: str = "succeeded", blockers: list[dict] | None = None, recommendations: list[dict] | None = None, exit_code: int = 0, workflow_id: str | None = None, run_id: str | None = None, stage_id: str | None = None) -> supervisor_agents.AgentRunResult:
    command_id = f"{role}_{cycle}"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{command_id}.json"
    markdown_path = output_dir / f"{command_id}.md"
    stdout_path = output_dir / f"{command_id}.stdout.txt"
    stderr_path = output_dir / f"{command_id}.stderr.txt"
    readonly_path = output_dir / f"{command_id}.readonly.md"
    read_only = (
        {"method": "test_snapshot", "before_hash": "a" * 64, "after_hash": "a" * 64, "diff_path": relpath(root, readonly_path), "status": "passed", "changed_paths": []}
        if role in {"codex_review_agent", "claude_review_agent"}
        else None
    )
    payload = {
        "schema_version": "responses_runner_v2.review_decision.v1",
        "decision_id": command_id,
        "created_at": runner_now().isoformat(),
        "supervisor_session_id": None,
        "workflow_id": workflow_id,
        "run_id": run_id,
        "stage_id": stage_id,
        "review_cycle_id": cycle,
        "review_kind": review_kind,
        "actor_role": role,
        "agent_command_id": command_id,
        "status": status,
        "approval_decision": "approve" if status == "succeeded" and not blockers else "blocked",
        "summary": "Synthetic bound review decision.",
        "markdown_report_path": relpath(root, markdown_path),
        "json_report_path": relpath(root, json_path),
        "reviewed_artifacts": [],
        "missing_artifacts": [],
        "blocking_issues": blockers or [],
        "non_blocking_improvements": [],
        "recommendations": recommendations or [],
        "unsupported_claims": [],
        "evidence": [],
        "command": None,
        "read_only_check": read_only,
        "validation_errors": [] if status == "succeeded" else [status],
        "next_action": "proceed_to_consolidation" if status == "succeeded" else "blocked",
    }
    revision_job = output_dir.parent / "revision_job.json"
    frozen_job = revision_job if revision_job.exists() else next(
        (parent / "subject" / "frozen_job.json" for parent in (output_dir, *output_dir.parents) if (parent / "subject" / "frozen_job.json").exists()),
        None,
    )
    job_sha256 = "b" * 64
    review_input_path = None
    review_input_sha256 = None
    reviewed_artifact_manifest_sha256 = None
    if frozen_job is not None:
        candidate = frozen_job.parent / "review_input.json"
        if candidate.exists():
            job_sha256 = sha256_file(frozen_job)
            review_input = json.loads(candidate.read_text(encoding="utf-8"))
            review_input_path = relpath(root, candidate)
            review_input_sha256 = sha256_file(candidate)
            reviewed_artifact_manifest_sha256 = review_input["reviewed_artifact_manifest_sha256"]
        else:
            frozen_payload = json.loads(frozen_job.read_text(encoding="utf-8"))
            job_sha256 = sha256_text(
                json.dumps(frozen_payload, indent=2, ensure_ascii=False, sort_keys=True)
            )
    command = {
        "argv": [role],
        "cwd": str(root),
        "started_at": runner_now().isoformat(),
        "completed_at": runner_now().isoformat(),
        "exit_code": exit_code,
        "job_sha256": job_sha256,
        "review_input_path": review_input_path,
        "review_input_sha256": review_input_sha256,
        "reviewed_artifact_manifest_sha256": reviewed_artifact_manifest_sha256,
    }
    payload["command"] = {
        "command_id": command_id,
        "actor_role": role,
        "argv": command["argv"],
        "cwd": command["cwd"],
        "started_at": command["started_at"],
        "completed_at": command["completed_at"],
        "exit_code": exit_code,
        "stdout_path": relpath(root, stdout_path),
        "stderr_path": relpath(root, stderr_path),
    }
    # The supervisor owns these identity fields in real invocations.
    session_manifest = next((parent / "supervisor_session.json" for parent in output_dir.parents if (parent / "supervisor_session.json").exists()), None)
    if session_manifest is None:
        raise AssertionError("synthetic reviewer output is not inside a supervisor session")
    session = json.loads(session_manifest.read_text(encoding="utf-8"))
    payload["supervisor_session_id"] = session["supervisor_session_id"]
    markdown_path.write_text("# Synthetic review\n", encoding="utf-8")
    stdout_path.write_text("", encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")
    readonly_path.write_text("", encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return supervisor_agents.AgentRunResult(
        command_id=command_id,
        actor_role=role,
        status=status,
        approval_decision=payload["approval_decision"],
        decision_path=relpath(root, json_path),
        markdown_path=relpath(root, markdown_path),
        stdout_path=relpath(root, stdout_path),
        stderr_path=relpath(root, stderr_path),
        command=command,
        read_only_check=read_only,
    )


def _with_recovery_evidence(result: supervisor_agents.AgentRunResult, *, output_dir: Path) -> supervisor_agents.AgentRunResult:
    prompt_path = output_dir / f"{result.command_id}.composed_prompt.md"
    prompt_path.write_text(f"# {result.actor_role} prompt\n", encoding="utf-8")
    result.command.update({
        "command_id": result.command_id,
        "actor_role": result.actor_role,
        "stdout_path": result.stdout_path,
        "stderr_path": result.stderr_path,
        "composed_prompt_path": relpath(ROOT, prompt_path),
        "composed_prompt_sha256": sha256_file(prompt_path),
        "fallback_used": False,
    })
    decision_path = ROOT / result.decision_path
    payload = json.loads(decision_path.read_text(encoding="utf-8"))
    payload["command"] = result.command
    decision_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    usage_path = output_dir / f"{result.command_id}.reviewer_usage_attempt.json"
    usage = telemetry.build_usage_report([{
        "attempt_id": result.command_id,
        "lane": "reviewer",
        "model": "test-model",
        "status": "succeeded",
        "duration_ms": 1,
        "retry_count": 0,
        "upload_count": 0,
        "uploaded_bytes": 0,
        "usage": None,
    }])["attempts"][0]
    usage_path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
    return supervisor_agents.AgentRunResult(
        command_id=result.command_id,
        actor_role=result.actor_role,
        status=result.status,
        approval_decision=result.approval_decision,
        decision_path=result.decision_path,
        markdown_path=result.markdown_path,
        stdout_path=result.stdout_path,
        stderr_path=result.stderr_path,
        command=result.command,
        read_only_check=result.read_only_check,
        usage_attempt_path=relpath(ROOT, usage_path),
    )


class SupervisorIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_supervisor_output(self, ROOT)

    def _session(self, temp: Path) -> tuple[dict, Path, Path]:
        brief = temp / "brief.md"
        brief.write_text("# Accepted brief\n", encoding="utf-8")
        session = supervisor.create_session(root=ROOT, clarified_task_brief=brief.relative_to(ROOT), summary="test")
        scaffold = temp / "scaffold"
        scaffold.mkdir()
        reviewed = scaffold / "reviewed.md"
        reviewed.write_text("reviewed\n", encoding="utf-8")
        supervisor.stage_scaffold(root=ROOT, session_ref=session["supervisor_session_id"], scaffold_path=scaffold.relative_to(ROOT))
        return session, scaffold, reviewed

    def _register_v2_run(self, session: dict, temp: Path) -> dict[str, str]:
        workflow = temp / "registered.workflow.json"
        workflow.write_text('{"workflow_id":"registered_workflow"}\n', encoding="utf-8")
        run_dir = temp / "registered_run"
        attempt_dir = run_dir / "stages" / "01_stage" / "attempt_001"
        attempt_dir.mkdir(parents=True)
        response_latest = attempt_dir / "response.latest.json"
        response_latest.write_text('{"id":"resp_registered","status":"completed"}\n', encoding="utf-8")
        artifact_markdown = attempt_dir / "artifact.md"
        artifact_markdown.write_text("# Reviewed terminal artifact\n", encoding="utf-8")
        for name in ("request_payload.json", "input_manifest.json"):
            (attempt_dir / name).write_text("{}\n", encoding="utf-8")
        (attempt_dir / "input_manifest.md").write_text("# inputs\n", encoding="utf-8")
        checkpoint_path = attempt_dir / "stage_checkpoint.json"
        checkpoint = {
            "schema_version": "responses_runner_v2.stage_checkpoint.v2",
            "run_id": "registered_run",
            "stage_id": "stage",
            "stage_number": 1,
            "attempt_id": "attempt_001",
            "attempt_dir": relpath(ROOT, attempt_dir),
            "updated_at": runner_now().isoformat(),
            "status": "waiting_for_review",
            "local_state": "waiting_for_review",
            "remote_status": "completed",
            "terminal": True,
            "resume_mode": "fresh_submit",
            "review_checkpoint_required": True,
            "request_payload_path": relpath(ROOT, attempt_dir / "request_payload.json"),
            "input_manifest_json_path": relpath(ROOT, attempt_dir / "input_manifest.json"),
            "input_manifest_markdown_path": relpath(ROOT, attempt_dir / "input_manifest.md"),
            "token_preflight": {"status": "succeeded"},
            "artifacts": {
                "stage_dir": relpath(ROOT, attempt_dir),
                "response_latest_json_path": relpath(ROOT, response_latest),
                "artifact_markdown_path": relpath(ROOT, artifact_markdown),
                "artifact_markdown_sha256": sha256_file(artifact_markdown),
            },
            "finalization": {"status": "completed"},
        }
        checkpoint_path.write_text(json.dumps(checkpoint, indent=2) + "\n", encoding="utf-8")
        members = [{"role": "workflow_manifest", "path": relpath(ROOT, workflow), "sha256": sha256_file(workflow), "bytes": workflow.stat().st_size}]
        runtime: dict = {}
        contract = {
            "schema_version": "responses_runner_v2.run_contract.v1",
            "created_at": runner_now().isoformat(),
            "workflow_id": "registered_workflow",
            "assurance_profile": "critical",
            "data_handling_policy": json.loads(json.dumps(ASSURANCE_PROFILES["critical"]["data_handling"])),
            "workflow_asset_set_hash": sha256_text(json.dumps(members, sort_keys=True, separators=(",", ":"))),
            "effective_runtime": runtime,
            "effective_runtime_sha256": sha256_text(json.dumps(runtime, sort_keys=True, separators=(",", ":"))),
            "members": members,
        }
        contract["contract_sha256"] = sha256_text(json.dumps(contract, sort_keys=True, separators=(",", ":")))
        contract_path = run_dir / "run_contract.json"
        contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
        checkpoint_sha = sha256_file(checkpoint_path)
        manifest = {
            "schema_version": "responses_runner_v2.run_manifest.v2",
            "run_id": "registered_run",
            "run_name": "registered_run",
            "workflow_id": "registered_workflow",
            "workflow_manifest_path": relpath(ROOT, workflow),
            "workflow_manifest_sha256": sha256_file(workflow),
            "workflow_asset_set_hash": contract["workflow_asset_set_hash"],
            "run_contract_path": relpath(ROOT, contract_path),
            "run_contract_sha256": sha256_file(contract_path),
            "assurance_profile": "critical",
            "revision": 1,
            "run_dir": relpath(ROOT, run_dir),
            "started_at": runner_now().isoformat(),
            "updated_at": runner_now().isoformat(),
            "status": "waiting_for_review",
            "current_stage_id": "stage",
            "stage_order": ["stage"],
            "stages": [{
                "stage_id": "stage",
                "stage_number": 1,
                "gate": "review_required",
                "stage_dir": relpath(ROOT, attempt_dir.parent),
                "status": "waiting_for_review",
                "local_state": "waiting_for_review",
                "current_attempt_id": "attempt_001",
                "attempts": [{
                    "attempt_id": "attempt_001",
                    "attempt_number": 1,
                    "attempt_dir": relpath(ROOT, attempt_dir),
                    "local_state": "waiting_for_review",
                    "created_at": runner_now().isoformat(),
                    "checkpoint_path": relpath(ROOT, checkpoint_path),
                    "checkpoint_sha256": checkpoint_sha,
                }],
                "checkpoint_path": relpath(ROOT, checkpoint_path),
                "checkpoint_sha256": checkpoint_sha,
                "artifact_markdown_path": relpath(ROOT, artifact_markdown),
                "artifact_markdown_sha256": sha256_file(artifact_markdown),
            }],
        }
        manifest_path = run_dir / "run_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        current, session_path = supervisor._load_session_and_path(ROOT, session["supervisor_session_id"])
        supervisor._register_run_result(
            ROOT,
            current,
            {"run_dir": relpath(ROOT, run_dir), "run_manifest_path": relpath(ROOT, manifest_path), "status": "waiting_for_review"},
        )
        supervisor._write_session(ROOT, session_path, current)
        return {
            "run_dir": relpath(ROOT, run_dir),
            "manifest_path": relpath(ROOT, manifest_path),
            "contract_path": relpath(ROOT, contract_path),
            "checkpoint_path": relpath(ROOT, checkpoint_path),
            "workflow_asset_sha256": contract["workflow_asset_set_hash"],
            "artifact_path": relpath(ROOT, artifact_markdown),
        }

    def _run_cycle(self, session: dict, reviewed: Path, cycle: str = "cycle_bound", *, codex_status: str = "succeeded", codex_blockers: list[dict] | None = None, codex_recommendations: list[dict] | None = None, codex_exit: int = 0) -> dict:
        job = reviewed.parent / f"{cycle}.job.json"
        job.write_text(json.dumps({"review_job_id": cycle, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

        def operator(**kwargs):
            return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle=cycle, review_kind="scaffold")

        def codex(**kwargs):
            return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="codex_review_agent", cycle=cycle, review_kind="scaffold", status=codex_status, blockers=codex_blockers, recommendations=codex_recommendations, exit_code=codex_exit)

        def claude(**kwargs):
            return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="claude_review_agent", cycle=cycle, review_kind="scaffold")

        with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=operator), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=codex), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=claude):
            return supervisor.run_review_cycle(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle, review_kind="scaffold", job_json=job.relative_to(ROOT))

    def test_review_cycle_freezes_subject_quorum_and_never_accepts(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            result = self._run_cycle(session, reviewed)
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = updated["review_cycles"][0]
            self.assertEqual(result["acceptance_status"], "pending")
            self.assertEqual(cycle["quorum"]["status"], "passed")
            self.assertTrue(cycle["subject_id"])
            self.assertEqual(cycle["acceptance_status"], "pending")
            subject = json.loads((ROOT / cycle["subject_path"]).read_text(encoding="utf-8"))
            review_input_path = ROOT / subject["review_input_path"]
            review_input = json.loads(review_input_path.read_text(encoding="utf-8"))
            artifact_manifest = json.loads(
                (ROOT / subject["reviewed_artifact_manifest_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(review_input["schema_version"], "responses_runner_v2.review_input.v2")
            self.assertEqual(review_input["reviewed_artifacts"], artifact_manifest["artifacts"])
            self.assertEqual(sha256_file(review_input_path), subject["review_input_sha256"])
            independent_gates = [
                cycle["review_gates"][role]
                for role in ("codex_review_agent", "claude_review_agent")
            ]
            quorum = json.loads((ROOT / cycle["quorum"]["path"]).read_text(encoding="utf-8"))
            validate_against_schema(quorum, "review_quorum.schema.json", "common review-input quorum")
            self.assertEqual(
                {gate["review_input_sha256"] for gate in independent_gates},
                {subject["review_input_sha256"]},
            )
            self.assertEqual(
                {gate["review_input_binding_status"] for gate in independent_gates},
                {"passed"},
            )
            self.assertEqual(
                {gate["reviewed_artifact_manifest_sha256"] for gate in independent_gates},
                {subject["reviewed_artifact_manifest_sha256"]},
            )
            independent_invocations = [
                item
                for item in updated["review_agent_invocations"]
                if item["actor_role"] in {"codex_review_agent", "claude_review_agent"}
            ]
            self.assertEqual(
                {item["review_input_path"] for item in independent_invocations},
                {subject["review_input_path"]},
            )
            self.assertEqual(
                {item["review_input_sha256"] for item in independent_invocations},
                {subject["review_input_sha256"]},
            )
            self.assertEqual(
                {item["reviewed_artifact_manifest_sha256"] for item in independent_invocations},
                {subject["reviewed_artifact_manifest_sha256"]},
            )
            self.assertEqual(cycle["invocation_reservations"]["operator_provisional"]["status"], "completed")
            self.assertEqual(cycle["invocation_reservations"]["independent_reviewers"]["status"], "completed")

    def test_concurrent_review_cycle_invokes_exactly_one_operator_and_reviewer_pair(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_id = "cycle_concurrent_review"
            job = reviewed.parent / f"{cycle_id}.job.json"
            job.write_text(json.dumps({"review_job_id": cycle_id, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")
            start = threading.Barrier(3)
            operator_started = threading.Event()
            release_operator = threading.Event()
            counts = {"operator_codex": 0, "codex_review_agent": 0, "claude_review_agent": 0}
            counts_lock = threading.Lock()

            def decision(role: str, **kwargs):
                with counts_lock:
                    counts[role] += 1
                if role == "operator_codex":
                    operator_started.set()
                    if not release_operator.wait(timeout=10):
                        raise AssertionError("concurrent review test did not release the operator")
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=cycle_id, review_kind="scaffold")

            def run_once():
                start.wait(timeout=10)
                return supervisor.run_review_cycle(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=cycle_id,
                    review_kind="scaffold",
                    job_json=job.relative_to(ROOT),
                )

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)), ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(run_once) for _ in range(2)]
                start.wait(timeout=10)
                self.assertTrue(operator_started.wait(timeout=10))
                release_operator.set()
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(("succeeded", future.result(timeout=10)))
                    except SystemExit as exc:
                        outcomes.append(("blocked", str(exc)))

            self.assertEqual([status for status, _ in outcomes].count("succeeded"), 1)
            self.assertEqual([status for status, _ in outcomes].count("blocked"), 1)
            self.assertEqual(counts, {"operator_codex": 1, "codex_review_agent": 1, "claude_review_agent": 1})

    def test_concurrent_consolidation_and_acceptance_commit_exactly_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_id = "cycle_concurrent_gates"
            job = reviewed.parent / f"{cycle_id}.job.json"
            job.write_text(json.dumps({"review_job_id": cycle_id, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

            def decision(role: str, **kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=cycle_id, review_kind="scaffold")

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)):
                supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))
                supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))

            original_consolidate = supervisor._consolidate_reviews_locked
            consolidation_entered = threading.Event()
            release_consolidation = threading.Event()
            consolidation_calls = 0

            def blocking_consolidation(**kwargs):
                nonlocal consolidation_calls
                consolidation_calls += 1
                consolidation_entered.set()
                if not release_consolidation.wait(timeout=10):
                    raise AssertionError("concurrent consolidation test did not release the winner")
                return original_consolidate(**kwargs)

            with mock.patch.object(supervisor, "_consolidate_reviews_locked", side_effect=blocking_consolidation), ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(supervisor.consolidate_reviews, root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id)
                self.assertTrue(consolidation_entered.wait(timeout=10))
                second = executor.submit(supervisor.consolidate_reviews, root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id)
                with self.assertRaisesRegex(SystemExit, "already owned"):
                    second.result(timeout=10)
                release_consolidation.set()
                first.result(timeout=10)
            self.assertEqual(consolidation_calls, 1)

            original_accept = supervisor._accept_consolidated_review_locked
            acceptance_entered = threading.Event()
            release_acceptance = threading.Event()
            acceptance_calls = 0

            def blocking_acceptance(**kwargs):
                nonlocal acceptance_calls
                acceptance_calls += 1
                acceptance_entered.set()
                if not release_acceptance.wait(timeout=10):
                    raise AssertionError("concurrent acceptance test did not release the winner")
                return original_accept(**kwargs)

            with mock.patch.object(supervisor, "_accept_consolidated_review_locked", side_effect=blocking_acceptance), ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(supervisor.accept_consolidated_review, root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, accepted_recommendation_ids=[])
                self.assertTrue(acceptance_entered.wait(timeout=10))
                second = executor.submit(supervisor.accept_consolidated_review, root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, accepted_recommendation_ids=[])
                with self.assertRaisesRegex(SystemExit, "already owned"):
                    second.result(timeout=10)
                release_acceptance.set()
                first.result(timeout=10)
            self.assertEqual(acceptance_calls, 1)
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == cycle_id)
            self.assertEqual(sha256_file(ROOT / cycle["consolidation"]), cycle["consolidation_sha256"])
            self.assertEqual(sha256_file(ROOT / cycle["acceptance_record"]), cycle["acceptance_record_sha256"])

    def test_cross_cycle_transitions_serialize_before_derived_writes(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_ids = ("cycle_cross_a", "cycle_cross_b")
            for cycle_id in cycle_ids:
                job = reviewed.parent / f"{cycle_id}.job.json"
                job.write_text(json.dumps({"review_job_id": cycle_id, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

                def decision(role: str, **kwargs):
                    return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=cycle_id, review_kind="scaffold")

                with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)):
                    supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))
                    supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))

            original = supervisor._consolidate_reviews_locked
            first_entered = threading.Event()
            release_first = threading.Event()

            def blocking(**kwargs):
                if kwargs["review_cycle_id"] == cycle_ids[0]:
                    first_entered.set()
                    if not release_first.wait(timeout=10):
                        raise AssertionError("cross-cycle transition test did not release the first cycle")
                return original(**kwargs)

            with mock.patch.object(supervisor, "_consolidate_reviews_locked", side_effect=blocking), ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(supervisor.consolidate_reviews, root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_ids[0])
                self.assertTrue(first_entered.wait(timeout=10))
                second = executor.submit(supervisor.consolidate_reviews, root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_ids[1])
                with self.assertRaisesRegex(SystemExit, "session mutation is already owned"):
                    second.result(timeout=10)
                current = load_session(ROOT, session["supervisor_session_id"])
                second_cycle = next(item for item in current["review_cycles"] if item["review_cycle_id"] == cycle_ids[1])
                self.assertFalse((ROOT / second_cycle["derived_paths"]["consolidation_json"]).exists())
                release_first.set()
                first.result(timeout=10)

            second_result = supervisor.consolidate_reviews(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_ids[1])
            self.assertEqual(second_result["review_cycle_id"], cycle_ids[1])

    def test_consolidation_retry_reuses_precommit_derived_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_id = "cycle_consolidation_retry"
            job = reviewed.parent / f"{cycle_id}.job.json"
            job.write_text(json.dumps({"review_job_id": cycle_id, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

            def decision(role: str, **kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=cycle_id, review_kind="scaffold")

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)):
                supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))
                supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))

            original_write_session = supervisor._write_session
            crashed = False

            def crash_before_commit(root, session_path, payload):
                nonlocal crashed
                cycle = next(item for item in payload["review_cycles"] if item["review_cycle_id"] == cycle_id)
                if cycle.get("consolidation") and not crashed:
                    crashed = True
                    raise SystemExit("synthetic crash before consolidation session commit")
                return original_write_session(root, session_path, payload)

            with mock.patch.object(supervisor, "_write_session", side_effect=crash_before_commit):
                with self.assertRaisesRegex(SystemExit, "synthetic crash"):
                    supervisor.consolidate_reviews(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id)
            interrupted = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(item for item in interrupted["review_cycles"] if item["review_cycle_id"] == cycle_id)
            paths = cycle["derived_paths"]
            before = {
                key: sha256_file(ROOT / paths[key])
                for key in ("quorum", "consolidation_json", "consolidation_md")
            }
            result = supervisor.consolidate_reviews(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id)
            self.assertEqual(result["review_cycle_id"], cycle_id)
            self.assertEqual(before, {key: sha256_file(ROOT / paths[key]) for key in before})

    def test_write_once_json_serializes_conflicting_creates(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            output = Path(raw) / "immutable.json"
            start = threading.Barrier(3)

            def write(value: str):
                start.wait(timeout=10)
                return supervisor._write_once_json(ROOT, output.relative_to(ROOT), {"value": value})

            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(write, value) for value in ("first", "second")]
                start.wait(timeout=10)
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(("written", future.result(timeout=10)))
                    except SystemExit as exc:
                        outcomes.append(("blocked", str(exc)))

            self.assertEqual([status for status, _ in outcomes].count("written"), 1)
            self.assertEqual([status for status, _ in outcomes].count("blocked"), 1)
            self.assertIn(json.loads(output.read_text(encoding="utf-8"))["value"], {"first", "second"})

    def test_acceptance_and_operator_revision_share_cycle_transition_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, scaffold, reviewed = self._session(Path(raw))
            recommendation = {
                "recommendation_id": "revise_content",
                "source_agent": "codex_review_agent",
                "severity": "medium",
                "recommendation": "Revise the reviewed content.",
                "evidence": [{"artifact_path": relpath(ROOT, reviewed), "quote_or_summary": "The content needs revision."}],
                "affected_artifacts": [relpath(ROOT, reviewed)],
                "exact_change_needed": "Replace the reviewed content.",
            }
            result = self._run_cycle(session, reviewed, codex_recommendations=[recommendation])
            consolidation = json.loads((ROOT / result["consolidation"]).read_text(encoding="utf-8"))
            recommendation_id = consolidation["recommendations"][0]["recommendation_id"]
            supervisor.create_revision_directive(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                accepted_recommendation_ids=[recommendation_id],
                rejected_recommendations={},
                revised_artifacts=[reviewed.relative_to(ROOT)],
                revision_scaffold_path=scaffold.relative_to(ROOT),
            )

            revision_entered = threading.Event()
            release_revision = threading.Event()

            def blocking_revision(**_kwargs):
                revision_entered.set()
                if not release_revision.wait(timeout=10):
                    raise AssertionError("acceptance/revision race test did not release the revision")
                return {"status": "synthetic_revision_complete"}

            with mock.patch.object(supervisor, "_run_revision_and_review_locked", side_effect=blocking_revision), ThreadPoolExecutor(max_workers=2) as executor:
                revision = executor.submit(
                    supervisor.run_revision_and_review,
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    source_review_cycle_id="cycle_bound",
                    new_review_cycle_id="cycle_revised",
                )
                self.assertTrue(revision_entered.wait(timeout=10))
                acceptance = executor.submit(
                    supervisor.accept_consolidated_review,
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_bound",
                    accepted_recommendation_ids=[],
                )
                with self.assertRaisesRegex(SystemExit, "already owned"):
                    acceptance.result(timeout=10)
                release_revision.set()
                self.assertEqual(revision.result(timeout=10)["status"], "synthetic_revision_complete")

            with self.assertRaisesRegex(SystemExit, "active revision"):
                supervisor.accept_consolidated_review(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_bound",
                    accepted_recommendation_ids=[],
                )

    def test_acceptance_retry_preserves_primary_artifacts_after_binding_crash(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            self._run_cycle(session, reviewed)
            original_write_once = supervisor._write_once_json
            crashed = False

            def crash_before_binding(root, path, payload, schema=None, label="artifact"):
                nonlocal crashed
                if label == "operator acceptance binding" and not crashed:
                    crashed = True
                    raise SystemExit("synthetic crash before acceptance binding")
                return original_write_once(root, path, payload, schema, label)

            with mock.patch.object(supervisor, "_write_once_json", side_effect=crash_before_binding):
                with self.assertRaisesRegex(SystemExit, "synthetic crash"):
                    supervisor.accept_consolidated_review(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id="cycle_bound",
                        accepted_recommendation_ids=[],
                    )
            interrupted = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(item for item in interrupted["review_cycles"] if item["review_cycle_id"] == "cycle_bound")
            decision_path = ROOT / cycle["derived_paths"]["acceptance_json"]
            markdown_path = ROOT / cycle["derived_paths"]["acceptance_md"]
            before = (sha256_file(decision_path), sha256_file(markdown_path))
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            self.assertEqual(before, (sha256_file(decision_path), sha256_file(markdown_path)))

    def test_blocked_acceptance_can_be_superseded_without_rerunning_reviewers(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, reviewed = self._session(temp)
            recommendation = {
                "recommendation_id": "must_fix",
                "source_agent": "codex_review_agent",
                "severity": "blocking",
                "recommendation": "Apply the verified correction.",
                "evidence": [
                    {
                        "artifact_path": relpath(ROOT, reviewed),
                        "quote_or_summary": "The correction is required.",
                    }
                ],
                "affected_artifacts": [relpath(ROOT, reviewed)],
                "exact_change_needed": "Apply the correction.",
            }
            result = self._run_cycle(
                session,
                reviewed,
                codex_recommendations=[recommendation],
            )
            consolidation = json.loads(
                (ROOT / result["consolidation"]).read_text(encoding="utf-8")
            )
            recommendation_id = consolidation["recommendations"][0]["recommendation_id"]

            blocked = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                accepted_recommendation_ids=[recommendation_id],
            )
            self.assertEqual(blocked["approval_decision"], "do_not_approve")

            evidence_path = temp / "applied_change_evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "recommendations": {
                            recommendation_id: {
                                "operator_rationale": "Applied after correcting the evidence input.",
                                "changes_applied": [
                                    {
                                        "path": relpath(ROOT, reviewed),
                                        "summary": "Recorded the verified correction.",
                                        "evidence": [
                                            {
                                                "source": "operator",
                                                "quote_or_summary": "Correction applied.",
                                            }
                                        ],
                                    }
                                ],
                                "validation_evidence": [
                                    {
                                        "source": "focused_test",
                                        "quote_or_summary": "Focused validation passed.",
                                    }
                                ],
                            }
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            approved = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                accepted_recommendation_ids=[recommendation_id],
                applied_change_evidence=evidence_path.relative_to(ROOT),
            )

            self.assertEqual(approved["approval_decision"], "approve")
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = updated["review_cycles"][0]
            self.assertEqual(len(cycle["acceptance_history"]), 2)
            self.assertEqual(
                cycle["acceptance_history"][1]["supersedes"],
                blocked["json_report_path"],
            )
            self.assertNotEqual(
                cycle["acceptance_history"][0]["path"],
                cycle["acceptance_history"][1]["path"],
            )

    def test_review_bundle_retry_reconciles_primary_json_after_binding_crash(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, _reviewed = self._session(temp)
            run = self._register_v2_run(session, temp)
            manifest_path = ROOT / run["manifest_path"]
            failed_complete_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            failed_complete_manifest["stages"][0]["status"] = "failed_complete"
            manifest_path.write_text(
                json.dumps(failed_complete_manifest, indent=2) + "\n",
                encoding="utf-8",
            )
            checkpoint = json.loads((ROOT / run["checkpoint_path"]).read_text(encoding="utf-8"))
            response_path = checkpoint["artifacts"]["response_latest_json_path"]
            anomaly_path = temp / "stage.monitoring_anomaly.json"
            anomaly_path.write_text(json.dumps({"review_bundle_allowed": False, "reviewable": False}) + "\n", encoding="utf-8")
            outcome_path = temp / "stage_outcome.json"
            outcome_path.write_text(json.dumps({"review_bundle_allowed": True, "reviewable": True}) + "\n", encoding="utf-8")
            current, session_path = supervisor._load_session_and_path(ROOT, session["supervisor_session_id"])
            current["stage_outcomes"].append({"run_id": "registered_run", "stage_id": "stage", "artifact_path": relpath(ROOT, anomaly_path)})
            current["stage_outcomes"].append({"run_id": "registered_run", "stage_id": "stage", "artifact_path": relpath(ROOT, outcome_path)})
            supervisor._write_session(ROOT, session_path, current)
            cycle_id = "stage_bundle_retry"
            job = temp / "stage_bundle_retry.job.json"
            job.write_text(
                json.dumps({
                    "review_job_id": cycle_id,
                    "reviewed_artifacts": [run["artifact_path"], response_path, relpath(ROOT, outcome_path)],
                    "run_id": "registered_run",
                    "stage_id": "stage",
                }) + "\n",
                encoding="utf-8",
            )

            def decision(role: str, **kwargs):
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role=role,
                    cycle=cycle_id,
                    review_kind="stage_output",
                    workflow_id="registered_workflow",
                    run_id="registered_run",
                    stage_id="stage",
                )

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)):
                review = supervisor.run_review_cycle(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=cycle_id,
                    review_kind="stage_output",
                    job_json=job.relative_to(ROOT),
                )
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle_id,
                accepted_recommendation_ids=[],
            )
            current = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(item for item in current["review_cycles"] if item["review_cycle_id"] == cycle_id)
            bundle_args = {
                "root": ROOT,
                "session_ref": session["supervisor_session_id"],
                "review_cycle_id": cycle_id,
                "output_path": None,
            }
            original_write_once = supervisor._write_once_json
            crashed = False

            def crash_before_binding(root, path, payload, schema=None, label="artifact"):
                nonlocal crashed
                if label == "review bundle binding" and not crashed:
                    crashed = True
                    raise SystemExit("synthetic crash before review bundle binding")
                return original_write_once(root, path, payload, schema, label)

            with mock.patch.object(supervisor, "_write_once_json", side_effect=crash_before_binding):
                with self.assertRaisesRegex(SystemExit, "synthetic crash"):
                    supervisor.create_approved_review_bundle_for_cycle(**bundle_args)
            bundle_path = ROOT / cycle["derived_paths"]["review_bundle"]
            before_sha256 = sha256_file(bundle_path)
            bundle = supervisor.create_approved_review_bundle_for_cycle(**bundle_args)
            self.assertEqual(sha256_file(bundle_path), before_sha256)
            self.assertEqual(bundle["bundle_path"], relpath(ROOT, bundle_path))
            completed = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(len(completed["approved_review_bundles"]), 1)
            repeated = supervisor.accept_and_create_review_bundle(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle_id,
                accepted_recommendation_ids=[],
            )
            self.assertEqual(repeated["acceptance"]["approval_decision"], "approve")
            self.assertEqual(repeated["bundle"]["bundle_path"], relpath(ROOT, bundle_path))
            next_result = {
                "run_dir": run["run_dir"],
                "run_manifest_path": run["manifest_path"],
                "status": "in_progress",
                "stage_id": "next_stage",
            }
            with mock.patch.object(
                supervisor,
                "run_workflow",
                return_value=next_result,
            ) as launch:
                continued = supervisor.continue_after_approved_review(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=cycle_id,
                )
            self.assertEqual(continued, next_result)
            runtime = launch.call_args.kwargs["runtime"]
            self.assertEqual(runtime.run_dir, Path(run["run_dir"]))
            self.assertEqual(runtime.review_bundles, [relpath(ROOT, bundle_path)])

    def test_cross_finalizer_calls_share_one_session_lock(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, _reviewed = self._session(Path(raw))
            start = threading.Barrier(3)
            entered = threading.Event()
            release = threading.Event()
            calls: list[str] = []
            calls_lock = threading.Lock()

            def finalizer(kind: str, **_kwargs):
                with calls_lock:
                    calls.append(kind)
                entered.set()
                if not release.wait(timeout=10):
                    raise AssertionError("cross-finalizer test did not release the winner")
                return {"kind": kind}

            def invoke(kind: str):
                start.wait(timeout=10)
                if kind == "implementation":
                    return supervisor.create_final_implementation_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload={}, output=None)
                return supervisor.create_final_delivery_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload={}, output=None)

            with mock.patch.object(supervisor, "_create_final_implementation_bundle_locked", side_effect=lambda **kwargs: finalizer("implementation", **kwargs)), mock.patch.object(supervisor, "_create_final_delivery_bundle_locked", side_effect=lambda **kwargs: finalizer("delivery", **kwargs)), ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(invoke, kind) for kind in ("implementation", "delivery")]
                start.wait(timeout=10)
                self.assertTrue(entered.wait(timeout=10))
                for _ in range(100):
                    if any(future.done() for future in futures):
                        break
                    threading.Event().wait(0.01)
                self.assertTrue(any(future.done() for future in futures))
                release.set()
                outcomes = []
                for future in futures:
                    try:
                        outcomes.append(future.result(timeout=10))
                    except SystemExit as exc:
                        outcomes.append(str(exc))
            self.assertEqual(len(calls), 1)
            self.assertEqual(sum(isinstance(item, dict) for item in outcomes), 1)
            self.assertEqual(sum("already owned" in item for item in outcomes if isinstance(item, str)), 1)

    def test_reviewer_pair_crash_window_recovers_exact_outputs_without_reinvocation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_id = "cycle_reviewer_recovery"
            job = reviewed.parent / f"{cycle_id}.job.json"
            job.write_text(json.dumps({"review_job_id": cycle_id, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

            with mock.patch.object(
                supervisor_agents,
                "invoke_operator_codex",
                side_effect=lambda **kwargs: _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle=cycle_id, review_kind="scaffold"),
            ):
                supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))

            calls = {"codex_review_agent": 0, "claude_review_agent": 0}

            def recoverable_decision(role: str, **kwargs):
                calls[role] += 1
                result = _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=cycle_id, review_kind="scaffold")
                output_dir = Path(kwargs["output_dir"])
                prompt_path = output_dir / f"{result.command_id}.composed_prompt.md"
                prompt_path.write_text(f"# {role} prompt\n", encoding="utf-8")
                result.command.update({
                    "command_id": result.command_id,
                    "actor_role": role,
                    "stdout_path": result.stdout_path,
                    "stderr_path": result.stderr_path,
                    "composed_prompt_path": relpath(ROOT, prompt_path),
                    "composed_prompt_sha256": sha256_file(prompt_path),
                    "fallback_used": False,
                })
                decision_path = ROOT / result.decision_path
                payload = json.loads(decision_path.read_text(encoding="utf-8"))
                payload["command"] = result.command
                decision_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                usage_path = output_dir / f"{result.command_id}.reviewer_usage_attempt.json"
                usage = telemetry.build_usage_report([{
                    "attempt_id": result.command_id,
                    "lane": "reviewer",
                    "model": "test-model",
                    "status": "succeeded",
                    "duration_ms": 1,
                    "retry_count": 0,
                    "upload_count": 0,
                    "uploaded_bytes": 0,
                    "usage": None,
                }])["attempts"][0]
                usage_path.write_text(json.dumps(usage, indent=2) + "\n", encoding="utf-8")
                return supervisor_agents.AgentRunResult(
                    command_id=result.command_id,
                    actor_role=result.actor_role,
                    status=result.status,
                    approval_decision=result.approval_decision,
                    decision_path=result.decision_path,
                    markdown_path=result.markdown_path,
                    stdout_path=result.stdout_path,
                    stderr_path=result.stderr_path,
                    command=result.command,
                    read_only_check=result.read_only_check,
                    usage_attempt_path=relpath(ROOT, usage_path),
                )

            original_write_session = supervisor._write_session
            write_count = 0

            def crash_after_outputs(root, session_path, payload):
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise SystemExit("synthetic crash before reviewer completion CAS")
                return original_write_session(root, session_path, payload)

            with mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: recoverable_decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: recoverable_decision("claude_review_agent", **kwargs)), mock.patch.object(supervisor, "_write_session", side_effect=crash_after_outputs):
                with self.assertRaisesRegex(SystemExit, "synthetic crash"):
                    supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))

            interrupted = load_session(ROOT, session["supervisor_session_id"])
            interrupted_cycle = next(item for item in interrupted["review_cycles"] if item["review_cycle_id"] == cycle_id)
            self.assertEqual(interrupted_cycle["invocation_reservations"]["independent_reviewers"]["status"], "reserved")
            with mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=AssertionError("Codex reviewer must not be reinvoked")), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=AssertionError("Claude reviewer must not be reinvoked")):
                recovered = supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id=cycle_id, review_kind="scaffold", job_json=job.relative_to(ROOT))
            self.assertEqual(set(recovered), {"codex_review", "claude_review"})
            self.assertEqual(calls, {"codex_review_agent": 1, "claude_review_agent": 1})
            completed = load_session(ROOT, session["supervisor_session_id"])
            completed_cycle = next(item for item in completed["review_cycles"] if item["review_cycle_id"] == cycle_id)
            reservation = completed_cycle["invocation_reservations"]["independent_reviewers"]
            self.assertEqual(reservation["status"], "completed")
            self.assertEqual(reservation["recovery_count"], 1)

    def test_partial_reviewer_reservation_recovers_delivered_role_and_reinvokes_missing_role(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_id = "cycle_partial_reviewer_recovery"
            job = reviewed.parent / f"{cycle_id}.job.json"
            job.write_text(
                json.dumps(
                    {
                        "review_job_id": cycle_id,
                        "reviewed_artifacts": [relpath(ROOT, reviewed)],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                supervisor_agents,
                "invoke_operator_codex",
                side_effect=lambda **kwargs: _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role="operator_codex",
                    cycle=cycle_id,
                    review_kind="scaffold",
                ),
            ):
                supervisor.invoke_operator(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=cycle_id,
                    review_kind="scaffold",
                    job_json=job.relative_to(ROOT),
                )

            delivered: supervisor_agents.AgentRunResult | None = None

            def codex_first(**kwargs):
                nonlocal delivered
                delivered = _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role="codex_review_agent",
                    cycle=cycle_id,
                    review_kind="scaffold",
                )
                return delivered

            with mock.patch.object(
                supervisor_agents,
                "invoke_codex_review_agent",
                side_effect=codex_first,
            ), mock.patch.object(
                supervisor_agents,
                "invoke_claude_review_agent",
                side_effect=SystemExit("synthetic claude crash"),
            ):
                with self.assertRaisesRegex(SystemExit, "synthetic claude crash"):
                    supervisor.invoke_reviewers(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id=cycle_id,
                        review_kind="scaffold",
                        job_json=job.relative_to(ROOT),
                    )
            self.assertIsNotNone(delivered)

            def recover_role(**kwargs):
                return delivered if kwargs["actor_role"] == "codex_review_agent" else None

            with mock.patch.object(
                supervisor,
                "_recover_reserved_agent_result",
                side_effect=recover_role,
            ), mock.patch.object(
                supervisor_agents,
                "invoke_codex_review_agent",
                side_effect=AssertionError("delivered Codex verdict must not be reinvoked"),
            ) as codex_reinvoke, mock.patch.object(
                supervisor_agents,
                "invoke_claude_review_agent",
                side_effect=lambda **kwargs: _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role="claude_review_agent",
                    cycle=cycle_id,
                    review_kind="scaffold",
                ),
            ) as claude_reinvoke:
                recovered = supervisor.invoke_reviewers(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=cycle_id,
                    review_kind="scaffold",
                    job_json=job.relative_to(ROOT),
                )

            self.assertEqual(set(recovered), {"codex_review", "claude_review"})
            codex_reinvoke.assert_not_called()
            claude_reinvoke.assert_called_once()
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(
                item for item in updated["review_cycles"] if item["review_cycle_id"] == cycle_id
            )
            self.assertEqual(
                cycle["invocation_reservations"]["independent_reviewers"]["status"],
                "completed",
            )
            self.assertEqual(
                cycle["invocation_reservations"]["independent_reviewers"]["recovery_count"],
                1,
            )

    def test_zero_result_operator_reservation_can_be_released_and_reinvoked(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            cycle_id = "cycle_operator_release"
            job = reviewed.parent / f"{cycle_id}.job.json"
            job.write_text(
                json.dumps(
                    {
                        "review_job_id": cycle_id,
                        "reviewed_artifacts": [relpath(ROOT, reviewed)],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            with mock.patch.object(
                supervisor_agents,
                "invoke_operator_codex",
                side_effect=RuntimeError("synthetic pre-output crash"),
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic pre-output crash"):
                    supervisor.invoke_operator(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id=cycle_id,
                        review_kind="scaffold",
                        job_json=job.relative_to(ROOT),
                    )

            interrupted = load_session(ROOT, session["supervisor_session_id"])
            interrupted_cycle = next(
                item for item in interrupted["review_cycles"] if item["review_cycle_id"] == cycle_id
            )
            self.assertEqual(
                interrupted_cycle["invocation_reservations"]["operator_provisional"]["status"],
                "reserved",
            )

            released = supervisor.release_invocation_reservation(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle_id,
                operation="operator_provisional",
                reason="The agent process exited before producing any decision artifact.",
            )
            release_payload = json.loads((ROOT / released["path"]).read_text(encoding="utf-8"))
            self.assertEqual(release_payload["decision_candidates"], [])
            self.assertEqual(release_payload["reservation"]["status"], "reserved")

            with mock.patch.object(
                supervisor_agents,
                "invoke_operator_codex",
                side_effect=lambda **kwargs: _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role="operator_codex",
                    cycle=cycle_id,
                    review_kind="scaffold",
                ),
            ):
                result = supervisor.invoke_operator(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=cycle_id,
                    review_kind="scaffold",
                    job_json=job.relative_to(ROOT),
                )
            self.assertIn("operator_review", result)
            completed = load_session(ROOT, session["supervisor_session_id"])
            completed_cycle = next(
                item for item in completed["review_cycles"] if item["review_cycle_id"] == cycle_id
            )
            self.assertEqual(
                completed_cycle["invocation_reservations"]["operator_provisional"]["status"],
                "completed",
            )
            self.assertEqual(len(completed_cycle["released_invocation_reservations"]), 1)

    def test_cross_cycle_or_arbitrary_decision_path_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            self._run_cycle(session, reviewed)
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = updated["review_cycles"][0]
            fake = Path(raw) / "fake.json"
            fake.write_text((ROOT / cycle["review_agent_outputs"]["codex_review_agent"]).read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaises(SystemExit):
                supervisor.consolidate_reviews(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", codex_review=fake.relative_to(ROOT))

    def test_post_invocation_json_or_markdown_tampering_cannot_consolidate(self) -> None:
        for tampered_kind in ("decision", "markdown"):
            with self.subTest(tampered_kind=tampered_kind), tempfile.TemporaryDirectory(dir=ROOT) as raw:
                session, _scaffold, reviewed = self._session(Path(raw))
                cycle_id = f"cycle_tampered_{tampered_kind}"
                job = reviewed.parent / f"{cycle_id}.job.json"
                job.write_text(
                    json.dumps({"review_job_id": cycle_id, "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n",
                    encoding="utf-8",
                )

                def decision(role: str, **kwargs):
                    return _decision(
                        root=ROOT,
                        output_dir=Path(kwargs["output_dir"]),
                        role=role,
                        cycle=cycle_id,
                        review_kind="scaffold",
                    )

                with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)):
                    supervisor.invoke_operator(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id=cycle_id,
                        review_kind="scaffold",
                        job_json=job.relative_to(ROOT),
                    )
                    supervisor.invoke_reviewers(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id=cycle_id,
                        review_kind="scaffold",
                        job_json=job.relative_to(ROOT),
                    )

                updated = load_session(ROOT, session["supervisor_session_id"])
                cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == cycle_id)
                gate = cycle["review_gates"]["codex_review_agent"]
                if tampered_kind == "decision":
                    path = ROOT / gate["decision_path"]
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    payload["summary"] = "Tampered after invocation."
                    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
                else:
                    path = ROOT / gate["markdown_path"]
                    path.write_text(path.read_text(encoding="utf-8") + "\nTampered after invocation.\n", encoding="utf-8")

                with self.assertRaisesRegex(SystemExit, r"codex_review_agent review (decision|markdown) hash mismatch"):
                    supervisor.consolidate_reviews(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id=cycle_id,
                    )

    def test_post_consolidation_reviewer_tampering_blocks_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            self._run_cycle(session, reviewed)
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = updated["review_cycles"][0]
            markdown = ROOT / cycle["review_gates"]["claude_review_agent"]["markdown_path"]
            markdown.write_text(markdown.read_text(encoding="utf-8") + "\nTampered after consolidation.\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "claude_review_agent review markdown hash mismatch"):
                supervisor.accept_consolidated_review(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_bound",
                    accepted_recommendation_ids=[],
                )

    def test_reviewers_require_exact_successful_operator_subject_gate(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            job = reviewed.parent / "operator_gate.job.json"
            job.write_text(json.dumps({"review_job_id": "operator_gate", "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

            def operator(**kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle="operator_gate", review_kind="scaffold")

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=operator):
                supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="operator_gate", review_kind="scaffold", job_json=job.relative_to(ROOT))
            current, session_path = supervisor._load_session_and_path(ROOT, session["supervisor_session_id"])
            current["review_cycles"][0]["review_gates"]["operator_codex"]["subject_id"] = "0" * 64
            supervisor._write_session(ROOT, session_path, current)
            with mock.patch.object(supervisor_agents, "invoke_codex_review_agent") as codex, mock.patch.object(supervisor_agents, "invoke_claude_review_agent") as claude:
                with self.assertRaises(SystemExit):
                    supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="operator_gate", review_kind="scaffold", job_json=job.relative_to(ROOT))
            codex.assert_not_called()
            claude.assert_not_called()

    def test_reviewers_reject_existing_failed_operator_provisional(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            job = reviewed.parent / "failed_operator.job.json"
            job.write_text(json.dumps({"review_job_id": "failed_operator", "reviewed_artifacts": [relpath(ROOT, reviewed)]}) + "\n", encoding="utf-8")

            def operator(**kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle="failed_operator", review_kind="scaffold", status="failed")

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=operator):
                supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="failed_operator", review_kind="scaffold", job_json=job.relative_to(ROOT))
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertTrue(updated["review_cycles"][0]["operator_provisional_record"])
            self.assertEqual(updated["review_cycles"][0]["review_gates"]["operator_codex"]["gate_status"], "blocked")
            with mock.patch.object(supervisor_agents, "invoke_codex_review_agent") as codex, mock.patch.object(supervisor_agents, "invoke_claude_review_agent") as claude:
                with self.assertRaises(SystemExit):
                    supervisor.invoke_reviewers(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="failed_operator", review_kind="scaffold", job_json=job.relative_to(ROOT))
            codex.assert_not_called()
            claude.assert_not_called()

    def test_stage_review_rejects_unregistered_caller_subject(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            job = reviewed.parent / "unregistered_stage.job.json"
            job.write_text(
                json.dumps(
                    {
                        "review_job_id": "unregistered_stage",
                        "reviewed_artifacts": [relpath(ROOT, reviewed)],
                        "workflow_id": "caller_workflow",
                        "workflow_asset_sha256": "1" * 64,
                        "run_id": "caller_run",
                        "stage_id": "caller_stage",
                        "attempt_id": "attempt_999",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with mock.patch.object(supervisor_agents, "invoke_operator_codex") as operator:
                with self.assertRaises(SystemExit):
                    supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="unregistered_stage", review_kind="stage_output", job_json=job.relative_to(ROOT))
            operator.assert_not_called()

    def test_stage_review_derives_complete_subject_from_registered_v2_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, reviewed = self._session(temp)
            run = self._register_v2_run(session, temp)
            job = reviewed.parent / "registered_stage.job.json"
            job_payload = {
                "review_job_id": "registered_stage",
                "reviewed_artifacts": [relpath(ROOT, reviewed)],
                "run_id": "registered_run",
                "stage_id": "stage",
            }
            job.write_text(json.dumps(job_payload) + "\n", encoding="utf-8")

            def operator(**kwargs):
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role="operator_codex",
                    cycle="registered_stage",
                    review_kind="stage_output",
                    workflow_id="registered_workflow",
                    run_id="registered_run",
                    stage_id="stage",
                )

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=operator):
                supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="registered_stage", review_kind="stage_output", job_json=job.relative_to(ROOT))
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "registered_stage")
            subject = json.loads((ROOT / cycle["subject_path"]).read_text(encoding="utf-8"))
            self.assertEqual(subject["attempt_id"], "attempt_001")
            self.assertEqual(subject["checkpoint_path"], run["checkpoint_path"])
            self.assertEqual(subject["run_manifest_path"], run["manifest_path"])
            self.assertEqual(subject["run_contract_path"], run["contract_path"])
            self.assertEqual(subject["workflow_asset_sha256"], run["workflow_asset_sha256"])

            spoofed = reviewed.parent / "spoofed_attempt.job.json"
            spoofed.write_text(json.dumps({**job_payload, "review_job_id": "spoofed_attempt", "attempt_id": "attempt_999"}) + "\n", encoding="utf-8")
            with mock.patch.object(supervisor_agents, "invoke_operator_codex") as spoofed_operator:
                with self.assertRaises(SystemExit):
                    supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="spoofed_attempt", review_kind="stage_output", job_json=spoofed.relative_to(ROOT))
            spoofed_operator.assert_not_called()

            spoofed_asset = reviewed.parent / "spoofed_asset.job.json"
            spoofed_asset.write_text(json.dumps({**job_payload, "review_job_id": "spoofed_asset", "workflow_asset_sha256": "f" * 64}) + "\n", encoding="utf-8")
            with mock.patch.object(supervisor_agents, "invoke_operator_codex") as spoofed_operator:
                with self.assertRaises(SystemExit):
                    supervisor.invoke_operator(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="spoofed_asset", review_kind="stage_output", job_json=spoofed_asset.relative_to(ROOT))
            spoofed_operator.assert_not_called()

    def test_stage_review_job_is_derived_from_registered_outcome_and_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, _reviewed = self._session(temp)
            run = self._register_v2_run(session, temp)
            anomaly_path = temp / "monitoring_anomaly.json"
            anomaly_path.write_text(
                json.dumps({"review_bundle_allowed": False, "reviewable": False}) + "\n",
                encoding="utf-8",
            )
            earlier_outcome_path = temp / "stage_outcome_earlier.json"
            earlier_outcome_path.write_text(
                json.dumps({"review_bundle_allowed": True, "reviewable": True}) + "\n",
                encoding="utf-8",
            )
            outcome_path = temp / "stage_outcome_latest.json"
            outcome_path.write_text(
                json.dumps({"review_bundle_allowed": True, "reviewable": True}) + "\n",
                encoding="utf-8",
            )
            stale_checkpoint = temp / "stale_attempt" / "stage_checkpoint.json"
            stale_checkpoint.parent.mkdir()
            stale_checkpoint.write_text("{}\n", encoding="utf-8")
            stale_outcome_path = temp / "stage_outcome_stale_attempt.json"
            stale_outcome_path.write_text(
                json.dumps(
                    {
                        "review_bundle_allowed": True,
                        "reviewable": True,
                        "checkpoint_path": relpath(ROOT, stale_checkpoint),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            current, session_path = supervisor._load_session_and_path(
                ROOT,
                session["supervisor_session_id"],
            )
            for path in (
                anomaly_path,
                earlier_outcome_path,
                outcome_path,
                stale_outcome_path,
            ):
                current["stage_outcomes"].append(
                    {
                        "run_id": "registered_run",
                        "stage_id": "stage",
                        "artifact_path": relpath(ROOT, path),
                    }
                )
            supervisor._write_session(ROOT, session_path, current)

            job = supervisor._derived_stage_review_job(
                root=ROOT,
                session=current,
                review_cycle_id="derived_stage_cycle",
                run_dir=run["run_dir"],
                stage_id="stage",
            )

            self.assertEqual(job["run_id"], "registered_run")
            self.assertEqual(job["stage_id"], "stage")
            self.assertIn(run["artifact_path"], job["reviewed_artifacts"])
            checkpoint = json.loads((ROOT / run["checkpoint_path"]).read_text(encoding="utf-8"))
            self.assertIn(checkpoint["request_payload_path"], job["reviewed_artifacts"])
            self.assertIn(checkpoint["input_manifest_json_path"], job["reviewed_artifacts"])
            self.assertIn(checkpoint["input_manifest_markdown_path"], job["reviewed_artifacts"])
            self.assertIn(relpath(ROOT, outcome_path), job["reviewed_artifacts"])
            self.assertNotIn(relpath(ROOT, anomaly_path), job["reviewed_artifacts"])
            self.assertNotIn(relpath(ROOT, earlier_outcome_path), job["reviewed_artifacts"])
            self.assertNotIn(relpath(ROOT, stale_outcome_path), job["reviewed_artifacts"])

    def test_stage_review_with_only_anomaly_reclassifies_before_invoking_agents(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, _reviewed = self._session(temp)
            run = self._register_v2_run(session, temp)
            anomaly_path = temp / "monitoring_anomaly.json"
            anomaly_path.write_text(
                json.dumps({"review_bundle_allowed": False, "reviewable": False}) + "\n",
                encoding="utf-8",
            )
            current, session_path = supervisor._load_session_and_path(
                ROOT,
                session["supervisor_session_id"],
            )
            current["stage_outcomes"].append(
                {
                    "run_id": "registered_run",
                    "stage_id": "stage",
                    "artifact_path": relpath(ROOT, anomaly_path),
                }
            )
            supervisor._write_session(ROOT, session_path, current)

            with mock.patch.object(supervisor, "classify_stage", return_value={}) as classify, mock.patch.object(
                supervisor,
                "run_review_cycle",
            ) as reviewers:
                with self.assertRaisesRegex(SystemExit, "reviewable stage outcome"):
                    supervisor.run_stage_review_cycle(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        review_cycle_id="anomaly_only_cycle",
                        run_dir=run["run_dir"],
                        stage_id="stage",
                    )
            classify.assert_called_once()
            reviewers.assert_not_called()

    def test_stale_scaffold_cannot_be_accepted(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, reviewed = self._session(temp)
            result = self._run_cycle(session, reviewed)
            replacement = temp / "replacement"
            replacement.mkdir()
            (replacement / "new.md").write_text("new\n", encoding="utf-8")
            supervisor.stage_scaffold(root=ROOT, session_ref=session["supervisor_session_id"], scaffold_path=replacement.relative_to(ROOT))
            acceptance = supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", consolidated_review=result["consolidation"], accepted_recommendation_ids=[])
            self.assertEqual(acceptance["approval_decision"], "do_not_approve")
            self.assertIn("stale_scaffold_review_subject", {item["issue_id"] for item in acceptance["blocking_issues"]})

    def test_failed_transport_quorum_cannot_be_resolved_away(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            result = self._run_cycle(session, reviewed, codex_status="missing_cli", codex_exit=127)
            acceptance = supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", consolidated_review=result["consolidation"], accepted_recommendation_ids=[])
            self.assertEqual(acceptance["approval_decision"], "do_not_approve")
            self.assertIn("required_review_quorum_not_satisfied", {item["issue_id"] for item in acceptance["blocking_issues"]})

    def test_hash_bound_blocker_resolution_allows_operator_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            reviewed_rel = relpath(ROOT, reviewed)
            blocker = {"issue_id": "needs_fix", "severity": "blocking", "description": "Fix required.", "evidence": ["evidence"], "affected_artifacts": [reviewed_rel]}
            result = self._run_cycle(session, reviewed, codex_blockers=[blocker])
            consolidated = json.loads((ROOT / result["consolidation"]).read_text(encoding="utf-8"))
            blocker_id = next(item["issue_id"] for item in consolidated["blocking_issues"] if item["issue_id"].endswith("needs_fix"))
            with self.assertRaises(SystemExit):
                supervisor.record_blocker_resolution(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_bound",
                    blocker_id=blocker_id,
                    resolution="resolved",
                    evidence=["No applied change was recorded."],
                    affected_artifacts=[{"path": reviewed_rel, "sha256": sha256_file(reviewed)}],
                    applied_changes=[],
                    validation_evidence=[{"method": "manual review", "result": "No change."}],
                    operator_rationale="This must not clear the blocker without an applied change.",
                )
            supervisor.record_blocker_resolution(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                blocker_id=blocker_id,
                resolution="accepted_risk",
                evidence=["Risk was explicitly evaluated."],
                affected_artifacts=[{"path": reviewed_rel, "sha256": sha256_file(reviewed)}],
                applied_changes=[],
                validation_evidence=[{"method": "manual evidence review", "result": "Known residual risk is bounded."}],
                operator_rationale="The evidence supports proceeding despite the bounded risk.",
                accepted_risk_rationale="The remaining risk is understood and explicitly accepted.",
            )
            acceptance = supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", consolidated_review=result["consolidation"], accepted_recommendation_ids=[])
            self.assertEqual(acceptance["approval_decision"], "approve")

    def test_acceptance_rejects_tampered_cycle_recorded_blocker_resolution(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            reviewed_rel = relpath(ROOT, reviewed)
            blocker = {"issue_id": "needs_fix", "severity": "blocking", "description": "Fix required.", "evidence": ["evidence"], "affected_artifacts": [reviewed_rel]}
            result = self._run_cycle(session, reviewed, codex_blockers=[blocker])
            consolidated = json.loads((ROOT / result["consolidation"]).read_text(encoding="utf-8"))
            blocker_id = next(item["issue_id"] for item in consolidated["blocking_issues"] if item["issue_id"].endswith("needs_fix"))
            supervisor.record_blocker_resolution(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                blocker_id=blocker_id,
                resolution="accepted_risk",
                evidence=["Risk was explicitly evaluated."],
                affected_artifacts=[{"path": reviewed_rel, "sha256": sha256_file(reviewed)}],
                applied_changes=[],
                validation_evidence=[{"method": "manual evidence review", "result": "Known residual risk is bounded."}],
                operator_rationale="Proceed with the explicitly documented bounded risk.",
                accepted_risk_rationale="The remaining risk is understood and explicitly accepted.",
            )
            updated = load_session(ROOT, session["supervisor_session_id"])
            resolution_path = ROOT / updated["review_cycles"][0]["blocker_resolutions"][0]["path"]
            payload = json.loads(resolution_path.read_text(encoding="utf-8"))
            payload["operator_rationale"] = "Tampered after recording."
            resolution_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", consolidated_review=result["consolidation"], accepted_recommendation_ids=[])

    def test_subject_artifact_tampering_blocks_acceptance(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            session, _scaffold, reviewed = self._session(Path(raw))
            result = self._run_cycle(session, reviewed)
            reviewed.write_text("tampered\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", consolidated_review=result["consolidation"], accepted_recommendation_ids=[])

    def test_supervisor_launch_registers_only_the_accepted_scaffold_run(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, reviewed = self._session(temp)
            result = self._run_cycle(session, reviewed)
            acceptance = supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", consolidated_review=result["consolidation"], accepted_recommendation_ids=[])
            self.assertEqual(acceptance["approval_decision"], "approve")
            updated = load_session(ROOT, session["supervisor_session_id"])
            workflow_path = ROOT / updated["scaffold_versions"][-1]["path"] / "reviewed.md"

            def fake_engine(**kwargs):
                run_dir = ROOT / kwargs["runtime"].run_dir
                run_dir.mkdir(parents=True)
                run_manifest = run_dir / "run_manifest.json"
                run_manifest.write_text(json.dumps({"schema_version": "responses_runner_v2.run_manifest.v2", "run_id": "run_registered", "workflow_id": "workflow_registered", "status": "waiting_for_review"}) + "\n", encoding="utf-8")
                return {"run_dir": relpath(ROOT, run_dir), "run_manifest_path": relpath(ROOT, run_manifest), "status": "waiting_for_review", "stage_id": "stage"}

            loaded_workflow = type("LoadedWorkflow", (), {"workflow_id": "workflow_registered"})()
            with mock.patch.object(supervisor, "load_workflow_definition", return_value=loaded_workflow), mock.patch.object(supervisor, "run_workflow", side_effect=fake_engine):
                launched = supervisor.launch_scaffold(root=ROOT, session_ref=session["supervisor_session_id"], workflow_file=workflow_path.relative_to(ROOT), client=object())
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(launched["run_dir"], updated["launch_reservations"][0]["run_dir"])
            self.assertEqual(updated["runs"][0]["run_id"], "run_registered")

    def test_concurrent_launch_reservation_submits_exactly_once_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            brief = temp / "brief.md"
            brief.write_text("# Accepted brief\n", encoding="utf-8")
            scaffold = temp / "scaffold"
            shutil.copytree(
                ROOT / "automation/examples/responses_runner_v2_synthetic",
                scaffold,
            )
            session = supervisor.create_session(
                root=ROOT,
                clarified_task_brief=brief.relative_to(ROOT),
                summary="concurrent launch",
            )
            supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=scaffold.relative_to(ROOT),
            )
            cycle = "cycle_concurrent_launch"
            review = self._run_cycle(session, scaffold / "README.md", cycle=cycle)
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle,
                consolidated_review=review["consolidation"],
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            accepted = load_session(ROOT, session["supervisor_session_id"])
            workflow_path = (
                Path(accepted["scaffold_versions"][-1]["path"])
                / "workflows/one_pass.workflow.json"
            )
            client = _BlockingLaunchClient()

            with mock.patch.object(
                supervisor,
                "_register_reserved_launch",
                side_effect=SystemExit("synthetic crash after engine result"),
            ), ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    supervisor.launch_scaffold,
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    workflow_file=workflow_path,
                    client=client,
                )
                self.assertTrue(client.create_started.wait(timeout=10))
                second = executor.submit(
                    supervisor.launch_scaffold,
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    workflow_file=workflow_path,
                    client=client,
                )
                with self.assertRaisesRegex(SystemExit, "already owned by another process"):
                    second.result(timeout=10)
                client.release_create.set()
                with self.assertRaisesRegex(SystemExit, "synthetic crash after engine result"):
                    first.result(timeout=10)

            self.assertEqual(len(client.create_requests), 1)
            interrupted = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(interrupted["launch_reservations"][0]["status"], "reserved")
            self.assertEqual(interrupted["runs"], [])
            recovered = supervisor.launch_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                workflow_file=workflow_path,
                client=client,
            )
            self.assertEqual(
                recovered["run_dir"], interrupted["launch_reservations"][0]["run_dir"]
            )
            recovered_session = load_session(ROOT, session["supervisor_session_id"])
            idempotent = supervisor.launch_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                workflow_file=workflow_path,
                client=client,
            )
            self.assertEqual(idempotent["run_dir"], recovered["run_dir"])
            self.assertEqual(len(client.create_requests), 1)
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(updated["revision"], recovered_session["revision"])
            self.assertEqual(len(updated["launch_reservations"]), 1)
            self.assertEqual(updated["launch_reservations"][0]["status"], "registered")
            self.assertEqual(len(updated["runs"]), 1)

    def test_pristine_reserved_run_reenters_after_pre_attempt_crash_once(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            brief = temp / "brief.md"
            brief.write_text("# Accepted brief\n", encoding="utf-8")
            scaffold = temp / "scaffold"
            shutil.copytree(
                ROOT / "automation/examples/responses_runner_v2_synthetic",
                scaffold,
            )
            session = supervisor.create_session(
                root=ROOT,
                clarified_task_brief=brief.relative_to(ROOT),
                summary="pre-attempt launch recovery",
            )
            supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=scaffold.relative_to(ROOT),
            )
            cycle = "cycle_pre_attempt_launch"
            review = self._run_cycle(session, scaffold / "README.md", cycle=cycle)
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle,
                consolidated_review=review["consolidation"],
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            accepted = load_session(ROOT, session["supervisor_session_id"])
            workflow_path = (
                Path(accepted["scaffold_versions"][-1]["path"])
                / "workflows/one_pass.workflow.json"
            )
            client = _BlockingLaunchClient()

            with mock.patch.object(
                workflow_module.artifacts,
                "allocate_stage_attempt",
                side_effect=SystemExit("synthetic crash before attempt allocation"),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "synthetic crash before attempt allocation",
                ):
                    supervisor.launch_scaffold(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        workflow_file=workflow_path,
                        client=client,
                    )

            interrupted = load_session(ROOT, session["supervisor_session_id"])
            reservation = interrupted["launch_reservations"][0]
            run_dir = ROOT / reservation["run_dir"]
            manifest = workflow_module.artifacts.load_run_manifest(ROOT, run_dir)
            self.assertEqual(manifest["status"], "created")
            self.assertTrue(all(not stage.get("attempts") for stage in manifest["stages"]))
            self.assertEqual(client.create_requests, [])
            self.assertEqual(interrupted["runs"], [])

            client.release_create.set()
            recovered = supervisor.launch_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                workflow_file=workflow_path,
                client=client,
            )
            self.assertEqual(recovered["run_dir"], reservation["run_dir"])
            self.assertEqual(len(client.create_requests), 1)
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(updated["launch_reservations"][0]["status"], "registered")
            self.assertEqual(updated["launch_reservations"][0]["recovery_count"], 1)
            self.assertEqual(len(updated["runs"]), 1)

    def test_reserved_run_finalizes_partial_initialization_before_one_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            brief = temp / "brief.md"
            brief.write_text("# Accepted brief\n", encoding="utf-8")
            scaffold = temp / "scaffold"
            shutil.copytree(
                ROOT / "automation/examples/responses_runner_v2_synthetic",
                scaffold,
            )
            session = supervisor.create_session(
                root=ROOT,
                clarified_task_brief=brief.relative_to(ROOT),
                summary="pre-manifest launch recovery",
            )
            supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=scaffold.relative_to(ROOT),
            )
            cycle = "cycle_pre_manifest_launch"
            review = self._run_cycle(session, scaffold / "README.md", cycle=cycle)
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle,
                consolidated_review=review["consolidation"],
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            accepted = load_session(ROOT, session["supervisor_session_id"])
            workflow_path = (
                Path(accepted["scaffold_versions"][-1]["path"])
                / "workflows/one_pass.workflow.json"
            )
            client = _BlockingLaunchClient()

            with mock.patch.object(
                workflow_module.artifacts,
                "write_run_manifest",
                side_effect=SystemExit("synthetic crash after contract before manifest"),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "synthetic crash after contract before manifest",
                ):
                    supervisor.launch_scaffold(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        workflow_file=workflow_path,
                        client=client,
                    )

            interrupted = load_session(ROOT, session["supervisor_session_id"])
            reservation = interrupted["launch_reservations"][0]
            run_dir = ROOT / reservation["run_dir"]
            self.assertFalse((run_dir / "run_manifest.json").exists())
            self.assertTrue((run_dir / "run_initialization.intent.json").exists())
            self.assertTrue((run_dir / "run_contract.json").exists())
            self.assertTrue((run_dir / "stages" / "01_draft_summary").is_dir())
            self.assertEqual(client.create_requests, [])
            self.assertEqual(interrupted["runs"], [])

            client.release_create.set()
            recovered = supervisor.launch_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                workflow_file=workflow_path,
                client=client,
            )
            self.assertEqual(recovered["run_dir"], reservation["run_dir"])
            self.assertEqual(len(client.create_requests), 1)
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(updated["launch_reservations"][0]["status"], "registered")
            self.assertEqual(updated["launch_reservations"][0]["recovery_count"], 1)
            self.assertEqual(len(updated["runs"]), 1)

    def test_archived_rerun_recovers_engine_evidence_without_duplicate_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            brief = temp / "brief.md"
            brief.write_text("# Accepted brief\n", encoding="utf-8")
            scaffold = temp / "scaffold"
            shutil.copytree(
                ROOT / "automation/examples/responses_runner_v2_synthetic",
                scaffold,
            )
            session = supervisor.create_session(
                root=ROOT,
                clarified_task_brief=brief.relative_to(ROOT),
                summary="archive rerun recovery",
            )
            supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=scaffold.relative_to(ROOT),
            )
            cycle = "cycle_archive_rerun"
            review = self._run_cycle(session, scaffold / "README.md", cycle=cycle)
            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=cycle,
                consolidated_review=review["consolidation"],
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            accepted = load_session(ROOT, session["supervisor_session_id"])
            workflow_path = (
                Path(accepted["scaffold_versions"][-1]["path"])
                / "workflows/one_pass.workflow.json"
            )
            first = supervisor.launch_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                workflow_file=workflow_path,
                client=_FailedLaunchClient(),
            )
            self.assertEqual(first["status"], "failed")
            failed_manifest = workflow_module.artifacts.load_run_manifest(
                ROOT,
                ROOT / first["run_dir"],
            )
            self.assertEqual(
                failed_manifest["stages"][0]["status"],
                "failed_no_artifact",
            )
            archive = supervisor.archive_attempt(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                run_dir=first["run_dir"],
                stage_id="draft_summary",
                reason="failed_no_artifact",
            )
            rerun_client = _BlockingLaunchClient()
            rerun_client.release_create.set()

            original_allocate = workflow_module.artifacts.allocate_stage_attempt

            def allocate_then_crash(*args, **kwargs):
                original_allocate(*args, **kwargs)
                raise SystemExit("synthetic crash after rerun attempt allocation")

            with mock.patch.object(
                workflow_module.artifacts,
                "allocate_stage_attempt",
                side_effect=allocate_then_crash,
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "synthetic crash after rerun attempt allocation",
                ):
                    supervisor.rerun_archived_stage(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        archive_manifest=archive["archive_manifest_path"],
                        workflow_file=workflow_path,
                        client=rerun_client,
                    )
            self.assertEqual(rerun_client.create_requests, [])

            with mock.patch.object(
                supervisor,
                "_register_reserved_rerun",
                side_effect=SystemExit("synthetic crash after rerun engine result"),
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "synthetic crash after rerun engine result",
                ):
                    supervisor.rerun_archived_stage(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        archive_manifest=archive["archive_manifest_path"],
                        workflow_file=workflow_path,
                        client=rerun_client,
                    )

            interrupted = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(len(interrupted["rerun_reservations"]), 1)
            self.assertEqual(interrupted["rerun_reservations"][0]["status"], "reserved")
            self.assertIn(
                "attempt_002",
                interrupted["rerun_reservations"][0]["baseline_attempt_ids"],
            )
            self.assertEqual(len(rerun_client.create_requests), 1)

            recovered = supervisor.rerun_archived_stage(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                archive_manifest=archive["archive_manifest_path"],
                workflow_file=workflow_path,
                client=rerun_client,
            )
            self.assertEqual(recovered["run_dir"], first["run_dir"])
            recovered_session = load_session(ROOT, session["supervisor_session_id"])
            idempotent = supervisor.rerun_archived_stage(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                archive_manifest=archive["archive_manifest_path"],
                workflow_file=workflow_path,
                client=rerun_client,
            )
            self.assertEqual(idempotent, recovered)
            self.assertEqual(len(rerun_client.create_requests), 1)
            updated = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(updated["revision"], recovered_session["revision"])
            self.assertEqual(updated["rerun_reservations"][0]["status"], "registered")
            self.assertEqual(len(updated["runs"]), 1)

    def test_revision_directive_requires_fresh_review_and_traces_accept_reject(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, scaffold, reviewed = self._session(temp)
            revision_target = scaffold / "revision_target.md"
            revision_target.write_text("stable staged bytes\n", encoding="utf-8")
            latest_scaffold = supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=scaffold.relative_to(ROOT),
            )
            recommendations = [
                {
                    "recommendation_id": "apply_change",
                    "source_agent": "codex_review_agent",
                    "severity": "medium",
                    "recommendation": "Apply the grounded content change.",
                    "evidence": [{"artifact_path": relpath(ROOT, reviewed), "quote_or_summary": "Current content needs revision."}],
                    "affected_artifacts": [relpath(ROOT, reviewed)],
                    "exact_change_needed": "Replace the placeholder content.",
                },
                {
                    "recommendation_id": "expand_scope",
                    "source_agent": "codex_review_agent",
                    "severity": "low",
                    "recommendation": "Add unrelated scope.",
                    "evidence": [{"artifact_path": relpath(ROOT, reviewed), "quote_or_summary": "Optional expansion was proposed."}],
                    "affected_artifacts": [relpath(ROOT, reviewed)],
                    "exact_change_needed": "Add unrelated material.",
                },
            ]
            first = self._run_cycle(session, reviewed, codex_recommendations=recommendations)
            latest_scaffold = supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=scaffold.relative_to(ROOT),
            )
            consolidated = json.loads((ROOT / first["consolidation"]).read_text(encoding="utf-8"))
            by_text = {item["recommendation"]: item["recommendation_id"] for item in consolidated["recommendations"]}
            accepted_id = by_text["Apply the grounded content change."]
            rejected_id = by_text["Add unrelated scope."]
            # Reproduce the edge where a revision changes an unreviewed target
            # but restores the exact bytes of the already-staged scaffold.
            revision_target.write_text("drifted before revision\n", encoding="utf-8")
            directive = supervisor.create_revision_directive(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="cycle_bound",
                accepted_recommendation_ids=[accepted_id],
                rejected_recommendations={rejected_id: "Unrelated to the authorized task."},
                revised_artifacts=[revision_target.relative_to(ROOT)],
                revision_scaffold_path=scaffold.relative_to(ROOT),
            )
            self.assertEqual(directive["accepted_recommendations"][0]["recommendation_id"], accepted_id)
            self.assertEqual(directive["rejected_recommendations"][0]["recommendation_id"], rejected_id)

            recovery_calls = 0

            def operator(**kwargs):
                nonlocal recovery_calls
                cycle = kwargs["review_cycle_id"]
                kind = kwargs["review_kind"]
                if kind == "recovery":
                    recovery_calls += 1
                    if recovery_calls > 1:
                        raise AssertionError("reserved operator revision must not be reinvoked")
                    revision_target.write_text("stable staged bytes\n", encoding="utf-8")
                    staged_root = ROOT / latest_scaffold["path"]
                    source_files = {
                        path.relative_to(scaffold).as_posix(): sha256_file(path)
                        for path in supervisor_artifacts._iter_files(scaffold)
                    }
                    staged_files = {
                        path.relative_to(staged_root).as_posix(): sha256_file(path)
                        for path in supervisor_artifacts._iter_files(staged_root)
                    }
                    self.assertEqual(
                        source_files,
                        staged_files,
                    )
                    traced = [
                        {
                            **directive["accepted_recommendations"][0],
                            "severity": "medium",
                            "exact_change_needed": "Replace the placeholder content.",
                            "operator_decision": "accepted",
                            "decision_rationale": "Grounded and in scope.",
                            "changes_applied": [{"path": relpath(ROOT, revision_target), "summary": "Restored the reviewed scaffold state.", "evidence": [{"source": "revision", "quote_or_summary": "Updated content."}]}],
                            "validation_evidence": [{"source": "test", "quote_or_summary": "Validated revised content."}],
                        },
                        {
                            **{key: value for key, value in directive["rejected_recommendations"][0].items() if key != "rejection_rationale"},
                            "severity": "low",
                            "rationale_for_no_change": "The proposal is outside the authorized scope.",
                            "operator_decision": "rejected",
                            "decision_rationale": "Out of scope.",
                            "rejected_reason": "Unrelated to the authorized task.",
                        },
                    ]
                    output_dir = Path(kwargs["output_dir"])
                    result = _decision(root=ROOT, output_dir=output_dir, role="operator_codex", cycle=cycle, review_kind=kind, recommendations=traced)
                    return _with_recovery_evidence(result, output_dir=output_dir)
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle=cycle, review_kind=kind)

            def codex(**kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="codex_review_agent", cycle=kwargs["review_cycle_id"], review_kind=kwargs["review_kind"])

            def claude(**kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="claude_review_agent", cycle=kwargs["review_cycle_id"], review_kind=kwargs["review_kind"])

            original_write_session = supervisor._write_session
            write_count = 0

            def crash_before_revision_completion(root, session_path, payload):
                nonlocal write_count
                write_count += 1
                if write_count == 2:
                    raise SystemExit("synthetic crash before operator revision completion CAS")
                return original_write_session(root, session_path, payload)

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=operator), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=codex), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=claude):
                with mock.patch.object(supervisor, "_write_session", side_effect=crash_before_revision_completion):
                    with self.assertRaisesRegex(SystemExit, "synthetic crash"):
                        supervisor.run_revision_and_review(root=ROOT, session_ref=session["supervisor_session_id"], source_review_cycle_id="cycle_bound", new_review_cycle_id="cycle_revised")
                interrupted = load_session(ROOT, session["supervisor_session_id"])
                interrupted_cycle = next(item for item in interrupted["review_cycles"] if item["review_cycle_id"] == "cycle_bound")
                self.assertEqual(interrupted_cycle["invocation_reservations"]["operator_revision"]["status"], "reserved")
                revised = supervisor.run_revision_and_review(root=ROOT, session_ref=session["supervisor_session_id"], source_review_cycle_id="cycle_bound", new_review_cycle_id="cycle_revised")
            self.assertEqual(recovery_calls, 1)
            self.assertEqual(revised["new_review_cycle_id"], "cycle_revised")
            updated = load_session(ROOT, session["supervisor_session_id"])
            source_cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "cycle_bound")
            fresh_cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "cycle_revised")
            self.assertEqual(len(updated["scaffold_versions"]), 3)
            self.assertEqual(
                source_cycle["revision"]["staged_scaffold_version_id"],
                latest_scaffold["version_id"],
            )
            self.assertEqual(source_cycle["invocation_reservations"]["operator_revision"]["recovery_count"], 1)
            self.assertEqual(source_cycle["acceptance_status"], "superseded")
            self.assertEqual(fresh_cycle["acceptance_status"], "pending")
            self.assertNotEqual(source_cycle["subject_id"], fresh_cycle["subject_id"])
            result_payload = json.loads((ROOT / revised["revision_result"]).read_text(encoding="utf-8"))
            self.assertEqual(result_payload["accepted_recommendation_ids"], [accepted_id])
            self.assertEqual(result_payload["rejected_recommendation_ids"], [rejected_id])
            with self.assertRaises(SystemExit):
                supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_bound", accepted_recommendation_ids=[])
            acceptance = supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="cycle_revised", accepted_recommendation_ids=[])
            self.assertEqual(acceptance["approval_decision"], "approve")
            updated = load_session(ROOT, session["supervisor_session_id"])
            accepted_cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "cycle_revised")
            binding = json.loads((ROOT / accepted_cycle["acceptance_binding"]["path"]).read_text(encoding="utf-8"))
            self.assertEqual(binding["subject_id"], fresh_cycle["subject_id"])

    def test_revision_continuation_recovers_after_every_committed_phase(self) -> None:
        phases = ("operator_completed", "result_ready", "superseded", "fresh_cycle_lineage", "fresh_review")
        for phase in phases:
            with self.subTest(phase=phase), tempfile.TemporaryDirectory(dir=ROOT) as raw:
                session, scaffold, reviewed = self._session(Path(raw))
                recommendation = {
                    "recommendation_id": "apply_revision",
                    "source_agent": "codex_review_agent",
                    "severity": "medium",
                    "recommendation": "Apply the reviewed revision.",
                    "evidence": [{"artifact_path": relpath(ROOT, reviewed), "quote_or_summary": "The content needs revision."}],
                    "affected_artifacts": [relpath(ROOT, reviewed)],
                    "exact_change_needed": "Replace the current content.",
                }
                initial = self._run_cycle(session, reviewed, codex_recommendations=[recommendation])
                source_consolidation = ROOT / initial["consolidation"]
                source_consolidation_sha256 = sha256_file(source_consolidation)
                consolidated = json.loads((ROOT / initial["consolidation"]).read_text(encoding="utf-8"))
                recommendation_id = consolidated["recommendations"][0]["recommendation_id"]
                directive = supervisor.create_revision_directive(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="cycle_bound",
                    accepted_recommendation_ids=[recommendation_id],
                    rejected_recommendations={},
                    revised_artifacts=[reviewed.relative_to(ROOT)],
                    revision_scaffold_path=scaffold.relative_to(ROOT),
                )
                fresh_cycle_id = f"cycle_fresh_{phase}"
                calls = {"recovery": 0, "fresh_operator": 0, "codex": 0, "claude": 0}

                def operator(**kwargs):
                    if kwargs["review_kind"] == "recovery":
                        calls["recovery"] += 1
                        if calls["recovery"] > 1:
                            raise AssertionError("completed operator revision was reinvoked")
                        reviewed.write_text(f"revised after {phase}\n", encoding="utf-8")
                        traced = [{
                            **directive["accepted_recommendations"][0],
                            "operator_decision": "accepted",
                            "decision_rationale": "Grounded and in scope.",
                            "changes_applied": [{"path": relpath(ROOT, reviewed), "summary": "Applied revision.", "evidence": [{"source": "revision", "quote_or_summary": "Updated content."}]}],
                            "validation_evidence": [{"source": "test", "quote_or_summary": "Validated revised content."}],
                        }]
                        return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle="cycle_bound", review_kind="recovery", recommendations=traced)
                    calls["fresh_operator"] += 1
                    return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle=fresh_cycle_id, review_kind="scaffold")

                def reviewer(role: str, **kwargs):
                    key = "codex" if role == "codex_review_agent" else "claude"
                    calls[key] += 1
                    return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=fresh_cycle_id, review_kind="scaffold")

                original_write_session = supervisor._write_session
                crashed = False

                def phase_reached(payload):
                    cycles = {item["review_cycle_id"]: item for item in payload.get("review_cycles", [])}
                    source = cycles.get("cycle_bound", {})
                    revision = source.get("revision") or {}
                    fresh = cycles.get(fresh_cycle_id)
                    if phase == "operator_completed":
                        return revision.get("status") == "operator_completed"
                    if phase == "result_ready":
                        return revision.get("status") == "result_ready"
                    if phase == "superseded":
                        return revision.get("status") == "superseded_by_fresh_review" and source.get("acceptance_status") == "superseded" and fresh is None
                    if phase == "fresh_cycle_lineage":
                        return isinstance(fresh, dict) and isinstance(fresh.get("revision_lineage"), dict) and not fresh.get("operator_provisional_record")
                    return isinstance(fresh, dict) and isinstance(fresh.get("consolidation"), str)

                def crash_after_phase(root, session_path, payload):
                    nonlocal crashed
                    result = original_write_session(root, session_path, payload)
                    if not crashed and phase_reached(payload):
                        crashed = True
                        raise SystemExit(f"synthetic crash after {phase}")
                    return result

                agent_patches = (
                    mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=operator),
                    mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: reviewer("codex_review_agent", **kwargs)),
                    mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: reviewer("claude_review_agent", **kwargs)),
                )
                with agent_patches[0], agent_patches[1], agent_patches[2]:
                    if phase == "operator_completed":
                        with self.assertRaisesRegex(SystemExit, "must differ"):
                            supervisor.run_revision_and_review(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                source_review_cycle_id="cycle_bound",
                                new_review_cycle_id="cycle_bound",
                            )
                        self.assertEqual(calls, {"recovery": 0, "fresh_operator": 0, "codex": 0, "claude": 0})
                    with mock.patch.object(supervisor, "_write_session", side_effect=crash_after_phase):
                        with self.assertRaisesRegex(SystemExit, f"synthetic crash after {phase}"):
                            supervisor.run_revision_and_review(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                source_review_cycle_id="cycle_bound",
                                new_review_cycle_id=fresh_cycle_id,
                            )
                    if phase == "operator_completed":
                        with self.assertRaisesRegex(SystemExit, "reserved by active revision"):
                            supervisor.create_review_cycle(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                review_cycle_id=fresh_cycle_id,
                                review_kind="scaffold",
                            )
                        with self.assertRaisesRegex(SystemExit, "reserved by active revision"):
                            supervisor.invoke_operator(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                review_cycle_id=fresh_cycle_id,
                                review_kind="scaffold",
                                job_json=reviewed.relative_to(ROOT),
                            )
                        with self.assertRaisesRegex(SystemExit, "must differ"):
                            supervisor.run_revision_and_review(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                source_review_cycle_id="cycle_bound",
                                new_review_cycle_id="cycle_bound",
                            )
                        with self.assertRaisesRegex(SystemExit, "does not match its invocation reservation"):
                            supervisor.run_revision_and_review(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                source_review_cycle_id="cycle_bound",
                                new_review_cycle_id="cycle_wrong_retry",
                            )
                        injected, injected_session_path = supervisor._load_session_and_path(ROOT, session["supervisor_session_id"])
                        unrelated = supervisor._new_review_cycle_record(
                            root=ROOT,
                            session_path=injected_session_path,
                            review_cycle_id=fresh_cycle_id,
                            review_kind="scaffold",
                        )
                        unrelated["operator_provisional_record"] = injected["review_cycles"][0]["operator_provisional_record"]
                        unrelated["consolidation"] = injected["review_cycles"][0]["consolidation"]
                        injected["review_cycles"].append(unrelated)
                        supervisor._write_session(ROOT, injected_session_path, injected)
                        with self.assertRaisesRegex(SystemExit, "lineage mismatch"):
                            supervisor.run_revision_and_review(
                                root=ROOT,
                                session_ref=session["supervisor_session_id"],
                                source_review_cycle_id="cycle_bound",
                                new_review_cycle_id=fresh_cycle_id,
                            )
                        cleaned, cleaned_session_path = supervisor._load_session_and_path(ROOT, session["supervisor_session_id"])
                        cleaned["review_cycles"] = [item for item in cleaned["review_cycles"] if item["review_cycle_id"] != fresh_cycle_id]
                        supervisor._write_session(ROOT, cleaned_session_path, cleaned)
                        rejected = load_session(ROOT, session["supervisor_session_id"])
                        self.assertEqual([item["review_cycle_id"] for item in rejected["review_cycles"]], ["cycle_bound"])
                        self.assertEqual(sha256_file(source_consolidation), source_consolidation_sha256)
                        self.assertEqual(calls, {"recovery": 1, "fresh_operator": 0, "codex": 0, "claude": 0})
                    recovered = supervisor.run_revision_and_review(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        source_review_cycle_id="cycle_bound",
                        new_review_cycle_id=fresh_cycle_id,
                    )
                    repeated = supervisor.run_revision_and_review(
                        root=ROOT,
                        session_ref=session["supervisor_session_id"],
                        source_review_cycle_id="cycle_bound",
                        new_review_cycle_id=fresh_cycle_id,
                    )
                self.assertTrue(crashed)
                self.assertEqual(recovered["revision_result"], repeated["revision_result"])
                self.assertEqual(calls, {"recovery": 1, "fresh_operator": 1, "codex": 1, "claude": 1})
                completed = load_session(ROOT, session["supervisor_session_id"])
                source = next(item for item in completed["review_cycles"] if item["review_cycle_id"] == "cycle_bound")
                fresh = next(item for item in completed["review_cycles"] if item["review_cycle_id"] == fresh_cycle_id)
                self.assertEqual(source["revision"]["status"], "superseded_by_fresh_review")
                self.assertIsInstance(fresh.get("revision_lineage"), dict)
                self.assertIsInstance(fresh.get("consolidation"), str)

    def test_final_packet_revision_rebinds_changed_draft_for_fresh_review_and_finalization(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, _reviewed = self._session(temp)
            run = self._register_v2_run(session, temp)
            artifact_sha = sha256_file(ROOT / run["artifact_path"])
            draft_body = {
                "schema_version": "responses_runner_v2.final_delivery_bundle.v1",
                "delivery_id": "revised_final_delivery",
                "created_at": runner_now().isoformat(),
                "assurance_profile": "critical",
                "subject": {
                    "workflow_id": "registered_workflow",
                    "run_id": "registered_run",
                    "terminal_stage_id": "stage",
                    "terminal_attempt_id": "attempt_001",
                    "terminal_artifact_sha256": artifact_sha,
                },
                "summary": "Initial final delivery draft.",
                "deliverables": [{"deliverable_id": "terminal_artifact", "kind": "document", "path": run["artifact_path"], "sha256": artifact_sha, "description": "Reviewed terminal artifact."}],
                "evidence": [{"evidence_id": "terminal_evidence", "citation_type": "stage_artifact", "locator": run["artifact_path"], "sha256": artifact_sha, "claim": "The terminal artifact is present."}],
                "validation_evidence": [{"check_id": "content_check", "method": "hash verification", "status": "passed", "evidence": "Artifact hash matches."}],
                "open_items": [],
                "residual_risks": [],
            }
            draft = temp / "revised_final.draft.json"
            draft.write_text(json.dumps(draft_body, indent=2) + "\n", encoding="utf-8")
            original_draft_sha256 = sha256_file(draft)
            job = temp / "revised_final.job.json"
            job.write_text(
                json.dumps(
                    {
                        "review_job_id": "final_revision_source",
                        "reviewed_artifacts": [run["artifact_path"]],
                        "run_id": "registered_run",
                        "stage_id": "stage",
                        "final_packet_draft": relpath(ROOT, draft),
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            recommendation = {
                "recommendation_id": "revise_final_summary",
                "source_agent": "codex_review_agent",
                "severity": "medium",
                "recommendation": "Revise the final delivery summary.",
                "evidence": [{"artifact_path": relpath(ROOT, draft), "quote_or_summary": "The initial summary requires correction."}],
                "affected_artifacts": [relpath(ROOT, draft)],
                "exact_change_needed": "Replace the initial summary with the reviewed revision.",
            }

            def initial_decision(role: str, **kwargs):
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role=role,
                    cycle="final_revision_source",
                    review_kind="final_packet",
                    recommendations=[recommendation] if role == "codex_review_agent" else [],
                    workflow_id="registered_workflow",
                    run_id="registered_run",
                    stage_id="stage",
                )

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: initial_decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: initial_decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: initial_decision("claude_review_agent", **kwargs)):
                supervisor.run_review_cycle(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id="final_revision_source",
                    review_kind="final_packet",
                    job_json=job.relative_to(ROOT),
                )
            current = load_session(ROOT, session["supervisor_session_id"])
            source_cycle = next(item for item in current["review_cycles"] if item["review_cycle_id"] == "final_revision_source")
            source_subject = json.loads((ROOT / source_cycle["subject_path"]).read_text(encoding="utf-8"))
            consolidated = json.loads((ROOT / source_cycle["consolidation"]).read_text(encoding="utf-8"))
            accepted_id = consolidated["recommendations"][0]["recommendation_id"]
            directive = supervisor.create_revision_directive(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="final_revision_source",
                accepted_recommendation_ids=[accepted_id],
                rejected_recommendations={},
                revised_artifacts=[draft.relative_to(ROOT)],
            )

            revised_draft_body = {**draft_body, "summary": "Revised and freshly reviewed final delivery."}

            def revised_operator(**kwargs):
                if kwargs["review_kind"] == "recovery":
                    draft.write_text(json.dumps(revised_draft_body, indent=2) + "\n", encoding="utf-8")
                    traced = [{
                        **directive["accepted_recommendations"][0],
                        "operator_decision": "accepted",
                        "decision_rationale": "The correction is grounded and in scope.",
                        "changes_applied": [{"path": relpath(ROOT, draft), "summary": "Corrected final summary.", "evidence": [{"source": "revision", "quote_or_summary": "Updated summary."}]}],
                        "validation_evidence": [{"source": "test", "quote_or_summary": "Draft remains valid JSON."}],
                    }]
                    return _decision(
                        root=ROOT,
                        output_dir=Path(kwargs["output_dir"]),
                        role="operator_codex",
                        cycle="final_revision_source",
                        review_kind="recovery",
                        recommendations=traced,
                        workflow_id="registered_workflow",
                        run_id="registered_run",
                        stage_id="stage",
                    )
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role="operator_codex", cycle=kwargs["review_cycle_id"], review_kind="final_packet", workflow_id="registered_workflow", run_id="registered_run", stage_id="stage")

            def fresh_reviewer(role: str, **kwargs):
                return _decision(root=ROOT, output_dir=Path(kwargs["output_dir"]), role=role, cycle=kwargs["review_cycle_id"], review_kind="final_packet", workflow_id="registered_workflow", run_id="registered_run", stage_id="stage")

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=revised_operator), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: fresh_reviewer("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: fresh_reviewer("claude_review_agent", **kwargs)):
                revised = supervisor.run_revision_and_review(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    source_review_cycle_id="final_revision_source",
                    new_review_cycle_id="final_revision_fresh",
                )

            updated = load_session(ROOT, session["supervisor_session_id"])
            fresh_cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "final_revision_fresh")
            fresh_subject = json.loads((ROOT / fresh_cycle["subject_path"]).read_text(encoding="utf-8"))
            fresh_job = json.loads((ROOT / revised["revision_result"]).read_text(encoding="utf-8"))
            fresh_job = json.loads((ROOT / fresh_job["revised_review_job_path"]).read_text(encoding="utf-8"))
            self.assertEqual(fresh_job["final_packet_draft"], relpath(ROOT, draft))
            self.assertEqual(source_subject["final_packet_draft_sha256"], original_draft_sha256)
            self.assertEqual(fresh_subject["final_packet_draft_sha256"], sha256_file(draft))
            self.assertNotEqual(source_subject["final_packet_draft_sha256"], fresh_subject["final_packet_draft_sha256"])
            fresh_manifest = json.loads((ROOT / fresh_subject["reviewed_artifact_manifest_path"]).read_text(encoding="utf-8"))
            self.assertIn(run["artifact_path"], {item["path"] for item in fresh_manifest["artifacts"]})

            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id="final_revision_fresh",
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            final_payload = {**revised_draft_body, "reviews": [], "operator_acceptance": {}}
            binding = supervisor._require_reviewed_final_draft(ROOT, fresh_subject, final_payload)
            self.assertEqual(binding["sha256"], fresh_subject["final_packet_draft_sha256"])
            altered_payload = {**final_payload, "summary": "Changed after the fresh review."}
            with self.assertRaisesRegex(SystemExit, "differs from the exact reviewed"):
                supervisor._require_reviewed_final_draft(ROOT, fresh_subject, altered_payload)
            updated = load_session(ROOT, session["supervisor_session_id"])
            fresh_cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "final_revision_fresh")
            reviews = []
            for role in ("operator_codex", "codex_review_agent", "claude_review_agent"):
                path = fresh_cycle["operator_provisional_record"] if role == "operator_codex" else fresh_cycle["review_agent_outputs"][role]
                reviews.append({"role": role, "artifact_path": path, "artifact_sha256": sha256_file(ROOT / path), "decision": "approve"})
            reviews.append({"role": "consolidation", "artifact_path": fresh_cycle["consolidation"], "artifact_sha256": sha256_file(ROOT / fresh_cycle["consolidation"]), "decision": "advisory"})
            delivery_payload = {
                **revised_draft_body,
                "reviews": reviews,
                "operator_acceptance": {"decision_id": acceptance["decision_id"], "decision": "approve", "artifact_path": fresh_cycle["acceptance_record"], "artifact_sha256": sha256_file(ROOT / fresh_cycle["acceptance_record"])},
            }
            completed = supervisor.create_final_delivery_bundle(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                payload=delivery_payload,
                output=None,
            )
            self.assertEqual(completed["summary"], revised_draft_body["summary"])

    def test_final_delivery_requires_reviewed_deliverables_and_passing_validation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            session, _scaffold, _reviewed = self._session(temp)
            run = self._register_v2_run(session, temp)
            artifact_sha = sha256_file(ROOT / run["artifact_path"])
            draft_body = {
                "schema_version": "responses_runner_v2.final_delivery_bundle.v1",
                "delivery_id": "final_delivery",
                "created_at": runner_now().isoformat(),
                "assurance_profile": "critical",
                "subject": {
                    "workflow_id": "registered_workflow",
                    "run_id": "registered_run",
                    "terminal_stage_id": "stage",
                    "terminal_attempt_id": "attempt_001",
                    "terminal_artifact_sha256": artifact_sha,
                },
                "summary": "Reviewed final delivery.",
                "deliverables": [{"deliverable_id": "terminal_artifact", "kind": "document", "path": run["artifact_path"], "sha256": artifact_sha, "description": "Reviewed terminal artifact."}],
                "evidence": [{"evidence_id": "terminal_evidence", "citation_type": "stage_artifact", "locator": run["artifact_path"], "sha256": artifact_sha, "claim": "The terminal artifact is present."}],
                "validation_evidence": [{"check_id": "content_check", "method": "hash verification", "status": "passed", "evidence": "Artifact hash matches."}],
                "open_items": [],
                "residual_risks": [],
            }
            draft = temp / "final.draft.json"
            draft.write_text(json.dumps(draft_body, indent=2) + "\n", encoding="utf-8")
            job = temp / "final.job.json"
            job.write_text(
                json.dumps(
                    {
                        "review_job_id": "final_cycle",
                        "reviewed_artifacts": [run["artifact_path"]],
                        "run_id": "registered_run",
                        "stage_id": "stage",
                        "final_packet_draft": relpath(ROOT, draft),
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            def decision(role: str, **kwargs):
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role=role,
                    cycle="final_cycle",
                    review_kind="final_packet",
                    workflow_id="registered_workflow",
                    run_id="registered_run",
                    stage_id="stage",
                )

            with mock.patch.object(supervisor_agents, "invoke_operator_codex", side_effect=lambda **kwargs: decision("operator_codex", **kwargs)), mock.patch.object(supervisor_agents, "invoke_codex_review_agent", side_effect=lambda **kwargs: decision("codex_review_agent", **kwargs)), mock.patch.object(supervisor_agents, "invoke_claude_review_agent", side_effect=lambda **kwargs: decision("claude_review_agent", **kwargs)):
                supervisor.run_review_cycle(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="final_cycle", review_kind="final_packet", job_json=job.relative_to(ROOT))
            acceptance = supervisor.accept_consolidated_review(root=ROOT, session_ref=session["supervisor_session_id"], review_cycle_id="final_cycle", accepted_recommendation_ids=[])
            self.assertEqual(acceptance["approval_decision"], "approve")
            updated = load_session(ROOT, session["supervisor_session_id"])
            cycle = next(item for item in updated["review_cycles"] if item["review_cycle_id"] == "final_cycle")
            review_items = []
            for role in ("operator_codex", "codex_review_agent", "claude_review_agent"):
                path = cycle["operator_provisional_record"] if role == "operator_codex" else cycle["review_agent_outputs"][role]
                review_items.append({"role": role, "artifact_path": path, "artifact_sha256": sha256_file(ROOT / path), "decision": "approve"})
            review_items.append({"role": "consolidation", "artifact_path": cycle["consolidation"], "artifact_sha256": sha256_file(ROOT / cycle["consolidation"]), "decision": "advisory"})
            payload = {
                **draft_body,
                "reviews": review_items,
                "operator_acceptance": {"decision_id": acceptance["decision_id"], "decision": "approve", "artifact_path": cycle["acceptance_record"], "artifact_sha256": sha256_file(ROOT / cycle["acceptance_record"])},
            }
            altered_body = json.loads(json.dumps(payload))
            altered_body["summary"] = "Changed after final review."
            with self.assertRaisesRegex(SystemExit, "differs from the exact reviewed"):
                supervisor.create_final_delivery_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=altered_body, output=None)
            late = temp / "late_unreviewed.md"
            late.write_text("not reviewed\n", encoding="utf-8")
            unreviewed = json.loads(json.dumps(payload))
            unreviewed["deliverables"][0].update({"path": relpath(ROOT, late), "sha256": sha256_file(late)})
            with self.assertRaises(SystemExit):
                supervisor.create_final_delivery_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=unreviewed, output=None)
            unreviewed_evidence = json.loads(json.dumps(payload))
            unreviewed_evidence["evidence"][0].update({"locator": relpath(ROOT, late), "sha256": sha256_file(late)})
            with self.assertRaisesRegex(SystemExit, "not path/hash-bound"):
                supervisor._require_reviewed_artifact(
                    ROOT,
                    {run["artifact_path"]: {"path": run["artifact_path"], "sha256": artifact_sha}},
                    unreviewed_evidence["evidence"][0]["locator"],
                    unreviewed_evidence["evidence"][0]["sha256"],
                    "evidence",
                )
            failed = json.loads(json.dumps(payload))
            failed["validation_evidence"][0]["status"] = "failed"
            with self.assertRaises(SystemExit):
                supervisor.create_final_delivery_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=failed, output=None)
            failed_implementation = {
                "packet_version": "failed_validation",
                "summary": "Must not complete.",
                "file_inventory": [{"path": run["artifact_path"]}],
                "emitted_files": [{"path": run["artifact_path"], "sha256": artifact_sha}],
                "validation_evidence": [{"status": "failed"}],
                "agent_reviews": {role: {} for role in ("operator_codex", "codex_review_agent", "claude_review_agent")},
                "consolidation": {},
                "operator_acceptance": {},
                "model_migration_summary": {},
                "failure_policy_summary": [],
                "human_pause_summary": [],
                "rollout_instructions": "Do not roll out.",
                "residual_risks": [],
            }
            with self.assertRaises(SystemExit):
                supervisor.create_final_implementation_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=failed_implementation, output=None)
            operator_markdown = ROOT / cycle["review_gates"]["operator_codex"]["markdown_path"]
            original_markdown = operator_markdown.read_text(encoding="utf-8")
            operator_markdown.write_text("# Tampered review\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                supervisor.create_final_delivery_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=payload, output=None)
            operator_markdown.write_text(original_markdown, encoding="utf-8")
            self.assertNotEqual(load_session(ROOT, session["supervisor_session_id"])["status"], "completed")
            completed = supervisor.create_final_delivery_bundle(root=ROOT, session_ref=session["supervisor_session_id"], payload=payload, output=None)
            self.assertEqual(completed["delivery_id"], "final_delivery")
            self.assertEqual(load_session(ROOT, session["supervisor_session_id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
