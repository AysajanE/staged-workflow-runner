from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2 import attachments, validators


ROOT = Path(__file__).resolve().parents[2]


class _FailingUploadClient:
    def upload_file(self, *_args, **_kwargs):
        raise TimeoutError("ambiguous upload timeout")


class _RecordingUploadClient:
    def __init__(self) -> None:
        self.calls = 0

    def upload_file(self, path, purpose, file_expiration_policy=None):
        self.calls += 1
        return {"id": f"file_{self.calls}", "purpose": purpose, "created_at": 1}


class _RecordingDeleteClient:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def delete_file(self, file_id):
        self.calls.append(file_id)
        return {"id": file_id, "deleted": True}


def _valid_playbook() -> str:
    return "\n".join(
        [
            "# Delivery Playbook",
            "",
            "Context for the staged work.",
            "",
            "## 1. Phase Overview",
            "",
            "One bounded phase.",
            "",
            "## 2. Execution Items",
            "",
            "| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | required_verification_commands |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            "| 01 | build | Implement change | Required now | agent | none | src/app.py | src/app.py | tests pass | src | true | python -m unittest |",
            "",
            "## 3. Phase Details",
            "",
            "Details.",
            "",
            "## 4. Shared Guidance",
            "",
            "Guidance.",
            "",
            "## 5. Risks And Contingencies",
            "",
            "Risks.",
            "",
            "## 6. Immediate Next Actions",
            "",
            "Save and validate.",
            "",
        ]
    )


class AttachmentSafetyTests(unittest.TestCase):
    def test_text_classification_streams_only_the_sample(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = Path(tmp) / "large.unknown"
            path.write_bytes(b"plain text" + b"x" * 8192)
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("full read")):
                self.assertTrue(attachments.is_probably_utf8_text(path))

    def test_wrappers_use_dynamic_fences_and_collision_free_names(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            first = base / "a" / "b__c.unknown"
            second = base / "a__b" / "c.unknown"
            first.parent.mkdir()
            second.parent.mkdir()
            first.write_text("inside ``` and ```` markers\n", encoding="utf-8")
            second.write_text("different\n", encoding="utf-8")
            staging = base / "staging"
            first_wrapper = attachments.build_context_wrapper(ROOT, first, staging)
            second_wrapper = attachments.build_context_wrapper(ROOT, second, staging)
            self.assertNotEqual(first_wrapper.name, second_wrapper.name)
            self.assertRegex(first_wrapper.name, r"\.[0-9a-f]{64}\.md$")
            wrapper_text = first_wrapper.read_text(encoding="utf-8")
            self.assertIn("`````\ninside ``` and ```` markers", wrapper_text)
            self.assertTrue(wrapper_text.rstrip().endswith("`````"))

    def test_wrapper_and_bundle_sources_are_streamed_without_read_text(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            source = base / "source.unknown"
            source.write_text("streamed body with ``` fence\n", encoding="utf-8")
            staging = base / "staging"
            item = {
                "source_path": source,
                "wrapped_as_markdown": False,
            }
            with mock.patch.object(Path, "read_text", side_effect=AssertionError("full text read")):
                wrapper = attachments.build_context_wrapper(ROOT, source, staging)
                bundle = attachments.build_attachment_bundle(
                    root=ROOT,
                    role_label="Reference Context",
                    bundle_items=[item],
                    staging_dir=staging,
                )
            with wrapper.open("r", encoding="utf-8") as handle:
                self.assertIn("streamed body", handle.read())
            with bundle.open("r", encoding="utf-8") as handle:
                self.assertIn("streamed body", handle.read())

    def test_secret_and_symlink_escape_inputs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp, tempfile.TemporaryDirectory() as outside:
            base = Path(tmp)
            sensitive_directory = base / "sensitive"
            sensitive_directory.mkdir()
            secret = sensitive_directory / ".env.production"
            secret.write_text("TOKEN=not-a-real-secret\n", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "Sensitive attachment"):
                attachments.expand_attachment_target(ROOT, sensitive_directory, exclude_globs=())

            outside_file = Path(outside) / "outside.txt"
            outside_file.write_text("outside", encoding="utf-8")
            escape = base / "escape.txt"
            escape.symlink_to(outside_file)
            with self.assertRaisesRegex(SystemExit, "escapes workspace root"):
                attachments.expand_attachment_target(ROOT, escape, exclude_globs=())

    def test_authority_duplicate_detector_reports_path_and_hash_conflicts(self) -> None:
        digest = hashlib.sha256(b"same").hexdigest()
        manifest = {field_name: [] for field_name in attachments.ROLE_TO_FIELD.values()}
        for field_name in ("primary_job_inputs", "reference_context"):
            manifest[field_name] = [
                {
                    "resolved": {
                        "expanded_paths": [
                            {"path": "docs/same.md", "sha256": digest, "bytes": 4}
                        ]
                    }
                }
            ]
        duplicates = attachments.detect_authority_duplicates(manifest)
        self.assertEqual({item["duplicate_by"] for item in duplicates}, {"path", "content_hash"})

    def test_workspace_inventory_is_bounded_metadata_not_source_content(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            (base / "src").mkdir()
            (base / "src" / "app.py").write_text("print('inventory sentinel')\n", encoding="utf-8")
            (base / ".env").write_text("SECRET=omitted\n", encoding="utf-8")
            entry = attachments.AttachmentEntry(
                path=base.relative_to(ROOT).as_posix(),
                kind="workspace_inventory",
            )
            with mock.patch.object(attachments, "_git_ignored_entries", return_value=()):
                resolved_entry = attachments._resolve_entry(ROOT, entry)
            resolved = resolved_entry["resolved"]
            self.assertEqual(resolved["inventory_entry_count"], 1)
            self.assertEqual(resolved["sensitive_entries_omitted"], 1)
            self.assertEqual(resolved["expanded_paths"], [])

            manifest_path = base / "input_manifest.md"
            manifest_path.write_text("# manifest\n", encoding="utf-8")
            manifest = {field: [] for field in attachments.ROLE_TO_FIELD.values()}
            manifest["attached_repository_files"] = [resolved_entry]
            plan = attachments.prepare_upload_plan(
                root=ROOT,
                resolved_manifest=manifest,
                input_manifest_markdown_path=manifest_path,
                staging_dir=base / "staging",
            )
            inventory_upload = [item for item in plan if item.get("generated_kind")][0]
            projection = inventory_upload["upload_path"].read_text(encoding="utf-8")
            self.assertIn("src/app.py", projection)
            self.assertNotIn("inventory sentinel", projection)
            self.assertNotIn("SECRET=omitted", projection)

    def test_upload_callback_persists_intent_success_and_unknown_outcome(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            manifest_markdown = base / "input_manifest.md"
            manifest_markdown.write_text("# manifest\n", encoding="utf-8")
            plan = [
                {
                    "role_label": "Stage Input Manifest",
                    "field_name": None,
                    "attachment_index": None,
                    "expanded_index": None,
                    "display_name": "input_manifest.md",
                    "source_path": manifest_markdown,
                    "upload_path": manifest_markdown,
                    "wrapped_as_markdown": False,
                }
            ]
            journal: list[dict] = []
            attachments.upload_prepared_attachments(
                root=ROOT,
                client=_RecordingUploadClient(),
                resolved_manifest={},
                prepared_uploads=plan,
                purpose="user_data",
                file_expiration_policy=None,
                delete_uploaded_files_on_complete=False,
                journal_callback=journal.append,
            )
            self.assertEqual([item["files"][0]["status"] for item in journal], ["uploading", "uploaded"])
            self.assertRegex(journal[-1]["files"][0]["upload_sha256"], r"^[0-9a-f]{64}$")

            failed_journal: list[dict] = []
            with self.assertRaises(TimeoutError):
                attachments.upload_prepared_attachments(
                    root=ROOT,
                    client=_FailingUploadClient(),
                    resolved_manifest={},
                    prepared_uploads=plan,
                    purpose="user_data",
                    file_expiration_policy=None,
                    delete_uploaded_files_on_complete=False,
                    journal_callback=failed_journal.append,
                )
            self.assertEqual(failed_journal[-1]["files"][0]["status"], "upload_outcome_unknown")

    def test_changed_source_is_rejected_before_upload(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            source = Path(tmp) / "source.txt"
            source.write_text("original", encoding="utf-8")
            digest = hashlib.sha256(b"original").hexdigest()
            manifest = {field_name: [] for field_name in attachments.ROLE_TO_FIELD.values()}
            manifest["primary_job_inputs"] = [
                {
                    "resolved": {
                        "expanded_paths": [
                            {"path": source.relative_to(ROOT).as_posix(), "sha256": digest, "bytes": 8}
                        ]
                    }
                }
            ]
            prepared = {
                "role_label": "Primary Job Inputs",
                "field_name": "primary_job_inputs",
                "attachment_index": 0,
                "expanded_index": 0,
                "display_name": source.name,
                "source_path": source,
                "upload_path": source,
                "wrapped_as_markdown": False,
            }
            source.write_text("changed", encoding="utf-8")
            client = _RecordingUploadClient()
            with self.assertRaisesRegex(SystemExit, "changed after manifest"):
                attachments.upload_prepared_attachments(
                    root=ROOT,
                    client=client,
                    resolved_manifest=manifest,
                    prepared_uploads=[prepared],
                    purpose="user_data",
                    file_expiration_policy=None,
                    delete_uploaded_files_on_complete=False,
                )
            self.assertEqual(client.calls, 0)

    def test_cleanup_checkpoints_each_result_and_skips_completed_deletes(self) -> None:
        client = _RecordingDeleteClient()
        journal: list[dict] = []
        payload = {
            "files": [
                {"file_id": "file_done", "delete_status": "deleted"},
                {"file_id": "file_pending", "status": "uploaded"},
            ]
        }
        cleaned = attachments.cleanup_uploaded_files(
            client=client,
            uploads_payload=payload,
            journal_callback=journal.append,
        )
        self.assertEqual(client.calls, ["file_pending"])
        self.assertEqual(
            [snapshot["files"][1]["delete_status"] for snapshot in journal],
            ["deleting", "deleted"],
        )
        client.calls.clear()
        attachments.cleanup_uploaded_files(
            client=client,
            uploads_payload=cleaned,
            journal_callback=journal.append,
        )
        self.assertEqual(client.calls, [])


class ValidatorTests(unittest.TestCase):
    def test_evidence_reference_validator_resolves_typed_hash_bound_sources_only(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            base = Path(tmp)
            run_dir = base / "run"
            attempt = run_dir / "stages" / "02_current" / "attempt_001"
            prior_attempt = run_dir / "stages" / "01_prior" / "attempt_001"
            attempt.mkdir(parents=True)
            prior_attempt.mkdir(parents=True)
            source = base / "source.md"
            source.write_text("source evidence\n", encoding="utf-8")
            source_rel = source.relative_to(ROOT).as_posix()
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            prior_artifact = prior_attempt / "artifact.md"
            prior_artifact.write_text("prior evidence\n", encoding="utf-8")
            prior_rel = prior_artifact.relative_to(ROOT).as_posix()
            prior_hash = hashlib.sha256(prior_artifact.read_bytes()).hexdigest()
            (run_dir / "run_manifest.json").write_text(
                json.dumps(
                    {
                        "stages": [
                            {
                                "stage_id": "prior",
                                "artifact_markdown_path": prior_rel,
                                "artifact_markdown_sha256": prior_hash,
                            },
                            {"stage_id": "current"},
                        ],
                        "operator_overrides": {
                            "input_bindings": [
                                {
                                    "binding_id": "question",
                                    "path": source_rel,
                                    "stage_ids": ["current"],
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "stage_id": "current",
                "primary_job_inputs": [
                    {
                        "resolved": {
                            "expanded_paths": [
                                {"path": source_rel, "sha256": source_hash, "bytes": source.stat().st_size}
                            ]
                        }
                    }
                ],
                "reviewed_handoff_inputs": [],
                "attached_repository_files": [],
                "reference_context": [],
            }
            manifest_path = attempt / "input_manifest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            artifact = attempt / "artifact.md"
            artifact.write_text(
                "Supported [workspace_file:" + source_rel + "] [stage_artifact:prior] "
                "[operator_input:question]. Proposed path src/future.py is not a citation.\n",
                encoding="utf-8",
            )
            context = {
                "citation_policy": {
                    "allowed_locator_types": [
                        "workspace_file",
                        "stage_artifact",
                        "operator_input",
                    ]
                },
                "input_manifest_path": manifest_path.relative_to(ROOT).as_posix(),
            }
            result = validators.run_validator(
                "evidence_references_v1",
                artifact,
                root=ROOT,
                context=context,
            )
            self.assertTrue(result["passed"], result["violations"])

            source.write_text("drifted evidence\n", encoding="utf-8")
            artifact.write_text(
                "Unsupported [workspace_file:" + source_rel + "] and "
                "[workspace_file:src/future.py].\n",
                encoding="utf-8",
            )
            failed = validators.run_validator(
                "evidence_references_v1",
                artifact,
                root=ROOT,
                context=context,
            )
            self.assertFalse(failed["passed"])
            self.assertEqual(
                {item["rule_id"] for item in failed["violations"]},
                {"citation.hash", "citation.manifest_member"},
            )

    def test_validator_registry_rejects_untrusted_ids(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            artifact = Path(tmp) / "artifact.md"
            artifact.write_text("# artifact\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Unknown trusted validator"):
                validators.run_validator("shell_command", artifact, root=ROOT)


if __name__ == "__main__":
    unittest.main()
