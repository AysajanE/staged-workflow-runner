from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from automation import run_responses_v2
from automation.responses_runner_v2 import artifacts, reviewer
from automation.responses_runner_v2.contracts import (
    REVISION_INSTRUCTIONS,
    GateType,
    ReviewConfig,
    RuntimeOptions,
)
from automation.responses_runner_v2.pack_loader import load_workflow_definition
from automation.responses_runner_v2.workflow import resume_stage, run_workflow

ROOT = Path(__file__).resolve().parents[2]
SYNTHETIC = ROOT / "automation/examples/responses_runner_v2_synthetic"

APPROVE = {
    "verdict": "approve",
    "summary": "The artifact satisfies the stage objective.",
    "blocking_findings": [],
    "notes": ["Consider tightening section two."],
}
REVISE = {
    "verdict": "revise",
    "summary": "The artifact omits the required risk table.",
    "blocking_findings": [
        {
            "id": "f1",
            "description": "Risk table missing.",
            "evidence": "artifact.md has no section 4.",
            "required_change": "Add the risk table with one row per open question.",
        }
    ],
    "notes": [],
}


def _completed_response(response_id: str, text: str) -> dict:
    return {
        "id": response_id,
        "status": "completed",
        "model": "gpt-5.6",
        "background": True,
        "store": True,
        "created_at": 1773752598,
        "completed_at": 1773752600,
        "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
    }


class ChainClient:
    """Minimal Responses client double: every stage completes with stage-specific text."""

    def __init__(self, *, in_progress_first: bool = False) -> None:
        self.in_progress_first = in_progress_first
        self.uploads = 0
        self.create_requests: list[dict] = []
        self.deleted: list[str] = []

    def upload_file(self, path, purpose, file_expiration_policy=None):
        self.uploads += 1
        return {"id": f"file_{self.uploads}", "purpose": purpose, "created_at": 1}

    def _text(self, payload: dict) -> str:
        stage_id = payload["metadata"]["stage_id"]
        task_text = payload["input"][0]["content"][0]["text"]
        suffix = " (revised)" if task_text.startswith("REVISION OF YOUR PREVIOUS OUTPUT") else ""
        return f"Synthetic response for {stage_id}{suffix}"

    def create_response(self, payload):
        self.create_requests.append(payload)
        response_id = f"resp_{len(self.create_requests)}"
        if self.in_progress_first and len(self.create_requests) == 1:
            return {
                "id": response_id,
                "status": "in_progress",
                "model": "gpt-5.6",
                "background": True,
                "store": True,
                "created_at": 1773752598,
                "output": [],
            }
        return _completed_response(response_id, self._text(payload))

    def retrieve_response(self, response_id):
        payload = self.create_requests[int(response_id.rsplit("_", 1)[1]) - 1]
        return _completed_response(response_id, self._text(payload))

    def wait_for_terminal_response(self, response_id, **_kwargs):
        return self.retrieve_response(response_id)

    def count_input_tokens_once(self, _payload):
        return {"input_tokens": 123}

    def delete_file(self, file_id):
        self.deleted.append(file_id)
        return {"id": file_id, "deleted": True}


class ScriptedReviewer:
    def __init__(self, verdicts: list) -> None:
        self.verdicts = list(verdicts)
        self.calls: list[dict] = []

    def __call__(self, argv, *, input_text, cwd, timeout, env):
        self.calls.append({"argv": argv, "input_text": input_text, "cwd": cwd, "env": env})
        if not self.verdicts:
            raise AssertionError("reviewer invoked more times than scripted")
        verdict = self.verdicts.pop(0)
        if verdict == "crash":
            return SimpleNamespace(returncode=1, stdout="", stderr="reviewer exploded")
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps(verdict),
            stderr="codex: tokens used: 1,234",
        )


def _make_pack(
    tmp_path: Path,
    *,
    gate1: str = "reviewed",
    gate2: str = "reviewed",
    review: dict | None = None,
    stage_overrides: dict | None = None,
) -> Path:
    pack = tmp_path / "pack"
    shutil.copytree(SYNTHETIC, pack)
    source = pack / "workflows/reviewed_three_stage.workflow.json"
    payload = json.loads(source.read_text(encoding="utf-8"))
    if review is not None:
        payload["defaults"]["review"] = review
    proposal, revision, final = payload["stages"]
    proposal["gate"] = gate1
    revision["gate"] = gate2
    revision["carry_forward"] = {
        "reference_context_from_stage_ids": ["proposal"],
        "handoff_from_stage_id": "proposal",
    }
    final["carry_forward"] = {
        "reference_context_from_stage_ids": ["proposal", "revision"],
        "handoff_from_stage_id": "revision",
    }
    final["output"] = {"primary_format": "text"}
    for stage in payload["stages"]:
        for key, value in (stage_overrides or {}).get(stage["stage_id"], {}).items():
            stage[key] = value
    target = pack / "workflows/reviewed_chain.workflow.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def _manifest(result: dict) -> dict:
    return artifacts.load_run_manifest(ROOT, ROOT / result["run_dir"])


def _summary(manifest: dict, stage_id: str) -> dict:
    return artifacts.find_stage_summary(manifest, stage_id)


def _attempt_dir(summary: dict, index: int = -1) -> Path:
    return ROOT / summary["attempts"][index]["attempt_dir"]


def _handoff_paths(attempt_dir: Path) -> list[str]:
    manifest = json.loads((attempt_dir / "input_manifest.json").read_text(encoding="utf-8"))
    paths: list[str] = []
    for entry in manifest["reviewed_handoff_inputs"]:
        for expanded in entry["resolved"]["expanded_paths"]:
            paths.append(expanded["path"])
    return paths


def _run(workflow_path: Path, output_root: Path, *, client, review_runner=None, **runtime_kwargs):
    runtime = RuntimeOptions(
        run_name="reviewed-chain",
        output_root=output_root.relative_to(ROOT),
        wait=True,
        **runtime_kwargs,
    )
    return run_workflow(
        workflow_file=workflow_path.relative_to(ROOT),
        runtime=runtime,
        client=client,
        root=ROOT,
        review_runner=review_runner,
    )


class LoaderGateTests(unittest.TestCase):
    def test_loader_accepts_new_gates_and_review_config(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = _make_pack(
                Path(tmp),
                review={"reviewer": "claude"},
                stage_overrides={"revision": {"review": {"max_revisions": 0, "reviewer": "codex"}}},
            )
            workflow = load_workflow_definition(path.relative_to(ROOT), root=ROOT)
        self.assertEqual(workflow.stages[0].gate, GateType.REVIEWED)
        self.assertEqual(workflow.stages[1].carry_forward.handoff_from_stage_id, "proposal")
        self.assertEqual(workflow.review_defaults.reviewer, "claude")
        self.assertEqual(workflow.review_defaults.effective_effort, "xhigh")
        self.assertEqual(workflow.review_defaults.effective_model, "opus")
        self.assertIsNone(workflow.stages[0].review)
        self.assertEqual(workflow.stages[1].review.reviewer, "codex")
        self.assertEqual(workflow.stages[1].review.effective_effort, "high")
        self.assertEqual(workflow.stages[1].review.max_revisions, 0)
        self.assertEqual(ReviewConfig().effective_model, "gpt-5.6-sol")

    def test_loader_rejects_bad_handoff_sources(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = _make_pack(Path(tmp), gate1="auto")
            with self.assertRaisesRegex(SystemExit, "reviewed.*or.*human"):
                load_workflow_definition(path.relative_to(ROOT), root=ROOT)
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            path = _make_pack(Path(tmp), review={"reviewer": "gemini"})
            with self.assertRaisesRegex(SystemExit, "review.reviewer"):
                load_workflow_definition(path.relative_to(ROOT), root=ROOT)


class ReviewerModuleTests(unittest.TestCase):
    def test_extract_verdict_handles_prose_and_cli_envelopes(self) -> None:
        plain = json.dumps(APPROVE)
        self.assertEqual(reviewer.extract_verdict(plain)["verdict"], "approve")
        prose = "Settings were {effort: xhigh, mode: pro}; my decision follows.\n" + json.dumps(REVISE)
        self.assertEqual(reviewer.extract_verdict(prose)["verdict"], "revise")
        envelope = json.dumps({"type": "result", "result": "Done.\n" + json.dumps(APPROVE), "total_cost_usd": 1.5})
        self.assertEqual(reviewer.extract_verdict(envelope)["summary"], APPROVE["summary"])
        structured = json.dumps({"result": "ignored", "structured_output": REVISE})
        self.assertEqual(reviewer.extract_verdict(structured)["verdict"], "revise")
        with self.assertRaises(reviewer.ReviewError):
            reviewer.extract_verdict("no json here")

    def test_normalize_verdict_maps_aliases_and_fills_findings(self) -> None:
        normalized = reviewer.normalize_verdict({"verdict": "Approved", "summary": "ok", "notes": ["a", 3]})
        self.assertEqual(normalized["verdict"], "approve")
        self.assertEqual(normalized["blocking_findings"], [])
        self.assertEqual(normalized["notes"], ["a", "3"])
        revise = reviewer.normalize_verdict({"verdict": "do_not_approve", "summary": "missing section"})
        self.assertEqual(revise["verdict"], "revise")
        self.assertEqual(len(revise["blocking_findings"]), 1)
        self.assertEqual(revise["blocking_findings"][0]["description"], "missing section")
        with self.assertRaises(reviewer.ReviewError):
            reviewer.normalize_verdict({"verdict": "maybe", "summary": ""})

    def test_build_command_for_codex_and_claude(self) -> None:
        prompt_path = Path("/tmp/prompt.md")
        argv, stdin_text, env = reviewer.build_command(
            ReviewConfig(reviewer="codex"), prompt_path=prompt_path, job_text="{}", prompt_text="PROMPT"
        )
        self.assertEqual(argv[:4], ["codex", "exec", "--sandbox", "read-only"])
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertIn("--model", argv)
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-sol")
        self.assertTrue(argv[argv.index("--output-schema") + 1].endswith("stage_review_verdict.schema.json"))
        self.assertEqual(argv[-1], "-")
        self.assertEqual(stdin_text, "PROMPT")
        self.assertIsNone(env)

        with mock.patch.dict(os.environ, {"ANTHROPIC_API_KEY": "secret", "HOME": "/tmp/home"}):
            argv, stdin_text, env = reviewer.build_command(
                ReviewConfig(reviewer="claude"), prompt_path=prompt_path, job_text='{"a": 1}', prompt_text="PROMPT"
            )
        self.assertEqual(argv[:2], ["claude", "-p"])
        self.assertEqual(argv[argv.index("--effort") + 1], "xhigh")
        self.assertEqual(argv[argv.index("--model") + 1], "opus")
        self.assertEqual(argv[argv.index("--tools") + 1], "Read,Grep,Glob")
        self.assertEqual(argv[-2:], ["--append-system-prompt-file", str(prompt_path)])
        self.assertIn('{"a": 1}', stdin_text)
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertEqual(env["HOME"], "/tmp/home")

    def test_run_review_writes_evidence_and_fails_loudly(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            artifact = tmp_path / "artifact.md"
            artifact.write_text("# artifact\n", encoding="utf-8")
            manifest_md = tmp_path / "input_manifest.md"
            manifest_md.write_text("manifest\n", encoding="utf-8")
            job = reviewer.build_review_job(
                root=ROOT,
                workflow_id="wf",
                run_id="run",
                stage_id="stage",
                stage_title="Stage",
                attempt_id="attempt_001",
                task_text="Do the thing.",
                artifact_path=artifact,
                input_manifest_markdown_path=manifest_md,
                handoff_paths=[],
                revision_of_attempt_id=None,
            )
            runner = ScriptedReviewer([APPROVE])
            result = reviewer.run_review(
                root=ROOT, config=ReviewConfig(), job=job, review_dir=tmp_path / "review", runner=runner
            )
            verdict = json.loads((ROOT / result.verdict_path).read_text(encoding="utf-8"))
            invocation = json.loads((ROOT / result.invocation_path).read_text(encoding="utf-8"))
            notes = (ROOT / result.notes_path).read_text(encoding="utf-8")
            self.assertTrue(result.approved)
            self.assertEqual(verdict["verdict"], "approve")
            self.assertEqual(invocation["tokens_used"], 1234)
            self.assertEqual(invocation["exit_code"], 0)
            self.assertIn("Consider tightening", notes)
            self.assertIn("Review job", runner.calls[0]["input_text"])

            failing = ScriptedReviewer(["crash"])
            with self.assertRaisesRegex(reviewer.ReviewError, "exited with code 1"):
                reviewer.run_review(
                    root=ROOT, config=ReviewConfig(), job=job, review_dir=tmp_path / "review2", runner=failing
                )
            self.assertFalse((tmp_path / "review2" / "verdict.json").exists())
            self.assertEqual(len(list((tmp_path / "review2").glob("invocation_*.json"))), 1)


class ReviewedGateEngineTests(unittest.TestCase):
    def test_reviewed_chain_completes_in_one_invocation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            runner = ScriptedReviewer([APPROVE, APPROVE])
            client = ChainClient()
            result = _run(workflow_path, tmp_path / "runs", client=client, review_runner=runner)
            manifest = _manifest(result)
            proposal = _summary(manifest, "proposal")
            revision = _summary(manifest, "revision")
            final = _summary(manifest, "final_delivery")
            revision_handoffs = _handoff_paths(_attempt_dir(revision))
            proposal_dir = _attempt_dir(proposal)
            verdict = json.loads((proposal_dir / "review/verdict.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(len(client.create_requests), 3)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual([proposal["status"], revision["status"], final["status"]], ["completed"] * 3)
        self.assertEqual(proposal["review_status"], "approved")
        self.assertEqual(revision["review_status"], "approved")
        self.assertNotIn("review_status", final)
        self.assertEqual(verdict["disposition"], "approved")
        self.assertEqual(
            revision_handoffs,
            [
                (proposal_dir / "review/reviewer_notes.md").relative_to(ROOT).as_posix(),
                (proposal_dir / "artifact.md").relative_to(ROOT).as_posix(),
            ],
        )
        self.assertTrue(proposal["review_approved"])

    def test_reviewed_gate_requests_one_revision_then_continues(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            runner = ScriptedReviewer([REVISE, APPROVE, APPROVE])
            client = ChainClient()
            result = _run(workflow_path, tmp_path / "runs", client=client, review_runner=runner)
            manifest = _manifest(result)
            proposal = _summary(manifest, "proposal")
            first_dir = _attempt_dir(proposal, 0)
            second_dir = _attempt_dir(proposal, 1)
            first_verdict = json.loads((first_dir / "review/verdict.json").read_text(encoding="utf-8"))
            second_payload = json.loads((second_dir / "request_payload.json").read_text(encoding="utf-8"))
            second_handoffs = _handoff_paths(second_dir)
            second_artifact = (second_dir / "artifact.md").read_text(encoding="utf-8")
            revision_handoffs = _handoff_paths(_attempt_dir(_summary(manifest, "revision")))

        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(len(client.create_requests), 4)
        self.assertEqual(len(runner.calls), 3)
        self.assertEqual(len(proposal["attempts"]), 2)
        self.assertEqual(proposal["attempts"][1]["revision_of_attempt_id"], "attempt_001")
        self.assertEqual(proposal["current_attempt_id"], "attempt_002")
        self.assertEqual(proposal["review_status"], "approved")
        self.assertEqual(first_verdict["disposition"], "revision_requested")
        task_text = second_payload["input"][0]["content"][0]["text"]
        self.assertTrue(task_text.startswith(REVISION_INSTRUCTIONS.strip().splitlines()[0]))
        self.assertEqual(
            second_handoffs,
            [
                (first_dir / "review/reviewer_notes.md").relative_to(ROOT).as_posix(),
                (first_dir / "artifact.md").relative_to(ROOT).as_posix(),
            ],
        )
        self.assertIn("(revised)", second_artifact)
        self.assertEqual(revision_handoffs[1], (second_dir / "artifact.md").relative_to(ROOT).as_posix())

    def test_reviewed_gate_blocks_after_max_revisions_and_accepts_handoff_note(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            client = ChainClient()
            blocked = _run(
                workflow_path, tmp_path / "runs", client=client, review_runner=ScriptedReviewer([REVISE, REVISE])
            )
            manifest = _manifest(blocked)
            proposal = _summary(manifest, "proposal")
            self.assertEqual(blocked["status"], "waiting_for_review")
            self.assertEqual(proposal["status"], "waiting_for_review")
            self.assertEqual(proposal["review_status"], "blocked")
            self.assertEqual(len(proposal["attempts"]), 2)

            with self.assertRaisesRegex(SystemExit, "handoff note"):
                _run(
                    workflow_path,
                    tmp_path / "runs",
                    client=client,
                    review_runner=ScriptedReviewer([]),
                    run_dir=(ROOT / blocked["run_dir"]).relative_to(ROOT),
                )

            note = tmp_path / "handoff_note.md"
            note.write_text("# Human decision\n\nProceed; the risk table is deferred to stage 2.\n", encoding="utf-8")
            runner = ScriptedReviewer([APPROVE])
            resumed = _run(
                workflow_path,
                tmp_path / "runs",
                client=client,
                review_runner=runner,
                run_dir=(ROOT / blocked["run_dir"]).relative_to(ROOT),
                handoff_note=note.relative_to(ROOT).as_posix(),
            )
            manifest = _manifest(resumed)
            proposal = _summary(manifest, "proposal")
            revision_handoffs = _handoff_paths(_attempt_dir(_summary(manifest, "revision")))
            proposal_dir = _attempt_dir(proposal)

        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(proposal["status"], "waiting_for_review")
        self.assertEqual(proposal["review_status"], "human_approved")
        self.assertEqual(proposal["handoff_note_path"], note.relative_to(ROOT).as_posix())
        self.assertEqual(
            revision_handoffs,
            [
                note.relative_to(ROOT).as_posix(),
                (proposal_dir / "review/reviewer_notes.md").relative_to(ROOT).as_posix(),
                (proposal_dir / "artifact.md").relative_to(ROOT).as_posix(),
            ],
        )
        self.assertEqual(len(runner.calls), 1)

    def test_human_gate_waits_then_proceeds_with_note(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path, gate1="human")
            client = ChainClient()
            runner = ScriptedReviewer([APPROVE])
            waiting = _run(workflow_path, tmp_path / "runs", client=client, review_runner=runner)
            self.assertEqual(waiting["status"], "waiting_for_review")
            self.assertEqual(_summary(_manifest(waiting), "proposal")["status"], "waiting_for_review")
            self.assertEqual(runner.calls, [])

            note = tmp_path / "note.md"
            note.write_text("Approved by owner.\n", encoding="utf-8")
            done = _run(
                workflow_path,
                tmp_path / "runs",
                client=client,
                review_runner=runner,
                run_dir=(ROOT / waiting["run_dir"]).relative_to(ROOT),
                handoff_note=note.relative_to(ROOT).as_posix(),
            )
            manifest = _manifest(done)
            proposal = _summary(manifest, "proposal")
            handoffs = _handoff_paths(_attempt_dir(_summary(manifest, "revision")))

        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(proposal["review_status"], "human_approved")
        self.assertEqual(handoffs[0], note.relative_to(ROOT).as_posix())
        self.assertEqual(len(runner.calls), 1)

    def test_reviewer_none_skips_invocation(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path, review={"reviewer": "none"})
            runner = ScriptedReviewer([])
            result = _run(workflow_path, tmp_path / "runs", client=ChainClient(), review_runner=runner)
            manifest = _manifest(result)
            proposal = _summary(manifest, "proposal")
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(proposal["review_status"], "not_required")
        self.assertEqual(runner.calls, [])

    def test_reviewer_override_from_runtime(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            runner = ScriptedReviewer([APPROVE, APPROVE])
            _run(
                workflow_path,
                tmp_path / "runs",
                client=ChainClient(),
                review_runner=runner,
                reviewer_override="claude",
            )
        self.assertEqual(runner.calls[0]["argv"][:2], ["claude", "-p"])

    def test_dry_run_covers_all_stages_with_stubs(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            output_root = tmp_path / "runs"
            result = run_workflow(
                workflow_file=workflow_path.relative_to(ROOT),
                runtime=RuntimeOptions(
                    run_name="reviewed-chain-dry",
                    output_root=output_root.relative_to(ROOT),
                    dry_run=True,
                ),
                root=ROOT,
            )
            run_dir = ROOT / result["run_dir"]
            payloads = sorted(path.parent.name for path in run_dir.glob("dry_runs/stages/*/request_payload.json"))
            stubs = sorted(path.relative_to(run_dir).as_posix() for path in run_dir.glob("dry_runs/stubs/*/*"))
            manifest = artifacts.load_run_manifest(ROOT, run_dir)
        self.assertEqual([item["stage_id"] for item in result["stages"]], ["proposal", "revision", "final_delivery"])
        self.assertEqual(payloads, ["01_proposal", "02_revision", "03_final_delivery"])
        self.assertIn("dry_runs/stubs/proposal/artifact.md", stubs)
        self.assertIn("dry_runs/stubs/proposal/handoff_notes.md", stubs)
        self.assertIn("dry_runs/stubs/revision/artifact.md", stubs)
        self.assertEqual(manifest["status"], "created")

    def test_pending_review_is_applied_on_reentry(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            client = ChainClient()
            with self.assertRaisesRegex(SystemExit, "review pending"):
                _run(workflow_path, tmp_path / "runs", client=client, review_runner=ScriptedReviewer(["crash"]))
            run_dir = next(path for path in (tmp_path / "runs").iterdir() if (path / "run_manifest.json").exists())
            manifest = artifacts.load_run_manifest(ROOT, run_dir)
            proposal = _summary(manifest, "proposal")
            self.assertEqual(proposal["status"], "completed")
            self.assertNotIn("review_status", proposal)

            runner = ScriptedReviewer([APPROVE, APPROVE])
            result = _run(
                workflow_path,
                tmp_path / "runs",
                client=client,
                review_runner=runner,
                run_dir=run_dir.relative_to(ROOT),
            )
            manifest = _manifest(result)
            proposal = _summary(manifest, "proposal")
            invocations = list((_attempt_dir(proposal) / "review").glob("invocation_*.json"))
        self.assertEqual(manifest["status"], "completed")
        self.assertEqual(proposal["review_status"], "approved")
        self.assertEqual(len(client.create_requests), 3)
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(len(invocations), 2)

    def test_resume_applies_gate_after_finalization(self) -> None:
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tmp_path = Path(tmp)
            workflow_path = _make_pack(tmp_path)
            client = ChainClient(in_progress_first=True)
            submitted = run_workflow(
                workflow_file=workflow_path.relative_to(ROOT),
                runtime=RuntimeOptions(
                    run_name="reviewed-chain-resume",
                    output_root=(tmp_path / "runs").relative_to(ROOT),
                    wait=False,
                ),
                client=client,
                root=ROOT,
            )
            self.assertEqual(_summary(_manifest(submitted), "proposal")["status"], "in_progress")
            runner = ScriptedReviewer([APPROVE])
            resume_stage(
                run_dir=(ROOT / submitted["run_dir"]).relative_to(ROOT),
                stage_id="proposal",
                wait=True,
                poll_interval=0.0,
                max_wait_seconds=None,
                client=client,
                root=ROOT,
                review_runner=runner,
            )
            proposal = _summary(_manifest(submitted), "proposal")
        self.assertEqual(proposal["status"], "completed")
        self.assertEqual(proposal["review_status"], "approved")
        self.assertEqual(len(runner.calls), 1)


class ReviewedGateCliTests(unittest.TestCase):
    def test_run_accepts_handoff_note_and_reviewer_flags(self) -> None:
        args = run_responses_v2.parse_args(
            [
                "run",
                "--root",
                str(ROOT),
                "--workflow-file",
                "workflow.json",
                "--handoff-note",
                "notes/handoff.md",
                "--reviewer",
                "claude",
            ]
        )
        self.assertEqual(str(args.handoff_note), "notes/handoff.md")
        self.assertEqual(args.reviewer, "claude")


if __name__ == "__main__":
    unittest.main()
