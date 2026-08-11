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
    "automation/examples/responses_runner_v2_evidence_synthesis/workflows/document_evidence_synthesis.workflow.json",
    "automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json",
    "automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json",
    "automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json",
]

STATIC_SCAN_TARGETS = [
    "AGENTS.md",
    "README.md",
    "docs/runbooks/responses-runner-v2.md",
    "automation/run_responses_supervisor_v2.py",
    "automation/responses_runner_v2",
    "automation/task_packs",
    "automation/examples",
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
    def test_engine_defaults_are_durable_gpt56_alias(self) -> None:
        self.assertEqual(contracts.DEFAULT_PRIMARY_MODEL, "gpt-5.6")
        self.assertEqual(contracts.DEFAULT_STRUCTURAL_MODEL, "gpt-5.6")
        self.assertEqual(
            contracts.RuntimeOptions().prompt_cache_key_strategy,
            "stable_lane_v1",
        )

    def test_gpt56_model_caps_and_base_model_normalization(self) -> None:
        self.assertEqual(contracts.base_model_name("gpt-5.6"), "gpt-5.6")
        self.assertEqual(contracts.base_model_name("gpt-5.6-sol-2026-08-01"), "gpt-5.6-sol")
        self.assertEqual(contracts.model_context_window("gpt-5.6"), 1_050_000)
        self.assertEqual(contracts.model_max_output_tokens("gpt-5.6"), 128000)

    def test_gpt56_uses_current_cache_and_reasoning_contract(self) -> None:
        for bad_options in (
            {"prompt_cache_retention": "24h", "prompt_cache_ttl": None, "reasoning_mode": "pro"},
            {"prompt_cache_retention": None, "prompt_cache_ttl": "24h", "reasoning_mode": "pro"},
            {"prompt_cache_retention": None, "prompt_cache_ttl": "30m", "reasoning_mode": "turbo"},
        ):
            with self.subTest(bad_options=bad_options), self.assertRaises(SystemExit):
                contracts.validate_model_options(
                    model="gpt-5.6",
                    max_output_tokens=128000,
                    text_format="text",
                    **bad_options,
                )

        contracts.validate_model_options(
            model="gpt-5.6",
            max_output_tokens=128000,
            prompt_cache_retention=None,
            prompt_cache_ttl="30m",
            reasoning_mode="pro",
            text_format="text",
        )

    def test_cache_keys_are_bounded_and_separate_incompatible_lanes(self) -> None:
        prefix = "stable:v1:" + ("workflow-segment-" * 12) + ":gpt-5.6"
        primary = contracts.build_prompt_cache_key(prefix, "primary_generation")
        sidecar = contracts.build_prompt_cache_key(prefix, "structural_processing")
        self.assertLessEqual(len(primary), contracts.MAX_PROMPT_CACHE_KEY_LENGTH)
        self.assertLessEqual(len(sidecar), contracts.MAX_PROMPT_CACHE_KEY_LENGTH)
        self.assertNotEqual(primary, sidecar)
        self.assertEqual(
            primary,
            contracts.build_prompt_cache_key(prefix, "primary_generation"),
        )

    def test_workflow_loader_requires_v2_cache_fields(self) -> None:
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
            role = {
                "model": "gpt-5.6",
                "reasoning_effort": "high",
                "reasoning_mode": "standard",
                "verbosity": "medium",
                "prompt_cache_mode": "implicit",
            }
            workflow = {
                "schema_version": "responses_runner_v2.workflow_manifest.v2",
                "workflow_id": "tmp_gpt56_missing_cache",
                "workflow_mode": "one_pass",
                "description": "Temporary workflow missing cache TTL.",
                "assurance_profile": "critical",
                "shared_instructions_file": instructions.as_posix(),
                "defaults": {
                    "model_roles": {
                        "primary_generation": dict(role),
                        "structural_processing": dict(role),
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
                        "max_input_tokens": 700000,
                        "max_output_tokens": 1000,
                        "gate": "terminal",
                        "output": {"primary_format": "text"},
                    }
                ],
            }
            workflow_path = tmp_path / "workflow.json"
            workflow_path.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                load_workflow_definition(workflow_path.relative_to(ROOT), root=ROOT)

            for profile in workflow["defaults"]["model_roles"].values():
                profile["prompt_cache_ttl"] = "30m"
            workflow_path.write_text(json.dumps(workflow, indent=2) + "\n", encoding="utf-8")
            loaded = load_workflow_definition(workflow_path.relative_to(ROOT), root=ROOT)
            self.assertEqual(loaded.model_roles["primary_generation"].prompt_cache_ttl, "30m")

    def test_all_active_workflows_use_gpt56_and_profile_budgets(self) -> None:
        for workflow_path in WORKFLOW_PATHS:
            with self.subTest(workflow=workflow_path):
                payload = json.loads((ROOT / workflow_path).read_text(encoding="utf-8"))
                self.assertEqual(payload["schema_version"], "responses_runner_v2.workflow_manifest.v2")
                expected_profile = (
                    "reviewed"
                    if "responses_runner_v2_evidence_synthesis" in workflow_path
                    else "critical"
                )
                self.assertEqual(payload["assurance_profile"], expected_profile)
                roles = payload["defaults"]["model_roles"]
                self.assertEqual(roles["primary_generation"]["model"], "gpt-5.6")
                self.assertEqual(roles["primary_generation"]["reasoning_mode"], "pro")
                self.assertEqual(roles["primary_generation"]["prompt_cache_mode"], "implicit")
                self.assertEqual(roles["primary_generation"]["prompt_cache_ttl"], "30m")
                self.assertEqual(roles["structural_processing"]["model"], "gpt-5.6")
                self.assertEqual(roles["structural_processing"]["reasoning_mode"], "standard")
                self.assertEqual(roles["structural_processing"]["prompt_cache_mode"], "implicit")
                self.assertEqual(roles["structural_processing"]["prompt_cache_ttl"], "30m")
                for stage in payload["stages"]:
                    self.assertGreater(stage["max_input_tokens"], 0)
                    self.assertLessEqual(
                        stage["max_input_tokens"] + stage["max_output_tokens"] + 21000,
                        1_050_000,
                    )
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
        self.assertIn("gpt-5.6", text)
        self.assertNotIn("gpt-5.5", text)
        self.assertIsNone(OLD_MODEL_PATTERN.search(text))

    def test_no_unallowlisted_gpt54_references_remain(self) -> None:
        allowlist_path = ROOT / "automation/responses_runner_v2/model_migration_allowlist.json"
        allowlist = json.loads(allowlist_path.read_text(encoding="utf-8"))["allowed_references"]
        allowed_by_path: dict[str, list[re.Pattern[str]]] = {}
        for entry in allowlist:
            path = ROOT / entry["path"]
            self.assertTrue(path.is_file(), entry)
            pattern = re.compile(entry["pattern"])
            self.assertIsNotNone(pattern.search(path.read_text(encoding="utf-8")), entry)
            allowed_by_path.setdefault(entry["path"], []).append(pattern)

        offenders: list[str] = []
        for path in _iter_scan_files():
            rel = path.relative_to(ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in OLD_MODEL_PATTERN.finditer(text):
                if not any(pattern.fullmatch(match.group(0)) for pattern in allowed_by_path.get(rel, [])):
                    offenders.append(f"{rel}:{match.group(0)}")
        self.assertEqual(offenders, [])

    def test_final_supervisory_packet_schema_requires_consolidation(self) -> None:
        schema_path = ROOT / "automation/task_packs/responses_runner_v2_supervised_end_to_end/schemas/final_supervisory_packet.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        incomplete_packet = {
            "packet_version": "responses_runner_v2.supervised_end_to_end.packet.v1",
            "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
            "summary": "x",
            "model_migration": {
                "primary_generation_model": "gpt-5.6",
                "primary_reasoning_mode": "pro",
                "structural_processing_model": "gpt-5.6",
                "structural_reasoning_mode": "standard",
                "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
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

    def test_final_bundle_versions_do_not_mix_current_and_historical_model_fields(self) -> None:
        import jsonschema  # type: ignore

        schema = json.loads(
            (
                ROOT
                / "automation/responses_runner_v2/schemas/final_implementation_bundle.schema.json"
            ).read_text(encoding="utf-8")
        )
        current, historical = schema["properties"]["model_migration_summary"]["oneOf"]
        current_payload = {
            "primary_generation_model": "gpt-5.6",
            "primary_reasoning_mode": "pro",
            "structural_processing_model": "gpt-5.6",
            "structural_reasoning_mode": "standard",
            "prompt_cache_options": {"mode": "implicit", "ttl": "30m"},
            "surfaces_updated": ["engine"],
        }
        historical_payload = {
            "primary_generation_model": "gpt-5.5-pro",
            "structural_processing_model": "gpt-5.5",
            "prompt_cache_retention": "24h",
            "surfaces_updated": ["frozen_fixture"],
        }
        jsonschema.Draft202012Validator(current).validate(current_payload)
        jsonschema.Draft202012Validator(historical).validate(historical_payload)
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(current).validate(
                {**current_payload, "prompt_cache_retention": "24h"}
            )
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(historical).validate(
                {**historical_payload, "prompt_cache_options": {"mode": "implicit", "ttl": "30m"}}
            )


if __name__ == "__main__":
    unittest.main()
