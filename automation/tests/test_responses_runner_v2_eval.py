from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "automation" / "run_responses_v2_eval.py"
DATASET_PATH = ROOT / "automation" / "evals" / "responses_runner_v2.eval.json"


def load_module():
    spec = importlib.util.spec_from_file_location("responses_runner_v2_eval", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResponsesRunnerV2EvalTests(unittest.TestCase):
    def test_dataset_has_expected_cases(self) -> None:
        dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        case_ids = [case["id"] for case in dataset["cases"]]
        self.assertIn("run-manifest-contract", case_ids)
        self.assertIn("review-bundle-contract", case_ids)
        self.assertIn("synthetic-summary-structured-output", case_ids)

    def test_grade_json_case(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "run_manifest.json"
            artifact.write_text(
                json.dumps(
                    {
                        "schema_version": "responses_runner_v2.run_manifest.v1",
                        "run_id": "run_test",
                        "run_name": "demo",
                        "workflow_id": "synthetic_one_pass",
                        "workflow_manifest_path": "workflow.json",
                        "workflow_manifest_sha256": "0" * 64,
                        "run_dir": "runs/demo",
                        "status": "created",
                        "stage_order": ["draft_summary"],
                        "stages": [{"stage_id": "draft_summary"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dataset = module.load_eval_dataset(DATASET_PATH)
            result = module.grade_case(dataset, "run-manifest-contract", artifact)
            self.assertTrue(result["passed"])

    def test_grade_structured_case(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "response.final.md"
            structured = Path(tmp) / "output.structured.json"
            artifact.write_text("# ok\n", encoding="utf-8")
            structured.write_text(
                json.dumps(
                    {
                        "summary_version": "responses_runner_v2.synthetic_summary.v1",
                        "workflow_id": "synthetic_one_pass",
                        "final_assessment": "ok",
                        "key_points": ["one"],
                        "open_questions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dataset = module.load_eval_dataset(DATASET_PATH)
            result = module.grade_case(
                dataset,
                "synthetic-summary-structured-output",
                artifact,
                structured_artifact_path=structured,
            )
            self.assertTrue(result["passed"])

    def test_freeze_gate(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            notes = temp / "notes.md"
            notes.write_text("# ok\n", encoding="utf-8")
            evidence = temp / "run_manifest.json"
            evidence.write_text('{"status":"completed"}\n', encoding="utf-8")
            eval_result = temp / "eval_result.json"
            eval_result.write_text('{"passed": true}\n', encoding="utf-8")
            freeze_gate = temp / "freeze_gate_manifest.json"
            freeze_gate.write_text(
                json.dumps(
                    {
                        "workflow": "responses_runner_v2",
                        "dataset_file": str(DATASET_PATH),
                        "reviewer_notes": str(notes),
                        "synthetic_example_evidence": str(evidence),
                        "eval_result_paths": [str(eval_result)],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            result = module.grade_freeze_gate(freeze_gate)
            self.assertTrue(result["passed"])

    def test_freeze_gate_reports_manifest_workflow_name(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            notes = temp / "notes.md"
            notes.write_text("# ok\n", encoding="utf-8")
            evidence = temp / "run_manifest.json"
            evidence.write_text('{"status":"completed"}\n', encoding="utf-8")
            eval_result = temp / "eval_result.json"
            eval_result.write_text('{"workflow":"wrong_workflow","passed": true}\n', encoding="utf-8")
            freeze_gate = temp / "freeze_gate_manifest.json"
            freeze_gate.write_text(
                json.dumps(
                    {
                        "workflow": "freeze_gate_workflow",
                        "dataset_file": str(DATASET_PATH),
                        "reviewer_notes": str(notes),
                        "synthetic_example_evidence": str(evidence),
                        "eval_result_paths": [str(eval_result)],
                    }
                )
                + "\n",
                encoding="utf-8",
            )

            result = module.grade_freeze_gate(freeze_gate)
            self.assertEqual(result["workflow"], "freeze_gate_workflow")


if __name__ == "__main__":
    unittest.main()
