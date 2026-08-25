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
    request_plan,
    supervisor_policies,
    telemetry,
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
from automation.responses_runner_v2.review_bundle import create_review_bundle
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
    checkpoint_path = summary.get("checkpoint_path")
    if checkpoint_path:
        return (ROOT / checkpoint_path).parent
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
    def test_archived_failed_no_artifact_rerun_creates_new_bound_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            with self.assertRaisesRegex(SystemExit, "failed_no_artifact"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_name="authorized-rerun",
                        output_root=tmp_path.relative_to(ROOT),
                    ),
                    client=KnownRejectedSubmitClient(),
                    root=ROOT,
                )
            run_dir = next(path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists())
            failed = artifacts.load_run_manifest(ROOT, run_dir)
            first_attempt = failed["stages"][0]["attempts"][0]
            first_attempt_dir = ROOT / first_attempt["attempt_dir"]
            first_error_hash = sha256_file(first_attempt_dir / "submission.error.json")
            failed_usage_attempt = json.loads(
                (first_attempt_dir / "usage_attempt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failed_usage_attempt["status"], "failed_no_artifact")
            self.assertIsInstance(failed_usage_attempt["request_wall_ms"], int)
            self.assertEqual(failed_usage_attempt["retry_count"], 0)
            self.assertIsNone(failed_usage_attempt["usage"])
            failed_usage_result = workflow_module.usage_report(
                run_dir=run_dir.relative_to(ROOT),
                root=ROOT,
            )
            failed_usage_report = json.loads(
                (ROOT / failed_usage_result["usage_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(failed_usage_report["totals"]["attempt_count"], 1)
            self.assertIsNone(failed_usage_report["totals"]["total_tokens"])
            request_path = first_attempt_dir / "request_payload.json"
            archive_dir = tmp_path / "supervisor_archive"
            archive_dir.mkdir()
            archived_request = archive_dir / "request_payload.json"
            shutil.copy2(request_path, archived_request)
            archive_path = archive_dir / "supervisor_archive.json"
            request_hash = "a" * 64
            write_json(
                archive_path,
                {
                    "schema_version": "responses_runner_v2.supervisor_archive.v1",
                    "archive_id": "archive_authorized_rerun",
                    "archived_at": "2026-08-11T12:00:00+00:00",
                    "reason": "failed_no_artifact",
                    "source": {
                        "run_dir": run_dir.relative_to(ROOT).as_posix(),
                        "run_id": failed["run_id"],
                        "workflow_id": failed["workflow_id"],
                        "stage_id": "draft_summary",
                        "response_id": None,
                    },
                    "included_artifacts": [
                        {
                            "source_path": request_path.relative_to(ROOT).as_posix(),
                            "archive_path": archived_request.relative_to(ROOT).as_posix(),
                            "sha256": sha256_file(request_path),
                            "bytes": request_path.stat().st_size,
                        }
                    ],
                    "request_hash": request_hash,
                    "scaffold_hash": "",
                    "request_evidence": {
                        "status": "complete",
                        "request_hash": request_hash,
                        "evidence_files": [],
                        "model_tool_settings": {},
                    },
                    "scaffold_evidence": {"status": "complete", "scaffold_hash": ""},
                    "unchanged_input_evidence": {
                        "request_hash_before": request_hash,
                        "scaffold_hash_before": "",
                        "rerun_requires_same_hashes": True,
                    },
                    "retry_budget_before": {"failed_no_artifact": 1},
                    "retry_budget_after": {"failed_no_artifact": 0},
                    "rerun_as_is_eligible": True,
                },
            )

            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="draft_summary",
                    rerun_archive_manifest=archive_path.relative_to(ROOT).as_posix(),
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
            self.assertEqual(
                attempts[1]["rerun_authorization"]["prior_attempt_id"],
                "attempt_001",
            )
            self.assertEqual(
                attempts[1]["rerun_authorization"]["archive_sha256"],
                sha256_file(archive_path),
            )

    def test_existing_run_rejects_effective_runtime_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_name="runtime-freeze",
                    output_root=tmp_path.relative_to(ROOT),
                    dry_run=True,
                ),
                root=ROOT,
            )
            with self.assertRaisesRegex(SystemExit, "effective runtime drifted"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=Path(result["run_dir"]),
                        max_output_tokens=1234,
                        dry_run=True,
                    ),
                    root=ROOT,
                )

    def test_review_gated_next_stage_allows_output_limit_increase(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = "automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json"
            stage1 = run_workflow(
                workflow_file=workflow_path,
                runtime=RuntimeOptions(
                    run_name="review-gated-output-increase",
                    output_root=tmp_path.relative_to(ROOT),
                    wait=True,
                ),
                client=FakeClient(),
                root=ROOT,
            )
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = artifacts.load_run_manifest(ROOT, run_dir)
            proposal_dir = _stage_dir(run_manifest, "proposal")
            notes = run_dir / "proposal.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "proposal.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id=run_manifest["run_id"],
                primary_artifact_markdown=(proposal_dir / "artifact.md").relative_to(ROOT),
                response_artifact_json=(proposal_dir / "response.final.json").relative_to(ROOT),
                reviewer_notes=notes.relative_to(ROOT),
            )

            run_workflow(
                workflow_file=workflow_path,
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="revision",
                    review_bundles=[bundle.relative_to(ROOT).as_posix()],
                    max_output_tokens=96000,
                    dry_run=True,
                ),
                root=ROOT,
            )

            request_payload = json.loads(
                (
                    run_dir
                    / "dry_runs/stages/02_revision/request_payload.json"
                ).read_text(encoding="utf-8")
            )
            contract = json.loads(
                (run_dir / "run_contract.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_payload["max_output_tokens"], 96000)
            self.assertIsNone(contract["effective_runtime"]["max_output_tokens"])

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
            stage["output"]["sidecar"]["schema_file"] = (
                ROOT
                / "automation/examples/responses_runner_v2_synthetic/schemas/synthetic_summary.schema.json"
            ).as_posix()
            alternate_path = tmp_path / "alternate.workflow.json"
            alternate_path.write_text(
                json.dumps(alternate_payload, indent=2) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(SystemExit, "does not match the frozen run contract"):
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
            original_create_contract = workflow_module.create_run_contract

            def blocking_create_contract(**kwargs):
                entered.set()
                if not release.wait(timeout=10):
                    raise AssertionError("test contract creation was not released")
                return original_create_contract(**kwargs)

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
                workflow_module,
                "create_run_contract",
                side_effect=blocking_create_contract,
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

    def test_partial_initialization_rejects_effective_runtime_drift(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            run_dir = Path(tmp) / "partial-run"
            with mock.patch.object(
                workflow_module.artifacts,
                "write_run_manifest",
                side_effect=SystemExit("synthetic pre-manifest crash"),
            ):
                with self.assertRaisesRegex(SystemExit, "synthetic pre-manifest crash"):
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_dir=run_dir.relative_to(ROOT),
                            dry_run=True,
                        ),
                        root=ROOT,
                    )

            self.assertTrue((run_dir / "run_initialization.intent.json").exists())
            self.assertTrue((run_dir / "run_contract.json").exists())
            self.assertFalse((run_dir / "run_manifest.json").exists())
            with self.assertRaisesRegex(
                SystemExit,
                "does not match the requested workflow/runtime binding",
            ):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=run_dir.relative_to(ROOT),
                        max_output_tokens=1234,
                        dry_run=True,
                    ),
                    root=ROOT,
                )

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
            self.assertFalse((run_dir / "run_initialization.intent.json").exists())
            self.assertFalse((run_dir / "run_contract.json").exists())
            self.assertFalse((run_dir / "run_manifest.json").exists())

    def test_remote_result_cannot_apply_after_manifest_revision_race(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=RuntimeOptions(
                    run_name="revision-race",
                    output_root=tmp_path.relative_to(ROOT),
                    wait=False,
                ),
                client=FakeClient(completed=False),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            with self.assertRaisesRegex(SystemExit, "revision conflict"):
                resume_stage(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="draft_summary",
                    wait=False,
                    poll_interval=0.0,
                    max_wait_seconds=None,
                    client=RacingRetrieveClient(run_dir),
                    root=ROOT,
                )
            raced = artifacts.load_run_manifest(ROOT, run_dir)
            self.assertEqual(raced["stages"][0]["status"], "in_progress")
            self.assertFalse((_stage_dir(raced) / "artifact.md").exists())

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
            self.assertTrue((stage_dir / "submission.intent.json").exists())
            self.assertTrue((stage_dir / "submission.error.json").exists())
            usage_attempt = json.loads(
                (stage_dir / "usage_attempt.json").read_text(encoding="utf-8")
            )
            self.assertEqual(usage_attempt["status"], "submission_outcome_unknown")
            self.assertIsInstance(usage_attempt["request_wall_ms"], int)
            self.assertEqual(usage_attempt["retry_count"], 0)
            self.assertIsNone(usage_attempt["usage"])

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
            usage_result = workflow_module.usage_report(
                run_dir=run_dir.relative_to(ROOT),
                root=ROOT,
            )
            usage_report = json.loads(
                (ROOT / usage_result["usage_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(usage_report["totals"]["attempt_count"], 1)
            self.assertIsNone(usage_report["totals"]["total_tokens"])
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

    def test_resume_completes_transition_after_finalized_state_crash(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            original_persist = workflow_module._persist_stage_state

            def persist_then_crash(**kwargs):
                result = original_persist(**kwargs)
                if kwargs.get("stage_status") == "finalized":
                    raise RuntimeError("synthetic crash after finalized persistence")
                return result

            with mock.patch.object(
                workflow_module,
                "_persist_stage_state",
                side_effect=persist_then_crash,
            ):
                with self.assertRaisesRegex(RuntimeError, "after finalized persistence"):
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_name="finalized-crash",
                            output_root=tmp_path.relative_to(ROOT),
                            wait=True,
                        ),
                        client=FakeClient(),
                        root=ROOT,
                    )
            run_dir = next(
                path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists()
            )
            crashed = artifacts.load_run_manifest(ROOT, run_dir)
            self.assertEqual(crashed["stages"][0]["status"], "finalized")
            self.assertTrue((_stage_dir(crashed) / "artifact.md").exists())

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

    def test_resume_reconciles_checkpoint_crash_without_duplicate_primary_post(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            client = FakeClient(completed=False)
            original_cas = artifacts.write_run_manifest_cas
            crash_injected = False

            def crash_before_submitted_manifest(**kwargs):
                nonlocal crash_injected
                summary = artifacts.find_stage_summary(
                    kwargs["manifest"],
                    "draft_summary",
                )
                if summary["status"] == "submitted" and not crash_injected:
                    crash_injected = True
                    raise RuntimeError("synthetic crash before submitted manifest CAS")
                return original_cas(**kwargs)

            with mock.patch.object(
                artifacts,
                "write_run_manifest_cas",
                side_effect=crash_before_submitted_manifest,
            ):
                with self.assertRaisesRegex(RuntimeError, "before submitted manifest CAS"):
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_name="checkpoint-manifest-crash",
                            output_root=tmp_path.relative_to(ROOT),
                            wait=False,
                        ),
                        client=client,
                        root=ROOT,
                    )

            run_dir = next(
                path for path in tmp_path.iterdir() if (path / "run_manifest.json").exists()
            )
            crashed = artifacts.load_run_manifest(ROOT, run_dir)
            crashed_summary = crashed["stages"][0]
            attempt_dir = ROOT / crashed_summary["attempts"][0]["attempt_dir"]
            checkpoint_path = attempt_dir / "stage_checkpoint.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(crashed_summary["status"], "submitting")
            self.assertEqual(checkpoint["status"], "submitted")
            self.assertNotEqual(
                crashed_summary["checkpoint_sha256"],
                sha256_file(checkpoint_path),
            )
            transitions = artifacts.list_stage_state_transitions(run_dir)
            self.assertTrue(transitions)
            pending = artifacts.load_stage_state_transition(transitions[-1])
            self.assertEqual(
                pending["target_manifest_revision"],
                crashed["revision"] + 1,
            )
            self.assertEqual(
                artifacts.find_stage_summary(
                    pending["target_run_manifest"],
                    "draft_summary",
                )["status"],
                "submitted",
            )

            duplicate_client = FakeClient()
            with self.assertRaisesRegex(SystemExit, "use resume"):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=run_dir.relative_to(ROOT),
                        stage_id="draft_summary",
                        wait=False,
                    ),
                    client=duplicate_client,
                    root=ROOT,
                )
            self.assertEqual(duplicate_client.create_requests, [])
            startup_reconciled = artifacts.load_run_manifest(ROOT, run_dir)
            startup_summary = startup_reconciled["stages"][0]
            self.assertEqual(startup_summary["status"], "submitted")
            self.assertEqual(
                startup_summary["checkpoint_sha256"],
                sha256_file(ROOT / startup_summary["checkpoint_path"]),
            )

            resumed = resume_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                wait=True,
                poll_interval=0.1,
                max_wait_seconds=10.0,
                client=client,
                root=ROOT,
            )
            self.assertEqual(resumed["status"], "completed")
            primary_posts = [
                payload
                for payload in client.create_requests
                if payload["text"]["format"]["type"] != "json_schema"
            ]
            self.assertEqual(len(primary_posts), 1)
            recovered = artifacts.load_run_manifest(ROOT, run_dir)
            recovered_summary = recovered["stages"][0]
            self.assertEqual(recovered_summary["status"], "completed")
            self.assertEqual(
                recovered_summary["checkpoint_sha256"],
                sha256_file(ROOT / recovered_summary["checkpoint_path"]),
            )

    def test_transition_recovery_never_interprets_v1_manifest_evidence(self) -> None:
        v1_manifest = {"schema_version": "responses_runner_v2.run_manifest.v1"}
        with mock.patch.object(
            artifacts,
            "list_stage_state_transitions",
            side_effect=AssertionError("v1 attempt evidence must not be inspected"),
        ):
            reconciled = workflow_module._reconcile_stage_state_transitions(
                root=ROOT,
                run_dir=ROOT / "unused-v1-run",
                run_manifest=v1_manifest,
            )
        self.assertIs(reconciled, v1_manifest)

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
            checkpoint_path = ROOT / run_manifest["stages"][0]["checkpoint_path"]
            outcome = supervisor_policies.classify_stage_outcome(
                root=ROOT,
                checkpoint_path=checkpoint_path.relative_to(ROOT),
            )
            self.assertEqual(outcome["classification"], "cancelled")
            self.assertEqual(outcome["response_status"], "cancelled")
            self.assertEqual(outcome["action"], "preserve_cancelled")
            self.assertFalse(outcome["rerun_allowed"])
            self.assertFalse(outcome["rerun_requires_archive"])
            self.assertFalse(
                supervisor_policies.can_rerun_failed_no_artifact(
                    outcome=outcome,
                    archive_manifest={"rerun_as_is_eligible": True},
                )
            )
            supervisor_policies.write_stage_outcome(
                ROOT,
                (tmp_path / "cancelled.stage_outcome.json").relative_to(ROOT),
                outcome,
            )

    def test_dry_run_writes_request_payload_and_checkpoint(self) -> None:
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
            self.assertTrue((stage_dir / "stage_checkpoint.json").exists())
            self.assertEqual(run_manifest["status"], "created")
            self.assertNotIn("attachment_role_blocks", request_payload)

    def test_same_run_dry_and_live_request_payloads_have_exact_symbolic_parity(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            run_dir = Path(tmp) / "same_run"
            runtime_path = run_dir.relative_to(ROOT)
            workflow_file = (
                "automation/examples/responses_runner_v2_synthetic/"
                "workflows/one_pass.workflow.json"
            )
            run_workflow(
                workflow_file=workflow_file,
                runtime=RuntimeOptions(
                    run_name="dry-live-parity",
                    run_dir=runtime_path,
                    dry_run=True,
                ),
                root=ROOT,
            )
            dry_stage_dir = run_dir / "dry_runs/stages/01_draft_summary"
            dry_payload = json.loads(
                (dry_stage_dir / "request_payload.json").read_text(encoding="utf-8")
            )
            dry_plan = json.loads(
                (dry_stage_dir / "request_plan.json").read_text(encoding="utf-8")
            )

            client = FakeClient(completed=False)
            live = run_workflow(
                workflow_file=workflow_file,
                runtime=RuntimeOptions(
                    run_name="dry-live-parity",
                    run_dir=runtime_path,
                ),
                client=client,
                root=ROOT,
            )
            live_manifest = artifacts.load_run_manifest(ROOT, ROOT / live["run_dir"])
            live_stage_dir = _stage_dir(live_manifest)
            live_payload = json.loads(
                (live_stage_dir / "request_payload.json").read_text(encoding="utf-8")
            )
            live_plan = json.loads(
                (live_stage_dir / "request_plan.json").read_text(encoding="utf-8")
            )

            self.assertEqual(dry_plan, live_plan)
            self.assertEqual(dry_payload, dry_plan["symbolic_request_payload"])
            self.assertEqual(
                request_plan.verify_materialized_request(live_plan, live_payload),
                dry_payload,
            )
            self.assertEqual(client.create_requests, [live_payload])
            self.assertEqual(
                dry_plan["normalized_request_sha256"],
                request_plan.normalized_request_sha256(dry_payload),
            )

    def test_dry_run_fails_closed_when_request_plan_exceeds_context(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output_root = Path(tmp)
            original_build_request_plan = workflow_module.build_request_plan

            def oversize_request_plan(**kwargs):
                plan = original_build_request_plan(**kwargs)
                plan["estimate"]["fits_context"] = False
                return plan

            with mock.patch.object(
                workflow_module,
                "build_request_plan",
                side_effect=oversize_request_plan,
            ):
                with self.assertRaisesRegex(
                    SystemExit,
                    "request plan exceeds the verified model context window",
                ):
                    run_workflow(
                        workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                        runtime=RuntimeOptions(
                            run_name="synthetic-oversize-dry-run",
                            output_root=output_root.relative_to(ROOT),
                            dry_run=True,
                            skip_token_count=True,
                        ),
                        root=ROOT,
                    )

            run_dir = next(
                path for path in output_root.iterdir() if (path / "run_manifest.json").exists()
            )
            run_manifest = artifacts.load_run_manifest(ROOT, run_dir)
            stage_dir = _stage_dir(run_manifest)
            checkpoint = json.loads(
                (stage_dir / "stage_checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(run_manifest["status"], "blocked")
            self.assertEqual(run_manifest["stages"][0]["status"], "blocked_preflight")
            self.assertEqual(checkpoint["status"], "blocked_preflight")
            self.assertTrue((stage_dir / "request_plan.json").exists())
            self.assertTrue((stage_dir / "token_preflight.error.json").exists())
            self.assertFalse((stage_dir / "request_payload.json").exists())

    def test_byte_upper_bound_is_advisory_when_exact_preflight_will_run(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output_root = Path(tmp)
            original_build_request_plan = workflow_module.build_request_plan
            original_local_context_estimate = workflow_module._local_context_estimate

            def oversize_request_plan(**kwargs):
                plan = original_build_request_plan(**kwargs)
                plan["estimate"]["fits_context"] = False
                return plan

            def advisory_local_context_estimate(**kwargs):
                estimate = original_local_context_estimate(**kwargs)
                estimate["within_context_window"] = False
                estimate["passed"] = False
                return estimate

            with mock.patch.object(
                workflow_module,
                "build_request_plan",
                side_effect=oversize_request_plan,
            ), mock.patch.object(
                workflow_module,
                "_local_context_estimate",
                side_effect=advisory_local_context_estimate,
            ):
                result = run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                    runtime=RuntimeOptions(
                        run_name="synthetic-advisory-byte-bound",
                        output_root=output_root.relative_to(ROOT),
                        dry_run=True,
                    ),
                    root=ROOT,
                )

            run_manifest = artifacts.load_run_manifest(ROOT, ROOT / result["run_dir"])
            stage_dir = _stage_dir(run_manifest)
            local_estimate = json.loads(
                (stage_dir / "local_context_estimate.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                local_estimate["enforcement"],
                "advisory_exact_preflight_pending",
            )
            self.assertEqual(run_manifest["status"], "created")
            self.assertEqual(
                result["warnings"][0]["code"],
                "exact_token_preflight_not_executed_in_dry_run",
            )
            self.assertTrue((stage_dir / "request_payload.json").exists())
            self.assertFalse((stage_dir / "token_preflight.error.json").exists())

    def test_continue_without_token_count_requires_passing_local_estimate(self) -> None:
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
        stage = replace(workflow.stages[0], max_input_tokens=None)
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
                    stage=stage,
                    stage_paths=stage_paths,
                    payload={},
                    runtime=runtime,
                    local_context_estimate={"passed": False},
                )
            failed = json.loads(stage_paths["token_preflight_error"].read_text(encoding="utf-8"))
            self.assertEqual(failed["fallback_decision"], "fail_closed")
            self.assertFalse(failed["local_advisory_estimate_passed"])

            continued = workflow_module._token_preflight_state(
                root=ROOT,
                client=client,
                workflow=workflow,
                stage=stage,
                stage_paths=stage_paths,
                payload={},
                runtime=runtime,
                local_context_estimate={"passed": True},
            )
            self.assertEqual(
                continued["status"],
                "continued_after_retryable_service_failure",
            )

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
            checkpoint = json.loads(
                (stage_dir / "stage_checkpoint.json").read_text(encoding="utf-8")
            )
            diagnostics = json.loads(
                (stage_dir / "token_preflight.error.json").read_text(encoding="utf-8")
            )
            self.assertEqual(checkpoint["status"], "blocked_preflight")
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

    def test_stage_can_exclude_raw_response_json_from_review_handoff_inputs(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = tmp_path / "workflow.json"
            workflow_payload = {
                "schema_version": "responses_runner_v2.workflow_manifest.v1",
                "workflow_id": "synthetic_review_handoff_trimmed",
                "workflow_mode": "two_pass",
                "description": "Synthetic workflow that trims raw response JSON from review handoff.",
                "shared_instructions_file": SYNTHETIC_SHARED_INSTRUCTIONS,
                "defaults": {
                    "model_roles": {
                        "primary_generation": {
                            "model": "gpt-5.5-pro",
                            "reasoning_effort": "xhigh",
                            "verbosity": "high",
                            "prompt_cache_retention": "24h"
                        },
                        "structural_processing": {
                            "model": "gpt-5.5",
                            "reasoning_effort": "medium",
                            "verbosity": "medium",
                            "prompt_cache_retention": "24h"
                        }
                    },
                    "request": {
                        "background": True,
                        "store": True,
                        "parallel_tool_calls": True,
                        "max_tool_calls": 8,
                        "token_preflight": {
                            "enabled": True,
                            "max_retries": 1,
                            "retryable_http_status_codes": [429, 500, 502, 503, 504],
                            "on_retryable_service_failure": "continue_without_token_count"
                        },
                        "file_uploads": {
                            "purpose": "user_data",
                            "delete_on_completion": False
                        }
                    }
                },
                "stages": [
                    {
                        "stage_id": "proposal",
                        "stage_number": 1,
                        "title": "Proposal",
                        "task_file": SYNTHETIC_REVIEWED_STAGE1_PROMPT,
                        "input_manifest_file": SYNTHETIC_REVIEWED_STAGE1_INPUT,
                        "model_role": "primary_generation",
                        "gate": "review_required",
                        "output": {"primary_format": "text"}
                    },
                    {
                        "stage_id": "revision",
                        "stage_number": 2,
                        "title": "Revision",
                        "task_file": SYNTHETIC_REVIEWED_STAGE2_PROMPT,
                        "input_manifest_file": SYNTHETIC_REVIEWED_STAGE2_INPUT,
                        "model_role": "primary_generation",
                        "gate": "terminal",
                        "carry_forward": {
                            "review_bundle_from_stage_id": "proposal",
                            "review_bundle_include_response_artifact_json": False
                        },
                        "output": {"primary_format": "text"}
                    }
                ]
            }
            workflow_path.write_text(json.dumps(workflow_payload, indent=2) + "\n", encoding="utf-8")

            stage1 = run_workflow(
                workflow_file=workflow_path.relative_to(ROOT).as_posix(),
                runtime=RuntimeOptions(
                    run_name="synthetic-trimmed-review-handoff",
                    output_root=tmp_path.relative_to(ROOT),
                    wait=True,
                ),
                client=FakeClient(),
                root=ROOT,
            )
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            proposal_stage_dir = _stage_dir(run_manifest, "proposal")
            notes = run_dir / "proposal.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "proposal.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_review_handoff_trimmed",
                source_stage_id="proposal",
                source_run_id=run_manifest["run_id"],
                primary_artifact_markdown=(proposal_stage_dir / "artifact.md").relative_to(ROOT),
                response_artifact_json=(proposal_stage_dir / "response.final.json").relative_to(ROOT),
                reviewer_notes=notes.relative_to(ROOT),
            )

            run_workflow(
                workflow_file=workflow_path.relative_to(ROOT).as_posix(),
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="revision",
                    output_root=tmp_path.relative_to(ROOT),
                    review_bundles=[bundle.relative_to(ROOT).as_posix()],
                    dry_run=True,
                ),
                root=ROOT,
            )

            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            revision_stage_dir = _stage_dir(run_manifest, "revision")
            manifest = json.loads((revision_stage_dir / "input_manifest.json").read_text(encoding="utf-8"))
            reviewed_paths = [entry["path"] for entry in manifest["reviewed_handoff_inputs"]]

        self.assertEqual(
            reviewed_paths,
            [
                bundle.relative_to(ROOT).as_posix(),
                notes.relative_to(ROOT).as_posix(),
                (proposal_stage_dir / "artifact.md").relative_to(ROOT).as_posix(),
            ],
        )

    def test_run_workflow_can_carry_approved_handoff_markdown_ahead_of_raw_stage_artifact(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = tmp_path / "workflow.json"
            workflow_payload = {
                "schema_version": "responses_runner_v2.workflow_manifest.v1",
                "workflow_id": "synthetic_review_handoff_with_approved_markdown",
                "workflow_name": "Synthetic Reviewed Handoff With Approved Markdown",
                "workflow_mode": "two_pass",
                "description": "Synthetic workflow that carries a concise approved handoff markdown.",
                "shared_instructions_file": SYNTHETIC_SHARED_INSTRUCTIONS,
                "defaults": {
                    "model_roles": {
                        "primary_generation": {
                            "model": "gpt-5.5-pro",
                            "reasoning_effort": "xhigh",
                            "verbosity": "high",
                            "prompt_cache_retention": "24h"
                        },
                        "structural_processing": {
                            "model": "gpt-5.5",
                            "reasoning_effort": "medium",
                            "verbosity": "medium",
                            "prompt_cache_retention": "24h"
                        }
                    },
                    "request": {
                        "background": False,
                        "store": True,
                        "parallel_tool_calls": True,
                        "max_tool_calls": 4,
                        "token_preflight": {
                            "enabled": False,
                            "max_retries": 1,
                            "retryable_http_status_codes": [429],
                            "on_retryable_service_failure": "continue_without_token_count"
                        },
                        "file_uploads": {
                            "purpose": "user_data",
                            "delete_on_completion": False
                        }
                    }
                },
                "stages": [
                    {
                        "stage_id": "proposal",
                        "stage_number": 1,
                        "title": "Proposal",
                        "task_file": SYNTHETIC_REVIEWED_STAGE1_PROMPT,
                        "input_manifest_file": SYNTHETIC_REVIEWED_STAGE1_INPUT,
                        "model_role": "primary_generation",
                        "gate": "review_required",
                        "output": {"primary_format": "text"}
                    },
                    {
                        "stage_id": "revision",
                        "stage_number": 2,
                        "title": "Revision",
                        "task_file": SYNTHETIC_REVIEWED_STAGE2_PROMPT,
                        "input_manifest_file": SYNTHETIC_REVIEWED_STAGE2_INPUT,
                        "model_role": "primary_generation",
                        "gate": "terminal",
                        "carry_forward": {
                            "review_bundle_from_stage_id": "proposal",
                            "review_bundle_include_response_artifact_json": False
                        },
                        "output": {"primary_format": "text"}
                    }
                ]
            }
            workflow_path.write_text(json.dumps(workflow_payload, indent=2) + "\n", encoding="utf-8")

            stage1 = run_workflow(
                workflow_file=workflow_path.relative_to(ROOT).as_posix(),
                runtime=RuntimeOptions(
                    run_name="synthetic-approved-review-handoff",
                    output_root=tmp_path.relative_to(ROOT),
                    wait=True,
                ),
                client=FakeClient(),
                root=ROOT,
            )
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            proposal_stage_dir = _stage_dir(run_manifest, "proposal")
            notes = run_dir / "proposal.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            handoff = run_dir / "proposal.approved_handoff.md"
            handoff.write_text("# concise reviewed handoff\n", encoding="utf-8")
            bundle = run_dir / "proposal.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_review_handoff_with_approved_markdown",
                source_stage_id="proposal",
                source_run_id=run_manifest["run_id"],
                primary_artifact_markdown=(proposal_stage_dir / "artifact.md").relative_to(ROOT),
                response_artifact_json=(proposal_stage_dir / "response.final.json").relative_to(ROOT),
                reviewer_notes=notes.relative_to(ROOT),
                approved_handoff_markdown=handoff.relative_to(ROOT),
            )

            run_workflow(
                workflow_file=workflow_path.relative_to(ROOT).as_posix(),
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    stage_id="revision",
                    output_root=tmp_path.relative_to(ROOT),
                    review_bundles=[bundle.relative_to(ROOT).as_posix()],
                    dry_run=True,
                ),
                root=ROOT,
            )

            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            revision_stage_dir = _stage_dir(run_manifest, "revision")
            manifest = json.loads((revision_stage_dir / "input_manifest.json").read_text(encoding="utf-8"))
            reviewed_paths = [entry["path"] for entry in manifest["reviewed_handoff_inputs"]]

        self.assertEqual(
            reviewed_paths,
            [
                bundle.relative_to(ROOT).as_posix(),
                handoff.relative_to(ROOT).as_posix(),
                notes.relative_to(ROOT).as_posix(),
                (proposal_stage_dir / "artifact.md").relative_to(ROOT).as_posix(),
            ],
        )

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
            checkpoint = json.loads((stage_dir / "stage_checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["status"], "blocked_preflight")
            self.assertEqual(checkpoint["token_preflight"]["status"], "failed_closed")
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
            checkpoint = json.loads(
                (_stage_dir(run_manifest) / "stage_checkpoint.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                checkpoint["token_preflight"],
                {"status": "skipped_by_operator", "attempts": 0},
            )

    def test_refresh_status_only_does_not_rewrite_terminal_artifacts_or_rerun_sidecar(self) -> None:
        class RefreshOnlyClient:
            def retrieve_response(self, response_id):
                return _completed_response(response_id)

            def upload_file(self, *_args, **_kwargs):
                raise AssertionError("refresh must not upload files")

            def create_response(self, *_args, **_kwargs):
                raise AssertionError("refresh must not create a sidecar response")

            def wait_for_terminal_response(self, *_args, **_kwargs):
                raise AssertionError("refresh must not wait on a terminal response")

            def delete_file(self, *_args, **_kwargs):
                raise AssertionError("refresh must not delete uploaded files")

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-refresh-only",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
            )
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=FakeClient(),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            sentinel_main = "# sentinel main artifact\n"
            sentinel_sidecar = "# sentinel sidecar artifact\n"
            sentinel_structured = '{\n  "sentinel": true\n}\n'
            (stage_dir / "response.final.md").write_text(sentinel_main, encoding="utf-8")
            (stage_dir / "sidecar.response.md").write_text(sentinel_sidecar, encoding="utf-8")
            (stage_dir / "output.structured.json").write_text(sentinel_structured, encoding="utf-8")

            refreshed = refresh_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                client=RefreshOnlyClient(),
                root=ROOT,
            )

            self.assertEqual(refreshed["status"], "completed")
            self.assertEqual((stage_dir / "response.final.md").read_text(encoding="utf-8"), sentinel_main)
            self.assertEqual((stage_dir / "sidecar.response.md").read_text(encoding="utf-8"), sentinel_sidecar)
            self.assertEqual((stage_dir / "output.structured.json").read_text(encoding="utf-8"), sentinel_structured)
            checkpoint = json.loads((stage_dir / "stage_checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["resume_mode"], "refresh_status_only")

    def test_resume_after_refresh_materializes_missing_terminal_sidecar_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-refresh-then-resume",
                output_root=Path(tmp).relative_to(ROOT),
                wait=False,
            )
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=FakeClient(completed=False),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)

            self.assertFalse((stage_dir / "response.final.md").exists())
            self.assertFalse((stage_dir / "output.structured.json").exists())
            self.assertFalse((stage_dir / "sidecar.response.json").exists())
            self.assertFalse((stage_dir / "sidecar.response.md").exists())

            refreshed = refresh_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                client=FakeClient(),
                root=ROOT,
            )
            self.assertEqual(refreshed["status"], "pending_finalization")
            self.assertFalse((stage_dir / "response.final.md").exists())
            self.assertFalse((stage_dir / "output.structured.json").exists())
            self.assertFalse((stage_dir / "sidecar.response.json").exists())
            self.assertFalse((stage_dir / "sidecar.response.md").exists())

            resumed = resume_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                wait=False,
                poll_interval=0.1,
                max_wait_seconds=10.0,
                client=FakeClient(),
                root=ROOT,
            )
            self.assertEqual(resumed["status"], "completed")
            self.assertTrue((stage_dir / "response.final.md").exists())
            self.assertTrue((stage_dir / "output.structured.json").exists())
            self.assertTrue((stage_dir / "sidecar.response.json").exists())
            self.assertTrue((stage_dir / "sidecar.response.md").exists())
            usage_result = workflow_module.usage_report(
                run_dir=run_dir.relative_to(ROOT),
                root=ROOT,
            )
            usage_report = json.loads(
                (ROOT / usage_result["usage_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                sum(
                    attempt["lane"] == "primary"
                    for attempt in usage_report["attempts"]
                ),
                1,
            )

    def test_sidecar_uses_background_high_budget_and_persists_recovery_state(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-sidecar-state",
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
            run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            stage_summary = run_manifest["stages"][0]
            sidecar_requests = [
                request
                for request in client.create_requests
                if request.get("metadata", {}).get("kind") == "sidecar"
            ]

            self.assertEqual(len(sidecar_requests), 1)
            self.assertTrue(sidecar_requests[0]["background"])
            self.assertEqual(sidecar_requests[0]["max_output_tokens"], 128000)
            self.assertTrue((stage_dir / "sidecar.response.request.json").exists())
            self.assertTrue((stage_dir / "sidecar.response.latest.json").exists())
            self.assertTrue((stage_dir / "sidecar.response.raw.json").exists())
            request_payload = json.loads((stage_dir / "sidecar.response.request.json").read_text(encoding="utf-8"))
            submission_journal = json.loads(
                (stage_dir / "sidecar.response.submissions.json").read_text(encoding="utf-8")
            )
            sidecar_uploads = json.loads(
                (stage_dir / "sidecar.response.uploads.json").read_text(encoding="utf-8")
            )
            primary_usage_attempt = json.loads(
                (stage_dir / "usage_attempt.json").read_text(encoding="utf-8")
            )
            sidecar_usage_attempts = json.loads(
                (stage_dir / "sidecar.response.attempts.json").read_text(encoding="utf-8")
            )["attempts"]
            self.assertTrue(request_payload["background"])
            self.assertEqual(request_payload["max_output_tokens"], 128000)
            self.assertEqual(
                request_payload["prompt_cache_key"],
                build_prompt_cache_key(
                    f"stable:v1:synthetic_one_pass:{RUNNER_VERSION}:gpt-5.6",
                    "structural_processing",
                ),
            )
            self.assertEqual(submission_journal["attempts"][0]["status"], "submitted")
            self.assertEqual(
                submission_journal["attempts"][0]["request_sha256"],
                sha256_file(stage_dir / "sidecar.response.request.json"),
            )
            self.assertEqual(sidecar_uploads["files"][0]["status"], "uploaded")
            self.assertIsNone(primary_usage_attempt["usage"])
            self.assertIsNone(sidecar_usage_attempts[0]["usage"])
            usage_result = workflow_module.usage_report(
                run_dir=result["run_dir"],
                root=ROOT,
            )
            usage_report = json.loads(
                (ROOT / usage_result["usage_report_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(
                [attempt["lane"] for attempt in usage_report["attempts"]],
                ["primary", "sidecar"],
            )
            for attempt in usage_report["attempts"]:
                self.assertTrue(
                    all(
                        attempt["usage"][field] is None
                        for field in telemetry.USAGE_COUNTER_FIELDS
                    )
                )
            for totals in [*usage_report["by_lane"].values(), usage_report["totals"]]:
                self.assertTrue(
                    all(totals[field] is None for field in telemetry.USAGE_COUNTER_FIELDS)
                )
            self.assertEqual(
                stage_summary["sidecar_response_json_path"],
                (stage_dir / "sidecar.response.json").relative_to(ROOT).as_posix(),
            )
            self.assertEqual(
                stage_summary["sidecar_response_markdown_path"],
                (stage_dir / "sidecar.response.md").relative_to(ROOT).as_posix(),
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

    def test_resume_retries_legacy_output_limited_sidecar_with_current_budget(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-sidecar-output-limit-retry",
                output_root=Path(tmp).relative_to(ROOT),
                wait=False,
            )
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=FakeClient(completed=False),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            (stage_dir / "sidecar.response.latest.json").write_text(
                json.dumps(
                    {
                        "id": "resp_legacy_sidecar",
                        "status": "incomplete",
                        "max_output_tokens": 16000,
                        "incomplete_details": {"reason": "max_output_tokens"},
                        "output": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (stage_dir / "sidecar.response.request.json").write_text(
                json.dumps({"max_output_tokens": 16000}, indent=2) + "\n",
                encoding="utf-8",
            )
            client = FakeClient()

            resumed = resume_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                wait=False,
                poll_interval=0.1,
                max_wait_seconds=10.0,
                client=client,
                root=ROOT,
            )
            sidecar_requests = [
                request
                for request in client.create_requests
                if request.get("metadata", {}).get("kind") == "sidecar"
            ]

            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(len(sidecar_requests), 1)
            self.assertEqual(sidecar_requests[0]["max_output_tokens"], 128000)
            latest = json.loads((stage_dir / "sidecar.response.latest.json").read_text(encoding="utf-8"))
            self.assertEqual(latest["id"], "resp_sidecar")
            self.assertTrue((stage_dir / "sidecar.response.json").exists())
            self.assertTrue((stage_dir / "output.structured.json").exists())

    def test_resume_retries_retryable_failed_sidecar_and_records_attempt(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-sidecar-server-error-retry",
                output_root=Path(tmp).relative_to(ROOT),
                wait=False,
            )
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=FakeClient(completed=False),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            (stage_dir / "sidecar.response.latest.json").write_text(
                json.dumps(
                    {
                        "id": "resp_failed_sidecar",
                        "status": "failed",
                        "max_output_tokens": 128000,
                        "error": {"code": "server_error", "message": "Synthetic transient failure"},
                        "output": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            client = FakeClient()

            resumed = resume_stage(
                run_dir=run_dir.relative_to(ROOT),
                stage_id="draft_summary",
                wait=False,
                poll_interval=0.1,
                max_wait_seconds=10.0,
                client=client,
                root=ROOT,
            )
            sidecar_requests = [
                request
                for request in client.create_requests
                if request.get("metadata", {}).get("kind") == "sidecar"
            ]
            attempts = json.loads((stage_dir / "sidecar.response.attempts.json").read_text(encoding="utf-8"))

            self.assertEqual(resumed["status"], "completed")
            self.assertEqual(len(sidecar_requests), 1)
            self.assertEqual(
                [attempt["response_id"] for attempt in attempts["attempts"]],
                ["resp_failed_sidecar", "resp_sidecar"],
            )
            self.assertEqual(attempts["attempts"][0]["retry_reason"], "retryable_terminal_server_error")
            self.assertEqual(
                [attempt["retry_count"] for attempt in attempts["attempts"]],
                [None, 1],
            )
            self.assertTrue((stage_dir / "sidecar.response.json").exists())

    def test_terminal_cleanup_tracks_and_deletes_sidecar_uploads(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-sidecar-cleanup",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
                delete_uploaded_files_on_complete=True,
                file_expires_after="3600",
            )
            client = FakeClient()
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=client,
                root=ROOT,
            )
            run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))
            stage_dir = _stage_dir(run_manifest)
            uploads_payload = json.loads((stage_dir / "uploads.json").read_text(encoding="utf-8"))

            self.assertEqual(len(client.delete_calls), 5)
            self.assertEqual(len(uploads_payload["files"]), 5)
            self.assertEqual(
                uploads_payload["file_expiration_policy"],
                {"anchor": "created_at", "seconds": 3600},
            )
            sidecar_files = [
                record
                for record in uploads_payload["files"]
                if str(record.get("attachment_role", "")).startswith("Sidecar ")
            ]
            self.assertEqual(len(sidecar_files), 1)
            self.assertTrue(all(record.get("delete_status") == "deleted" for record in uploads_payload["files"]))
            self.assertTrue(
                all(
                    request["file_expiration_policy"] == {"anchor": "created_at", "seconds": 3600}
                    for request in client.upload_requests[-2:]
                )
            )

    def test_review_required_stage_blocks_without_bundle(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-reviewed",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
            )
            client = FakeClient()
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=runtime,
                client=client,
                root=ROOT,
            )
            with self.assertRaises(SystemExit):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=Path(result["run_dir"]),
                        output_root=Path(tmp).relative_to(ROOT),
                        wait=True,
                    ),
                    client=client,
                    root=ROOT,
                )

    def test_failed_stage_with_real_artifacts_can_progress_via_approved_bundle_without_rewriting_status(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output_root = Path(tmp).relative_to(ROOT)
            client = SequenceClient([_failed_response("resp_stage1"), _completed_response("resp_stage2")])

            stage1 = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_name="synthetic-failed-reviewed-handoff",
                    output_root=output_root,
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            run_id = run_manifest["run_id"]
            proposal_stage_dir = _stage_dir(run_manifest, "proposal")

            notes = run_dir / "stage1.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "stage1.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id=run_id,
                primary_artifact_markdown=(proposal_stage_dir / "artifact.md").relative_to(ROOT),
                response_artifact_json=(proposal_stage_dir / "response.final.json").relative_to(ROOT),
                reviewer_notes=notes.relative_to(ROOT),
            )

            stage2 = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    output_root=output_root,
                    stage_id="revision",
                    review_bundles=[bundle.relative_to(ROOT).as_posix()],
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )

            self.assertIn(stage2["status"], {"waiting_for_review", "running"})
            updated_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            proposal_summary = updated_manifest["stages"][0]
            self.assertEqual(proposal_summary["status"], "failed_complete")
            self.assertTrue(proposal_summary["review_approved"])
            self.assertEqual(proposal_summary["approved_from_status"], "failed_complete")
            self.assertEqual(proposal_summary["review_bundle_path"], bundle.relative_to(ROOT).as_posix())

    def test_blocked_stage_cannot_progress_via_review_bundle(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output_root = Path(tmp).relative_to(ROOT)
            stage1 = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_name="synthetic-blocked-handoff",
                    output_root=output_root,
                    wait=True,
                ),
                client=FakeClient(),
                root=ROOT,
            )
            run_dir = ROOT / stage1["run_dir"]
            manifest_path = run_dir / "run_manifest.json"
            run_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            proposal_stage_dir = _stage_dir(run_manifest, "proposal")
            run_manifest["stages"][0]["status"] = "blocked"
            manifest_path.write_text(json.dumps(run_manifest, indent=2) + "\n", encoding="utf-8")

            notes = run_dir / "stage1.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "stage1.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id=run_manifest["run_id"],
                primary_artifact_markdown=(proposal_stage_dir / "artifact.md").relative_to(ROOT),
                response_artifact_json=(proposal_stage_dir / "response.final.json").relative_to(ROOT),
                reviewer_notes=notes.relative_to(ROOT),
            )

            with self.assertRaises(SystemExit):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=run_dir.relative_to(ROOT),
                        output_root=output_root,
                        stage_id="revision",
                        review_bundles=[bundle.relative_to(ROOT).as_posix()],
                        wait=True,
                    ),
                    client=FakeClient(),
                    root=ROOT,
                )

    def test_review_bundle_must_match_recorded_source_artifacts(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output_root = Path(tmp).relative_to(ROOT)
            stage1 = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_name="synthetic-bundle-provenance",
                    output_root=output_root,
                    wait=True,
                ),
                client=FakeClient(),
                root=ROOT,
            )
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))

            unrelated_md = run_dir / "unrelated.md"
            unrelated_json = run_dir / "unrelated.json"
            unrelated_md.write_text("# unrelated\n", encoding="utf-8")
            unrelated_json.write_text('{"id":"wrong"}\n', encoding="utf-8")
            notes = run_dir / "stage1.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "stage1.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id=run_manifest["run_id"],
                primary_artifact_markdown=unrelated_md.relative_to(ROOT),
                response_artifact_json=unrelated_json.relative_to(ROOT),
                reviewer_notes=notes.relative_to(ROOT),
            )

            with self.assertRaises(SystemExit):
                run_workflow(
                    workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                    runtime=RuntimeOptions(
                        run_dir=run_dir.relative_to(ROOT),
                        output_root=output_root,
                        stage_id="revision",
                        review_bundles=[bundle.relative_to(ROOT).as_posix()],
                        wait=True,
                    ),
                    client=FakeClient(),
                    root=ROOT,
                )


if __name__ == "__main__":
    unittest.main()
