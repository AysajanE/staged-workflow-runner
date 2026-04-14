from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2.review_bundle import (
    create_review_bundle,
    expand_review_bundle_inputs,
    load_review_bundle,
    validate_review_bundle_for_stage,
)


class ResponsesRunnerV2ReviewBundleTests(unittest.TestCase):
    def test_create_and_load_review_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "review_bundle.json"
            primary_md = root / "response.final.md"
            response_json = root / "response.final.json"
            reviewer_notes = root / "reviewer_notes.md"
            approved_handoff = root / "approved_handoff.md"
            primary_md.write_text("# ok\n", encoding="utf-8")
            response_json.write_text('{"id":"resp_1"}\n', encoding="utf-8")
            reviewer_notes.write_text("# notes\n", encoding="utf-8")
            approved_handoff.write_text("# downstream handoff\n", encoding="utf-8")

            create_review_bundle(
                root=root,
                output_path=output,
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id="run_test",
                primary_artifact_markdown=primary_md,
                response_artifact_json=response_json,
                reviewer_notes=reviewer_notes,
                approved_handoff_markdown=approved_handoff,
                locked_decisions=["keep the brief authoritative"],
                open_dependencies=[],
            )
            bundle = load_review_bundle(root=root, bundle_path=output)

        self.assertEqual(bundle["workflow_id"], "synthetic_reviewed_three_stage")
        self.assertEqual(bundle["source_stage_id"], "proposal")
        self.assertEqual(bundle["review_status"], "approved")
        self.assertEqual(bundle["approved_handoff_markdown"], "approved_handoff.md")

    def test_validate_review_bundle_for_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "review_bundle.json"
            primary_md = root / "response.final.md"
            response_json = root / "response.final.json"
            reviewer_notes = root / "reviewer_notes.md"
            primary_md.write_text("# ok\n", encoding="utf-8")
            response_json.write_text('{"id":"resp_1"}\n', encoding="utf-8")
            reviewer_notes.write_text("# notes\n", encoding="utf-8")

            create_review_bundle(
                root=root,
                output_path=output,
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id="run_test",
                primary_artifact_markdown=primary_md,
                response_artifact_json=response_json,
                reviewer_notes=reviewer_notes,
            )
            bundle = load_review_bundle(root=root, bundle_path=output)
            validate_review_bundle_for_stage(
                bundle,
                workflow_id="synthetic_reviewed_three_stage",
                expected_source_stage_id="proposal",
                expected_source_run_id="run_test",
            )
            entries = expand_review_bundle_inputs(bundle)

        self.assertGreaterEqual(len(entries), 4)
        self.assertEqual(entries[0].path, "review_bundle.json")

    def test_expand_review_bundle_inputs_can_exclude_raw_response_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "review_bundle.json"
            primary_md = root / "response.final.md"
            response_json = root / "response.final.json"
            reviewer_notes = root / "reviewer_notes.md"
            primary_md.write_text("# ok\n", encoding="utf-8")
            response_json.write_text('{"id":"resp_1"}\n', encoding="utf-8")
            reviewer_notes.write_text("# notes\n", encoding="utf-8")

            create_review_bundle(
                root=root,
                output_path=output,
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id="run_test",
                primary_artifact_markdown=primary_md,
                response_artifact_json=response_json,
                reviewer_notes=reviewer_notes,
            )
            bundle = load_review_bundle(root=root, bundle_path=output)
            entries = expand_review_bundle_inputs(bundle, include_response_artifact_json=False)

        self.assertEqual([entry.path for entry in entries], [
            "review_bundle.json",
            "reviewer_notes.md",
            "response.final.md",
        ])

    def test_expand_review_bundle_inputs_prefers_approved_handoff_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "review_bundle.json"
            primary_md = root / "response.final.md"
            response_json = root / "response.final.json"
            reviewer_notes = root / "reviewer_notes.md"
            approved_handoff = root / "approved_handoff.md"
            primary_md.write_text("# ok\n", encoding="utf-8")
            response_json.write_text('{"id":"resp_1"}\n', encoding="utf-8")
            reviewer_notes.write_text("# notes\n", encoding="utf-8")
            approved_handoff.write_text("# handoff\n", encoding="utf-8")

            create_review_bundle(
                root=root,
                output_path=output,
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id="run_test",
                primary_artifact_markdown=primary_md,
                response_artifact_json=response_json,
                reviewer_notes=reviewer_notes,
                approved_handoff_markdown=approved_handoff,
            )
            bundle = load_review_bundle(root=root, bundle_path=output)
            entries = expand_review_bundle_inputs(bundle, include_response_artifact_json=False)

        self.assertEqual([entry.path for entry in entries], [
            "review_bundle.json",
            "approved_handoff.md",
            "reviewer_notes.md",
            "response.final.md",
        ])

    def test_hash_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "review_bundle.json"
            primary_md = root / "response.final.md"
            response_json = root / "response.final.json"
            reviewer_notes = root / "reviewer_notes.md"
            primary_md.write_text("# ok\n", encoding="utf-8")
            response_json.write_text('{"id":"resp_1"}\n', encoding="utf-8")
            reviewer_notes.write_text("# notes\n", encoding="utf-8")

            create_review_bundle(
                root=root,
                output_path=output,
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id="run_test",
                primary_artifact_markdown=primary_md,
                response_artifact_json=response_json,
                reviewer_notes=reviewer_notes,
            )
            primary_md.write_text("# changed\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_review_bundle(root=root, bundle_path=output)


if __name__ == "__main__":
    unittest.main()
