from __future__ import annotations

import json
import unittest
from pathlib import Path

from automation.responses_runner_v2 import artifacts
from automation.responses_runner_v2.review_bundle import load_review_bundle
from automation.responses_runner_v2.schema_validation import validate_contract
from automation.responses_runner_v2.workflow import resume_stage


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "automation/tests/fixtures/persisted_v1"


class PersistedV1CompatibilityTests(unittest.TestCase):
    def test_frozen_v1_contracts_remain_readable_without_reinterpretation(self) -> None:
        run_manifest = artifacts.load_run_manifest(ROOT, FIXTURE / "run")
        self.assertEqual(run_manifest["schema_version"], "responses_runner_v2.run_manifest.v1")

        checkpoint = json.loads(
            (FIXTURE / "run/stages/01_draft/stage_checkpoint.json").read_text(encoding="utf-8")
        )
        validate_contract(checkpoint, "stage_checkpoint.schema.json", label="frozen checkpoint")
        self.assertEqual(checkpoint["response"]["model"], "gpt-5.5-pro")

        bundle = load_review_bundle(root=ROOT, bundle_path=FIXTURE / "review_bundle.json")
        self.assertEqual(bundle["schema_version"], "responses_runner_v2.review_bundle.v1")

    def test_frozen_v1_run_live_continuation_fails_closed_with_recovery_direction(self) -> None:
        with self.assertRaisesRegex(SystemExit, "cannot be resumed under v2 semantics"):
            resume_stage(
                run_dir=(FIXTURE / "run").relative_to(ROOT),
                stage_id="draft",
                wait=False,
                poll_interval=0,
                max_wait_seconds=None,
                client=object(),
                root=ROOT,
            )


if __name__ == "__main__":
    unittest.main()
