Produce the Stage 1 architecture and supervision protocol.

This stage must adapt the reusable prior architecture and incorporate the new two-agent review lane.

This is not a file-emission stage.

Return exactly these sections:

## 1. Architecture Decision Summary

State the recommended architecture, the minimum-change integration boundary, and why the supervisory lane belongs inside this repository.

## 2. Current Runner Capabilities To Reuse

Use this exact table:

| capability | current_behavior | evidence | reuse_decision |

Rules:

- Include at least:
  - workspace-root resolution
  - workflow loading
  - input manifest loading
  - tool profile loading
  - run artifacts
  - stage checkpoints
  - resume
  - refresh
  - review bundle creation
  - review bundle validation
  - sidecar extraction
  - failure status handling
- `evidence` must cite attached repo-relative paths.

## 3. New Capabilities Required

Use this exact table:

| capability_id | required_new_capability | why_required | minimum_change_implementation_boundary | primary_risks |

Include:

- operator Codex orchestration
- Codex review agent
- Claude review agent
- review consolidation
- selective acceptance by operator
- scaffold review before stage launch
- live monitoring
- failed-with-artifact reviewability
- failed-without-artifact rerun-as-is with archive
- incomplete output-limit blocking
- GPT-5.5 model migration

## 4. Agent Topology Decision

Explain whether the operator tasks should remain with one operator Codex agent or be split among additional agents.

You must decide, not merely list options.

Cover:

- accountability
- drift prevention
- review independence
- failure recovery
- artifact ownership
- when human pause is required

## 5. End-To-End Supervisory Workflow

Use this exact table:

| seq | phase | triggering_artifact_or_event | responsible_actor | automated_action | output_artifact | human_pause_condition |

Actors must be one of:

- `human_delegator`
- `operator_codex`
- `codex_review_agent`
- `claude_review_agent`
- `consolidation_pass`
- `responses_runner_v2`
- `supervisor_runtime`

## 6. Review Loop Protocol

Use this exact table:

| review_point | reviewed_artifacts | codex_review_focus | claude_review_focus | consolidation_rule | operator_acceptance_rule | next_artifact |

Include:

- scaffold review
- intermediate stage review
- final stage review

## 7. Failure And Recovery Model

Use this exact table:

| case_id | detection_signals | classification | automated_action | bundle_or_rerun_rule | human_pause_required |

Include all required failure classes from the primary inputs.

## 8. Model Migration Architecture

Use this exact table:

| surface | current_model_reference | target_model_reference | migration_rule | validation_required |

Cover engine defaults, caps, task packs, examples, tests, docs, and internal supervisor workflows.

## 9. Stage 2 Locks Required

List the exact decisions Stage 2 must lock.

Every bullet must begin with:

`Stage 2 must lock:`

## 10. Residual Risks And Assumptions

Separate risks from assumptions.

Do not create false open questions.
