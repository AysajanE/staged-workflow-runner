from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from automation.responses_runner_v2 import artifacts, run_contract, sidecar, telemetry
from automation.responses_runner_v2.contracts import sha256_file, write_json
from automation.responses_runner_v2.data_lifecycle import purge_evidence
from automation.responses_runner_v2.locking import RunLockError, run_lock
from automation.responses_runner_v2.openai_client import (
    OUTCOME_AMBIGUOUS,
    OUTCOME_KNOWN_REJECTED,
    ApiError,
    OpenAIClient,
    _encode_multipart,
)


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return self.body


class _AmbiguousSidecarClient:
    def __init__(self) -> None:
        self.upload_calls = 0
        self.create_calls = 0

    def upload_file(self, _path, purpose, file_expiration_policy=None):
        self.upload_calls += 1
        return {"id": f"file_{self.upload_calls}", "purpose": purpose, "created_at": 1}

    def create_response(self, _payload):
        self.create_calls += 1
        raise ApiError("timeout after possible acceptance", outcome_certainty=OUTCOME_AMBIGUOUS)


class _RetryableSidecarClient:
    def __init__(self) -> None:
        self.upload_calls = 0
        self.create_calls = 0

    def upload_file(self, _path, purpose, file_expiration_policy=None):
        self.upload_calls += 1
        return {"id": f"file_{self.upload_calls}", "purpose": purpose, "created_at": 1}

    def create_response(self, payload):
        self.create_calls += 1
        if self.create_calls == 1:
            return {
                "id": "resp_sidecar_failed",
                "status": "failed",
                "model": "gpt-5.6",
                "max_output_tokens": payload["max_output_tokens"],
                "error": {"code": "server_error", "message": "retry me"},
                "output": [],
            }
        return {
            "id": "resp_sidecar_completed",
            "status": "completed",
            "model": "gpt-5.6",
            "max_output_tokens": payload["max_output_tokens"],
            "output_parsed": {
                "summary_version": "responses_runner_v2.synthetic_summary.v1",
                "workflow_id": "workflow",
                "final_assessment": "complete",
                "key_points": ["complete"],
                "open_questions": [],
            },
            "output": [],
        }


class _KnownRejectedThenCompletedSidecarClient(_RetryableSidecarClient):
    def create_response(self, payload):
        if self.create_calls == 0:
            self.create_calls += 1
            raise ApiError(
                "request rejected",
                outcome_certainty=OUTCOME_KNOWN_REJECTED,
            )
        return super().create_response(payload)


class ArtifactDurabilityTests(unittest.TestCase):
    def test_contract_directory_projection_matches_attachment_safety_rules(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "inputs"
            target.mkdir()
            allowed = target / "allowed.txt"
            allowed.write_text("allowed", encoding="utf-8")
            ignored = target / "ignored.txt"
            ignored.write_text("ignored", encoding="utf-8")
            secret = target / ".env"
            secret.write_text("secret", encoding="utf-8")
            outside = root.parent / f"{root.name}-outside.txt"
            outside.write_text("outside", encoding="utf-8")
            symlink = target / "outside-link.txt"
            symlink.symlink_to(outside)
            ignored_rel = ignored.relative_to(root).as_posix()
            try:
                with mock.patch.object(
                    run_contract.attachments,
                    "_git_ignored_entries",
                    return_value=(ignored_rel,),
                ):
                    with self.assertRaisesRegex(SystemExit, "Sensitive attachment filename"):
                        run_contract._expand_member("runtime:primary:0", target, root=root)
                    secret.unlink()
                    with self.assertRaisesRegex(SystemExit, "escapes workspace root"):
                        run_contract._expand_member("runtime:primary:0", target, root=root)
                    symlink.unlink()
                    members = run_contract._expand_member(
                        "runtime:primary:0",
                        target,
                        root=root,
                    )
            finally:
                outside.unlink(missing_ok=True)
            self.assertEqual(members, [("runtime:primary:0:allowed.txt", allowed.resolve())])

    def test_atomic_state_write_is_owner_only_under_permissive_umask(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "state" / "record.json"
            previous_umask = os.umask(0)
            try:
                write_json(path, {"status": "safe"})
            finally:
                os.umask(previous_umask)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode), 0o700)

    def test_same_timestamp_run_creation_never_reuses_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            output_root = root / "runs"
            with mock.patch.object(artifacts, "timestamp_slug", return_value="2026-08-11_120000"):
                first = artifacts.create_run_dir(
                    root=root,
                    output_root=output_root,
                    run_name="job",
                    workflow_id="workflow",
                )
                second = artifacts.create_run_dir(
                    root=root,
                    output_root=output_root,
                    run_name="job",
                    workflow_id="workflow",
                )
            self.assertNotEqual(first, second)
            self.assertTrue(first.is_dir())
            self.assertTrue(second.is_dir())

    def test_attempt_allocation_is_monotonic_and_does_not_modify_prior_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            first_number, first_paths = artifacts.allocate_stage_attempt(run_dir, 1, "draft")
            marker = first_paths["stage_dir"] / "marker.txt"
            marker.write_text("original", encoding="utf-8")

            second_number, second_paths = artifacts.allocate_stage_attempt(run_dir, 1, "draft")

            self.assertEqual(first_number, 1)
            self.assertEqual(second_number, 2)
            self.assertEqual(first_paths["stage_dir"].name, "attempt_001")
            self.assertEqual(second_paths["stage_dir"].name, "attempt_002")
            self.assertEqual(marker.read_text(encoding="utf-8"), "original")

    def test_legacy_stage_paths_remain_explicitly_available(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            legacy = artifacts.build_stage_paths(run_dir, 1, "draft")
            attempt = artifacts.build_stage_paths(run_dir, 1, "draft", attempt_number=1)
            self.assertEqual(legacy["stage_dir"], legacy["stage_root"])
            self.assertEqual(attempt["stage_dir"], legacy["stage_root"] / "attempt_001")

    def test_run_lock_reports_a_clean_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            with run_lock(run_dir):
                with self.assertRaises(RunLockError):
                    with run_lock(run_dir):
                        self.fail("second lock unexpectedly acquired")

    def test_run_lock_enforces_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory) / "run"
            with run_lock(run_dir) as lock_path:
                self.assertEqual(stat.S_IMODE(run_dir.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE(lock_path.stat().st_mode), 0o600)

    def test_clean_artifact_is_idempotent_but_not_replaceable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "artifact.md"
            response = {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Final answer"}],
                    }
                ]
            }
            artifacts.write_clean_artifact(path, response)
            artifacts.write_clean_artifact(path, response)
            self.assertEqual(path.read_text(encoding="utf-8"), "Final answer\n")
            changed = {
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Changed"}],
                    }
                ]
            }
            with self.assertRaises(FileExistsError):
                artifacts.write_clean_artifact(path, changed)

    def test_submission_intent_is_idempotent_but_not_replaceable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            paths = artifacts.build_stage_paths(
                Path(temporary_directory) / "run",
                1,
                "draft",
                attempt_number=1,
            )
            intent = {"attempt_id": "attempt_001", "request_sha256": "a" * 64}
            artifacts.write_submission_intent(paths, intent)
            artifacts.write_submission_intent(paths, intent)
            with self.assertRaises(FileExistsError):
                artifacts.write_submission_intent(
                    paths,
                    {"attempt_id": "attempt_001", "request_sha256": "b" * 64},
                )

    def test_responses_usage_fields_are_rendered(self) -> None:
        response = {
            "usage": {
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "input_tokens_details": {"cached_tokens": 7, "cache_write_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 4},
            }
        }
        self.assertEqual(
            artifacts.normalize_response_usage(response),
            {
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "cached_tokens": 7,
                "cache_write_tokens": 3,
                "reasoning_tokens": 4,
            },
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            markdown_path = root / "response.final.md"
            artifacts.write_response_pair(
                root=root,
                markdown_path=markdown_path,
                json_path=root / "response.final.json",
                title="Stage Output",
                workflow_id="workflow",
                run_id="run",
                stage_id="draft",
                stage_number=1,
                response_json=response,
                requested_text_format="text",
            )
            rendered = markdown_path.read_text(encoding="utf-8")
            self.assertIn("- cached_tokens: 7", rendered)
            self.assertIn("- cache_write_tokens: 3", rendered)
            self.assertIn("- reasoning_tokens: 4", rendered)

    def test_purge_is_hash_bound_and_resume_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            run_dir = root / "run"
            attempt_dir = run_dir / "stages" / "01_draft" / "attempt_001"
            attempt_dir.mkdir(parents=True)
            request = attempt_dir / "request_payload.json"
            artifact = attempt_dir / "artifact.md"
            request.write_text('{"model":"gpt-5.6"}\n', encoding="utf-8")
            artifact.write_text("preserve me\n", encoding="utf-8")

            result = purge_evidence(
                root=root,
                target_dir=run_dir,
                categories=["raw_request"],
                reason="retention window elapsed",
            )
            self.assertFalse(request.exists())
            self.assertTrue(artifact.exists())
            tombstone_path = root / result["tombstone_path"]
            tombstone = json.loads(tombstone_path.read_text(encoding="utf-8"))
            self.assertEqual(tombstone["status"], "completed")
            self.assertEqual(tombstone["records"][0]["status"], "deleted")

            resumed = purge_evidence(
                root=root,
                target_dir=run_dir,
                resume_tombstone=tombstone_path,
            )
            self.assertEqual(resumed["tombstone_path"], result["tombstone_path"])


class OpenAIClientRetryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = OpenAIClient(api_key="test", request_max_retries=5)

    @mock.patch("automation.responses_runner_v2.openai_client.request.urlopen")
    def test_create_response_never_retries_ambiguous_transport_failure(self, urlopen) -> None:
        urlopen.side_effect = TimeoutError("timed out after acceptance")
        with self.assertRaises(ApiError) as raised:
            self.client.create_response({"model": "gpt-5.6"})
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(raised.exception.outcome_certainty, OUTCOME_AMBIGUOUS)
        self.assertTrue(raised.exception.outcome_unknown)

    @mock.patch("automation.responses_runner_v2.openai_client.time.sleep")
    @mock.patch("automation.responses_runner_v2.openai_client.request.urlopen")
    def test_safe_get_retries_transport_failure(self, urlopen, _sleep) -> None:
        urlopen.side_effect = [
            error.URLError("temporary"),
            _FakeHttpResponse({"id": "resp_1", "status": "in_progress"}),
        ]
        response = self.client.retrieve_response("resp_1")
        self.assertEqual(response["id"], "resp_1")
        self.assertEqual(urlopen.call_count, 2)

    @mock.patch("automation.responses_runner_v2.openai_client.request.urlopen")
    def test_cancel_response_never_retries_and_escapes_id(self, urlopen) -> None:
        urlopen.side_effect = error.URLError("lost response")
        with self.assertRaises(ApiError) as raised:
            self.client.cancel_response("resp/unsafe")
        self.assertEqual(urlopen.call_count, 1)
        request_object = urlopen.call_args.args[0]
        self.assertTrue(request_object.full_url.endswith("/responses/resp%2Funsafe/cancel"))
        self.assertEqual(raised.exception.outcome_certainty, OUTCOME_AMBIGUOUS)

    def test_multipart_body_streams_file_in_bounded_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "large.txt"
            path.write_bytes(b"x" * (2 * 1024 * 1024 + 17))
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("full read")):
                boundary, body = _encode_multipart({"purpose": "user_data"}, "file", path)
                chunks = list(body)
            self.assertIn(boundary.encode("utf-8"), chunks[0])
            self.assertEqual(sum(len(chunk) for chunk in chunks), body.content_length)
            self.assertLessEqual(max(len(chunk) for chunk in chunks[1:-1]), 1024 * 1024)


class SidecarDurabilityTests(unittest.TestCase):
    def test_known_rejected_sidecar_create_is_recorded_once_before_safe_retry(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary_directory:
            root = Path(__file__).resolve().parents[2]
            base = Path(temporary_directory)
            source_markdown = base / "artifact.md"
            source_json = base / "response.final.json"
            source_markdown.write_text("clean source\n", encoding="utf-8")
            source_json.write_text('{"id":"primary"}\n', encoding="utf-8")
            client = _KnownRejectedThenCompletedSidecarClient()
            kwargs = {
                "root": root,
                "client": client,
                "workflow_id": "workflow",
                "run_id": "run",
                "stage_id": "stage",
                "stage_number": 1,
                "structural_model": "gpt-5.6",
                "reasoning_effort": "high",
                "prompt_cache_retention": None,
                "schema_file": "automation/examples/responses_runner_v2_synthetic/schemas/synthetic_summary.schema.json",
                "schema_name": "synthetic_summary",
                "response_markdown_path": source_markdown,
                "response_json_path": source_json,
                "sidecar_response_json_path": base / "sidecar.response.json",
                "sidecar_response_markdown_path": base / "sidecar.response.md",
                "structured_output_path": base / "output.structured.json",
                "service_tier": None,
                "safety_identifier": None,
                "file_expiration_policy": None,
                "delete_uploaded_files_on_complete": False,
            }

            with self.assertRaises(ApiError):
                sidecar.run_sidecar_processing(**kwargs)
            first_attempts = json.loads(
                (base / "sidecar.response.attempts.json").read_text(encoding="utf-8")
            )["attempts"]
            self.assertEqual(len(first_attempts), 1)
            self.assertEqual(first_attempts[0]["status"], "known_rejected")
            self.assertIsInstance(first_attempts[0]["request_wall_ms"], int)
            self.assertEqual(first_attempts[0]["retry_count"], 0)
            self.assertIsNone(first_attempts[0]["usage"])

            sidecar.run_sidecar_processing(**kwargs)
            attempts = json.loads(
                (base / "sidecar.response.attempts.json").read_text(encoding="utf-8")
            )["attempts"]
            self.assertEqual(
                [attempt["status"] for attempt in attempts],
                ["known_rejected", "completed"],
            )
            self.assertEqual([attempt["retry_count"] for attempt in attempts], [0, 1])
            report = telemetry.build_usage_report(attempts)
            self.assertEqual(report["totals"]["attempt_count"], 2)
            self.assertIsNone(report["totals"]["total_tokens"])
            self.assertEqual(client.create_calls, 2)

    def test_retryable_terminal_sidecar_attempts_are_recorded_once_with_retry_counts(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary_directory:
            root = Path(__file__).resolve().parents[2]
            base = Path(temporary_directory)
            source_markdown = base / "artifact.md"
            source_json = base / "response.final.json"
            source_markdown.write_text("clean source\n", encoding="utf-8")
            source_json.write_text('{"id":"primary"}\n', encoding="utf-8")
            sidecar_json = base / "sidecar.response.json"
            client = _RetryableSidecarClient()
            kwargs = {
                "root": root,
                "client": client,
                "workflow_id": "workflow",
                "run_id": "run",
                "stage_id": "stage",
                "stage_number": 1,
                "structural_model": "gpt-5.6",
                "reasoning_effort": "high",
                "prompt_cache_retention": None,
                "schema_file": "automation/examples/responses_runner_v2_synthetic/schemas/synthetic_summary.schema.json",
                "schema_name": "synthetic_summary",
                "response_markdown_path": source_markdown,
                "response_json_path": source_json,
                "sidecar_response_json_path": sidecar_json,
                "sidecar_response_markdown_path": base / "sidecar.response.md",
                "structured_output_path": base / "output.structured.json",
                "service_tier": None,
                "safety_identifier": None,
                "file_expiration_policy": None,
                "delete_uploaded_files_on_complete": False,
            }

            with self.assertRaisesRegex(SystemExit, "retry me"):
                sidecar.run_sidecar_processing(**kwargs)
            sidecar.run_sidecar_processing(**kwargs)

            attempts = json.loads(
                (base / "sidecar.response.attempts.json").read_text(encoding="utf-8")
            )["attempts"]
            self.assertEqual(
                [attempt["response_id"] for attempt in attempts],
                ["resp_sidecar_failed", "resp_sidecar_completed"],
            )
            self.assertEqual(len({attempt["response_id"] for attempt in attempts}), 2)
            self.assertEqual([attempt["retry_count"] for attempt in attempts], [0, 1])
            self.assertEqual(
                attempts[0]["retry_reason"],
                "retryable_terminal_server_error",
            )
            usage_report = telemetry.build_usage_report(attempts)
            self.assertEqual(usage_report["totals"]["attempt_count"], 2)
            self.assertEqual(
                [attempt["retry_count"] for attempt in usage_report["attempts"]],
                [0, 1],
            )
            self.assertEqual(client.create_calls, 2)

    def test_ambiguous_create_is_journaled_and_never_implicitly_retried(self) -> None:
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as temporary_directory:
            root = Path(__file__).resolve().parents[2]
            base = Path(temporary_directory)
            source_markdown = base / "artifact.md"
            source_json = base / "response.final.json"
            source_markdown.write_text("clean source\n", encoding="utf-8")
            source_json.write_text('{"id":"primary"}\n', encoding="utf-8")
            sidecar_json = base / "sidecar.response.json"
            client = _AmbiguousSidecarClient()

            kwargs = {
                "root": root,
                "client": client,
                "workflow_id": "workflow",
                "run_id": "run",
                "stage_id": "stage",
                "stage_number": 1,
                "structural_model": "gpt-5.6",
                "reasoning_effort": "high",
                "prompt_cache_retention": None,
                "schema_file": "automation/examples/responses_runner_v2_synthetic/schemas/synthetic_summary.schema.json",
                "schema_name": "synthetic_summary",
                "response_markdown_path": source_markdown,
                "response_json_path": source_json,
                "sidecar_response_json_path": sidecar_json,
                "sidecar_response_markdown_path": base / "sidecar.response.md",
                "structured_output_path": base / "output.structured.json",
                "service_tier": None,
                "safety_identifier": None,
                "file_expiration_policy": None,
                "delete_uploaded_files_on_complete": False,
            }
            with self.assertRaises(ApiError):
                sidecar.run_sidecar_processing(**kwargs)

            request_path = base / "sidecar.response.request.json"
            submissions_path = base / "sidecar.response.submissions.json"
            submissions = json.loads(submissions_path.read_text(encoding="utf-8"))
            self.assertEqual(submissions["attempts"][-1]["status"], "submission_outcome_unknown")
            self.assertEqual(submissions["attempts"][-1]["request_sha256"], sha256_file(request_path))
            attempts_path = base / "sidecar.response.attempts.json"
            attempts = json.loads(attempts_path.read_text(encoding="utf-8"))["attempts"]
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0]["status"], "submission_outcome_unknown")
            self.assertIsInstance(attempts[0]["request_wall_ms"], int)
            self.assertEqual(attempts[0]["retry_count"], 0)
            self.assertIsNone(attempts[0]["usage"])
            self.assertEqual(
                telemetry.build_usage_report(attempts)["totals"]["attempt_count"],
                1,
            )
            self.assertEqual(client.create_calls, 1)
            self.assertEqual(client.upload_calls, 1)

            with self.assertRaisesRegex(SystemExit, "submission outcome is unknown"):
                sidecar.run_sidecar_processing(**kwargs)
            self.assertEqual(
                len(json.loads(attempts_path.read_text(encoding="utf-8"))["attempts"]),
                1,
            )
            self.assertEqual(client.create_calls, 1)
            self.assertEqual(client.upload_calls, 1)


if __name__ == "__main__":
    unittest.main()
