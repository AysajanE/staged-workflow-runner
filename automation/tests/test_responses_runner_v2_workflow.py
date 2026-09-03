from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2 import (
    artifacts,
    workflow as workflow_module,
)
from automation.responses_runner_v2.contracts import (
    RUNNER_VERSION,
    RuntimeOptions,
    build_prompt_cache_key,
    sha256_file,
    write_json,
)
from automation.responses_runner_v2.openai_client import (
    OUTCOME_AMBIGUOUS,
    OUTCOME_KNOWN_REJECTED,
    ApiError,
)
from automation.responses_runner_v2.pack_loader import load_workflow_definition
from automation.responses_runner_v2.workflow import cancel_stage, refresh_stage, resume_stage, run_workflow


ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC_SHARED_INSTRUCTIONS = (
    ROOT / "automation/examples/responses_runner_v2_synthetic/shared_instructions.md"
).as_posix()
SYNTHETIC_REVIEWED_STAGE1_PROMPT = (
    ROOT / "automation/examples/responses_runner_v2_synthetic/prompts/reviewed_stage1.md"
).as_posix()
SYNTHETIC_REVIEWED_STAGE2_PROMPT = (
    ROOT / "automation/examples/responses_runner_v2_synthetic/prompts/reviewed_stage2.md"
).as_posix()
SYNTHETIC_REVIEWED_STAGE1_INPUT = (
    ROOT / "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage1.input_manifest.json"
).as_posix()
SYNTHETIC_REVIEWED_STAGE2_INPUT = (
    ROOT / "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage2.input_manifest.json"
).as_posix()


def _stage_dir(run_manifest: dict, stage_id: str | None = None, index: int = 0) -> Path:
    summary = (
        next(item for item in run_manifest["stages"] if item["stage_id"] == stage_id)
        if stage_id is not None
        else run_manifest["stages"][index]
    )
    current_attempt_id = summary.get("current_attempt_id")
    for attempt in summary.get("attempts", []):
        if attempt.get("attempt_id") == current_attempt_id:
            return ROOT / attempt["attempt_dir"]
    if summary.get("dry_run_dir"):
        return ROOT / summary["dry_run_dir"]
    return ROOT / summary["stage_dir"]


def _completed_response(
    response_id: str,
    *,
    model: str = "gpt-5.6",
    text: str = "Synthetic response",
) -> dict:
    return {
        "id": response_id,
        "status": "completed",
        "model": model,
        "background": True,
        "store": True,
        "created_at": 1773752598,
        "completed_at": 1773752600,
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": text}],
            }
        ],
    }


def _in_progress_response(response_id: str, *, model: str = "gpt-5.6") -> dict:
    return {
        "id": response_id,
        "status": "in_progress",
        "model": model,
        "background": True,
        "store": True,
        "created_at": 1773752598,
        "output": [],
    }


def _failed_response(response_id: str, *, model: str = "gpt-5.6") -> dict:
    return {
        "id": response_id,
        "status": "failed",
        "model": model,
        "background": True,
        "store": True,
        "created_at": 1773752598,
        "error": {"code": "rate_limit_exceeded", "message": "Synthetic failure"},
        "output": [
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "Synthetic response despite failure"}],
            }
        ],
    }


def _cancelled_response(response_id: str, *, model: str = "gpt-5.6") -> dict:
    return {
        "id": response_id,
        "status": "cancelled",
        "model": model,
        "background": True,
        "store": True,
        "created_at": 1773752598,
        "completed_at": 1773752600,
        "output": [],
    }


class FakeClient:
    def __init__(
        self,
        *,
        token_error: ApiError | None = None,
        token_count: int = 123,
        completed: bool = True,
    ) -> None:
        self.token_error = token_error
        self.token_count = token_count
        self.completed = completed
        self.upload_count = 0
        self.upload_requests: list[dict] = []
        self.create_requests: list[dict] = []
        self.delete_calls: list[str] = []

    def upload_file(self, path, purpose, file_expiration_policy=None):
        self.upload_count += 1
        self.upload_requests.append(
            {
                "path": str(path),
                "purpose": purpose,
                "file_expiration_policy": file_expiration_policy,
            }
        )
        response = {"id": f"file_{self.upload_count}", "purpose": purpose, "created_at": 1}
        if isinstance(file_expiration_policy, dict) and isinstance(file_expiration_policy.get("seconds"), int):
            response["expires_at"] = 1 + int(file_expiration_policy["seconds"])
        return response

    def create_response(self, payload):
        self.create_requests.append(payload)
        if payload["text"]["format"]["type"] == "json_schema":
            return {
                "id": "resp_sidecar",
                "status": "completed",
                "model": "gpt-5.6",
                "background": payload.get("background"),
                "store": True,
                "max_output_tokens": payload.get("max_output_tokens"),
                "output_parsed": {
                    "summary_version": "responses_runner_v2.synthetic_summary.v1",
                    "workflow_id": payload["metadata"]["workflow_id"],
                    "final_assessment": "Synthetic response",
                    "key_points": ["Synthetic response"],
                    "open_questions": []
                },
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "{\"summary_version\":\"responses_runner_v2.synthetic_summary.v1\"}"
                            }
                        ]
                    }
                ]
            }
        return _completed_response("resp_main") if self.completed else _in_progress_response("resp_main")

    def retrieve_response(self, response_id):
        return _completed_response(response_id)

    def wait_for_terminal_response(self, response_id, **_kwargs):
        return _completed_response(response_id)

    def count_input_tokens_once(self, _payload):
        if self.token_error is not None:
            raise self.token_error
        return {"input_tokens": self.token_count}

    def delete_file(self, file_id):
        self.delete_calls.append(file_id)
        return {"id": file_id, "deleted": True}


class SequenceClient(FakeClient):
    def __init__(self, responses: list[dict]) -> None:
        super().__init__()
        self.responses = list(responses)

    def create_response(self, payload):
        if payload["text"]["format"]["type"] == "json_schema":
            return super().create_response(payload)
        if not self.responses:
            raise AssertionError("No queued response available for create_response")
        return self.responses.pop(0)


class AmbiguousSubmitClient(FakeClient):
    def create_response(self, payload):
        self.create_requests.append(payload)
        raise ApiError("transport outcome unknown", outcome_certainty=OUTCOME_AMBIGUOUS)


class KnownRejectedSubmitClient(FakeClient):
    def create_response(self, payload):
        self.create_requests.append(payload)
        raise ApiError("request rejected", outcome_certainty=OUTCOME_KNOWN_REJECTED)


class RacingRetrieveClient(FakeClient):
    def __init__(self, run_dir: Path) -> None:
        super().__init__()
        self.run_dir = run_dir

    def retrieve_response(self, response_id):
        manifest = artifacts.load_run_manifest(ROOT, self.run_dir)
        manifest["revision"] += 1
        artifacts.write_run_manifest(self.run_dir, manifest)
        return _completed_response(response_id)


class CancellingClient(FakeClient):
    def __init__(self) -> None:
        super().__init__(completed=False)
        self.cancel_calls: list[str] = []

    def cancel_response(self, response_id):
        self.cancel_calls.append(response_id)
        return _cancelled_response(response_id)

    def retrieve_response(self, response_id):
        return _cancelled_response(response_id)


class BlockingUploadClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.upload_started = threading.Event()
        self.release_upload = threading.Event()

    def upload_file(self, path, purpose, file_expiration_policy=None):
        self.upload_started.set()
        if not self.release_upload.wait(timeout=10):
            raise AssertionError("test upload was not released")
        return super().upload_file(path, purpose, file_expiration_policy)


class ResponsesRunnerV2WorkflowTests(unittest.TestCase):
    def test_failed_no_artifact_stage_can_be_rerun_with_explicit_stage(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            with self.assertRaisesRegex(SystemExit, "failed_no_artifact"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_name="plain-rerun",
                        output_root=tmp_path.relative_to(ROOT),
                    ),
                    client=KnownRejectedSubmitClient(),
                    root=ROOT,
                )
            run_dir = next(path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists())
            failed = artifacts.load_run_manifest(ROOT, run_dir)
            first_attempt_dir = ROOT / failed["stages"][0]["attempts"][0]["attempt_dir"]
            first_error_hash = sha256_file(first_attempt_dir / "submission.error.json")

            with self.assertRaisesRegex(SystemExit, "Rerun it as a new attempt"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(run_dir=run_dir.relative_to(ROOT), wait=True),
                    client=FakeClient(),
                    root=ROOT,
                )

            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="draft_summary",
                    wait=True,
                ),
                client=FakeClient(),
                root=ROOT,
            )

            self.assertEqual(result["status"], "completed")
            rerun = artifacts.load_run_manifest(ROOT, run_dir)
            attempts = rerun["stages"][0]["attempts"]
            self.assertEqual([item["attempt_id"] for item in attempts], ["attempt_001", "attempt_002"])
            self.assertEqual(sha256_file(first_attempt_dir / "submission.error.json"), first_error_hash)
            self.assertTrue((ROOT / attempts[1]["attempt_dir"] / "artifact.md").exists())

    def test_existing_run_rejects_same_id_workflow_from_another_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_name="workflow-freeze",
                    output_root=tmp_path.relative_to(ROOT),
                    dry_run=True,
                ),
                root=ROOT,
            )
            original_path = (
                ROOT
                / "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json"
            )
            alternate_payload = json.loads(original_path.read_text(encoding="utf-8"))
            alternate_payload["description"] = "Different workflow bytes with the same workflow_id."
            alternate_payload["shared_instructions_file"] = SYNTHETIC_SHARED_INSTRUCTIONS
            stage = alternate_payload["stages"][0]
            stage["task_file"] = (
                ROOT / "automation/examples/responses_runner_v2_synthetic/prompts/one_pass_task.md"
            ).as_posix()
            stage["input_manifest_file"] = (
                ROOT
                / "automation/examples/responses_runner_v2_synthetic/inputs/one_pass.input_manifest.json"
            ).as_posix()
            stage["tool_profile_file"] = (
                ROOT
                / "automation/examples/responses_runner_v2_synthetic/tools/no_tools.profile.json"
            ).as_posix()
            alternate_path = tmp_path / "alternate.workflow.json"
            alternate_path.write_text(
                json.dumps(alternate_payload, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "does not match the workflow this run was started with"):
                run_workflow(
                    workflow_file=alternate_path.relative_to(ROOT),
                    runtime=RuntimeOptions(
                        run_dir=Path(result["run_dir"]),
                        dry_run=True,
                    ),
                    root=ROOT,
                )

    def test_explicit_empty_run_directory_initialization_is_locked(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            run_dir = Path(tmp) / "explicit-run"
            entered = threading.Event()
            release = threading.Event()
            first_errors: list[BaseException] = []
            original_initialize = workflow_module.artifacts.initialize_run_manifest

            def blocking_initialize(**kwargs):
                entered.set()
                if not release.wait(timeout=10):
                    raise AssertionError("test manifest initialization was not released")
                return original_initialize(**kwargs)

            def initialize_first() -> None:
                try:
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_dir=run_dir.relative_to(ROOT),
                            dry_run=True,
                        ),
                        root=ROOT,
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    first_errors.append(exc)

            with mock.patch.object(
                workflow_module.artifacts,
                "initialize_run_manifest",
                side_effect=blocking_initialize,
            ):
                thread = threading.Thread(target=initialize_first)
                thread.start()
                self.assertTrue(entered.wait(timeout=10))
                try:
                    with self.assertRaisesRegex(SystemExit, "Run is locked"):
                        run_workflow(
                            workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                            runtime=RuntimeOptions(
                                run_dir=run_dir.relative_to(ROOT),
                                dry_run=True,
                            ),
                            root=ROOT,
                        )
                finally:
                    release.set()
                    thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first_errors, [])
            self.assertTrue((run_dir / "run_manifest.json").exists())

    def test_nonempty_unjournaled_run_directory_is_preserved_and_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            run_dir = Path(tmp) / "foreign-run"
            run_dir.mkdir()
            marker = run_dir / "operator-evidence.txt"
            marker.write_text("preserve me\n", encoding="utf-8")

            with self.assertRaisesRegex(
                SystemExit,
                "Refusing to initialize nonempty run directory",
            ):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=run_dir.relative_to(ROOT),
                        dry_run=True,
                    ),
                    root=ROOT,
                )

            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve me\n")
            self.assertFalse((run_dir / "run_manifest.json").exists())

    def test_concurrent_start_allows_exactly_one_primary_submission(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            first_client = BlockingUploadClient()
            first_errors: list[BaseException] = []

            def launch_first() -> None:
                try:
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_name="concurrent-submit",
                            output_root=tmp_path.relative_to(ROOT),
                            wait=True,
                        ),
                        client=first_client,
                        root=ROOT,
                    )
                except BaseException as exc:  # pragma: no cover - asserted below
                    first_errors.append(exc)

            thread = threading.Thread(target=launch_first)
            thread.start()
            self.assertTrue(first_client.upload_started.wait(timeout=10))
            run_dir = next(path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists())
            second_client = FakeClient()
            try:
                with self.assertRaises(SystemExit):
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_dir=run_dir.relative_to(ROOT),
                            stage_id="draft_summary",
                            output_root=tmp_path.relative_to(ROOT),
                            wait=True,
                        ),
                        client=second_client,
                        root=ROOT,
                    )
            finally:
                first_client.release_upload.set()
                thread.join(timeout=15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first_errors, [])
            primary_requests = [
                payload
                for payload in first_client.create_requests
                if payload["text"]["format"]["type"] != "json_schema"
            ]
            self.assertEqual(len(primary_requests), 1)
            self.assertEqual(second_client.create_requests, [])

    def test_ambiguous_submission_is_durable_and_cannot_be_resubmitted_or_cancelled(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            client = AmbiguousSubmitClient()
            with self.assertRaisesRegex(SystemExit, "submission_outcome_unknown"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_name="unknown-submit",
                        output_root=tmp_path.relative_to(ROOT),
                        wait=False,
                    ),
                    client=client,
                    root=ROOT,
                )
            run_dir = next(path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists())
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            self.assertEqual(run_manifest["status"], "submission_outcome_unknown")
            self.assertTrue((stage_dir / "submission.error.json").exists())

            second_client = FakeClient()
            with self.assertRaises(SystemExit):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=run_dir.relative_to(ROOT),
                        stage_id="draft_summary",
                        output_root=tmp_path.relative_to(ROOT),
                    ),
                    client=second_client,
                    root=ROOT,
                )
            with self.assertRaisesRegex(SystemExit, "unknown submission outcome"):
                cancel_stage(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="draft_summary",
                    client=second_client,
                    root=ROOT,
                )
            self.assertEqual(len(client.create_requests), 1)
            self.assertEqual(second_client.create_requests, [])

    def test_finalization_failure_remains_pending_and_resume_completes_it(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            client = FakeClient()
            with mock.patch(
                "automation.responses_runner_v2.workflow._write_stage_artifacts_for_response",
                side_effect=RuntimeError("synthetic artifact write failure"),
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic artifact write failure"):
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_name="pending-finalization",
                            output_root=tmp_path.relative_to(ROOT),
                            wait=True,
                        ),
                        client=client,
                        root=ROOT,
                    )
            run_dir = next(path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists())
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            self.assertEqual(run_manifest["status"], "pending_finalization")
            self.assertEqual(run_manifest["stages"][0]["status"], "remote_terminal_pending_finalization")
            self.assertTrue((stage_dir / "finalization.error.json").exists())
            self.assertFalse((stage_dir / "artifact.md").exists())

            resumed = resume_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                wait=True,
                poll_interval=0.1,
                max_wait_seconds=10.0,
                client=FakeClient(),
                root=ROOT,
            )
            self.assertEqual(resumed["status"], "completed")
            self.assertTrue((stage_dir / "artifact.md").exists())

    def test_cancel_is_idempotent_and_finalizes_terminal_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            client = CancellingClient()
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_name="cancel-live",
                    output_root=tmp_path.relative_to(ROOT),
                    wait=False,
                ),
                client=client,
                root=ROOT,
            )
            first = cancel_stage(
                run_dir=result["run_dir"],
                stage_id="draft_summary",
                client=client,
                root=ROOT,
            )
            second = cancel_stage(
                run_dir=result["run_dir"],
                stage_id="draft_summary",
                client=client,
                root=ROOT,
            )
            self.assertEqual(first["status"], "cancelled")
            self.assertEqual(second["status"], "cancelled")
            self.assertEqual(client.cancel_calls, ["resp_main"])
            run_manifest = json.loads(
                (ROOT / result["run_manifest_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(run_manifest["stages"][0]["status"], "cancelled")

    def test_dry_run_writes_request_payload(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-dry-run",
                output_root=Path(tmp).relative_to(ROOT),
                dry_run=True,
            )
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                root=ROOT,
            )
            run_manifest_path = ROOT / result["run_manifest_path"]
            run_manifest = json.loads(run_manifest_path.read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            request_payload = json.loads((stage_dir / "request_payload.json").read_text(encoding="utf-8"))
            self.assertTrue((stage_dir / "request_payload.json").exists())
            self.assertEqual(run_manifest["status"], "created")
            self.assertNotIn("attachment_role_blocks", request_payload)

    def test_continue_without_token_count_requires_no_configured_budget(self) -> None:
        workflow = load_workflow_definition(
            "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
            root=ROOT,
        )
        policy = replace(
            workflow.request_defaults.token_preflight,
            max_retries=1,
            on_retryable_service_failure="continue_without_token_count",
        )
        workflow = replace(
            workflow,
            request_defaults=replace(workflow.request_defaults, token_preflight=policy),
        )
        budgeted_stage = workflow.stages[0]
        unbudgeted_stage = replace(workflow.stages[0], max_input_tokens=None)
        runtime = RuntimeOptions()

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            temp = Path(tmp)
            stage_paths = {
                "token_preflight": temp / "token_preflight.json",
                "token_preflight_error": temp / "token_preflight.error.json",
            }
            client = FakeClient(token_error=ApiError("retryable", status_code=503))
            with self.assertRaisesRegex(SystemExit, "failed closed"):
                workflow_module._token_preflight_state(
                    root=ROOT,
                    client=client,
                    workflow=workflow,
                    stage=budgeted_stage,
                    stage_paths=stage_paths,
                    payload={},
                    runtime=runtime,
                )
            failed = json.loads(stage_paths["token_preflight_error"].read_text(encoding="utf-8"))
            self.assertEqual(failed["fallback_decision"], "fail_closed")

            continued = workflow_module._token_preflight_state(
                root=ROOT,
                client=client,
                workflow=workflow,
                stage=unbudgeted_stage,
                stage_paths=stage_paths,
                payload={},
                runtime=runtime,
            )
            self.assertEqual(continued["status"], "continued_after_retryable_service_failure")

    def test_exact_token_preflight_enforces_configured_input_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-exact-input-limit",
                output_root=Path(tmp).relative_to(ROOT),
            )
            client = FakeClient(token_count=700001)
            with self.assertRaisesRegex(SystemExit, "exact preflight limit"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=runtime,
                    client=client,
                    root=ROOT,
                )
            run_dir = next(
                path for path in Path(tmp).iterdir() if (path / "run_manifest.json").exists()
            )
            run_manifest = artifacts.load_run_manifest(ROOT, run_dir)
            stage_dir = _stage_dir(run_manifest)
            diagnostics = json.loads(
                (stage_dir / "token_preflight.error.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_manifest["stages"][0]["status"], "blocked_preflight")
            self.assertEqual(diagnostics["reason"], "max_input_tokens_exceeded")
            self.assertEqual(client.create_requests, [])

    def test_exact_token_preflight_enforces_model_context_window(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-exact-context-limit",
                output_root=Path(tmp).relative_to(ROOT),
                max_input_tokens=1040000,
            )
            client = FakeClient(token_count=1030000)
            with self.assertRaisesRegex(SystemExit, "model_context_window_exceeded"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=runtime,
                    client=client,
                    root=ROOT,
                )
            run_dir = next(
                path for path in Path(tmp).iterdir() if (path / "run_manifest.json").exists()
            )
            run_manifest = artifacts.load_run_manifest(ROOT, run_dir)
            diagnostics = json.loads(
                (_stage_dir(run_manifest) / "token_preflight.error.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(diagnostics["context_window"], 1050000)
            self.assertEqual(diagnostics["context_input_limit"], 1025000)
            self.assertEqual(diagnostics["reason"], "model_context_window_exceeded")
            self.assertEqual(client.create_requests, [])

    def test_critical_workflow_fails_closed_on_token_count_service_failure(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-preflight-fallback",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
            )
            client = FakeClient(token_error=ApiError("retryable", status_code=503))
            with self.assertRaisesRegex(SystemExit, "failed closed"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=runtime,
                    client=client,
                    root=ROOT,
                )
            run_dir = next(path for path in Path(tmp).iterdir() if (path / "run_manifest.json").exists())
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            self.assertEqual(run_manifest["stages"][0]["status"], "blocked_preflight")
            self.assertEqual(run_manifest["stages"][0]["token_preflight"]["status"], "failed_closed")
            self.assertTrue((stage_dir / "token_preflight.error.json").exists())
            self.assertEqual(client.create_requests, [])

    def test_resume_and_refresh_use_existing_stage_metadata(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-resume",
                output_root=Path(tmp).relative_to(ROOT),
                wait=False,
            )
            client = FakeClient(completed=False)
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=client,
                root=ROOT,
            )
            run_dir = result["run_dir"]
            resumed = resume_stage(
                run_dir=run_dir,
                stage_id="draft_summary",
                wait=True,
                poll_interval=0.1,
                max_wait_seconds=10.0,
                client=FakeClient(),
                root=ROOT,
            )
            refreshed = refresh_stage(
                run_dir=run_dir,
                stage_id="draft_summary",
                client=FakeClient(),
                root=ROOT,
            )
            self.assertEqual(resumed["status"], "completed")
            self.assertIn(refreshed["status"], {"completed", "running"})

    def test_refresh_preserves_operator_token_preflight_skip(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_name="synthetic-refresh-skipped-preflight",
                    output_root=Path(tmp).relative_to(ROOT),
                    skip_token_count=True,
                    wait=False,
                ),
                client=FakeClient(completed=False),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            refresh_client = FakeClient(completed=False)
            refresh_client.retrieve_response = lambda response_id: _in_progress_response(response_id)

            refresh_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                client=refresh_client,
                root=ROOT,
            )

            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(
                run_manifest["stages"][0]["token_preflight"],
                {"status": "skipped_by_operator", "attempts": 0},
            )

    def test_completed_stage_rejects_resume_without_side_effects(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-sidecar-idempotent",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
            )
            client = FakeClient()
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=client,
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            upload_count = client.upload_count
            create_count = len(client.create_requests)

            with self.assertRaisesRegex(SystemExit, "not resumable"):
                resume_stage(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="draft_summary",
                    wait=False,
                    poll_interval=0.1,
                    max_wait_seconds=10.0,
                    client=client,
                    root=ROOT,
                )
            self.assertEqual(client.upload_count, upload_count)
            self.assertEqual(len(client.create_requests), create_count)

if __name__ == "__main__":
    unittest.main()


class AdvisoryValidatorTests(unittest.TestCase):
    def test_failed_blocking_validator_is_recorded_without_wedging_the_stage(self) -> None:
        import shutil

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            pack = tmp_path / "pack"
            shutil.copytree(ROOT / "automation/examples/responses_runner_v2_synthetic", pack)
            workflow_path = pack / "workflows/one_pass.workflow.json"
            payload = json.loads(workflow_path.read_text(encoding="utf-8"))
            stage = payload["stages"][0]
            stage["output"] = {"primary_format": "text"}
            stage["citation_policy"] = {"allowed_locator_types": ["workspace_file"]}
            stage["post_output_validators"] = [
                {"validator_id": "evidence_references_v1", "gate": "blocking"}
            ]
            workflow_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            output_root = tmp_path / "runs"
            output_root.mkdir()

            client = FakeClient()
            result = run_workflow(
                workflow_file=workflow_path.relative_to(ROOT),
                runtime=RuntimeOptions(
                    run_name="synthetic-advisory-validator",
                    output_root=output_root.relative_to(ROOT),
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )

            run_manifest = artifacts.load_run_manifest(ROOT, ROOT / result["run_dir"])
            summary = run_manifest["stages"][0]
            stage_dir = _stage_dir(run_manifest)
            report = json.loads((stage_dir / "validator_report.json").read_text(encoding="utf-8"))
            finalization_error_exists = (stage_dir / "finalization.error.json").exists()
            artifact_exists = (stage_dir / "artifact.md").exists()

        self.assertEqual(run_manifest["status"], "completed")
        self.assertEqual(summary["status"], "completed")
        self.assertFalse(report["passed"])
        self.assertFalse(summary["validators_passed"])
        self.assertTrue(summary["validator_report_path"].endswith("validator_report.json"))
        self.assertFalse(finalization_error_exists)
        self.assertTrue(artifact_exists)
