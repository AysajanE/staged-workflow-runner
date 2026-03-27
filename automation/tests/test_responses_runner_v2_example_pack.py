from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.responses_runner_v2.contracts import RuntimeOptions
from automation.responses_runner_v2.review_bundle import create_review_bundle
from automation.responses_runner_v2.workflow import run_workflow

from automation.tests.test_responses_runner_v2_workflow import FakeClient


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "automation" / "examples" / "responses_runner_v2_synthetic"

WORKFLOW_CASES = [
    ("one_pass.workflow.json", "synthetic_one_pass", 1),
    ("two_pass.workflow.json", "synthetic_two_pass", 2),
    ("reviewed_three_stage.workflow.json", "synthetic_reviewed_three_stage", 3),
]


class ResponsesRunnerV2ExamplePackTests(unittest.TestCase):
    def test_example_workflows_load_and_dry_run(self) -> None:
        for workflow_filename, workflow_id, stage_count in WORKFLOW_CASES:
            with self.subTest(workflow_filename=workflow_filename):
                with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
                    runtime = RuntimeOptions(
                        run_name=f"{workflow_id}-dry-run",
                        output_root=Path(tmp).relative_to(ROOT),
                        dry_run=True,
                    )
                    result = run_workflow(
                        workflow_file=f"automation/examples/responses_runner_v2_synthetic/workflows/{workflow_filename}",
                        runtime=runtime,
                        root=ROOT,
                    )
                    run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))
                    self.assertEqual(run_manifest["workflow_id"], workflow_id)
                    self.assertEqual(len(run_manifest["stages"]), stage_count)
                    self.assertEqual(run_manifest["status"], "created")

    def test_one_pass_example_live_run_writes_structured_output(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            runtime = RuntimeOptions(
                run_name="synthetic-one-pass-live",
                output_root=Path(tmp).relative_to(ROOT),
                wait=True,
            )
            result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json",
                runtime=runtime,
                client=FakeClient(),
                root=ROOT,
            )
            run_manifest = json.loads((ROOT / result["run_manifest_path"]).read_text(encoding="utf-8"))
            stage_dir = ROOT / run_manifest["stages"][0]["stage_dir"]
            self.assertEqual(run_manifest["status"], "completed")
            self.assertTrue((stage_dir / "output.structured.json").exists())

    def test_reviewed_three_stage_proof_path(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            output_root = Path(tmp).relative_to(ROOT)
            client = FakeClient()

            stage1 = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_name="synthetic-reviewed-proof",
                    output_root=output_root,
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )
            self.assertEqual(stage1["status"], "waiting_for_review")
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            run_id = run_manifest["run_id"]

            notes1 = run_dir / "stage1.review.md"
            notes1.write_text("# approved\n", encoding="utf-8")
            bundle1 = run_dir / "stage1.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle1.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="proposal",
                source_run_id=run_id,
                primary_artifact_markdown=(run_dir / "stages/01_proposal/response.final.md").relative_to(ROOT),
                response_artifact_json=(run_dir / "stages/01_proposal/response.final.json").relative_to(ROOT),
                reviewer_notes=notes1.relative_to(ROOT),
            )

            stage2 = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    output_root=output_root,
                    review_bundles=[bundle1.relative_to(ROOT).as_posix()],
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )
            self.assertEqual(stage2["status"], "waiting_for_review")

            notes2 = run_dir / "stage2.review.md"
            notes2.write_text("# approved\n", encoding="utf-8")
            bundle2 = run_dir / "stage2.review_bundle.json"
            create_review_bundle(
                root=ROOT,
                output_path=bundle2.relative_to(ROOT),
                workflow_id="synthetic_reviewed_three_stage",
                source_stage_id="revision",
                source_run_id=run_id,
                primary_artifact_markdown=(run_dir / "stages/02_revision/response.final.md").relative_to(ROOT),
                response_artifact_json=(run_dir / "stages/02_revision/response.final.json").relative_to(ROOT),
                reviewer_notes=notes2.relative_to(ROOT),
            )

            final_result = run_workflow(
                workflow_file="automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    output_root=output_root,
                    review_bundles=[bundle2.relative_to(ROOT).as_posix()],
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )

            final_manifest = json.loads((ROOT / final_result["run_manifest_path"]).read_text(encoding="utf-8"))
            final_stage_dir = ROOT / final_manifest["stages"][-1]["stage_dir"]
            self.assertEqual(final_manifest["status"], "completed")
            self.assertTrue((final_stage_dir / "output.structured.json").exists())

    def test_static_input_manifests_only_reference_tracked_pack_assets(self) -> None:
        manifest_paths = sorted((PACK_ROOT / "inputs").glob("*.input_manifest.json"))
        self.assertEqual(len(manifest_paths), 6)

        for manifest_path in manifest_paths:
            with self.subTest(manifest_path=manifest_path.name):
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                for field_name in (
                    "primary_job_inputs",
                    "reviewed_handoff_inputs",
                    "attached_repository_files",
                    "reference_context",
                ):
                    for entry in manifest[field_name]:
                        entry_path = str(entry["path"])
                        self.assertFalse(
                            entry_path.startswith(".local/"),
                            f"{manifest_path} contains machine-local snapshot path {entry_path!r}",
                        )
                        self.assertTrue(
                            entry_path.startswith("automation/examples/responses_runner_v2_synthetic/"),
                            f"{manifest_path} contains non-pack path {entry_path!r}",
                        )
                        self.assertTrue(
                            (ROOT / entry_path).exists(),
                            f"{manifest_path} references missing path {entry_path!r}",
                        )


if __name__ == "__main__":
    unittest.main()
