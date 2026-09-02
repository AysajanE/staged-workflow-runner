from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2 import contracts
from automation.responses_runner_v2.attachments import needs_context_wrapper
from automation.responses_runner_v2.pack_loader import (
    load_input_manifest,
    load_tool_profile,
    load_workflow_definition,
)


ROOT = Path(__file__).resolve().parents[2]


class ResponsesRunnerV2ContractsTests(unittest.TestCase):
    SYNTHETIC_SHARED_INSTRUCTIONS = (
        ROOT / "automation/examples/responses_runner_v2_synthetic/shared_instructions.md"
    ).as_posix()
    SYNTHETIC_ONE_PASS_PROMPT = (
        ROOT / "automation/examples/responses_runner_v2_synthetic/prompts/one_pass_task.md"
    ).as_posix()
    SYNTHETIC_ONE_PASS_INPUT = (
        ROOT / "automation/examples/responses_runner_v2_synthetic/inputs/one_pass.input_manifest.json"
    ).as_posix()

    def test_prompt_cache_retention_normalization(self) -> None:
        self.assertEqual(contracts.normalize_prompt_cache_retention("in_memory"), "in_memory")
        self.assertEqual(contracts.normalize_prompt_cache_retention("24h"), "24h")
        self.assertIsNone(contracts.normalize_prompt_cache_retention(None))

    def test_engine_defaults_are_durable_gpt56_alias(self) -> None:
        self.assertEqual(contracts.DEFAULT_PRIMARY_MODEL, "gpt-5.6")
        self.assertEqual(contracts.DEFAULT_STRUCTURAL_MODEL, "gpt-5.6")

    def test_gpt55_base_model_caps_and_prompt_cache_validation(self) -> None:
        self.assertEqual(contracts.base_model_name("gpt-5.5-pro-2026-04-23"), "gpt-5.5-pro")
        self.assertEqual(contracts.base_model_name("gpt-5.5-2026-04-23"), "gpt-5.5")
        self.assertEqual(contracts.model_max_output_tokens("gpt-5.5-pro"), 128000)
        self.assertEqual(contracts.model_max_output_tokens("gpt-5.5"), 128000)
        contracts.validate_model_options(
            model="gpt-5.5-pro",
            max_output_tokens=128000,
            prompt_cache_retention="24h",
            text_format="text",
        )
        with self.assertRaises(SystemExit):
            contracts.validate_model_options(model="gpt-5.5-pro", max_output_tokens=128000, prompt_cache_retention="in_memory", text_format="text")

    def test_load_one_pass_workflow_definition(self) -> None:
        workflow = load_workflow_definition(
            "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
            root=ROOT,
        )
        self.assertEqual(workflow.workflow_id, "synthetic_one_pass")
        self.assertEqual(len(workflow.stages), 1)
        self.assertEqual(workflow.stages[0].stage_id, "draft_summary")
        self.assertEqual(workflow.stages[0].gate.value, "terminal")

    def test_load_reviewed_three_stage_workflow_definition(self) -> None:
        workflow = load_workflow_definition(
            "automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
            root=ROOT,
        )
        self.assertEqual(workflow.workflow_mode, "reviewed_three_stage")
        self.assertEqual(len(workflow.stages), 3)
        self.assertEqual(workflow.stages[1].carry_forward.handoff_from_stage_id, "proposal")
        self.assertEqual(workflow.stages[0].gate.value, "human")

    def test_load_input_manifest(self) -> None:
        manifest = load_input_manifest(
            "automation/examples/responses_runner_v2_synthetic/inputs/one_pass.input_manifest.json",
            root=ROOT,
        )
        self.assertEqual(manifest["manifest_id"], "synthetic_one_pass")
        self.assertEqual(len(manifest["primary_job_inputs"]), 1)
        self.assertEqual(len(manifest["attached_repository_files"]), 1)

    def test_no_tools_profile_normalizes_to_empty_toolset(self) -> None:
        tool_profile = load_tool_profile(
            "automation/examples/responses_runner_v2_synthetic/tools/no_tools.profile.json",
            root=ROOT,
        )
        self.assertEqual(tool_profile, {})

    def test_explicit_root_wins_over_environment_override(self) -> None:
        with tempfile.TemporaryDirectory() as explicit_tmp, tempfile.TemporaryDirectory() as env_tmp:
            explicit_root = Path(explicit_tmp) / "target" / "workspace"
            env_root = Path(env_tmp) / "different" / "workspace"
            explicit_root.mkdir(parents=True)
            env_root.mkdir(parents=True)
            with mock.patch.dict(
                os.environ,
                {contracts.REPO_ROOT_ENV_VAR: str(env_root)},
                clear=False,
            ):
                self.assertEqual(contracts.repo_root(explicit_root), explicit_root.resolve())

    def test_environment_override_is_used_exactly_when_no_explicit_root_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as env_tmp:
            env_root = Path(env_tmp) / "workspace" / "nested"
            env_root.mkdir(parents=True)
            with mock.patch.dict(
                os.environ,
                {contracts.REPO_ROOT_ENV_VAR: str(env_root)},
                clear=False,
            ):
                self.assertEqual(contracts.repo_root(), env_root.resolve())

    def test_current_working_directory_is_used_as_is_when_no_root_is_supplied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp) / "target" / "workspace"
            workspace.mkdir(parents=True)
            original_cwd = Path.cwd()
            try:
                with mock.patch.dict(os.environ, {}, clear=False):
                    os.environ.pop(contracts.REPO_ROOT_ENV_VAR, None)
                    os.chdir(workspace)
                    self.assertEqual(contracts.repo_root(), workspace.resolve())
            finally:
                os.chdir(original_cwd)

    def test_workspace_root_must_be_a_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root_file = Path(tmp) / "not_a_directory.txt"
            root_file.write_text("x\n", encoding="utf-8")
            with self.assertRaises(SystemExit):
                contracts.repo_root(root_file)

    def test_unsupported_text_suffix_requires_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "sample_contract.sol"
            sample.write_text("contract Sample {}\n", encoding="utf-8")
            self.assertTrue(needs_context_wrapper(sample))

    def test_extensionless_text_file_requires_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sample = Path(tmp) / "Makefile"
            sample.write_text("test:\n\tpython -m pytest\n", encoding="utf-8")
            self.assertTrue(needs_context_wrapper(sample))


if __name__ == "__main__":
    unittest.main()
