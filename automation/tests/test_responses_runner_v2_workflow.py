from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2.contracts import RuntimeOptions
from automation.responses_runner_v2.openai_client import ApiError
from automation.responses_runner_v2.review_bundle import create_review_bundle
from automation.responses_runner_v2.workflow import refresh_stage, resume_stage, run_workflow


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


def _completed_response(response_id: str, *, model: str = "gpt-5.4-pro") -> dict:
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
                "content": [{"type": "output_text", "text": "Synthetic response"}],
            }
        ],
    }


def _in_progress_response(response_id: str, *, model: str = "gpt-5.4-pro") -> dict:
    return {
        "id": response_id,
        "status": "in_progress",
        "model": model,
        "background": True,
        "store": True,
        "created_at": 1773752598,
        "output": [],
    }


def _failed_response(response_id: str, *, model: str = "gpt-5.4-pro") -> dict:
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


class FakeClient:
    def __init__(self, *, token_error: ApiError | None = None, completed: bool = True) -> None:
        self.token_error = token_error
        self.completed = completed
        self.upload_count = 0
        self.upload_requests: list[dict] = []
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
        if payload["text"]["format"]["type"] == "json_schema":
            return {
                "id": "resp_sidecar",
                "status": "completed",
                "model": "gpt-5.4",
                "background": False,
                "store": True,
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
        return {"input_tokens": 123}

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


class ResponsesRunnerV2WorkflowTests(unittest.TestCase):
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
            stage_dir = ROOT / run_manifest["stages"][0]["stage_dir"]
            request_payload = json.loads((stage_dir / "request_payload.json").read_text(encoding="utf-8"))
            self.assertTrue((stage_dir / "request_payload.json").exists())
            self.assertTrue((stage_dir / "stage_checkpoint.json").exists())
            self.assertEqual(run_manifest["status"], "created")
            self.assertNotIn("attachment_role_blocks", request_payload)

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
                            "model": "gpt-5.4-pro",
                            "reasoning_effort": "xhigh",
                            "verbosity": "high"
                        },
                        "structural_processing": {
                            "model": "gpt-5.4-mini",
                            "reasoning_effort": "medium",
                            "verbosity": "medium"
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
            notes = run_dir / "proposal.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "proposal.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_review_handoff_trimmed",
                source_stage_id="proposal",
                source_run_id=run_manifest["run_id"],
                primary_artifact_markdown=(run_dir / "stages/01_proposal/response.final.md").relative_to(ROOT),
                response_artifact_json=(run_dir / "stages/01_proposal/response.final.json").relative_to(ROOT),
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

            revision_stage_dir = run_dir / "stages/02_revision"
            manifest = json.loads((revision_stage_dir / "input_manifest.json").read_text(encoding="utf-8"))
            reviewed_paths = [entry["path"] for entry in manifest["reviewed_handoff_inputs"]]

        self.assertEqual(
            reviewed_paths,
            [
                bundle.relative_to(ROOT).as_posix(),
                (run_dir / "stages/01_proposal/response.final.md").relative_to(ROOT).as_posix(),
                notes.relative_to(ROOT).as_posix(),
            ],
        )

    def test_token_preflight_retryable_failure_can_continue_without_hard_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-preflight-fallback",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
            )
            client = FakeClient(token_error=ApiError("retryable", status_code=503))
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=client,
                root=ROOT,
            )
            run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))
            stage_dir = ROOT / run_manifest["stages"][0]["stage_dir"]
            checkpoint = json.loads((stage_dir / "stage_checkpoint.json").read_text(encoding="utf-8"))
            self.assertEqual(
                checkpoint["token_preflight"]["status"],
                "continued_after_retryable_service_failure",
            )
            self.assertTrue((stage_dir / "token_preflight.error.json").exists())

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
            stage_dir = run_dir / "stages/01_draft_summary"
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
            stage_dir = ROOT / run_manifest["stages"][0]["stage_dir"]
            uploads_payload = json.loads((stage_dir / "uploads.json").read_text(encoding="utf-8"))

            self.assertEqual(len(client.delete_calls), 6)
            self.assertEqual(len(uploads_payload["files"]), 6)
            self.assertEqual(
                uploads_payload["file_expiration_policy"],
                {"anchor": "created_at", "seconds": 3600},
            )
            sidecar_files = [
                record
                for record in uploads_payload["files"]
                if str(record.get("attachment_role", "")).startswith("Sidecar ")
            ]
            self.assertEqual(len(sidecar_files), 2)
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

            notes = run_dir / "stage1.review.md"
            notes.write_text("# approved\n", encoding="utf-8")
            bundle = run_dir / "stage1.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id=run_id,
                primary_artifact_markdown=(run_dir / "stages/01_proposal/response.final.md").relative_to(ROOT),
                response_artifact_json=(run_dir / "stages/01_proposal/response.final.json").relative_to(ROOT),
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
            self.assertEqual(proposal_summary["status"], "failed")
            self.assertTrue(proposal_summary["review_approved"])
            self.assertEqual(proposal_summary["approved_from_status"], "failed")
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
                primary_artifact_markdown=(run_dir / "stages/01_proposal/response.final.md").relative_to(ROOT),
                response_artifact_json=(run_dir / "stages/01_proposal/response.final.json").relative_to(ROOT),
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
