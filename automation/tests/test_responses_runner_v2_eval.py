from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2 import workflow as workflow_module
from automation.responses_runner_v2.contracts import RuntimeOptions


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "automation" / "run_responses_v2_eval.py"
DATASET_PATH = ROOT / "automation" / "evals" / "responses_runner_v2.eval.json"
SYNTHETIC_WORKFLOW = (
    ROOT / "automation" / "examples" / "responses_runner_v2_synthetic" / "workflows" / "one_pass.workflow.json"
)


class OfflineCandidateClient:
    def __init__(self, candidate: dict) -> None:
        self.candidate = candidate
        self.upload_count = 0

    def upload_file(self, _path, purpose, file_expiration_policy=None):
        self.upload_count += 1
        return {"id": f"file_{self.upload_count}", "purpose": purpose, "created_at": 1}

    def count_input_tokens_once(self, _payload):
        return {"input_tokens": 100}

    def create_response(self, payload):
        if payload["text"]["format"]["type"] == "json_schema":
            parsed = {
                "summary_version": "responses_runner_v2.synthetic_summary.v1",
                "workflow_id": "synthetic_one_pass",
                "final_assessment": "Offline candidate captured.",
                "key_points": ["Candidate passed through the runner."],
                "open_questions": [],
            }
            return {
                "id": f"{self.candidate['producer']['response_id']}_sidecar",
                "status": "completed",
                "model": "gpt-5.6",
                "output_parsed": parsed,
                "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(parsed)}]}],
            }
        return {
            "id": self.candidate["producer"]["response_id"],
            "status": "completed",
            "model": "gpt-5.6",
            "background": True,
            "store": True,
            "created_at": 1,
            "completed_at": 2,
            "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(self.candidate, indent=2, ensure_ascii=False),
                        }
                    ],
                }
            ],
        }

    def delete_file(self, file_id):
        return {"id": file_id, "deleted": True}


def load_module():
    spec = importlib.util.spec_from_file_location("responses_runner_v2_eval", SCRIPT_PATH)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load {SCRIPT_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ResponsesRunnerV2EvalTests(unittest.TestCase):
    @staticmethod
    def _write_json(path: Path, payload: object) -> None:
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _freeze_fixture(self, module, temp: Path) -> tuple[Path, dict, dict[str, Path]]:
        notes = temp / "notes.md"
        notes.write_text("# reviewed\n", encoding="utf-8")
        evidence = temp / "synthetic_run_manifest.json"
        self._write_json(evidence, {"status": "completed"})
        dataset = module.load_eval_dataset(DATASET_PATH)

        artifact_payloads = {
            "workflow-manifest-reviewed-three-stage": {
                "schema_version": "responses_runner_v2.workflow.v1",
                "workflow_id": "synthetic_one_pass",
                "workflow_mode": "reviewed_three_stage",
                "defaults": {},
                "stages": [{"stage_id": "one"}, {"stage_id": "two"}, {"stage_id": "three"}],
            },
            "run-manifest-contract": {
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
            },
            "stage-checkpoint-contract": {
                "schema_version": "responses_runner_v2.stage_checkpoint.v1",
                "run_id": "run_test",
                "stage_id": "draft_summary",
                "stage_number": 1,
                "updated_at": "2026-08-11T00:00:00Z",
                "status": "completed",
                "terminal": True,
                "resume_mode": "none",
                "request_payload_path": "request.json",
                "input_manifest_json_path": "inputs.json",
                "input_manifest_markdown_path": "inputs.md",
                "token_preflight": {},
                "artifacts": {},
            },
            "review-bundle-contract": {
                "schema_version": "responses_runner_v2.review_bundle.v1",
                "workflow_id": "synthetic_one_pass",
                "source_stage_id": "draft_summary",
                "source_run_id": "run_test",
                "created_at": "2026-08-11T00:00:00Z",
                "review_status": "approved",
                "primary_artifact_markdown": "artifact.md",
                "response_artifact_json": "response.json",
                "reviewer_notes": "notes.md",
                "artifact_hashes": {},
                "locked_decisions": [],
                "open_dependencies": [],
            },
        }
        expected_cases = []
        result_paths: dict[str, Path] = {}
        for case in dataset["cases"]:
            case_id = case["id"]
            case_dir = temp / case_id
            case_dir.mkdir()
            structured_path = None
            if case.get("fixture"):
                artifact_path = DATASET_PATH.parent / case["candidate"]
            elif case_id == "synthetic-summary-structured-output":
                artifact_path = case_dir / "artifact.md"
                artifact_path.write_text("# summary\n", encoding="utf-8")
                structured_path = case_dir / "structured.json"
                self._write_json(
                    structured_path,
                    {
                        "summary_version": "responses_runner_v2.synthetic_summary.v1",
                        "workflow_id": "synthetic_one_pass",
                        "final_assessment": "ok",
                        "key_points": ["one"],
                        "open_questions": [],
                    },
                )
            else:
                artifact_path = case_dir / "artifact.json"
                self._write_json(artifact_path, artifact_payloads[case_id])
            result = module.grade_case(
                dataset,
                case_id,
                artifact_path,
                structured_artifact_path=structured_path,
            )
            result_path = case_dir / "eval_result.json"
            self._write_json(result_path, result)
            entry = {
                "case_id": case_id,
                "result_path": str(result_path),
                "result_sha256": module._sha256_file(result_path),
                "artifact_path": str(artifact_path),
                "artifact_sha256": module._sha256_file(artifact_path),
            }
            if structured_path is not None:
                entry["structured_artifact_path"] = str(structured_path)
                entry["structured_artifact_sha256"] = module._sha256_file(structured_path)
            expected_cases.append(entry)
            result_paths[case_id] = result_path

        manifest = {
            "schema_version": module.FREEZE_GATE_SCHEMA_VERSION,
            "workflow": dataset["workflow"],
            "dataset_file": str(DATASET_PATH),
            "dataset_sha256": module._sha256_file(DATASET_PATH),
            "reviewer_notes": str(notes),
            "reviewer_notes_sha256": module._sha256_file(notes),
            "synthetic_example_evidence": str(evidence),
            "synthetic_example_evidence_sha256": module._sha256_file(evidence),
            "expected_cases": expected_cases,
        }
        manifest_path = temp / "freeze_gate_manifest.json"
        self._write_json(manifest_path, manifest)
        return manifest_path, manifest, result_paths

    def test_dataset_has_expected_cases(self) -> None:
        dataset = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
        case_ids = [case["id"] for case in dataset["cases"]]
        self.assertIn("run-manifest-contract", case_ids)
        self.assertIn("review-bundle-contract", case_ids)
        self.assertIn("synthetic-summary-structured-output", case_ids)
        representative = {
            case["representative_task_type"]: case["id"]
            for case in dataset["cases"]
            if "representative_task_type" in case
        }
        self.assertEqual(
            representative,
            {
                "critical_coding_implementation": "representative-critical-coding",
                "repository_planning": "representative-repository-planning",
                "multi_document_research": "representative-research-synthesis",
                "policy_operational_decision": "representative-policy-decision",
                "document_report_generation": "representative-report-generation",
                "low_risk_transformation": "representative-low-risk-transformation",
            },
        )

    def test_representative_candidates_are_separate_and_pass_contract_and_citation_checks(self) -> None:
        module = load_module()
        dataset = module.load_eval_dataset(DATASET_PATH)
        representative_cases = [case for case in dataset["cases"] if case.get("fixture")]
        self.assertEqual(len(representative_cases), 6)
        for case in representative_cases:
            fixture = DATASET_PATH.parent / case["fixture"]
            candidate = DATASET_PATH.parent / case["candidate"]
            fixture_payload = json.loads(fixture.read_text(encoding="utf-8"))
            candidate_payload = json.loads(candidate.read_text(encoding="utf-8"))
            self.assertNotIn("expected_output", fixture_payload)
            self.assertIn("gold_output", fixture_payload)
            self.assertNotIn("gold_output", candidate_payload)
            self.assertEqual(candidate_payload["producer"]["kind"], "responses_runner_v2_offline_fake_client")
            result = module.grade_case(dataset, case["id"], candidate)
            self.assertTrue(result["passed"], case["id"])
            self.assertEqual(result["reference_fixture_sha256"], case["fixture_sha256"])
            self.assertEqual(
                [check["id"] for check in result["checks"]],
                [
                    "json_required_keys",
                    "json_path_equals",
                    "runner_candidate_provenance",
                    "expected_output_contract",
                    "citations_grounded",
                ],
            )

    def test_representative_candidates_replay_through_offline_runner(self) -> None:
        module = load_module()
        dataset = module.load_eval_dataset(DATASET_PATH)
        representative_cases = [case for case in dataset["cases"] if case.get("candidate")]
        for case in representative_cases:
            candidate_path = DATASET_PATH.parent / case["candidate"]
            candidate = json.loads(candidate_path.read_text(encoding="utf-8"))
            producer = candidate["producer"]
            with tempfile.TemporaryDirectory(dir=ROOT) as raw:
                run_dir = Path(raw) / producer["run_id"]
                with mock.patch.object(workflow_module, "new_run_id", return_value=producer["run_id"]):
                    result = workflow_module.run_workflow(
                        workflow_file=SYNTHETIC_WORKFLOW,
                        runtime=RuntimeOptions(
                            run_name=producer["run_id"],
                            run_dir=run_dir,
                        ),
                        client=OfflineCandidateClient(candidate),
                        root=ROOT,
                    )
                manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))
                self.assertEqual(manifest["run_id"], producer["run_id"])
                summary = manifest["stages"][0]
                self.assertEqual(summary["stage_id"], producer["stage_id"])
                artifact = ROOT / summary["artifact_markdown_path"]
                self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), candidate)
                response = json.loads((artifact.parent / "response.final.json").read_text(encoding="utf-8"))
                self.assertEqual(response["id"], producer["response_id"])

    def test_representative_citation_check_rejects_unquoted_evidence(self) -> None:
        module = load_module()
        dataset = module.load_eval_dataset(DATASET_PATH)
        case_id = "representative-research-synthesis"
        case = next(case for case in dataset["cases"] if case["id"] == case_id)
        candidate = json.loads((DATASET_PATH.parent / case["candidate"]).read_text(encoding="utf-8"))
        candidate["output"]["citations"][0]["quote"] = "This quote is absent from the frozen source."
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "tampered.json"
            self._write_json(artifact, candidate)
            result = module.grade_case(dataset, case_id, artifact)
        self.assertFalse(result["passed"])
        self.assertFalse(next(check for check in result["checks"] if check["id"] == "citations_grounded")["passed"])

    def test_representative_contract_check_rejects_missing_output_key(self) -> None:
        module = load_module()
        dataset = module.load_eval_dataset(DATASET_PATH)
        case_id = "representative-policy-decision"
        case = next(case for case in dataset["cases"] if case["id"] == case_id)
        candidate = json.loads((DATASET_PATH.parent / case["candidate"]).read_text(encoding="utf-8"))
        candidate["output"].pop("next_action")
        with tempfile.TemporaryDirectory() as tmp:
            artifact = Path(tmp) / "tampered.json"
            self._write_json(artifact, candidate)
            result = module.grade_case(dataset, case_id, artifact)
        self.assertFalse(result["passed"])
        self.assertFalse(next(check for check in result["checks"] if check["id"] == "expected_output_contract")["passed"])

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
            self.assertEqual(result["schema_version"], module.EVAL_RESULT_SCHEMA_VERSION)
            self.assertEqual(result["artifact_sha256"], module._sha256_file(artifact))

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
            freeze_gate, _manifest, _results = self._freeze_fixture(module, Path(tmp))
            result = module.grade_freeze_gate(freeze_gate)
            self.assertTrue(result["passed"])

    def test_freeze_gate_rejects_wrong_workflow(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            freeze_gate, manifest, _results = self._freeze_fixture(module, Path(tmp))
            manifest["workflow"] = "wrong_workflow"
            self._write_json(freeze_gate, manifest)
            self.assertFalse(module.grade_freeze_gate(freeze_gate)["passed"])

    def test_freeze_gate_rejects_wrong_case_identity(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            freeze_gate, manifest, results = self._freeze_fixture(module, Path(tmp))
            case_id = "run-manifest-contract"
            result_path = results[case_id]
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["case_id"] = "wrong-case"
            self._write_json(result_path, result)
            next(entry for entry in manifest["expected_cases"] if entry["case_id"] == case_id)[
                "result_sha256"
            ] = module._sha256_file(result_path)
            self._write_json(freeze_gate, manifest)
            self.assertFalse(module.grade_freeze_gate(freeze_gate)["passed"])

    def test_freeze_gate_rejects_missing_case(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            freeze_gate, manifest, _results = self._freeze_fixture(module, Path(tmp))
            manifest["expected_cases"].pop()
            self._write_json(freeze_gate, manifest)
            self.assertFalse(module.grade_freeze_gate(freeze_gate)["passed"])

    def test_freeze_gate_rejects_wrong_hash(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            freeze_gate, manifest, _results = self._freeze_fixture(module, Path(tmp))
            manifest["expected_cases"][0]["result_sha256"] = "0" * 64
            self._write_json(freeze_gate, manifest)
            self.assertFalse(module.grade_freeze_gate(freeze_gate)["passed"])

    def test_freeze_gate_rejects_forged_passed_result(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            freeze_gate, manifest, results = self._freeze_fixture(module, Path(tmp))
            case_id = "run-manifest-contract"
            entry = next(entry for entry in manifest["expected_cases"] if entry["case_id"] == case_id)
            artifact_path = Path(entry["artifact_path"])
            self._write_json(artifact_path, {})
            entry["artifact_sha256"] = module._sha256_file(artifact_path)
            result_path = results[case_id]
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result["artifact_sha256"] = entry["artifact_sha256"]
            self._write_json(result_path, result)
            entry["result_sha256"] = module._sha256_file(result_path)
            self._write_json(freeze_gate, manifest)
            self.assertTrue(result["passed"])
            self.assertFalse(module.grade_freeze_gate(freeze_gate)["passed"])

    def test_freeze_gate_rejects_schema_invalid_result(self) -> None:
        module = load_module()
        with tempfile.TemporaryDirectory() as tmp:
            freeze_gate, manifest, results = self._freeze_fixture(module, Path(tmp))
            case_id = "run-manifest-contract"
            result_path = results[case_id]
            result = json.loads(result_path.read_text(encoding="utf-8"))
            result.pop("checks")
            self._write_json(result_path, result)
            next(entry for entry in manifest["expected_cases"] if entry["case_id"] == case_id)[
                "result_sha256"
            ] = module._sha256_file(result_path)
            self._write_json(freeze_gate, manifest)
            self.assertFalse(module.grade_freeze_gate(freeze_gate)["passed"])


if __name__ == "__main__":
    unittest.main()
