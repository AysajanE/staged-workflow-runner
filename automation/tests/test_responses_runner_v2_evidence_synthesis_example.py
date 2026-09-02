from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2.contracts import (
    RuntimeInputBinding,
    RuntimeOptions,
    relpath,
    runner_now,
    sha256_file,
)
from automation.responses_runner_v2.workflow import run_workflow


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "automation/examples/responses_runner_v2_evidence_synthesis"
WORKFLOW_PATH = PACK_ROOT / "workflows/document_evidence_synthesis.workflow.json"


def _runtime_input_bindings() -> list[RuntimeInputBinding]:
    payload = json.loads(
        (PACK_ROOT / "runtime_input_bindings.example.json").read_text(encoding="utf-8")
    )
    return [
        RuntimeInputBinding(
            binding_id=binding["binding_id"],
            path=binding["path"],
            authority=binding["authority"],
            stage_ids=tuple(binding["scope"]["stage_ids"]),
        )
        for binding in payload["bindings"]
    ]


class ResponsesRunnerV2EvidenceSynthesisExampleTests(unittest.TestCase):
    def test_workflow_is_bounded_non_coding_and_reviewed(self) -> None:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(workflow["schema_version"], "responses_runner_v2.workflow_manifest.v2")
        self.assertEqual(workflow["workflow_mode"], "reviewed_three_stage")
        self.assertEqual(workflow["assurance_profile"], "reviewed")

        roles = workflow["defaults"]["model_roles"]
        self.assertEqual(roles["primary_generation"]["model"], "gpt-5.6")
        self.assertEqual(roles["primary_generation"]["reasoning_mode"], "pro")
        self.assertEqual(roles["structural_processing"]["model"], "gpt-5.6")
        self.assertEqual(roles["structural_processing"]["reasoning_mode"], "standard")

        stages = workflow["stages"]
        self.assertEqual([stage["stage_number"] for stage in stages], [1, 2, 3])
        self.assertEqual([stage["gate"] for stage in stages], ["human", "human", "terminal"])
        for stage in stages:
            self.assertEqual(stage["max_input_tokens"], 700000)
            self.assertNotIn("tool_profile_file", stage)

    def test_declared_bindings_are_root_confined_and_stage_scoped(self) -> None:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        known_stage_ids = {stage["stage_id"] for stage in workflow["stages"]}
        binding_contract = json.loads(
            (PACK_ROOT / "runtime_input_bindings.example.json").read_text(encoding="utf-8")
        )
        binding_ids: set[str] = set()
        for binding in binding_contract["bindings"]:
            self.assertNotIn(binding["binding_id"], binding_ids)
            binding_ids.add(binding["binding_id"])
            self.assertTrue((ROOT / binding["path"]).is_file())
            self.assertEqual(binding["scope"]["type"], "stages")
            self.assertTrue(set(binding["scope"]["stage_ids"]).issubset(known_stage_ids))

        self.assertEqual(
            binding_ids,
            {"decision_question", "finance_evidence", "customer_evidence", "vendor_evidence"},
        )

        for manifest_path in sorted((PACK_ROOT / "inputs").glob("*.input_manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for field_name in (
                "primary_job_inputs",
                "reviewed_handoff_inputs",
                "attached_repository_files",
                "reference_context",
            ):
                self.assertEqual(manifest[field_name], [])

    def test_prompts_require_typed_citations_and_no_execution_claims(self) -> None:
        prompts = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((PACK_ROOT / "prompts").glob("*.md"))
        )
        self.assertIn("[workspace_file:", prompts)
        self.assertIn("[stage_artifact:", prompts)
        self.assertIn("[operator_input:", prompts)
        self.assertIn("Do not claim", prompts)

    def test_every_stage_enforces_typed_evidence_references(self) -> None:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        for stage in workflow["stages"]:
            self.assertTrue(stage["citation_policy"]["allowed_locator_types"])
            self.assertEqual(
                stage["post_output_validators"],
                [{"validator_id": "evidence_references_v1", "gate": "blocking"}],
            )

    def test_runtime_binding_schema_is_valid_draft_2020_12(self) -> None:
        try:
            import jsonschema  # type: ignore
        except ImportError:
            self.skipTest("jsonschema not installed")
        else:
            binding_schema = json.loads(
                (ROOT / "automation/responses_runner_v2/schemas/runtime_input_bindings.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            jsonschema.Draft202012Validator(binding_schema).validate(
                json.loads(
                    (PACK_ROOT / "runtime_input_bindings.example.json").read_text(encoding="utf-8")
                )
            )



if __name__ == "__main__":
    unittest.main()
