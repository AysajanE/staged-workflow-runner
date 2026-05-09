from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2 import attachments
from automation.responses_runner_v2.contracts import AttachmentEntry


ROOT = Path(__file__).resolve().parents[2]


class FakeUploadClient:
    def __init__(self) -> None:
        self.uploads: list[Path] = []

    def upload_file(self, path, purpose, file_expiration_policy=None):
        self.uploads.append(Path(path))
        return {
            "id": f"file_{len(self.uploads)}",
            "purpose": purpose,
            "created_at": 1,
            "expires_at": 1 + int(file_expiration_policy["seconds"]) if file_expiration_policy else None,
        }


class ResponsesRunnerV2AttachmentTests(unittest.TestCase):
    def test_large_text_attachment_sets_are_bundled_below_response_file_limit(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            corpus = tmp_path / "corpus"
            corpus.mkdir()
            for index in range(105):
                (corpus / f"file_{index:03d}.ts").write_text(
                    f"export const value{index} = {index};\n",
                    encoding="utf-8",
                )
            manifest = attachments.resolve_stage_input_manifest(
                root=ROOT,
                workflow_id="workflow",
                stage_id="stage",
                run_id="run",
                manifest_id="manifest",
                description=None,
                primary_job_inputs=[],
                reviewed_handoff_inputs=[],
                attached_repository_files=[
                    AttachmentEntry(path=corpus.relative_to(ROOT).as_posix(), kind="directory")
                ],
                reference_context=[],
            )
            input_manifest_md = tmp_path / "input_manifest.md"
            input_manifest_md.write_text(
                attachments.render_input_manifest_markdown(manifest),
                encoding="utf-8",
            )
            staging_dir = tmp_path / "staging"
            staging_dir.mkdir()

            plan = attachments.prepare_upload_plan(
                root=ROOT,
                resolved_manifest=manifest,
                input_manifest_markdown_path=input_manifest_md,
                staging_dir=staging_dir,
            )

            self.assertLessEqual(len(plan), attachments.MAX_RESPONSE_INPUT_FILES)
            bundles = [item for item in plan if item.get("bundle_items")]
            self.assertEqual(len(bundles), 1)
            self.assertEqual(bundles[0]["role_label"], "Attached Repository Files")
            self.assertEqual(len(bundles[0]["bundle_items"]), 105)
            bundle_text = bundles[0]["upload_path"].read_text(encoding="utf-8")
            self.assertIn("Attachment Role Bundle: Attached Repository Files", bundle_text)
            self.assertIn("source_path:", bundle_text)

            client = FakeUploadClient()
            manifest_file_id, role_to_file_ids, uploads_payload, uploaded_manifest = attachments.upload_prepared_attachments(
                root=ROOT,
                client=client,
                resolved_manifest=manifest,
                prepared_uploads=plan,
                purpose="user_data",
                file_expiration_policy={"anchor": "created_at", "seconds": 60},
                delete_uploaded_files_on_complete=False,
            )

            self.assertEqual(manifest_file_id, "file_1")
            self.assertEqual(role_to_file_ids["Attached Repository Files"], ["file_2"])
            self.assertEqual(len(uploads_payload["files"]), 2)
            self.assertEqual(uploads_payload["files"][1]["bundled_file_count"], 105)
            expanded = uploaded_manifest["attached_repository_files"][0]["resolved"]["expanded_paths"]
            self.assertTrue(all(item["uploaded_file_id"] == "file_2" for item in expanded))


if __name__ == "__main__":
    unittest.main()
