from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2.contracts import RuntimeOptions
from automation.responses_runner_v2.pack_loader import load_input_manifest, load_workflow_definition
from automation.responses_runner_v2.workflow import run_workflow


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "automation" / "task_packs" / "gstack_design_to_po_playbook"
WORKFLOW_PATH = (
    "automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json"
)


class ResponsesRunnerV2GstackPlaybookPackTests(unittest.TestCase):
    def test_workflow_shape_and_runtime_input_contract(self) -> None:
        workflow = load_workflow_definition(WORKFLOW_PATH, root=ROOT)

        self.assertEqual(workflow.workflow_id, "gstack_design_to_po_playbook")
        self.assertEqual(workflow.workflow_mode, "custom_ordered")
        self.assertEqual(len(workflow.stages), 5)
        self.assertEqual(workflow.operator_requirements["minimum_primary_job_inputs"], 1)
        self.assertEqual(workflow.operator_requirements["maximum_primary_job_inputs"], 4)
        self.assertTrue(workflow.operator_requirements["allow_reference_context"])
        self.assertEqual([stage.gate.value for stage in workflow.stages[:-1]], ["review_required"] * 4)
        self.assertEqual(workflow.stages[-1].gate.value, "terminal")

    def test_static_input_manifests_reference_existing_paths(self) -> None:
        manifest_paths = sorted((PACK_ROOT / "inputs").glob("*.input_manifest.json"))
        self.assertEqual(len(manifest_paths), 5)

        for manifest_path in manifest_paths:
            with self.subTest(manifest_path=manifest_path.name):
                manifest = load_input_manifest(manifest_path.relative_to(ROOT), root=ROOT)
                self.assertEqual(manifest["primary_job_inputs"], [])
                for field_name in (
                    "reviewed_handoff_inputs",
                    "attached_repository_files",
                    "reference_context",
                ):
                    for entry in manifest[field_name]:
                        entry_path = str(entry.path)
                        self.assertFalse(entry_path.startswith(".local/"))
                        self.assertTrue((ROOT / entry_path).exists(), entry_path)

    def test_stage1_dry_run_requires_operator_primary_input(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            temp_dir = Path(tmp)
            primary = temp_dir / "approved_gstack_brief.md"
            primary.write_text("# Approved Brief\n\nBuild the reviewed thing.\n", encoding="utf-8")
            runtime = RuntimeOptions(
                run_name="gstack-design-to-po-playbook-pack",
                output_root=temp_dir.relative_to(ROOT),
                primary_job_inputs=[primary.relative_to(ROOT).as_posix()],
                dry_run=True,
            )

            result = run_workflow(
                workflow_file=WORKFLOW_PATH,
                runtime=runtime,
                root=ROOT,
            )
            run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))

        self.assertEqual(run_manifest["workflow_id"], "gstack_design_to_po_playbook")
        self.assertEqual(
            run_manifest["operator_overrides"]["primary_job_inputs"],
            [primary.relative_to(ROOT).as_posix()],
        )
        self.assertEqual(run_manifest["stages"][0]["stage_id"], "source_authority_map")
        self.assertEqual(run_manifest["status"], "created")


if __name__ == "__main__":
    unittest.main()
