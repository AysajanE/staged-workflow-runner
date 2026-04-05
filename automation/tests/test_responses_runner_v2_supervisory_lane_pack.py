from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2.attachments import expand_attachment_target
from automation.responses_runner_v2.contracts import RuntimeOptions
from automation.responses_runner_v2.workflow import run_workflow


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "automation" / "task_packs" / "responses_runner_v2_supervisory_lane"
WORKFLOW_PATH = (
    "automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json"
)


class ResponsesRunnerV2SupervisoryLanePackTests(unittest.TestCase):
    def _load_manifest(self, filename: str) -> dict:
        return json.loads((PACK_ROOT / "inputs" / filename).read_text(encoding="utf-8"))

    def _materialized_paths(self, manifest: dict, field_name: str) -> set[str]:
        paths: set[str] = set()
        for entry in manifest[field_name]:
            if entry["kind"] == "file":
                paths.add(str(entry["path"]))
                continue
            target = ROOT / str(entry["path"])
            expanded = expand_attachment_target(
                ROOT,
                target,
                exclude_globs=tuple(str(item) for item in entry.get("exclude_globs", [])),
            )
            for expanded_path in expanded:
                paths.add(expanded_path.relative_to(ROOT).as_posix())
        return paths

    def test_workflow_loads_and_dry_runs(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="responses-runner-v2-supervisory-lane-pack",
                output_root=Path(tmp).relative_to(ROOT),
                dry_run=True,
            )
            result = run_workflow(
                workflow_file=WORKFLOW_PATH,
                runtime=runtime,
                root=ROOT,
            )
            run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))

        self.assertEqual(
            run_manifest["workflow_id"],
            "responses_runner_v2_supervisory_lane_self_improvement",
        )
        self.assertEqual(len(run_manifest["stages"]), 3)
        self.assertEqual(run_manifest["status"], "created")

    def test_workflow_operator_inputs_are_self_contained(self) -> None:
        workflow = json.loads((PACK_ROOT / "workflows" / "three_stage.workflow.json").read_text(encoding="utf-8"))
        operator_requirements = workflow["operator_requirements"]
        self.assertEqual(operator_requirements["minimum_primary_job_inputs"], 0)
        self.assertEqual(operator_requirements["maximum_primary_job_inputs"], 0)
        self.assertFalse(operator_requirements["allow_reference_context"])

    def test_workflow_uses_stage_specific_web_search_profiles(self) -> None:
        workflow = json.loads((PACK_ROOT / "workflows" / "three_stage.workflow.json").read_text(encoding="utf-8"))
        expected_profiles = [
            "../tools/stage1_web_search.profile.json",
            "../tools/stage2_web_search.profile.json",
            "../tools/stage3_web_search.profile.json",
        ]
        self.assertEqual(
            [stage["tool_profile_file"] for stage in workflow["stages"]],
            expected_profiles,
        )

    def test_input_manifests_reference_existing_paths(self) -> None:
        manifest_paths = sorted((PACK_ROOT / "inputs").glob("*.input_manifest.json"))
        self.assertEqual(len(manifest_paths), 3)

        for manifest_path in manifest_paths:
            with self.subTest(manifest_path=manifest_path.name):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for field_name in (
                    "primary_job_inputs",
                    "reviewed_handoff_inputs",
                    "attached_repository_files",
                    "reference_context",
                ):
                    entries = manifest[field_name]
                    paths = [str(entry["path"]) for entry in entries]
                    self.assertEqual(
                        len(paths),
                        len(set(paths)),
                        f"{manifest_path} contains duplicate paths in {field_name}",
                    )
                    for entry in entries:
                        entry_path = str(entry["path"])
                        self.assertFalse(
                            entry_path.startswith(".local/"),
                            f"{manifest_path} contains machine-local path {entry_path!r}",
                        )
                        self.assertTrue(
                            (ROOT / entry_path).exists(),
                            f"{manifest_path} references missing path {entry_path!r}",
                        )

                for field_name in (
                    "primary_job_inputs",
                    "reviewed_handoff_inputs",
                    "attached_repository_files",
                    "reference_context",
                ):
                    materialized = []
                    for entry in manifest[field_name]:
                        if entry["kind"] == "file":
                            materialized.append(str(entry["path"]))
                            continue
                        target = ROOT / str(entry["path"])
                        materialized.extend(
                            item.relative_to(ROOT).as_posix()
                            for item in expand_attachment_target(
                                ROOT,
                                target,
                                exclude_globs=tuple(
                                    str(glob) for glob in entry.get("exclude_globs", [])
                                ),
                            )
                        )
                    self.assertEqual(
                        len(materialized),
                        len(set(materialized)),
                        f"{manifest_path} materializes duplicate files in {field_name}",
                    )

    def test_stage1_attaches_real_self_scaffold_surfaces(self) -> None:
        manifest = self._load_manifest("stage1.input_manifest.json")
        attached_paths = self._materialized_paths(manifest, "attached_repository_files")

        expected_paths = {
            "automation/task_packs/responses_runner_v2_supervisory_lane/README.md",
            "automation/task_packs/responses_runner_v2_supervisory_lane/shared_instructions.md",
            "automation/task_packs/responses_runner_v2_supervisory_lane/prompts/stage1_architecture_blueprint.md",
            "automation/task_packs/responses_runner_v2_supervisory_lane/prompts/stage2_draft_drop_in_packet.md",
            "automation/task_packs/responses_runner_v2_supervisory_lane/prompts/stage3_final_drop_in_packet.md",
            "automation/task_packs/responses_runner_v2_supervisory_lane/inputs/stage1.input_manifest.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/inputs/stage2.input_manifest.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/inputs/stage3.input_manifest.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/tools/stage1_web_search.profile.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/tools/stage2_web_search.profile.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/tools/stage3_web_search.profile.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json",
            "automation/task_packs/responses_runner_v2_supervisory_lane/schemas/final_drop_in_packet.schema.json",
        }
        self.assertTrue(expected_paths.issubset(attached_paths))

    def test_manifests_taper_by_stage(self) -> None:
        stage1 = self._load_manifest("stage1.input_manifest.json")
        stage2 = self._load_manifest("stage2.input_manifest.json")
        stage3 = self._load_manifest("stage3.input_manifest.json")

        stage1_attached = self._materialized_paths(stage1, "attached_repository_files")
        stage2_attached = self._materialized_paths(stage2, "attached_repository_files")
        stage3_attached = self._materialized_paths(stage3, "attached_repository_files")
        stage1_reference = self._materialized_paths(stage1, "reference_context")
        stage2_reference = self._materialized_paths(stage2, "reference_context")
        stage3_reference = self._materialized_paths(stage3, "reference_context")

        self.assertGreater(len(stage1_attached), len(stage2_attached))
        self.assertGreater(len(stage2_attached), len(stage3_attached))
        self.assertGreater(len(stage1_reference), len(stage2_reference))
        self.assertGreater(len(stage2_reference), len(stage3_reference))

        self.assertIn("TEAM_ONBOARDING.md", stage1_attached)
        self.assertNotIn("TEAM_ONBOARDING.md", stage2_attached)
        self.assertNotIn("TEAM_ONBOARDING.md", stage3_attached)

        self.assertIn(
            "automation/task_packs/responses_runner_v2_supervisory_lane/prompts/stage1_architecture_blueprint.md",
            stage1_attached,
        )
        self.assertNotIn(
            "automation/task_packs/responses_runner_v2_supervisory_lane/prompts/stage1_architecture_blueprint.md",
            stage2_attached,
        )
        self.assertNotIn(
            "automation/task_packs/responses_runner_v2_supervisory_lane/prompts/stage1_architecture_blueprint.md",
            stage3_attached,
        )

        self.assertIn(
            "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
            stage1_reference,
        )
        self.assertNotIn(
            "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
            stage2_reference,
        )
        self.assertNotIn(
            "automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
            stage3_reference,
        )

        self.assertIn(
            "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage1.input_manifest.json",
            stage2_reference,
        )
        self.assertNotIn(
            "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage1.input_manifest.json",
            stage3_reference,
        )

    def test_directory_entries_are_used_only_where_they_carry_majority_signal(self) -> None:
        stage1 = self._load_manifest("stage1.input_manifest.json")
        stage2 = self._load_manifest("stage2.input_manifest.json")
        stage3 = self._load_manifest("stage3.input_manifest.json")

        stage1_dirs = {entry["path"] for entry in stage1["attached_repository_files"] if entry["kind"] == "directory"}
        stage2_dirs = {entry["path"] for entry in stage2["attached_repository_files"] if entry["kind"] == "directory"}
        stage3_dirs = {entry["path"] for entry in stage3["attached_repository_files"] if entry["kind"] == "directory"}
        stage1_ref_dirs = {entry["path"] for entry in stage1["reference_context"] if entry["kind"] == "directory"}

        self.assertEqual(
            stage1_dirs,
            {
                "automation/responses_runner_v2",
                "automation/tests",
                "automation/task_packs/responses_runner_v2_supervisory_lane",
            },
        )
        self.assertEqual(
            stage2_dirs,
            {
                "automation/responses_runner_v2",
                "automation/task_packs/responses_runner_v2_supervisory_lane",
            },
        )
        self.assertEqual(stage3_dirs, {"automation/responses_runner_v2"})
        self.assertEqual(stage1_ref_dirs, {"automation/examples/responses_runner_v2_synthetic"})

    def test_web_search_profiles_match_requested_tool_caps(self) -> None:
        expected = {
            "stage1_web_search.profile.json": 128,
            "stage2_web_search.profile.json": 96,
            "stage3_web_search.profile.json": 64,
        }
        for filename, max_tool_calls in expected.items():
            with self.subTest(filename=filename):
                payload = json.loads((PACK_ROOT / "tools" / filename).read_text(encoding="utf-8"))
                self.assertEqual(payload["max_tool_calls"], max_tool_calls)
                self.assertTrue(payload["parallel_tool_calls"])
                self.assertEqual(payload["tool_choice"], "auto")
                self.assertEqual(payload["tools"][0]["type"], "web_search")

    def test_prompt_layer_separation_is_explicit(self) -> None:
        shared = (PACK_ROOT / "shared_instructions.md").read_text(encoding="utf-8")
        brief = (PACK_ROOT / "corpus" / "task_brief.md").read_text(encoding="utf-8")
        final_contract = (PACK_ROOT / "corpus" / "final_deliverable_contract.md").read_text(encoding="utf-8")
        stage1 = (PACK_ROOT / "prompts" / "stage1_architecture_blueprint.md").read_text(encoding="utf-8")
        stage2 = (PACK_ROOT / "prompts" / "stage2_draft_drop_in_packet.md").read_text(encoding="utf-8")
        stage3 = (PACK_ROOT / "prompts" / "stage3_final_drop_in_packet.md").read_text(encoding="utf-8")
        self.assertIn("<layer_separation_rules>", shared)
        self.assertIn("<completeness_contract>", shared)
        self.assertNotIn("<stage_economics_rules>", shared)
        self.assertNotIn("<scope_rules>", shared)
        self.assertIn("<requirement_source_rules>", shared)
        self.assertIn("locking a repo-fit architecture and integration boundary in stage 1", shared)
        self.assertIn("converting the approved architecture into a minimum-change draft drop-in package in stage 2", shared)
        self.assertIn("hardening the approved draft into a final drop-in-ready package in stage 3", shared)
        self.assertIn("The current run is still manually reviewed between stages.", shared)
        self.assertIn("For this current meta-run, stage reviews remain manual.", brief)
        self.assertIn("## Quality And Stage-Economics Rules", brief)
        self.assertIn("Treat the primary job inputs as the controlling source for target-system requirements.", stage1)
        self.assertIn("Treat the approved stage-one architecture and the primary job inputs as the controlling source for target-system requirements.", stage2)
        self.assertIn("Treat the approved architecture, approved reviewer notes, and primary job inputs as the controlling source for target-system requirements.", stage3)
        self.assertNotIn("Keep the future lane almost fully AI-operated after the initial human clarification gate.", stage1)
        self.assertNotIn("The package must explicitly encode:", stage2)
        self.assertNotIn("The final package must explicitly cover:", stage3)
        self.assertIn("## Testing Rule", final_contract)
        self.assertIn("red/green TDD", final_contract)
        self.assertIn("define the automated tests for each new or changed behavior before implementation", final_contract)
        self.assertIn("| phase | check_id | command_or_method | expected_result | why_it_matters |", stage2)
        self.assertIn("`phase` must be `red` or `green`.", stage2)
        self.assertIn("A `red` row must fail against the pre-change state", stage2)
        self.assertIn("| phase | check_id | command_or_method | expected_result | acceptance_reason |", stage3)
        self.assertIn("`phase` must be `red` or `green`.", stage3)
        self.assertIn("A `green` row must pass after the final package is applied.", stage3)

    def test_pack_does_not_prebundle_review_scaffold_artifacts(self) -> None:
        self.assertFalse((PACK_ROOT / "review_checklists").exists())
        self.assertFalse((PACK_ROOT / "review_templates").exists())

    def test_final_schema_requires_agents_and_required_failure_cases(self) -> None:
        schema = json.loads(
            (
                PACK_ROOT
                / "schemas"
                / "final_drop_in_packet.schema.json"
            ).read_text(encoding="utf-8")
        )

        self.assertEqual(
            schema["properties"]["workflow_id"]["const"],
            "responses_runner_v2_supervisory_lane_self_improvement",
        )

        file_contains = schema["properties"]["files"]["allOf"]
        self.assertEqual(len(file_contains), 1)
        self.assertEqual(
            file_contains[0]["contains"]["properties"]["path"]["const"],
            "AGENTS.md",
        )

        policy_case_ids = {
            item["contains"]["properties"]["case_id"]["const"]
            for item in schema["properties"]["failure_handling_policies"]["allOf"]
        }
        self.assertEqual(
            policy_case_ids,
            {
                "failed_retrievable_artifact",
                "failed_rerun_as_is",
                "failed_unrecoverable",
                "incomplete_output_limit",
            },
        )


if __name__ == "__main__":
    unittest.main()
