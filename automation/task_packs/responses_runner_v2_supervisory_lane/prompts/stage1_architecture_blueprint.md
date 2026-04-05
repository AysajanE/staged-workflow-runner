Produce the stage-one architecture blueprint for the integrated supervisory lane that this repository should gain next.

This is the architecture and operating-contract stage.
Its job is to lock the minimum-change integration boundary, the end-to-end supervisory workflow, the review protocol, and the failure-recovery model before stage 2 drafts any final file contents.

This is not the final drop-in packet.
Do not emit full final file contents in this stage.

Use the attached repository files, tests, schemas, runbooks, and synthetic example pack to ground every important claim about current runner behavior.

Non-negotiable rules:

- Treat the primary job inputs as the controlling source for target-system requirements.
- Treat approved reviewed handoffs, if any, as the controlling source for already-locked decisions.
- Do not restate prompt text as if it were the source of target-system policy; derive target requirements from the controlling inputs and approved decisions.
- If a requirement or design constraint is not supported by the controlling inputs, attached repo evidence, or approved prior-stage decisions, mark it as an inference, option, or open question rather than a fixed requirement.
- Use web search in this stage when current official guidance, current technical docs, or other primary-source evidence could materially improve the architecture or recovery model.
- The current meta-run is still manually reviewed between stages. Design the future supervisory lane's review machinery as a target-system capability; do not treat it as already present in this run.
- When you specify test strategy or validation expectations for later stages, preserve the controlling red/green TDD requirement from the primary job inputs.
- Cite attached repo-relative paths whenever you claim that the current runner already does something.
- Label inferences as inferences when they go beyond directly attached repo evidence or retrieved external sources.
- Do not propose broad churn across unrelated files.

Return these sections in this exact order:

## 1. Decision Summary

State:

- recommended architecture in one sentence
- why the supervisory lane belongs inside this repository
- the exact boundary relative to `responses_runner_v2`
- the minimum-change thesis in one paragraph
- the one-sentence failure-recovery thesis the later stages must preserve

## 2. Current Runner Behaviors To Reuse

Use this exact table:

| capability_or_contract | current_behavior | evidence | why_it_should_be_reused |

Rules:

- Include the core behaviors that materially constrain the design.
- Cover at least:
  - workspace-root resolution
  - workflow and input-manifest loading
  - review-bundle validation
  - approved handoff progression
  - artifact layout and durable outputs
  - response markdown / JSON writing
  - resume behavior
  - refresh behavior
  - sidecar extraction
  - token-preflight behavior
- `evidence` must cite attached repo-relative paths.

## 3. Missing Capabilities To Add

Use this exact table:

| gap_id | missing_capability | why_missing_now | minimum_change_solution | candidate_repo_surfaces |

Rules:

- Include only real gaps required for this task.
- Do not inflate the scope with optional nice-to-have features.
- `candidate_repo_surfaces` must use exact repo-relative paths or path families.

## 4. End-To-End Supervisory Workflow

Use this exact table:

| seq | phase | trigger | automated_action | required_inputs | outputs | human_involvement |

Rules:

- Derive the workflow phases from the controlling inputs rather than from prompt convenience wording.
- Start at the earliest externally-triggered phase supported by the controlling inputs.
- End at the terminal artifact state supported by the controlling inputs and approved scope.
- `human_involvement` must be one of:
  - `required_initial_gate`
  - `exception_only`
  - `none`
- Include every phase that the controlling inputs make mandatory.

## 5. Failure And Recovery Model

Use this exact table:

| case_id | detection_signals | classification | current_runner_evidence | supervisory_action | human_pause_required |

Rules:

- Include the concrete runtime cases required by the controlling inputs and attached repo evidence.
- `detection_signals` must name the exact signals the future lane should inspect.
- `current_runner_evidence` must cite attached repo-relative paths.
- `human_pause_required` must be `yes` or `no`.

## 6. Minimum-Change Repo Integration Plan

Use this exact table:

| path | action | responsibility | why_here | must_preserve |

Rules:

- `path` must be an exact repo-relative path.
- `action` must be `create` or `update`.
- `responsibility` should identify the role of the file, for example:
  - `engine`
  - `task_pack`
  - `docs`
  - `tests`
  - `config`
- Do not emit file contents yet.
- The set of rows should be specific enough that stage 2 can draft the package without reopening architecture.

## 7. Review Gate Protocol

Use this exact table:

| review_gate | artifact_under_review | approval_standard | required_reviewer_note_contents | human_pause_triggers |

Rules:

- Include every review gate required by the controlling inputs and approved scope.
- Make the approval standard operational, not decorative.

## 8. Risks, Assumptions, And Open Questions

### 8A. Risks

Use this exact table:

| risk | why_it_matters | mitigation_or_design_response |

### 8B. Assumptions

List only the assumptions the later stages are allowed to rely on.

### 8C. True Open Questions

List only the unresolved questions that genuinely require later resolution.
Do not relabel already-decidable architecture choices as open questions.

## 9. Locked Decisions For Stage 2

End this section with a flat bullet list where every bullet begins with:

`Locked decision:`

Quality bar:

- Be decisive.
- Be concrete.
- Ground the architecture in the current repo rather than in abstract framework language.
- Do not draft the full final file set yet.
- Do not push core failure-classification work to stage 2.
- Do not invent or relax target-system requirements that are not justified by the controlling inputs and approved decisions.
