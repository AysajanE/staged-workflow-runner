## 1. Executive Summary

Use a **four-stage `custom_ordered` task pack**. The old three-stage scaffold was too coarse for the new requirement because prompt/agent protocol design is now a first-class high-risk deliverable. The redesigned stages are:

1. Architecture and supervision protocol.
2. Agent review protocol and implementation contract.
3. Draft drop-in packet.
4. Final hardened drop-in packet.

The scaffold uses `gpt-5.5-pro` for primary generation and `gpt-5.5` for structural sidecar processing. GPT-5.5 guidance favors outcome-first prompts with explicit success criteria, constraints, evidence rules, and stopping conditions, rather than copying older process-heavy prompt stacks. ([OpenAI Developers][1])

For Codex non-interactive review, the scaffold uses only canonical `codex exec`. Current Codex docs describe `codex exec` as the stable non-interactive command. ([OpenAI Developers][2])

For Claude review, the scaffold uses `claude -p` / `--print`, with `--output-format json`, Opus model selection, and effort control. Claude docs recommend clear/direct prompts, XML structuring, explicit roles, and print mode for non-interactive automation. ([Claude][3])

---

## 2. Stage Architecture

| stage | stage_id                                     | objective                                                                                                                                 | criticality                                                                                                              | gate              |
| ----- | -------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ----------------- |
| 1     | `architecture_and_supervision_protocol`      | Lock the target architecture, end-to-end supervisory protocol, review-loop topology, failure model, and model-migration posture.          | Prevents implementation drift and decides whether operator work stays centralized or is split.                           | `review_required` |
| 2     | `agent_review_protocol_and_package_contract` | Lock exact Codex/Claude/operator review protocols, command contracts, prompt contracts, schemas, file inventory, and validation baseline. | The review lane quality depends on this stage; weak prompts or command contracts would poison all downstream automation. | `review_required` |
| 3     | `draft_drop_in_packet`                       | Produce a complete draft implementation packet with full file contents/patches, red-green tests, and rollout plan.                        | Converts design into concrete repo changes while still allowing a reviewer correction pass.                              | `review_required` |
| 4     | `final_drop_in_packet`                       | Harden the draft after review and emit the final full drop-in-ready packet.                                                               | Terminal artifact must be directly applicable without reinterpretation.                                                  | `terminal`        |

---

## 3. Review and Automation Integration

The future lane should use one **operator Codex agent** as the accountable orchestrator, but not as the only reviewer. After each non-terminal stage, the operator first performs its own substantive review and prepares a provisional reviewed bundle. Then a separate Codex review agent and Claude review agent independently audit the stage output plus the provisional bundle. The operator consolidates both reviews, rejects unsupported recommendations, updates only the recommendations it independently accepts, and then creates the approved review bundle for the next stage.

The scaffold below makes that protocol explicit in the stage prompts and corpus. The final implementation packet produced by this task pack must include the actual operator prompt, Codex review prompt, Claude review prompt, consolidation prompt, command wrappers, schemas, and tests.

---

## 4. Complete File Inventory

Root: `automation/task_packs/responses_runner_v2_supervised_end_to_end/`

| file                                                           | purpose                                                             |
| -------------------------------------------------------------- | ------------------------------------------------------------------- |
| `README.md`                                                    | Pack overview and commands.                                         |
| `shared_instructions.md`                                       | Shared stage behavior, authority, grounding, and output discipline. |
| `corpus/task_brief.md`                                         | Authoritative task brief.                                           |
| `corpus/final_deliverable_contract.md`                         | Final packet requirements.                                          |
| `corpus/review_agent_requirements.md`                          | Codex/Claude/operator review-loop requirements.                     |
| `corpus/prompting_guidance_reference.md`                       | Official prompt-design guidance distilled into task constraints.    |
| `corpus/prior_stage1_architecture_adaptation.md`               | Adapted reusable decisions from the previous Stage 1 output.        |
| `prompts/stage1_architecture_and_supervision_protocol.md`      | Stage 1 prompt.                                                     |
| `prompts/stage2_agent_review_protocol_and_package_contract.md` | Stage 2 prompt.                                                     |
| `prompts/stage3_draft_drop_in_packet.md`                       | Stage 3 prompt.                                                     |
| `prompts/stage4_final_drop_in_packet.md`                       | Stage 4 prompt.                                                     |
| `inputs/stage1.input_manifest.json`                            | Stage 1 curated input manifest.                                     |
| `inputs/stage2.input_manifest.json`                            | Stage 2 curated input manifest.                                     |
| `inputs/stage3.input_manifest.json`                            | Stage 3 curated input manifest.                                     |
| `inputs/stage4.input_manifest.json`                            | Stage 4 curated input manifest.                                     |
| `schemas/final_supervisory_packet.schema.json`                 | Final sidecar schema.                                               |
| `tools/stage1_web_search.profile.json`                         | Stage 1 web-search profile.                                         |
| `tools/stage2_web_search.profile.json`                         | Stage 2 web-search profile.                                         |
| `tools/stage3_web_search.profile.json`                         | Stage 3 web-search profile.                                         |
| `tools/stage4_web_search.profile.json`                         | Stage 4 web-search profile.                                         |
| `workflows/four_stage.workflow.json`                           | Four-stage workflow manifest.                                       |

---

## 5. Full File Contents

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/README.md`

````md
# Responses Runner V2 Supervised End-To-End Self-Improvement Pack

This task pack supersedes the older `responses_runner_v2_supervisory_lane` three-stage scaffold.

Its purpose is to run the current staged workflow runner on the task of designing the next-generation automated supervisory lane for itself.

The final stage must output a complete drop-in-ready implementation packet that the team can apply directly to this repository.

## Why This Pack Uses Four Stages

The new requirements add a second high-stakes design axis: independent non-interactive review by:

- a Codex review agent via `codex exec`
- a Claude review agent via `claude -p`
- an operator Codex agent that consolidates recommendations and accepts only supported changes

That review-and-agent protocol is too important to bury inside a generic draft stage. The workflow therefore has four material stages:

1. `architecture_and_supervision_protocol`
2. `agent_review_protocol_and_package_contract`
3. `draft_drop_in_packet`
4. `final_drop_in_packet`

Each stage performs a non-trivial, high-stakes task.

## Stage Summary

### Stage 1 — Architecture And Supervision Protocol

Locks:

- overall supervisory architecture
- operator/reviewer/consolidator topology
- failure and recovery model
- model migration posture from GPT-5.4 to GPT-5.5
- minimum-change integration boundary
- human-pause conditions

### Stage 2 — Agent Review Protocol And Package Contract

Locks:

- exact Codex operator prompt contract
- exact Codex review-agent prompt contract
- exact Claude review-agent prompt contract
- command and artifact protocol for `codex exec` and `claude -p`
- review consolidation protocol
- implementation file inventory
- schema and validation baseline

### Stage 3 — Draft Drop-In Packet

Produces a complete draft package, with full contents or exact patches for all files.

The draft must be good enough for an independent two-agent review loop.

### Stage 4 — Final Drop-In Packet

Hardens the draft after review and emits the final package.

No downstream design work should be required after this stage.

## Model Configuration

This pack uses:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`

The final implementation packet must update the repository's own defaults, model caps, example workflows, task packs, docs, and tests away from `gpt-5.4` / `gpt-5.4-pro` where appropriate.

## Recommended Dry Run

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --dry-run
```

## Recommended Live Stage 1 Run

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --skip-token-count \
  --wait
```

## Review-Bundle Pattern

After each review-required stage, create reviewer notes and an approved review bundle with:

```bash
python3 automation/create_review_bundle_v2.py \
  --root . \
  --output <run_dir>/<stage_id>.review_bundle.json \
  --workflow-id responses_runner_v2_supervised_end_to_end_self_improvement \
  --source-stage-id <stage_id> \
  --source-run-id <run_id> \
  --primary-artifact-markdown <run_dir>/stages/<NN_stage_id>/response.final.md \
  --response-artifact-json <run_dir>/stages/<NN_stage_id>/response.final.json \
  --reviewer-notes <run_dir>/<stage_id>.reviewer_notes.md
```

Then continue the run with:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --run-dir <run_dir> \
  --review-bundle <run_dir>/<stage_id>.review_bundle.json \
  --skip-token-count \
  --wait
```

## Manual Review For This Meta-Run

This current meta-run is still reviewed manually between stages.

The future lane produced by this workflow must automate those reviews with the operator Codex agent, Codex review agent, and Claude review agent.
````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/shared_instructions.md`

```md
# Shared Instructions — Responses Runner V2 Supervised End-To-End Self-Improvement

<role>
You are the repo-grounded automation architect and implementation-packet designer for `staged-workflow-runner`.
Your job is to design the task scaffold that will cause the runner to produce its own next-generation supervisory automation lane.
</role>

<primary_goal>
Produce stage outputs that culminate in a complete drop-in-ready implementation packet for end-to-end supervised execution of `responses_runner_v2`.
</primary_goal>

<authority_order>
Use this authority order:

1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Repository Files
4. Reference Context
</authority_order>

<critical_requirement>
The final implementation must include an operator Codex lane, a Codex review-agent lane, and a Claude review-agent lane.
The operator Codex agent may not blindly accept reviewer recommendations.
It must independently evaluate and accept only recommendations supported by repo evidence, stage artifacts, reviewer notes, and the task brief.
</critical_requirement>

<stage_boundary_rules>
- Stage 1 locks architecture and supervisory protocol.
- Stage 2 locks agent review protocol, command contracts, prompt contracts, file inventory, and implementation contracts.
- Stage 3 emits the complete draft implementation packet.
- Stage 4 emits the final hardened implementation packet.
</stage_boundary_rules>

<model_migration_rule>
The target implementation must update model configuration from the GPT-5.4 family to the GPT-5.5 family wherever the runner, examples, docs, schemas, tests, or task packs encode model defaults or expectations.
</model_migration_rule>

<prompt_design_rules>
Design prompts for the future operator and review agents using outcome-first instructions:
- state the objective
- state success criteria
- state constraints and authority rules
- state allowed side effects
- state output format
- state stopping conditions

For Claude prompts, use clear roles and XML-structured blocks where that reduces ambiguity.
For Codex prompts, keep commands non-interactive, repo-grounded, and acceptance-test oriented.
</prompt_design_rules>

<repo_grounding_rules>
- Cite attached repo-relative paths for claims about current code, tests, schemas, docs, or task packs.
- Do not claim current behavior unless the attached files support it.
- Separate current behavior, required new behavior, and optional future work.
- Do not invent target-system requirements absent from the primary inputs or approved handoffs.
</repo_grounding_rules>

<review_loop_rules>
For every non-terminal stage in the future lane:
1. the operator Codex agent prepares an initial review and provisional next-stage bundle;
2. the Codex review agent independently reviews the stage output and provisional bundle;
3. the Claude review agent independently reviews the same material;
4. a consolidation pass merges the two reviews into one report;
5. the operator Codex agent accepts only supported recommendations and emits the final approved bundle.

The current meta-run remains manually reviewed between stages.
</review_loop_rules>

<failure_recovery_rules>
The final implementation must distinguish:
- completed stage with complete artifact
- failed stage with complete substantive assistant artifact
- failed stage with empty or insufficient artifact
- incomplete output-limit outcome
- blocked token-preflight outcome
- long-running stage monitoring anomaly

A failed stage with a complete substantive artifact must be reviewable.
A failed stage without a substantive artifact may be rerun as-is only after the current attempt is archived.
An incomplete output-limit outcome must not auto-progress.
</failure_recovery_rules>

<output_rules>
- Follow the stage prompt exactly.
- Use exact requested section headings.
- Use exact table headers when provided.
- For file-emission stages, every file must be complete.
- Use exact repo-relative paths.
- Do not leave TODOs or placeholders in final file contents.
- Use quadruple-backtick fences for files that may contain fenced code.
</output_rules>

<verification_loop>
Before finalizing:
- verify that every requested section exists;
- verify that file inventories and file blocks match;
- verify that review-agent prompts are explicit enough for non-interactive execution;
- verify that model migration from GPT-5.4 to GPT-5.5 is covered;
- verify that failure handling and human-pause triggers are operational;
- verify that tests include true red/green evidence.
</verification_loop>
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/task_brief.md`

```md
# Primary Task Brief

## Task

Design a complete task-pack scaffold that will be used by `responses_runner_v2` to generate a final drop-in-ready implementation packet for the next-generation end-to-end supervisory lane.

The runner is improving itself.

## Required Final Capability

The final implementation packet produced by this workflow must make the staged workflow runner capable of:

1. receiving a high-stakes task after an initial clarification gate;
2. generating or improving a task-specific scaffold;
3. independently reviewing that scaffold before any costly stage execution;
4. launching stages;
5. monitoring long-running remote Responses API stages;
6. retrieving terminal artifacts;
7. reviewing intermediate stage outputs substantively;
8. invoking independent non-interactive review agents:
   - Codex review agent using `codex exec` as the canonical current command;
   - Claude review agent using `claude -p`;
9. consolidating independent reviews;
10. allowing the operator Codex agent to accept only supported reviewer recommendations;
11. preparing reviewer notes and approved review bundles for the next stage;
12. repeating the process until the terminal stage;
13. preparing the final approved implementation bundle.

## Human Participation Model

The only mandatory human interaction in the future lane should be the initial clarification gate.

After that gate, normal execution should be AI-operated.

Human pauses after the initial gate are exception paths only and must specify:

- exact trigger;
- artifact to present;
- decision required;
- safe continuation action.

## Stage-Economics Rule

Every paid stage must perform critical, non-trivial work.

Do not create a stage that only renames, formats, or lightly summarizes another stage.

## Review Quality Rule

The future review loop must preserve or exceed the current manual quality bar.

Automated reviews must evaluate:

- scaffold quality;
- prompt specificity;
- input manifest signal quality;
- attached context quality;
- model and tool settings;
- stage structure;
- stage-output quality;
- completeness;
- substantive soundness;
- next-stage readiness.

## Model Migration Rule

The final packet must update model configuration from `gpt-5.4` / `gpt-5.4-pro` to GPT-5.5 family models wherever appropriate.

At minimum, this must address:

- engine defaults;
- model caps;
- workflow manifests;
- example packs;
- self-improvement packs;
- tests;
- docs and runbooks;
- generated supervisor internal workflows.

## Output Requirement

The final stage of this workflow must output a full implementation packet.

For new files, it must provide complete file contents.

For existing files, it must provide complete replacement content or exact apply-ready patches.

The team must be able to materialize the packet without reinterpretation.
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/final_deliverable_contract.md`

`````md
# Final Deliverable Contract

## Final Output Form

The terminal stage must output a full drop-in-ready packet for this repository.

The packet must be directly applicable without additional design work.

## Required Final Packet Contents

The packet must include, at minimum:

1. a root `AGENTS.md`;
2. a supervisor CLI entrypoint;
3. a supervisor orchestration module;
4. a supervisor session schema;
5. an internal supervisor task pack;
6. operator Codex prompt content;
7. Codex review-agent prompt content;
8. Claude review-agent prompt content;
9. review consolidation prompt content;
10. command templates for:
    - `codex exec`
    - `claude -p`
11. tests for:
    - model migration;
    - supervisor session creation;
    - scaffold packet staging;
    - scaffold review gating;
    - Codex review invocation contract;
    - Claude review invocation contract;
    - consolidation and selective acceptance;
    - failed-with-artifact reviewability;
    - failed-without-artifact rerun-as-is with archive;
    - incomplete output-limit blocking;
    - final implementation bundle creation;
12. documentation or runbook updates.

## Required Model Migration

The final packet must update all relevant `gpt-5.4` and `gpt-5.4-pro` configurations to GPT-5.5 family configurations.

The final packet must decide and justify exact defaults.

Expected default posture:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`
- reasoning effort:
  - `xhigh` for high-stakes primary long-running generation
  - `high` or `medium` for structural sidecars if tests show adequate quality
- verbosity:
  - `high` for final packet generation
  - `medium` for structural processing

## Required Review-Agent Prompt Quality

The operator Codex prompt, Codex review-agent prompt, and Claude review-agent prompt must be complete final prompt artifacts.

They must specify:

- role;
- scope;
- inputs;
- output format;
- review criteria;
- evidence rules;
- refusal or blocked-state behavior;
- how to handle missing artifacts;
- how to identify unsupported recommendations;
- how to keep the review non-interactive;
- how to produce machine-ingestible output.

## Required Review Protocol

The final implementation must implement this review sequence for every non-terminal stage:

1. operator Codex reviews the stage output and prepares provisional notes and provisional bundle;
2. Codex review agent independently reviews output plus provisional bundle;
3. Claude review agent independently reviews output plus provisional bundle;
4. consolidation pass merges review findings;
5. operator Codex evaluates the consolidated report;
6. operator Codex updates the bundle only where it agrees with supported recommendations;
7. final approved review bundle is created.

## Required Failure Policies

The final packet must encode behavior for:

- `completed` with complete artifact;
- `failed` with complete substantive artifact;
- `failed` with no substantive artifact;
- `incomplete` from output-limit exhaustion;
- blocked preflight;
- monitoring timeout or stale status.

## Testing Rule

The final packet must include red/green TDD validation.

A valid red row must fail before the new package is applied and must exercise new behavior.

A valid green row must pass after the package is applied.

Already-passing tests do not count as red-phase evidence.

## Final Formatting Rule

When emitting final file contents, use:

```text
### File: `<repo-relative path>`

- action: `create` or `update`
- why required: `<brief rationale>`

````<language>
<complete final file contents>
`````

```

Do not emit partial files.

Do not hide uncertainty in final file contents.
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/review_agent_requirements.md`

````md
# Review Agent Requirements

## Agents

The future lane must use three distinct agent roles.

### 1. Operator Codex Agent

The operator Codex agent is the accountable orchestrator.

Responsibilities:

- run or resume stages;
- monitor live remote status;
- retrieve terminal artifacts;
- perform first substantive review;
- prepare provisional reviewer notes;
- prepare provisional next-stage bundle;
- invoke independent review agents;
- consolidate review feedback;
- accept only supported recommendations;
- create final approved review bundles;
- decide recovery actions according to policy.

The operator must not blindly accept reviewer recommendations.

### 2. Codex Review Agent

The Codex review agent runs non-interactively.

Required command:

```bash
codex exec "<prompt or prompt-file-driven task>"
```

The Codex review agent should be read-only unless the implementation explicitly stages a repair task. For review, it must produce an artifact, not patches.

### 3. Claude Review Agent

The Claude review agent runs non-interactively.

Canonical command pattern:

```bash
claude --bare -p \
  --model opus \
  --effort max \
  --output-format json \
  --append-system-prompt-file <prompt_file> \
  "<review job>"
```

If local Claude Code does not support `--effort max` for the configured Opus model, the final implementation may fall back to `--effort xhigh`, but the fallback must be explicit and logged.

The Claude review agent must use XML-structured prompts when multiple context blocks are supplied.

## Independent Review Requirements

Each independent review must evaluate:

- whether the stage output satisfies the stage objective;
- whether the next-stage provisional bundle is safe and sufficient;
- whether important facts are unsupported;
- whether stage-economics are respected;
- whether prompt/model/tool choices are aligned;
- whether input manifests contain high-signal context;
- whether any required failure-handling or review behavior is missing;
- whether downstream stages would be misled or under-specified.

## Consolidation Requirements

The consolidation output must classify recommendations as:

- accepted;
- rejected;
- needs operator judgment;
- duplicate;
- already satisfied;
- out of scope.

Each accepted item must have:

- source agent;
- evidence;
- affected artifact;
- exact change needed.

The operator Codex agent must perform a final independent judgment before applying accepted recommendations.

## Non-Interactive Output Requirement

Review agents must produce machine-ingestible output.

At minimum:

- markdown reviewer report;
- JSON sidecar or JSON block with decision fields;
- list of blocking issues;
- list of non-blocking improvements;
- explicit approval or non-approval decision.

## Prohibited Behavior

Review agents must not:

- edit files during review;
- create approved bundles directly;
- override the operator;
- accept recommendations without evidence;
- request interactive clarification during non-interactive review;
- silently skip missing artifacts.
````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prompting_guidance_reference.md`

```md
# Prompting Guidance Reference

This file records the official-source prompting constraints that this task pack must preserve.

## OpenAI GPT-5.5 Guidance

Official source URLs:

- https://developers.openai.com/api/docs/guides/latest-model
- https://developers.openai.com/api/docs/guides/prompt-guidance
- https://developers.openai.com/api/docs/models/gpt-5.5
- https://developers.openai.com/api/docs/models/gpt-5.5-pro
- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/cli/reference

Design implications for this task:

- Prompt for outcome, success criteria, constraints, allowed side effects, evidence rules, output format, and stopping conditions.
- Do not copy older process-heavy prompt stacks blindly.
- Use `gpt-5.5-pro` for high-stakes long-running primary generation.
- Use `gpt-5.5` for structural or lower-latency processing when sufficient.
- Use `reasoning_effort: xhigh` only where maximum intelligence matters more than latency/cost.
- Use `codex exec` as the canonical non-interactive Codex CLI command.

## Anthropic Claude Guidance

Official source URLs:

- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/settings

Design implications for this task:

- Claude prompts should be clear and direct.
- Use XML tags to separate instructions, context, inputs, and output contract.
- Give Claude a role.
- For non-interactive automation, use `claude -p` or `claude --print`.
- Prefer `--bare` for scripted calls when deterministic context loading is important.
- Use `--output-format json` and `--json-schema` when machine-ingestible output is required.
- Use `--append-system-prompt-file` for additional role/task instructions while preserving Claude Code's built-in behavior, unless the implementation has a strong reason to replace the system prompt.
- Use `--effort max` for the highest available Opus reasoning mode when supported; otherwise fall back explicitly to `--effort xhigh`.

## Required Prompt Style For Final Implementation

The final implementation must produce prompt artifacts that are:

- concise enough for GPT-5.5 and Claude Opus to follow;
- explicit enough for non-interactive operation;
- structured enough to prevent review drift;
- grounded in artifacts and repo paths;
- stable under command-line execution;
- compatible with JSON sidecar extraction.
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prior_stage1_architecture_adaptation.md`

````md
# Prior Stage 1 Architecture Adaptation

The previous self-improvement run completed an architecture blueprint.

This file adapts the reusable decisions from that output for the new four-stage scaffold.

## Reusable Architecture Decisions

The following decisions remain valid:

- Add an additive supervisory layer rather than rewriting `responses_runner_v2`.
- Preserve existing runner primitives:
  - `run_workflow`
  - `resume_stage`
  - `refresh_stage`
  - `create_review_bundle`
  - run manifests
  - stage checkpoints
  - response markdown/json artifact pairs
  - sidecar extraction
- Keep one exact workspace root.
- Hydrate any reusable supervisor task pack under the active workspace root instead of introducing dual-root resolution.
- Use existing review-bundle contracts for approved stage progression.
- Treat failed stages with complete substantive artifacts as reviewable.
- Treat failed stages without substantive artifacts as rerun candidates only after preserving the failed attempt.
- Treat output-limit `incomplete` as a separate exception path.
- Use polling and refresh/resume before adding webhook infrastructure.
- Keep implementation minimum-change and additive.

## Required Adaptations For This New Scaffold

The previous architecture did not fully cover:

- two independent non-interactive review agents;
- Codex review via `codex exec`;
- Claude review via `claude -p`;
- review consolidation;
- operator selective acceptance of recommendations;
- GPT-5.5 family model migration;
- prompt contracts following current GPT-5.5 and Claude Opus guidance;
- full red/green tests for review-agent invocation and consolidation.

This new scaffold must make those items first-class requirements.

## Target Agent Topology

The target topology should be:

```text
human delegator
  -> initial clarification gate
  -> operator Codex agent
       -> scaffold author/improver
       -> dry-run validator
       -> stage launcher / monitor
       -> first-stage-output reviewer
       -> provisional notes and bundle
       -> Codex review agent
       -> Claude review agent
       -> consolidation pass
       -> selective acceptance
       -> approved bundle creation
       -> next stage
```

The operator Codex agent remains accountable, but independent reviewers supply adversarial review pressure.

## Stage Design Implication

The new task scaffold should not jump directly from architecture to final packet.

The review-agent prompt and command protocol is critical enough to deserve its own stage.
````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/prompts/stage1_architecture_and_supervision_protocol.md`

```md
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
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/prompts/stage2_agent_review_protocol_and_package_contract.md`

`````md
Produce the Stage 2 agent review protocol and package contract.

Use the approved Stage 1 architecture and reviewer notes as controlling authority.

This stage must lock the exact contract that Stage 3 must implement.

Do not emit the full implementation package yet, except for the smallest subset of boundary-locking prompt or schema files whose exact content must be fixed now to avoid Stage 3 drift.

Return exactly these sections:

## 1. Locked Protocol Summary

Summarize:

- approved architecture
- operator/reviewer/consolidator topology
- model migration posture
- failure recovery posture
- exact Stage 2 to Stage 3 boundary

## 2. Final Package File Inventory

Use this exact table:

| path | action | category | purpose | stage3_obligation |

Categories must be one of:

- `engine`
- `task_pack`
- `docs`
- `tests`
- `config`
- `ops`

Rules:

- Include root `AGENTS.md`.
- Include all required operator/reviewer prompt files.
- Include all required schemas and tests.
- Include only files that Stage 3 must implement.

## 3. File Implementation Contracts

Use this exact table:

| path | required_behavior | must_include | dependencies_or_interfaces | stage3_completion_rule |

There must be exactly one row for every file in Section 2.

## 4. Agent Command Contracts

Use this exact table:

| agent | canonical_command_shape | command_constraints | required_inputs | required_outputs | failure_handling |

Include:

- operator Codex
- Codex review agent
- Claude review agent
- consolidation pass

## 5. Prompt Contracts

Use this exact table:

| prompt_artifact | target_agent | purpose | required_sections | output_contract | grounding_rules | non_interactive_constraints |

Include at least:

- operator Codex prompt
- Codex review prompt
- Claude review prompt
- review consolidation prompt
- scaffold author/improver prompt
- stage-output review prompt

## 6. Review Decision Schema Contract

Specify the schema fields the final implementation must include for machine-ingestible review decisions.

Use this exact table:

| field | type | required | meaning | validation_rule |

## 7. Supervisor Session Schema Contract

Specify the session state fields the final implementation must include.

Use this exact table:

| field | type | required | meaning | validation_rule |

## 8. Boundary-Locking Draft Files

Emit full file contents only where exact wording or structure must be preserved into Stage 3.

For each emitted file:

### File: `<repo-relative path>`

- action: `create` or `update`
- why locked now: `<reason>`
- stage3 rule: `<what Stage 3 must preserve or may tighten>`

````<language>
<complete file contents>
`````

If no file truly needs early locking, write `None.`

## 9. Red/Green Validation Baseline

Use this exact table:

| phase | check_id | command_or_method | expected_result | why_it_matters |

Rules:

* `phase` must be `red` or `green`.
* Each red check must fail before implementation.
* Each green check must pass after implementation.
* Include tests for review-agent invocation, consolidation, selective acceptance, failure recovery, model migration, and final packet schema.

## 10. Reviewer Focus For Stage 2

Use this exact table:

| focus_area | why_high_risk | what_reviewer_must_audit |

## 11. Stage 3 Charter

Format exactly:

* `Preserve:`
* `Implement fully:`
* `Do not reopen:`

`````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/prompts/stage3_draft_drop_in_packet.md`

````md
Produce the Stage 3 draft drop-in packet.

Use the approved Stage 1 architecture, approved Stage 2 package contract, and reviewer notes as controlling authority.

This is a full draft implementation stage.

Emit complete file contents or exact patches for every file in the approved Stage 2 inventory.

Return exactly these sections:

## 1. Draft Package Summary

State what the draft implements and whether it preserves the approved Stage 2 contract.

## 2. Contract Preservation Matrix

Use this exact table:

| stage2_contract_item | preserved_or_changed | justification | affected_files |

If anything changed without reviewer authority, mark it as a defect.

## 3. Draft File Inventory

Use this exact table:

| path | action | category | purpose |

The inventory must match the file blocks in Section 4 exactly.

## 4. Draft Drop-In Files

For every changed file, use this exact structure:

### File: `<repo-relative path>`

- action: `create` or `update`
- why required: `<brief rationale>`

````<language>
<complete final draft file contents or exact apply-ready patch>
`````

Rules:

* Use full replacement files unless a patch is safer for an existing large file.
* If using a patch, it must be complete and apply-ready.
* Do not include TODOs.
* Include all operator/reviewer/consolidation prompts as complete files.
* Include all tests as complete files.
* Include all docs as complete files or exact patches.
* Include model migration changes.

## 5. Draft Review-Agent Prompt Quality Check

Use this exact table:

| prompt_file | outcome_first_contract | evidence_rules | non_interactive_safety | machine_output_contract | remaining_risk |

## 6. Draft Failure-Policy Check

Use this exact table:

| failure_case | implemented_detection | implemented_action | tested_by | remaining_gap |

Include all required failure cases.

## 7. Draft Validation Plan

Use this exact table:

| phase | check_id | command_or_method | expected_result | acceptance_reason |

Rules:

* `phase` must be `red` or `green`.
* Red checks must be real pre-change failures.
* Green checks must be post-change passes.

## 8. Reviewer Notes For Stage 3 Reviewers

Use this exact table:

| issue_area | why_reviewer_should_focus | evidence_to_inspect |

## 9. Stage 4 Charter

Format exactly:

* `Preserve:`
* `Tighten:`
* `Do not reopen:`

`````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/prompts/stage4_final_drop_in_packet.md`

````md
Produce the Stage 4 final hardened drop-in packet.

Use the approved Stage 1 architecture, approved Stage 2 package contract, approved Stage 3 draft packet, and Stage 3 reviewer notes as controlling authority.

This is the terminal full-package emission stage.

Do not reopen approved architecture or package inventory unless the Stage 3 review explicitly required it.

Return exactly these sections:

## 1. Final Package Summary

State:

- what the final package implements;
- whether any approved Stage 2 or Stage 3 contract item changed;
- why the packet is directly materializable.

## 2. Final File Inventory

Use this exact table:

| path | action | category | purpose |

Rules:

- Must include root `AGENTS.md`.
- Must include every file emitted in Section 3.
- Must not include extra files not emitted in Section 3.

## 3. Final Drop-In Files

For every changed file, use this exact structure:

### File: `<repo-relative path>`

- action: `create` or `update`
- why required: `<brief rationale>`

````<language>
<complete final file contents or exact apply-ready patch>
`````

Rules:

* Complete final files are preferred.
* Exact patches are allowed only for existing large files where a patch is safer.
* No placeholders.
* No TODOs.
* No hidden dependencies.
* Every prompt file must be complete.
* Every test file must be complete.
* Every schema file must be complete.
* Model migration must be complete.

## 4. Final Review-Agent Protocol

Use this exact table:

| review_step | actor | command_or_method | input_artifacts | output_artifacts | acceptance_rule |

Include operator Codex, Codex review agent, Claude review agent, consolidation, and selective acceptance.

## 5. Final Failure And Recovery Policy

Use this exact table:

| case_id | detection_signal | action | human_pause | tested_by |

## 6. Final Validation And Acceptance Checks

Use this exact table:

| phase | check_id | command_or_method | expected_result | acceptance_reason |

Rules:

* `phase` must be `red` or `green`.
* Include all checks required by approved Stage 2 and Stage 3.
* Include model migration checks.
* Include dry-run/smoke checks.

## 7. Rollout Instructions

Provide exact commands and sequencing for applying and validating the packet.

## 8. Human Pause And Escalation Conditions

Use this exact table:

| condition | detection_signal | artifact_to_present | human_decision_required |

## 9. Residual Risks

If none remain, write `None.`

Quality bar:

* The packet must be materializable verbatim.
* The file inventory and emitted files must match exactly.
* The final implementation must not require another design pass.

`````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/inputs/stage1.input_manifest.json`

````json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "responses_runner_v2_supervised_end_to_end.stage1",
  "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
  "stage_id": "architecture_and_supervision_protocol",
  "description": "Stage 1 broad architecture corpus. It includes authoritative task inputs, adapted prior Stage 1 decisions, current runner code, tests, docs, the old supervisory-lane scaffold, and the synthetic proof pack as low-authority structure evidence.",
  "primary_job_inputs": [
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/task_brief.md",
      "kind": "file",
      "notes": "authoritative task brief"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/final_deliverable_contract.md",
      "kind": "file",
      "notes": "authoritative final packet contract"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/review_agent_requirements.md",
      "kind": "file",
      "notes": "authoritative review-agent lane requirements"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prompting_guidance_reference.md",
      "kind": "file",
      "notes": "official-source prompting and CLI guidance distilled for this task"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prior_stage1_architecture_adaptation.md",
      "kind": "file",
      "notes": "adapted reusable architecture decisions from the previous completed Stage 1"
    }
  ],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "README.md",
      "kind": "file",
      "notes": "top-level repository contract"
    },
    {
      "path": "TEAM_ONBOARDING.md",
      "kind": "file",
      "notes": "team mental model and reading order"
    },
    {
      "path": "docs/runbooks/responses-runner-v2.md",
      "kind": "file",
      "notes": "current runner operating runbook"
    },
    {
      "path": "automation/run_responses_v2.py",
      "kind": "file",
      "notes": "current runner CLI"
    },
    {
      "path": "automation/create_review_bundle_v2.py",
      "kind": "file",
      "notes": "current review-bundle CLI"
    },
    {
      "path": "automation/run_responses_v2_eval.py",
      "kind": "file",
      "notes": "current eval harness"
    },
    {
      "path": "automation/evals/responses_runner_v2.eval.json",
      "kind": "file",
      "notes": "current eval dataset"
    },
    {
      "path": "automation/responses_runner_v2",
      "kind": "directory",
      "exclude_globs": [
        "automation/responses_runner_v2/__pycache__",
        "automation/responses_runner_v2/__init__.py"
      ],
      "notes": "core engine package, schemas, prompts, and orchestration code"
    },
    {
      "path": "automation/tests",
      "kind": "directory",
      "exclude_globs": [
        "automation/tests/__pycache__"
      ],
      "notes": "current executable specifications"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervisory_lane",
      "kind": "directory",
      "notes": "previous three-stage self-improvement scaffold for comparison and reuse"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end",
      "kind": "directory",
      "exclude_globs": [
        "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus"
      ],
      "notes": "this new scaffold's prompts, manifests, schema, tool profiles, and workflow"
    }
  ],
  "reference_context": [
    {
      "path": "automation/examples/responses_runner_v2_synthetic",
      "kind": "directory",
      "notes": "low-authority proof-pack structure evidence"
    }
  ]
}
`````

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/inputs/stage2.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "responses_runner_v2_supervised_end_to_end.stage2",
  "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
  "stage_id": "agent_review_protocol_and_package_contract",
  "description": "Stage 2 narrows to agent protocol, implementation contracts, schemas, command behavior, tests, and docs. The approved Stage 1 bundle is added dynamically.",
  "primary_job_inputs": [
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/task_brief.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/final_deliverable_contract.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/review_agent_requirements.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prompting_guidance_reference.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prior_stage1_architecture_adaptation.md",
      "kind": "file"
    }
  ],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "README.md",
      "kind": "file"
    },
    {
      "path": "docs/runbooks/responses-runner-v2.md",
      "kind": "file"
    },
    {
      "path": "automation/run_responses_v2.py",
      "kind": "file"
    },
    {
      "path": "automation/create_review_bundle_v2.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/contracts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/workflow.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/review_bundle.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/artifacts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/pack_loader.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/sidecar.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/workflow_manifest.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/review_bundle.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/run_manifest.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/stage_checkpoint.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/tests/test_responses_runner_v2_workflow.py",
      "kind": "file"
    },
    {
      "path": "automation/tests/test_responses_runner_v2_review_bundle.py",
      "kind": "file"
    },
    {
      "path": "automation/tests/test_responses_runner_v2_contracts.py",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end",
      "kind": "directory",
      "exclude_globs": [
        "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus"
      ],
      "notes": "current scaffold surfaces"
    }
  ],
  "reference_context": [
    {
      "path": "automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
      "kind": "file"
    },
    {
      "path": "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage1.input_manifest.json",
      "kind": "file"
    },
    {
      "path": "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage2.input_manifest.json",
      "kind": "file"
    },
    {
      "path": "automation/examples/responses_runner_v2_synthetic/inputs/reviewed_stage3.input_manifest.json",
      "kind": "file"
    }
  ]
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/inputs/stage3.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "responses_runner_v2_supervised_end_to_end.stage3",
  "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
  "stage_id": "draft_drop_in_packet",
  "description": "Stage 3 implementation-drafting corpus. The approved Stage 2 bundle is added dynamically. Context is narrowed to files likely to be created or patched, tests, docs, and current scaffold constraints.",
  "primary_job_inputs": [
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/task_brief.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/final_deliverable_contract.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/review_agent_requirements.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prompting_guidance_reference.md",
      "kind": "file"
    }
  ],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "README.md",
      "kind": "file"
    },
    {
      "path": "docs/runbooks/responses-runner-v2.md",
      "kind": "file"
    },
    {
      "path": "automation/run_responses_v2.py",
      "kind": "file"
    },
    {
      "path": "automation/create_review_bundle_v2.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/contracts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/workflow.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/review_bundle.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/artifacts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/openai_client.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/pack_loader.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas",
      "kind": "directory"
    },
    {
      "path": "automation/tests/test_responses_runner_v2_workflow.py",
      "kind": "file"
    },
    {
      "path": "automation/tests/test_responses_runner_v2_review_bundle.py",
      "kind": "file"
    },
    {
      "path": "automation/tests/test_responses_runner_v2_contracts.py",
      "kind": "file"
    },
    {
      "path": "automation/examples/responses_runner_v2_synthetic/workflows",
      "kind": "directory"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end",
      "kind": "directory",
      "exclude_globs": [
        "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus"
      ]
    }
  ],
  "reference_context": [
    {
      "path": "automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervisory_lane/schemas/final_drop_in_packet.schema.json",
      "kind": "file"
    }
  ]
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/inputs/stage4.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "responses_runner_v2_supervised_end_to_end.stage4",
  "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
  "stage_id": "final_drop_in_packet",
  "description": "Stage 4 final hardening corpus. The approved Stage 3 bundle is added dynamically. Context focuses on final packet correctness, model migration, review-agent protocol, tests, and materialization readiness.",
  "primary_job_inputs": [
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/task_brief.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/final_deliverable_contract.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/review_agent_requirements.md",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/corpus/prompting_guidance_reference.md",
      "kind": "file"
    }
  ],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "README.md",
      "kind": "file"
    },
    {
      "path": "docs/runbooks/responses-runner-v2.md",
      "kind": "file"
    },
    {
      "path": "automation/run_responses_v2.py",
      "kind": "file"
    },
    {
      "path": "automation/create_review_bundle_v2.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/contracts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/workflow.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/review_bundle.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/artifacts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas",
      "kind": "directory"
    },
    {
      "path": "automation/tests",
      "kind": "directory",
      "exclude_globs": [
        "automation/tests/__pycache__",
        "automation/tests/test_responses_runner_v2_eval.py"
      ]
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/schemas/final_supervisory_packet.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/task_packs/responses_runner_v2_supervised_end_to_end/prompts/stage4_final_drop_in_packet.md",
      "kind": "file"
    }
  ],
  "reference_context": [
    {
      "path": "automation/examples/responses_runner_v2_synthetic/README.md",
      "kind": "file"
    },
    {
      "path": "automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json",
      "kind": "file"
    }
  ]
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/schemas/final_supervisory_packet.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Responses Runner V2 Supervised End To End Final Packet",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "packet_version",
    "workflow_id",
    "summary",
    "model_migration",
    "files",
    "agent_protocols",
    "failure_policies",
    "human_pause_conditions",
    "acceptance_checks"
  ],
  "properties": {
    "packet_version": {
      "const": "responses_runner_v2.supervised_end_to_end.packet.v1"
    },
    "workflow_id": {
      "const": "responses_runner_v2_supervised_end_to_end_self_improvement"
    },
    "summary": {
      "type": "string",
      "minLength": 1
    },
    "model_migration": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "primary_generation_model",
        "structural_processing_model",
        "surfaces_updated"
      ],
      "properties": {
        "primary_generation_model": {
          "const": "gpt-5.5-pro"
        },
        "structural_processing_model": {
          "const": "gpt-5.5"
        },
        "surfaces_updated": {
          "type": "array",
          "minItems": 1,
          "items": {
            "type": "string",
            "minLength": 1
          }
        }
      }
    },
    "files": {
      "type": "array",
      "minItems": 1,
      "allOf": [
        {
          "contains": {
            "type": "object",
            "required": [
              "path"
            ],
            "properties": {
              "path": {
                "const": "AGENTS.md"
              }
            }
          }
        }
      ],
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "path",
          "action",
          "category",
          "purpose"
        ],
        "properties": {
          "path": {
            "type": "string",
            "minLength": 1
          },
          "action": {
            "enum": [
              "create",
              "update"
            ]
          },
          "category": {
            "enum": [
              "engine",
              "task_pack",
              "docs",
              "tests",
              "config",
              "ops"
            ]
          },
          "purpose": {
            "type": "string",
            "minLength": 1
          }
        }
      }
    },
    "agent_protocols": {
      "type": "array",
      "minItems": 3,
      "allOf": [
        {
          "contains": {
            "type": "object",
            "required": [
              "agent"
            ],
            "properties": {
              "agent": {
                "const": "operator_codex"
              }
            }
          }
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "agent"
            ],
            "properties": {
              "agent": {
                "const": "codex_review_agent"
              }
            }
          }
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "agent"
            ],
            "properties": {
              "agent": {
                "const": "claude_review_agent"
              }
            }
          }
        }
      ],
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "agent",
          "command_shape",
          "prompt_file",
          "output_artifacts"
        ],
        "properties": {
          "agent": {
            "type": "string",
            "minLength": 1
          },
          "command_shape": {
            "type": "string",
            "minLength": 1
          },
          "prompt_file": {
            "type": "string",
            "minLength": 1
          },
          "output_artifacts": {
            "type": "array",
            "minItems": 1,
            "items": {
              "type": "string",
              "minLength": 1
            }
          }
        }
      }
    },
    "failure_policies": {
      "type": "array",
      "minItems": 1,
      "allOf": [
        {
          "contains": {
            "type": "object",
            "required": [
              "case_id"
            ],
            "properties": {
              "case_id": {
                "const": "completed_complete_artifact"
              }
            }
          }
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "case_id"
            ],
            "properties": {
              "case_id": {
                "const": "failed_complete_artifact"
              }
            }
          }
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "case_id"
            ],
            "properties": {
              "case_id": {
                "const": "failed_no_artifact"
              }
            }
          }
        },
        {
          "contains": {
            "type": "object",
            "required": [
              "case_id"
            ],
            "properties": {
              "case_id": {
                "const": "incomplete_output_limit"
              }
            }
          }
        }
      ],
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "case_id",
          "trigger",
          "decision_rule",
          "automation_action",
          "human_pause_required"
        ],
        "properties": {
          "case_id": {
            "type": "string",
            "minLength": 1
          },
          "trigger": {
            "type": "string",
            "minLength": 1
          },
          "decision_rule": {
            "type": "string",
            "minLength": 1
          },
          "automation_action": {
            "type": "string",
            "minLength": 1
          },
          "human_pause_required": {
            "type": "boolean"
          }
        }
      }
    },
    "human_pause_conditions": {
      "type": "array",
      "items": {
        "type": "string",
        "minLength": 1
      }
    },
    "acceptance_checks": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "string",
        "minLength": 1
      }
    }
  }
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/tools/stage1_web_search.profile.json`

```json
{
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "high",
      "user_location": {
        "type": "approximate",
        "country": "US",
        "timezone": "America/Toronto"
      }
    }
  ],
  "tool_choice": "auto",
  "include": [
    "web_search_call.action.sources"
  ],
  "parallel_tool_calls": true,
  "max_tool_calls": 128
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/tools/stage2_web_search.profile.json`

```json
{
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "high",
      "user_location": {
        "type": "approximate",
        "country": "US",
        "timezone": "America/Toronto"
      }
    }
  ],
  "tool_choice": "auto",
  "include": [
    "web_search_call.action.sources"
  ],
  "parallel_tool_calls": true,
  "max_tool_calls": 96
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/tools/stage3_web_search.profile.json`

```json
{
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "medium",
      "user_location": {
        "type": "approximate",
        "country": "US",
        "timezone": "America/Toronto"
      }
    }
  ],
  "tool_choice": "auto",
  "include": [
    "web_search_call.action.sources"
  ],
  "parallel_tool_calls": true,
  "max_tool_calls": 64
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/tools/stage4_web_search.profile.json`

```json
{
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "medium",
      "user_location": {
        "type": "approximate",
        "country": "US",
        "timezone": "America/Toronto"
      }
    }
  ],
  "tool_choice": "auto",
  "include": [
    "web_search_call.action.sources"
  ],
  "parallel_tool_calls": true,
  "max_tool_calls": 64
}
```

### File: `automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json`

```json
{
  "schema_version": "responses_runner_v2.workflow_manifest.v1",
  "workflow_id": "responses_runner_v2_supervised_end_to_end_self_improvement",
  "workflow_name": "Responses Runner V2 Supervised End To End Self Improvement",
  "workflow_mode": "custom_ordered",
  "description": "Generate the full drop-in-ready packet for an end-to-end supervisory lane with Codex and Claude non-interactive review agents.",
  "shared_instructions_file": "../shared_instructions.md",
  "operator_requirements": {
    "minimum_primary_job_inputs": 0,
    "maximum_primary_job_inputs": 0,
    "allow_reference_context": false
  },
  "defaults": {
    "model_roles": {
      "primary_generation": {
        "model": "gpt-5.5-pro",
        "reasoning_effort": "xhigh",
        "verbosity": "high",
        "prompt_cache_retention": "in_memory"
      },
      "structural_processing": {
        "model": "gpt-5.5",
        "reasoning_effort": "high",
        "verbosity": "medium",
        "prompt_cache_retention": "24h"
      }
    },
    "request": {
      "background": true,
      "store": true,
      "parallel_tool_calls": true,
      "max_tool_calls": 8,
      "service_tier": "default",
      "token_preflight": {
        "enabled": true,
        "max_retries": 2,
        "retryable_http_status_codes": [
          429,
          500,
          502,
          503,
          504
        ],
        "on_retryable_service_failure": "continue_without_token_count"
      },
      "file_uploads": {
        "purpose": "user_data",
        "delete_on_completion": false,
        "expires_after_seconds": 604800
      }
    }
  },
  "stages": [
    {
      "stage_id": "architecture_and_supervision_protocol",
      "stage_number": 1,
      "title": "Architecture And Supervision Protocol",
      "task_file": "../prompts/stage1_architecture_and_supervision_protocol.md",
      "input_manifest_file": "../inputs/stage1.input_manifest.json",
      "tool_profile_file": "../tools/stage1_web_search.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "output": {
        "primary_format": "text"
      }
    },
    {
      "stage_id": "agent_review_protocol_and_package_contract",
      "stage_number": 2,
      "title": "Agent Review Protocol And Package Contract",
      "task_file": "../prompts/stage2_agent_review_protocol_and_package_contract.md",
      "input_manifest_file": "../inputs/stage2.input_manifest.json",
      "tool_profile_file": "../tools/stage2_web_search.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "carry_forward": {
        "reference_context_from_stage_ids": [
          "architecture_and_supervision_protocol"
        ],
        "review_bundle_from_stage_id": "architecture_and_supervision_protocol",
        "review_bundle_include_response_artifact_json": false
      },
      "output": {
        "primary_format": "text"
      }
    },
    {
      "stage_id": "draft_drop_in_packet",
      "stage_number": 3,
      "title": "Draft Drop-In Packet",
      "task_file": "../prompts/stage3_draft_drop_in_packet.md",
      "input_manifest_file": "../inputs/stage3.input_manifest.json",
      "tool_profile_file": "../tools/stage3_web_search.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "carry_forward": {
        "reference_context_from_stage_ids": [
          "architecture_and_supervision_protocol",
          "agent_review_protocol_and_package_contract"
        ],
        "review_bundle_from_stage_id": "agent_review_protocol_and_package_contract",
        "review_bundle_include_response_artifact_json": false
      },
      "output": {
        "primary_format": "text"
      }
    },
    {
      "stage_id": "final_drop_in_packet",
      "stage_number": 4,
      "title": "Final Drop-In Packet",
      "task_file": "../prompts/stage4_final_drop_in_packet.md",
      "input_manifest_file": "../inputs/stage4.input_manifest.json",
      "tool_profile_file": "../tools/stage4_web_search.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "terminal",
      "carry_forward": {
        "reference_context_from_stage_ids": [
          "architecture_and_supervision_protocol",
          "agent_review_protocol_and_package_contract",
          "draft_drop_in_packet"
        ],
        "review_bundle_from_stage_id": "draft_drop_in_packet",
        "review_bundle_include_response_artifact_json": false
      },
      "output": {
        "primary_format": "text",
        "sidecar": {
          "schema_file": "../schemas/final_supervisory_packet.schema.json",
          "schema_name": "final_supervisory_packet"
        }
      }
    }
  ]
}
```

---

## 6. Integration Instructions

Create this new directory:

```text
automation/task_packs/responses_runner_v2_supervised_end_to_end/
```

Materialize every file above exactly at its listed path.

No existing repository file must be patched to use the scaffold. The old three-stage pack can remain in place for provenance.

Run the dry run:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --dry-run
```

Then launch Stage 1 live with `--skip-token-count --wait`.

[1]: https://developers.openai.com/api/docs/guides/latest-model "Using GPT-5.5 | OpenAI API"
[2]: https://developers.openai.com/codex/noninteractive "Non-interactive mode – Codex | OpenAI Developers"
[3]: https://code.claude.com/docs/en/headless "Run Claude Code programmatically - Claude Code Docs"
