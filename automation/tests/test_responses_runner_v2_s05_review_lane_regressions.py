"""Regression replays of the real 2026-06-01 S05 review-lane failures.

The fixtures under ``fixtures/s05_review_lane`` are sanitized copies of
reviewer stdout payloads and supervisor decision sidecars archived from a
product repository's S05 review lane on 2026-06-01 (the operator's absolute
workspace root is replaced with ``<workspace_root>``). They contain review
decisions about playbook stages only: no secrets, no credentials, and no
health data.

The four archived cases:

- cycle 1 (``cmd_codex_...133945``): reviewer emitted a real approval in a
  schema dialect (alias keys, extra keys) that raw validation rejected; the
  supervisor wrote a ``malformed_output``/``blocked`` sidecar that was then
  misread downstream as a reviewer rejection.
- cycle 4 (``cmd_codex_...214954``): same failure with ``status="completed"``
  and ``approval_decision="approved"`` spellings.
- cycle 5 codex (``cmd_codex_...220848``): a workspace snapshot diff of seven
  gitignored ``.DS_Store`` files produced a false ``read_only_violation``,
  masking the reviewer's real (blocked) verdict.
- cycle 5 claude (``cmd_claude_...221206``): the reviewer process was killed
  by an external SIGTERM (exit 143) with empty stdout and was classified as
  a generic agent failure instead of a transport interruption.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation.responses_runner_v2 import supervisor_agents
from automation.responses_runner_v2.contracts import runner_now
from automation.responses_runner_v2.supervisor_artifacts import diff_snapshots, snapshot_workspace

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "s05_review_lane"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _command_stub(actor_role: str) -> dict:
    return {
        "command_id": f"cmd_{actor_role}_replay",
        "actor_role": actor_role,
        "argv": ["replay"],
        "cwd": str(ROOT),
        "started_at": runner_now().isoformat(),
        "completed_at": runner_now().isoformat(),
        "exit_code": 0,
        "stdout_path": "stdout.txt",
        "stderr_path": "stderr.txt",
    }


def _passed_read_only_check() -> dict:
    return {
        "method": "workspace_snapshot_excluding_local_artifacts",
        "before_hash": "before",
        "after_hash": "before",
        "diff_path": "diff.md",
        "status": "passed",
        "changed_paths": [],
    }


def _replay_raw_decision(raw: dict, *, review_cycle_id: str) -> dict:
    decision = supervisor_agents._normalize_agent_decision(
        root=ROOT,
        output_dir=ROOT / "unused_replay_output",
        command_id=f"cmd_codex_review_agent_{review_cycle_id}",
        actor_role="codex_review_agent",
        review_kind="stage_output",
        review_cycle_id=review_cycle_id,
        supervisor_session_id="sup_replay",
        raw_decision=raw,
        command=_command_stub("codex_review_agent"),
        read_only_check=_passed_read_only_check(),
    )
    supervisor_agents.validate_review_decision(decision)
    return decision


class S05ReviewLaneRegressionTests(unittest.TestCase):
    def test_cycle1_raw_approve_dialect_canonicalizes_to_succeeded_approve(self) -> None:
        raw = _load_fixture("cycle1_codex_raw_approve.stdout.json")
        self.assertEqual(raw["status"], "succeeded")
        self.assertEqual(raw["approval_decision"], "approve")
        decision = _replay_raw_decision(raw, review_cycle_id="s05_cycle1")
        self.assertEqual(decision["status"], "succeeded")
        self.assertEqual(decision["approval_decision"], "approve")
        self.assertEqual(decision["blocking_issues"], [])
        self.assertEqual(decision["validation_errors"], [])
        self.assertNotIn("slice", decision)
        self.assertEqual(decision["next_action"], "proceed_to_consolidation")
        for artifact in decision["reviewed_artifacts"]:
            self.assertIn("path", artifact)
            self.assertIn("role", artifact)

    def test_cycle4_raw_approved_dialect_canonicalizes_to_succeeded_approve(self) -> None:
        raw = _load_fixture("cycle4_codex_raw_approved_dialect.stdout.json")
        self.assertEqual(raw["status"], "completed")
        self.assertEqual(raw["approval_decision"], "approved")
        decision = _replay_raw_decision(raw, review_cycle_id="s05_cycle4")
        self.assertEqual(decision["status"], "succeeded")
        self.assertEqual(decision["approval_decision"], "approve")
        self.assertEqual(decision["blocking_issues"], [])
        self.assertEqual(decision["next_action"], "proceed_to_consolidation")

    def test_cycle5_ds_store_only_diff_no_longer_triggers_read_only_violation(self) -> None:
        sidecar = _load_fixture("cycle5_codex_read_only_violation.decision.json")
        self.assertEqual(sidecar["status"], "read_only_violation")
        changed_paths = [change["path"] for change in sidecar["read_only_check"]["changed_paths"]]
        self.assertEqual(len(changed_paths), 7)
        self.assertTrue(all(path.endswith(".DS_Store") for path in changed_paths))

        with tempfile.TemporaryDirectory() as tmp:
            workspace = Path(tmp)
            source = workspace / "src" / "module.py"
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_text("print('before')\n", encoding="utf-8")
            for rel in changed_paths:
                target = workspace / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"finder-before")

            before = snapshot_workspace(workspace)
            for rel in changed_paths:
                (workspace / rel).write_bytes(b"finder-after")
            after = snapshot_workspace(workspace)
            self.assertEqual(diff_snapshots(before, after), [])

            source.write_text("print('after')\n", encoding="utf-8")
            with_source_change = snapshot_workspace(workspace)
            changes = diff_snapshots(before, with_source_change)
            self.assertEqual([change["path"] for change in changes], ["src/module.py"])

    def test_cycle5_codex_raw_blocked_is_a_real_reviewer_verdict(self) -> None:
        raw = _load_fixture("cycle5_codex_raw_blocked_missing_sources.stdout.json")
        self.assertEqual(raw["status"], "completed")
        self.assertEqual(raw["approval_decision"], "blocked")
        decision = _replay_raw_decision(raw, review_cycle_id="s05_cycle5_codex")
        self.assertEqual(decision["status"], "succeeded")
        self.assertEqual(decision["approval_decision"], "blocked")
        self.assertTrue(decision["blocking_issues"])
        self.assertTrue(decision["missing_artifacts"])
        self.assertEqual(decision["next_action"], "blocked")
        self.assertNotIn(decision["status"], supervisor_agents.TRANSPORT_FAILURE_STATUSES)

    def test_cycle5_claude_sigterm_exit143_now_classifies_as_interrupted(self) -> None:
        archived = _load_fixture("cycle5_claude_sigterm_exit143.decision.json")
        self.assertEqual(archived["command"]["exit_code"], 143)
        self.assertEqual(archived["status"], "failed")
        self.assertTrue(archived["blocking_issues"])

        def runner(_argv, **_kwargs):
            return SimpleNamespace(returncode=archived["command"]["exit_code"], stdout="", stderr="")

        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            result = supervisor_agents.invoke_claude_review_agent(
                root=ROOT,
                review_kind="stage_output",
                review_cycle_id="s05_cycle5_claude",
                supervisor_session_id="sup_replay",
                job={"review_job_id": "s05_cycle5_claude_replay"},
                output_dir=Path(tmp).relative_to(ROOT),
                runner=runner,
            )
            self.assertEqual(result.status, "interrupted")
            self.assertNotEqual(result.status, archived["status"])
            payload = json.loads((ROOT / result.decision_path).read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "interrupted")
            self.assertEqual(payload["blocking_issues"], [])
            self.assertIn("SIGTERM", payload["validation_errors"][0])
            self.assertIn("143", payload["validation_errors"][0])


if __name__ == "__main__":
    unittest.main()
