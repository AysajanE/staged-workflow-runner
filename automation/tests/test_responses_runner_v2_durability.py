from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib import error

from automation.responses_runner_v2 import artifacts
from automation.responses_runner_v2.contracts import sha256_file, write_json
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


class ArtifactDurabilityTests(unittest.TestCase):
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


class InputTokenCountPayloadTests(unittest.TestCase):
    def test_count_input_tokens_projects_payload_onto_count_endpoint_fields(self) -> None:
        client = OpenAIClient(api_key="test")
        create_payload = {
            "model": "gpt-5.6",
            "input": [{"role": "user", "content": "hello"}],
            "instructions": "instructions",
            "reasoning": {"effort": "xhigh", "mode": "pro"},
            "text": {"verbosity": "high"},
            "tools": [],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "truncation": "disabled",
            "background": True,
            "store": True,
            "max_output_tokens": 48000,
            "metadata": {"workflow_id": "w"},
            "prompt_cache_key": "key",
            "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
            "service_tier": "default",
            "safety_identifier": "safe",
        }
        with mock.patch.object(
            OpenAIClient, "json_request", return_value={"input_tokens": 7}
        ) as json_request:
            result = client.count_input_tokens_once(create_payload)

        self.assertEqual(result, {"input_tokens": 7})
        sent = json_request.call_args.kwargs["payload"]
        self.assertEqual(
            set(sent),
            {
                "model",
                "input",
                "instructions",
                "reasoning",
                "text",
                "tools",
                "tool_choice",
                "parallel_tool_calls",
                "truncation",
            },
        )
        self.assertEqual(json_request.call_args.args[:2], ("POST", "/responses/input_tokens"))
