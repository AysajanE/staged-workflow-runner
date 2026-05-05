from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2 import contracts
from automation.responses_runner_v2.pack_loader import load_workflow_definition


ROOT = Path(__file__).resolve().parents[2]
OLD_MODEL_PATTERN = re.compile("gpt-5\\." + "4(?:-pro)?")

WORKFLOW_PATHS = [
    "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
    "automation/examples/responses_runner_v2_synthetic/workflows/two_pass.workflow.json",
    "automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
    "automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json",
    "automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json",
]

STATIC_SCAN_TARGETS = [
    "AGENTS.md",
    "README.md",
    "docs/runbooks/responses-runner-v2.md",
    "automation/run_responses_supervisor_v2.py",
    "automation/responses_runner_v2/contracts.py",
    "automation/responses_runner_v2/pack_loader.py",
    "automation/responses_runner_v2/sidecar.py",
    "automation/responses_runner_v2/supervisor.py",
    "automation/responses_runner_v2/supervisor_agents.py",
    "automation/responses_runner_v2/supervisor_artifacts.py",
    "automation/responses_runner_v2/supervisor_policies.py",
    "automation/responses_runner_v2/schemas",
    "automation/task_packs/responses_runner_v2_supervisor_internal",
    "automation/task_packs/responses_runner_v2_supervised_end_to_end/README.md",
    "automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json",
    "automation/task_packs/responses_runner_v2_supervised_end_to_end/schemas/final_supervisory_packet.schema.json",
    "automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json",
    "automation/examples/responses_runner_v2_synthetic/workflows",
    "automation/examples/responses_runner_v2_synthetic/corpus/repo_fact_sheet.md",
    "automation/tests/test_responses_runner_v2_contracts.py",
    "automation/tests/test_responses_runner_v2_workflow.py",
    "automation/tests/test_responses_runner_v2_supervisor.py",
]


def _iter_scan_files() -> list[Path]:
    files: list[Path] = []
    for target in STATIC_SCAN_TARGETS:
        path = ROOT / target
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
        else:
            files.extend(
                child
                for child in sorted(path.rglob("*"))
                if child.is_file()
                and "__pycache__" not in child.parts
                and child.suffix in {".py", ".json", ".md", ".txt"}
            )
    return files


class ResponsesRunnerV2ModelMigrationTests(unittest.TestCase):
    def test_engine_defaults_are_gpt55_family(self) -> None:
        self.assertEqual(contracts.DEFAULT_PRIMARY_MODEL, "gpt-5.5-pro")
        self.assertEqual(contracts.DEFAULT_STRUCTURAL_MODEL, "gpt-5.5")

    def test_gpt55_model_caps_and_base_model_normalization(self) -> None:
        self.assertEqual(contracts.base_model_name("gpt-5.5-pro-2026-04-23"), "gpt-5.5-pro")
        self.assertEqual(contracts.base_model_name("gpt-5.5-2026-04-23"), "gpt-5.5")
        self.assertEqual(contracts.model_max_output_tokens("gpt-5.5-pro-2026-04-23"), 128000)
        self.assertEqual(contracts.model_max_output_tokens("gpt-5.5-2026-04-23"), 128000)

    def test_gpt55_rejects_in_memory_prompt_cache(self) -> None:
        with self.assertRaises(SystemExit):
            contracts.validate_model_options(
                model="gpt-5.5-pro",
                max_output_tokens=128000,
                prompt_cache_retention="in_memory",
                text_format="text",
            )
        contracts.validate_model_options(
            model="gpt-5.5-pro",
            max_output_tokens=128000,
            prompt_cache_retention="24h",
            text_format="text",
        )

    def test_workflow_loader_requires_explicit_24h_for_gpt55(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            prompt = tmp_path / "task.md"
            prompt.write_text("Say hello.\n", encoding="utf-8")
            instructions = tmp_path / "shared.md"
            instructions.write_text("Follow the task.\n", encoding="utf-8")
            manifest = tmp_path / "input.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema_version": "responses_runner_v2.input_manifest.v1",
                        "manifest_id": "tmp_manifest",
                        "primary_job_inputs": [],
                        "reviewed_handoff_inputs": [],
                        "attached_repository_files": [],
                        "reference_context": [],
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            workflow = {
                "schema_version": "responses_runner_v2.workflow_manifest.v1",
                "workflow_id": "tmp_gpt55_missing_cache",
                "workflow_mode": "one_pass",
                "description": "Temporary workflow missing cache.",
                "shared_instructions_file": instructions.as_posix(),
                "defaults": {
                    "model_roles": {
                        "primary_generation": {
                            "model": "gpt-5.5-pro",
                            "reasoning_effort": "xhigh",
                            "verbosity": "high",
                        },
                        "structural_processing": {
                            "model": "gpt-5.5",
                            "reasoning_effort": "high",
                            "verbosity": "medium",
                            "prompt_cache_retention": "24h",
                        },
                    },
                    "request": {
                        "background": False,
                        "store": True,
                        "parallel_tool_calls": True,
                        "max_tool_calls": 1,
                        "token_preflight": {
                            "enabled": False,
                            "max_retries": 1,
                            "retryable_http_status_codes": [429],
                            "on_retryable_service_failure": "fail_closed",
                        },
                        "file_uploads": {
                            "purpose": "user_data",
                            "delete_on_completion": False,
                        },
                    },
                },
                "stages": [
                    {
                        "stage_id": "stage",
                        "stage_number": 1,
                        "title": "Stage",
                        "task_file": prompt.as_posix(),
                        "input_manifest_file": manifest.as_posix(),
                        "model_role": "primary_generation",
                        "gate": "terminal",
                        "output": {"primary_format": "text"},
                    }
                ],
            }
            workflow_path = tmp_path / "workflow.json"
            workflow_path.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_workflow_definition(workflow_path.relative_to(ROOT), root=ROOT)
            workflow["defaults"]["model_roles"]["primary_generation"]["prompt_cache_retention"] = "24h"
            workflow_path.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
            loaded = load_workflow_definition(workflow_path.relative_to(ROOT), root=ROOT)
            self.assertEqual(loaded.model_roles["primary_generation"].prompt_cache_retention, "24h")

    def test_committed_gpt55_workflows_declare_24h(self) -> None:
        for workflow_path in WORKFLOW_PATHS:
            with self.subTest(workflow=workflow_path):
                payload = json.loads((ROOT / workflow_path).read_text(encoding="utf-8"))
                roles = payload["defaults"]["model_roles"]
                self.assertEqual(roles["primary_generation"]["model"], "gpt-5.5-pro")
                self.assertEqual(roles["primary_generation"]["prompt_cache_retention"], "24h")
                self.assertEqual(roles["structural_processing"]["model"], "gpt-5.5")
                self.assertEqual(roles["structural_processing"]["prompt_cache_retention"], "24h")
                load_workflow_definition(workflow_path, root=ROOT)

    def test_supervised_end_to_end_workflow_locks_128000_outputs(self) -> None:
        path = ROOT / "automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertTrue(payload["stages"])
        for stage in payload["stages"]:
            self.assertEqual(stage["max_output_tokens"], 128000)

    def test_current_four_stage_stage3_has_no_tool_profile(self) -> None:
        path = ROOT / "automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        stage3 = [stage for stage in payload["stages"] if stage["stage_id"] == "draft_drop_in_packet"][0]
        self.assertNotIn("tool_profile_file", stage3)

    def test_synthetic_fact_sheet_migrated(self) -> None:
        fact_sheet = ROOT / "automation/examples/responses_runner_v2_synthetic/corpus/repo_fact_sheet.md"
        text = fact_sheet.read_text(encoding="utf-8")
        self.assertIn("gpt-5.5-pro", text)
        self.assertIn("gpt-5.5", text)
        self.assertIsNone(OLD_MODEL_PATTERN.search(text))

    def test_no_unallowlisted_gpt54_references_remain(self) -> None:
        offenders: list[str] = []
        for path in _iter_scan_files():
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            if OLD_MODEL_PATTERN.search(text):
                offenders.append(rel)
        self.assertEqual(offenders, [])

    def test_final_supervisory_packet_schema_requires_consolidation(self) -> None:
        schema_path = ROOT / "automation/task_packs/responses_runner_v2_supervised_end_to_end/schemas/final_supervisory_packet.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        incomplete_packet = {
            "packet_version": "responses_runner_v2.supervised_end_to_end.packet.v1",
            "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
            "summary": "x",
            "model_migration": {
                "primary_generation_model": "gpt-5.5-pro",
                "structural_processing_model": "gpt-5.5",
                "prompt_cache_retention": "24h",
                "max_output_tokens": 128000,
                "surfaces_updated": ["engine"],
            },
            "files": [{"path": "AGENTS.md", "action": "create", "category": "config", "purpose": "root"}],
            "agent_protocols": [
                {"agent": "operator_codex", "command_shape": "codex exec", "prompt_file": "p", "output_artifacts": ["o"], "json_transport": "stdout", "failure_behavior": "fail"},
                {"agent": "codex_review_agent", "command_shape": "codex exec", "prompt_file": "p", "output_artifacts": ["o"], "json_transport": "stdout", "failure_behavior": "fail"},
                {"agent": "claude_review_agent", "command_shape": "claude -p", "prompt_file": "p", "output_artifacts": ["o"], "json_transport": "stdout", "failure_behavior": "fail"},
            ],
            "review_protocol": {
                "operator_provisional_review": "yes",
                "codex_review": "yes",
                "claude_review": "yes",
                "consolidation": "yes",
                "operator_selective_acceptance": "yes",
                "json_transport": "stdout",
                "read_only_enforcement": "snapshot",
            },
            "failure_policies": [
                {"case_id": "completed_complete_artifact", "trigger": "t", "decision_rule": "d", "automation_action": "a", "human_pause_required": False},
                {"case_id": "failed_complete_artifact", "trigger": "t", "decision_rule": "d", "automation_action": "a", "human_pause_required": False},
                {"case_id": "failed_no_artifact", "trigger": "t", "decision_rule": "d", "automation_action": "a", "human_pause_required": False},
                {"case_id": "incomplete_output_limit", "trigger": "t", "decision_rule": "d", "automation_action": "a", "human_pause_required": True},
                {"case_id": "blocked_token_preflight", "trigger": "t", "decision_rule": "d", "automation_action": "a", "human_pause_required": True},
                {"case_id": "long_running_monitoring_anomaly", "trigger": "t", "decision_rule": "d", "automation_action": "a", "human_pause_required": True},
            ],
            "human_pause_conditions": [],
            "acceptance_checks": ["pytest"],
        }
        complete_packet = json.loads(json.dumps(incomplete_packet))
        complete_packet["agent_protocols"].append(
            {
                "agent": "consolidation_pass",
                "command_shape": "python3 automation/run_responses_supervisor_v2.py consolidate",
                "prompt_file": "automation/task_packs/responses_runner_v2_supervisor_internal/prompts/review_consolidation.md",
                "output_artifacts": ["consolidated_review.json"],
                "json_transport": "file",
                "failure_behavior": "fail",
            }
        )
        try:
            import jsonschema  # type: ignore
        except ImportError:
            agents = {item["agent"] for item in incomplete_packet["agent_protocols"]}
            self.assertNotIn("consolidation_pass", agents)
            agents = {item["agent"] for item in complete_packet["agent_protocols"]}
            self.assertIn("consolidation_pass", agents)
        else:
            validator = jsonschema.Draft202012Validator(schema)
            self.assertTrue(list(validator.iter_errors(incomplete_packet)))
            validator.validate(complete_packet)


if __name__ == "__main__":
    unittest.main()
