from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation.responses_runner_v2 import supervisor, supervisor_agents
from automation.responses_runner_v2.contracts import (
    RuntimeInputBinding,
    RuntimeOptions,
    relpath,
    runner_now,
    sha256_file,
)
from automation.responses_runner_v2.review_bundle import create_review_bundle
from automation.responses_runner_v2.supervisor_artifacts import (
    load_session,
    validate_against_schema,
)
from automation.responses_runner_v2.workflow import run_workflow
from automation.tests.supervisor_test_support import isolate_supervisor_output
from automation.tests.test_responses_runner_v2_supervisor_integrity import _decision
from automation.tests.test_responses_runner_v2_workflow import (
    SequenceClient,
    _completed_response,
    _stage_dir,
)


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = ROOT / "automation/examples/responses_runner_v2_evidence_synthesis"
WORKFLOW_PATH = PACK_ROOT / "workflows/document_evidence_synthesis.workflow.json"


def _runtime_input_bindings() -> list[RuntimeInputBinding]:
    payload = json.loads(
        (PACK_ROOT / "runtime_input_bindings.example.json").read_text(encoding="utf-8")
    )
    return [
        RuntimeInputBinding(
            binding_id=binding["binding_id"],
            path=binding["path"],
            authority=binding["authority"],
            stage_ids=tuple(binding["scope"]["stage_ids"]),
        )
        for binding in payload["bindings"]
    ]


class ResponsesRunnerV2EvidenceSynthesisExampleTests(unittest.TestCase):
    def setUp(self) -> None:
        isolate_supervisor_output(self, ROOT)

    def test_workflow_is_bounded_non_coding_and_reviewed(self) -> None:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        self.assertEqual(workflow["schema_version"], "responses_runner_v2.workflow_manifest.v2")
        self.assertEqual(workflow["workflow_mode"], "reviewed_three_stage")
        self.assertEqual(workflow["assurance_profile"], "reviewed")

        roles = workflow["defaults"]["model_roles"]
        self.assertEqual(roles["primary_generation"]["model"], "gpt-5.6")
        self.assertEqual(roles["primary_generation"]["reasoning_mode"], "pro")
        self.assertEqual(roles["structural_processing"]["model"], "gpt-5.6")
        self.assertEqual(roles["structural_processing"]["reasoning_mode"], "standard")

        stages = workflow["stages"]
        self.assertEqual([stage["stage_number"] for stage in stages], [1, 2, 3])
        self.assertEqual([stage["gate"] for stage in stages], ["review_required", "review_required", "terminal"])
        for stage in stages:
            self.assertEqual(stage["max_input_tokens"], 700000)
            self.assertNotIn("tool_profile_file", stage)

    def test_declared_bindings_are_root_confined_and_stage_scoped(self) -> None:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        known_stage_ids = {stage["stage_id"] for stage in workflow["stages"]}
        binding_contract = json.loads(
            (PACK_ROOT / "runtime_input_bindings.example.json").read_text(encoding="utf-8")
        )
        binding_ids: set[str] = set()
        for binding in binding_contract["bindings"]:
            self.assertNotIn(binding["binding_id"], binding_ids)
            binding_ids.add(binding["binding_id"])
            self.assertTrue((ROOT / binding["path"]).is_file())
            self.assertEqual(binding["scope"]["type"], "stages")
            self.assertTrue(set(binding["scope"]["stage_ids"]).issubset(known_stage_ids))

        self.assertEqual(
            binding_ids,
            {"decision_question", "finance_evidence", "customer_evidence", "vendor_evidence"},
        )

        for manifest_path in sorted((PACK_ROOT / "inputs").glob("*.input_manifest.json")):
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            for field_name in (
                "primary_job_inputs",
                "reviewed_handoff_inputs",
                "attached_repository_files",
                "reference_context",
            ):
                self.assertEqual(manifest[field_name], [])

    def test_prompts_require_typed_citations_and_no_execution_claims(self) -> None:
        prompts = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((PACK_ROOT / "prompts").glob("*.md"))
        )
        self.assertIn("[workspace_file:", prompts)
        self.assertIn("[stage_artifact:", prompts)
        self.assertIn("[operator_input:", prompts)
        self.assertIn("Do not claim", prompts)

    def test_every_stage_enforces_typed_evidence_references(self) -> None:
        workflow = json.loads(WORKFLOW_PATH.read_text(encoding="utf-8"))
        for stage in workflow["stages"]:
            self.assertTrue(stage["citation_policy"]["allowed_locator_types"])
            self.assertEqual(
                stage["post_output_validators"],
                [{"validator_id": "evidence_references_v1", "gate": "blocking"}],
            )

    def test_final_delivery_schema_is_valid_draft_2020_12(self) -> None:
        schema_path = ROOT / "automation/responses_runner_v2/schemas/final_delivery_bundle.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        self.assertEqual(
            schema["properties"]["schema_version"]["const"],
            "responses_runner_v2.final_delivery_bundle.v1",
        )
        try:
            import jsonschema  # type: ignore
        except ImportError:
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        else:
            jsonschema.Draft202012Validator.check_schema(schema)
            binding_schema = json.loads(
                (ROOT / "automation/responses_runner_v2/schemas/runtime_input_bindings.schema.json").read_text(
                    encoding="utf-8"
                )
            )
            jsonschema.Draft202012Validator(binding_schema).validate(
                json.loads(
                    (PACK_ROOT / "runtime_input_bindings.example.json").read_text(encoding="utf-8")
                )
            )

    def test_offline_runner_to_revised_accepted_final_delivery(self) -> None:
        source_register = """# Source Register

## Decision Boundary

Evaluate only whether an advisory pilot should be recommended; no purchase or external action is
authorized. [operator_input:decision_question]

## Source Register

Finance evidence records the bounded budget assumptions.
[workspace_file:automation/examples/responses_runner_v2_evidence_synthesis/corpus/finance_evidence.md]
Customer evidence contains conflicting customer counts.
[workspace_file:automation/examples/responses_runner_v2_evidence_synthesis/corpus/customer_evidence.md]
Vendor evidence contains an unverified savings claim.
[workspace_file:automation/examples/responses_runner_v2_evidence_synthesis/corpus/vendor_evidence.md]

## Open Questions

The documents do not establish whether the quoted savings will recur.
"""
        evidence_synthesis = """# Evidence Synthesis

## Supported Findings

The finance evidence supports only a bounded pilot budget.
[workspace_file:automation/examples/responses_runner_v2_evidence_synthesis/corpus/finance_evidence.md]
The customer evidence does not support one reconciled customer count.
[workspace_file:automation/examples/responses_runner_v2_evidence_synthesis/corpus/customer_evidence.md]
The vendor savings claim is not independently verified.
[workspace_file:automation/examples/responses_runner_v2_evidence_synthesis/corpus/vendor_evidence.md]
The approved decision boundary remains advisory. [stage_artifact:source_register]

## Calculations And Inferences

A short pilot is an inference from the bounded budget, not a sourced fact.

## Evidence Gaps

Renewal economics and durable savings remain unknown.
"""
        final_brief = """# Conditional Pilot Decision Brief

## Recommendation

Conditionally recommend a small advisory pilot, subject to human authorization.
[operator_input:decision_question] [stage_artifact:evidence_synthesis]

## Conditions And Stop Rule

Treat the spend ceiling and review threshold as operator judgments. Stop if the pilot exceeds the
authorized ceiling or cannot measure the stated outcome. [stage_artifact:evidence_synthesis]

## Evidence And Limitations

The customer count and savings claim remain unresolved. No purchase, contract, external
communication, or execution has occurred. [stage_artifact:evidence_synthesis]
"""

        with tempfile.TemporaryDirectory(dir=ROOT) as raw:
            temp = Path(raw)
            output_root = temp.relative_to(ROOT) / "runs"
            bindings = _runtime_input_bindings()
            client = SequenceClient(
                [
                    _completed_response("resp_source_register", text=source_register),
                    _completed_response("resp_evidence_synthesis", text=evidence_synthesis),
                    _completed_response("resp_final_decision_brief", text=final_brief),
                ]
            )

            stage1 = run_workflow(
                workflow_file=WORKFLOW_PATH.relative_to(ROOT),
                runtime=RuntimeOptions(
                    run_name="evidence-synthesis-offline-e2e",
                    output_root=output_root,
                    input_bindings=bindings,
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )
            self.assertEqual(stage1["status"], "waiting_for_review")
            run_dir = ROOT / stage1["run_dir"]
            run_manifest = json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8"))
            run_id = run_manifest["run_id"]

            def approved_handoff(stage_id: str, label: str) -> Path:
                current_manifest = json.loads(
                    (run_dir / "run_manifest.json").read_text(encoding="utf-8")
                )
                stage_dir = _stage_dir(current_manifest, stage_id)
                notes = run_dir / f"{label}.review.md"
                notes.write_text(
                    "# Offline review\n\nThe non-coding artifact is grounded and approved for handoff.\n",
                    encoding="utf-8",
                )
                bundle = run_dir / f"{label}.review_bundle.json"
                create_review_bundle(
                    root=ROOT,
                    output_path=bundle.relative_to(ROOT),
                    workflow_id="document_evidence_synthesis",
                    source_stage_id=stage_id,
                    source_run_id=run_id,
                    primary_artifact_markdown=(stage_dir / "artifact.md").relative_to(ROOT),
                    response_artifact_json=(stage_dir / "response.final.json").relative_to(ROOT),
                    reviewer_notes=notes.relative_to(ROOT),
                )
                return bundle

            stage1_bundle = approved_handoff("source_register", "source_register")
            stage2 = run_workflow(
                workflow_file=WORKFLOW_PATH.relative_to(ROOT),
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    output_root=output_root,
                    input_bindings=bindings,
                    review_bundles=[stage1_bundle.relative_to(ROOT).as_posix()],
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )
            self.assertEqual(stage2["status"], "waiting_for_review")
            stage2_bundle = approved_handoff("evidence_synthesis", "evidence_synthesis")
            final_result = run_workflow(
                workflow_file=WORKFLOW_PATH.relative_to(ROOT),
                runtime=RuntimeOptions(
                    run_dir=run_dir.relative_to(ROOT),
                    output_root=output_root,
                    input_bindings=bindings,
                    review_bundles=[stage2_bundle.relative_to(ROOT).as_posix()],
                    wait=True,
                ),
                client=client,
                root=ROOT,
            )

            final_manifest = json.loads(
                (ROOT / final_result["run_manifest_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(final_manifest["status"], "completed")
            self.assertEqual(client.responses, [])
            self.assertEqual(
                [stage["stage_id"] for stage in final_manifest["stages"]],
                ["source_register", "evidence_synthesis", "final_decision_brief"],
            )
            self.assertTrue(final_manifest["stages"][0]["review_approved"])
            self.assertTrue(final_manifest["stages"][1]["review_approved"])
            terminal_stage = final_manifest["stages"][2]
            terminal_dir = _stage_dir(final_manifest, "final_decision_brief")
            terminal_artifact = terminal_dir / "artifact.md"
            terminal_text = terminal_artifact.read_text(encoding="utf-8")
            self.assertIn("# Conditional Pilot Decision Brief", terminal_text)
            self.assertIn("No purchase, contract, external", terminal_text)
            self.assertNotIn("```", terminal_text)
            for stage in final_manifest["stages"]:
                validator_result = json.loads(
                    (_stage_dir(final_manifest, stage["stage_id"]) / "validator_report.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertTrue(validator_result["passed"])

            brief = temp / "supervisor_brief.md"
            brief.write_text(
                "# Accepted task\n\nProduce and review a non-coding advisory decision brief.\n",
                encoding="utf-8",
            )
            session = supervisor.create_session(
                root=ROOT,
                clarified_task_brief=brief.relative_to(ROOT),
                summary="Offline evidence-synthesis delivery proof",
            )
            supervisor.stage_scaffold(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                scaffold_path=PACK_ROOT.relative_to(ROOT),
            )
            current, session_path = supervisor._load_session_and_path(
                ROOT, session["supervisor_session_id"]
            )
            supervisor._register_run_result(ROOT, current, final_result)
            supervisor._write_session(ROOT, session_path, current)

            terminal_rel = relpath(ROOT, terminal_artifact)
            terminal_sha = sha256_file(terminal_artifact)
            draft_body = {
                "schema_version": "responses_runner_v2.final_delivery_bundle.v1",
                "delivery_id": "evidence_synthesis_final_delivery",
                "created_at": runner_now().isoformat(),
                "assurance_profile": "reviewed",
                "subject": {
                    "workflow_id": "document_evidence_synthesis",
                    "run_id": run_id,
                    "terminal_stage_id": "final_decision_brief",
                    "terminal_attempt_id": terminal_stage["current_attempt_id"],
                    "terminal_artifact_sha256": terminal_sha,
                },
                "summary": "Advisory decision brief ready.",
                "deliverables": [
                    {
                        "deliverable_id": "conditional_pilot_decision_brief",
                        "kind": "decision_record",
                        "path": terminal_rel,
                        "sha256": terminal_sha,
                        "description": "Non-coding advisory decision brief; no action is authorized.",
                    }
                ],
                "evidence": [
                    {
                        "evidence_id": "terminal_decision_brief",
                        "citation_type": "stage_artifact",
                        "locator": terminal_rel,
                        "sha256": terminal_sha,
                        "claim": "The terminal runner artifact contains the conditional advisory brief.",
                    }
                ],
                "validation_evidence": [
                    {
                        "check_id": "typed_evidence_references",
                        "method": "trusted in-process evidence_references_v1 validator",
                        "status": "passed",
                        "evidence": "All three stage validator results passed.",
                    }
                ],
                "open_items": ["Human authorization is required before any pilot or purchase."],
                "residual_risks": ["Customer-count and vendor-savings claims remain unresolved."],
            }
            draft = temp / "final_delivery.draft.json"
            draft.write_text(json.dumps(draft_body, indent=2) + "\n", encoding="utf-8")
            initial_cycle_id = "evidence_final_initial"
            initial_job = temp / "final_delivery.initial.job.json"
            initial_job.write_text(
                json.dumps(
                    {
                        "review_job_id": initial_cycle_id,
                        "reviewed_artifacts": [terminal_rel],
                        "workflow_id": "document_evidence_synthesis",
                        "run_id": run_id,
                        "stage_id": "final_decision_brief",
                        "final_packet_draft": relpath(ROOT, draft),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            recommendation = {
                "recommendation_id": "clarify_advisory_summary",
                "source_agent": "codex_review_agent",
                "severity": "medium",
                "recommendation": "Clarify that the recommendation is conditional and non-executing.",
                "evidence": [
                    {
                        "artifact_path": relpath(ROOT, draft),
                        "quote_or_summary": "The summary does not state the decision boundary.",
                    }
                ],
                "affected_artifacts": [relpath(ROOT, draft)],
                "exact_change_needed": "State that the pilot is conditional and no action was executed.",
            }

            def initial_decision(role: str, **kwargs):
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role=role,
                    cycle=initial_cycle_id,
                    review_kind="final_packet",
                    recommendations=[recommendation] if role == "codex_review_agent" else None,
                    workflow_id="document_evidence_synthesis",
                    run_id=run_id,
                    stage_id="final_decision_brief",
                )

            with mock.patch.object(
                supervisor_agents,
                "invoke_operator_codex",
                side_effect=lambda **kwargs: initial_decision("operator_codex", **kwargs),
            ), mock.patch.object(
                supervisor_agents,
                "invoke_codex_review_agent",
                side_effect=lambda **kwargs: initial_decision("codex_review_agent", **kwargs),
            ), mock.patch.object(
                supervisor_agents,
                "invoke_claude_review_agent",
                side_effect=lambda **kwargs: initial_decision("claude_review_agent", **kwargs),
            ):
                initial_review = supervisor.run_review_cycle(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    review_cycle_id=initial_cycle_id,
                    review_kind="final_packet",
                    job_json=initial_job.relative_to(ROOT),
                )

            consolidated = json.loads(
                (ROOT / initial_review["consolidation"]).read_text(encoding="utf-8")
            )
            accepted_recommendation = next(
                item
                for item in consolidated["recommendations"]
                if item["recommendation"]
                == "Clarify that the recommendation is conditional and non-executing."
            )
            directive = supervisor.create_revision_directive(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=initial_cycle_id,
                accepted_recommendation_ids=[accepted_recommendation["recommendation_id"]],
                rejected_recommendations={},
                revised_artifacts=[draft.relative_to(ROOT)],
            )
            revised_summary = (
                "Conditional advisory pilot recommendation; human authorization remains required "
                "and no purchase, communication, or execution occurred."
            )

            def revised_operator(**kwargs):
                if kwargs["review_kind"] == "recovery":
                    revised_body = json.loads(draft.read_text(encoding="utf-8"))
                    revised_body["summary"] = revised_summary
                    draft.write_text(json.dumps(revised_body, indent=2) + "\n", encoding="utf-8")
                    traced = {
                        **directive["accepted_recommendations"][0],
                        "operator_decision": "accepted",
                        "decision_rationale": "The clarification is evidence-grounded and in scope.",
                        "changes_applied": [
                            {
                                "path": relpath(ROOT, draft),
                                "summary": "Clarified the advisory and non-executing boundary.",
                                "evidence": [
                                    {
                                        "source": "revised final delivery draft",
                                        "quote_or_summary": revised_summary,
                                    }
                                ],
                            }
                        ],
                        "validation_evidence": [
                            {
                                "source": "offline regression",
                                "quote_or_summary": "Revised draft remains valid JSON.",
                            }
                        ],
                    }
                    return _decision(
                        root=ROOT,
                        output_dir=Path(kwargs["output_dir"]),
                        role="operator_codex",
                        cycle=kwargs["review_cycle_id"],
                        review_kind="recovery",
                        recommendations=[traced],
                        workflow_id="document_evidence_synthesis",
                        run_id=run_id,
                        stage_id="final_decision_brief",
                    )
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role="operator_codex",
                    cycle=kwargs["review_cycle_id"],
                    review_kind=kwargs["review_kind"],
                    workflow_id="document_evidence_synthesis",
                    run_id=run_id,
                    stage_id="final_decision_brief",
                )

            def fresh_reviewer(role: str, **kwargs):
                return _decision(
                    root=ROOT,
                    output_dir=Path(kwargs["output_dir"]),
                    role=role,
                    cycle=kwargs["review_cycle_id"],
                    review_kind=kwargs["review_kind"],
                    workflow_id="document_evidence_synthesis",
                    run_id=run_id,
                    stage_id="final_decision_brief",
                )

            fresh_cycle_id = "evidence_final_revised"
            with mock.patch.object(
                supervisor_agents,
                "invoke_operator_codex",
                side_effect=revised_operator,
            ), mock.patch.object(
                supervisor_agents,
                "invoke_codex_review_agent",
                side_effect=lambda **kwargs: fresh_reviewer("codex_review_agent", **kwargs),
            ), mock.patch.object(
                supervisor_agents,
                "invoke_claude_review_agent",
                side_effect=lambda **kwargs: fresh_reviewer("claude_review_agent", **kwargs),
            ):
                revision = supervisor.run_revision_and_review(
                    root=ROOT,
                    session_ref=session["supervisor_session_id"],
                    source_review_cycle_id=initial_cycle_id,
                    new_review_cycle_id=fresh_cycle_id,
                )

            self.assertEqual(revision["new_review_cycle_id"], fresh_cycle_id)
            revised_body = json.loads(draft.read_text(encoding="utf-8"))
            self.assertEqual(revised_body["summary"], revised_summary)
            updated = load_session(ROOT, session["supervisor_session_id"])
            initial_cycle = next(
                cycle for cycle in updated["review_cycles"] if cycle["review_cycle_id"] == initial_cycle_id
            )
            fresh_cycle = next(
                cycle for cycle in updated["review_cycles"] if cycle["review_cycle_id"] == fresh_cycle_id
            )
            self.assertEqual(initial_cycle["acceptance_status"], "superseded")
            self.assertEqual(fresh_cycle["acceptance_status"], "pending")
            self.assertNotEqual(initial_cycle["subject_id"], fresh_cycle["subject_id"])
            fresh_subject = json.loads(
                (ROOT / fresh_cycle["subject_path"]).read_text(encoding="utf-8")
            )
            self.assertEqual(fresh_subject["final_packet_draft_path"], relpath(ROOT, draft))
            self.assertEqual(fresh_subject["final_packet_draft_sha256"], sha256_file(draft))

            acceptance = supervisor.accept_consolidated_review(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                review_cycle_id=fresh_cycle_id,
                accepted_recommendation_ids=[],
            )
            self.assertEqual(acceptance["approval_decision"], "approve")
            updated = load_session(ROOT, session["supervisor_session_id"])
            fresh_cycle = next(
                cycle for cycle in updated["review_cycles"] if cycle["review_cycle_id"] == fresh_cycle_id
            )
            self.assertEqual(fresh_cycle["acceptance_status"], "accepted")

            reviews = []
            for role in ("operator_codex", "codex_review_agent", "claude_review_agent"):
                review_path = (
                    fresh_cycle["operator_provisional_record"]
                    if role == "operator_codex"
                    else fresh_cycle["review_agent_outputs"][role]
                )
                reviews.append(
                    {
                        "role": role,
                        "artifact_path": review_path,
                        "artifact_sha256": sha256_file(ROOT / review_path),
                        "decision": "approve",
                    }
                )
            reviews.append(
                {
                    "role": "consolidation",
                    "artifact_path": fresh_cycle["consolidation"],
                    "artifact_sha256": sha256_file(ROOT / fresh_cycle["consolidation"]),
                    "decision": "advisory",
                }
            )
            delivery_payload = {
                **revised_body,
                "reviews": reviews,
                "operator_acceptance": {
                    "decision_id": acceptance["decision_id"],
                    "decision": "approve",
                    "artifact_path": fresh_cycle["acceptance_record"],
                    "artifact_sha256": sha256_file(ROOT / fresh_cycle["acceptance_record"]),
                },
            }
            delivered = supervisor.create_final_delivery_bundle(
                root=ROOT,
                session_ref=session["supervisor_session_id"],
                payload=delivery_payload,
                output=None,
            )

            self.assertEqual(delivered["summary"], revised_summary)
            self.assertEqual(
                {key: value for key, value in delivered.items() if key not in {"reviews", "operator_acceptance"}},
                revised_body,
            )
            completed_session = load_session(ROOT, session["supervisor_session_id"])
            self.assertEqual(completed_session["status"], "completed")
            self.assertEqual(completed_session["current_phase"], "finalization")
            self.assertEqual(
                completed_session["final_bundle"]["schema_validation_status"], "validated"
            )
            delivery_path = ROOT / completed_session["final_bundle"]["bundle_path"]
            binding_path = ROOT / completed_session["final_bundle"]["binding_path"]
            delivery_on_disk = json.loads(delivery_path.read_text(encoding="utf-8"))
            binding = json.loads(binding_path.read_text(encoding="utf-8"))
            validate_against_schema(
                delivery_on_disk,
                "final_delivery_bundle.schema.json",
                "offline evidence-synthesis final delivery",
            )
            validate_against_schema(
                binding,
                "final_bundle_binding.schema.json",
                "offline evidence-synthesis final binding",
            )
            self.assertEqual(binding["reviewed_draft_path"], relpath(ROOT, draft))
            self.assertEqual(binding["reviewed_draft_sha256"], sha256_file(draft))
            self.assertEqual(binding["bundle_sha256"], sha256_file(delivery_path))


if __name__ == "__main__":
    unittest.main()
