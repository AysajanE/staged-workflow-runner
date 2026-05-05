## Scaffold Overview

Five stages keep Round 4 tight: scope/pre-check, worker contract, trial/settlement design, audit/adversarial readiness, and terminal playbook emission. The prompt style uses outcome-first GPT‑5.5 guidance, exact output structures, and validation rules; the sidecars use strict structured-output schemas; and both model roles use 24h prompt cache retention where supported. ([OpenAI Developers][1])

## File Listings

### `automation/examples/round_four_autonomous_worker_v1/workflows/round_four_autonomous_worker.workflow.json`

```json
{
  "schema_version": "responses_runner_v2.workflow_manifest.v1",
  "workflow_id": "round-four-autonomous-worker-v1",
  "workflow_name": "Round Four Autonomous Worker Playbook V1",
  "workflow_mode": "custom_ordered",
  "description": "Reviewed five-stage workflow for turning the 2026-05-04 Round 4 focus memo into a direct markdown_playbook_v1 artifact for plan_orchestrator_v3. The generated playbook must implement the first autonomous V2 verified-issue worker settlement with audit-grade adversarial readiness, without implementing the protocol work inside this task pack.",
  "shared_instructions_file": "../shared_instructions.md",
  "operator_requirements": {
    "minimum_primary_job_inputs": 1,
    "maximum_primary_job_inputs": 1,
    "allow_reference_context": false
  },
  "defaults": {
    "model_roles": {
      "primary_generation": {
        "model": "gpt-5.5-pro",
        "reasoning_effort": "xhigh",
        "verbosity": "high",
        "prompt_cache_retention": "24h"
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
      "max_tool_calls": 128,
      "service_tier": "default",
      "token_preflight": {
        "enabled": false,
        "max_retries": 1,
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
      "stage_id": "scope_lock_v2_precheck",
      "stage_number": 1,
      "title": "Stage 1 scope lock and V2 live-readiness pre-check",
      "task_file": "../prompts/stage1_scope_lock_v2_precheck.md",
      "input_manifest_file": "../inputs/stage1.input_manifest.json",
      "tool_profile_file": "../tools/no_tools.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "output": {
        "primary_format": "text",
        "sidecar": {
          "schema_file": "../../../schemas/round_four_autonomous_worker_stage1.schema.json",
          "schema_name": "round_four_autonomous_worker_stage1"
        }
      }
    },
    {
      "stage_id": "autonomous_worker_contract",
      "stage_number": 2,
      "title": "Stage 2 autonomous worker authority and harness contract",
      "task_file": "../prompts/stage2_autonomous_worker_contract.md",
      "input_manifest_file": "../inputs/stage2.input_manifest.json",
      "tool_profile_file": "../tools/stage2_openai_agent_docs_web_search.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "carry_forward": {
        "review_bundle_from_stage_id": "scope_lock_v2_precheck"
      },
      "output": {
        "primary_format": "text",
        "sidecar": {
          "schema_file": "../../../schemas/round_four_autonomous_worker_stage2.schema.json",
          "schema_name": "round_four_autonomous_worker_stage2"
        }
      }
    },
    {
      "stage_id": "trial_settlement_protocol",
      "stage_number": 3,
      "title": "Stage 3 autonomous trials and low-value settlement protocol",
      "task_file": "../prompts/stage3_trial_settlement_protocol.md",
      "input_manifest_file": "../inputs/stage3.input_manifest.json",
      "tool_profile_file": "../tools/no_tools.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "carry_forward": {
        "reference_context_from_stage_ids": [
          "scope_lock_v2_precheck"
        ],
        "review_bundle_from_stage_id": "autonomous_worker_contract"
      },
      "output": {
        "primary_format": "text",
        "sidecar": {
          "schema_file": "../../../schemas/round_four_autonomous_worker_stage3.schema.json",
          "schema_name": "round_four_autonomous_worker_stage3"
        }
      }
    },
    {
      "stage_id": "audit_adversarial_readiness",
      "stage_number": 4,
      "title": "Stage 4 audit target freeze and adversarial readiness",
      "task_file": "../prompts/stage4_audit_adversarial_readiness.md",
      "input_manifest_file": "../inputs/stage4.input_manifest.json",
      "tool_profile_file": "../tools/stage4_capability_security_web_search.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "review_required",
      "carry_forward": {
        "reference_context_from_stage_ids": [
          "scope_lock_v2_precheck",
          "autonomous_worker_contract"
        ],
        "review_bundle_from_stage_id": "trial_settlement_protocol"
      },
      "output": {
        "primary_format": "text",
        "sidecar": {
          "schema_file": "../../../schemas/round_four_autonomous_worker_stage4.schema.json",
          "schema_name": "round_four_autonomous_worker_stage4"
        }
      }
    },
    {
      "stage_id": "final_markdown_playbook",
      "stage_number": 5,
      "title": "Stage 5 final markdown_playbook_v1 emission and validation audit",
      "task_file": "../prompts/stage5_final_markdown_playbook.md",
      "input_manifest_file": "../inputs/stage5.input_manifest.json",
      "tool_profile_file": "../tools/no_tools.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 128000,
      "gate": "terminal",
      "carry_forward": {
        "reference_context_from_stage_ids": [
          "scope_lock_v2_precheck",
          "autonomous_worker_contract",
          "trial_settlement_protocol"
        ],
        "review_bundle_from_stage_id": "audit_adversarial_readiness"
      },
      "output": {
        "primary_format": "text",
        "sidecar": {
          "schema_file": "../../../schemas/round_four_autonomous_worker_stage5.schema.json",
          "schema_name": "round_four_autonomous_worker_stage5"
        }
      }
    }
  ]
}
```

### `automation/examples/round_four_autonomous_worker_v1/shared_instructions.md`

```md
# Round Four Autonomous Worker Playbook

<role>
You are the senior protocol architect, workflow engineer, security reviewer, and repo-grounded execution planner for Openclaw.
Write with protocol rigor, implementation realism, adversarial thinking, and parser-safe planning discipline.
</role>

<goal>
Create a reviewed multi-stage Responses workflow that emits one terminal `markdown_playbook_v1` playbook for `plan_orchestrator_v3`.

The final playbook must plan the next development focus from the 2026-05-04 memo:

**First Autonomous V2 Worker Settlement + Audit-Grade Adversarial Readiness**

The primary proof point is a constrained autonomous AI coding worker completing one GitHub verified-issue task after activation, generating a patch, running the allowed harness, producing native V2 activation-bound proof evidence, submitting through the runtime-owned protocol path, settling, and closing with proof, runtime, cost, failure, and security evidence.

The safety rail is an audit-ready code freeze plus adversarial multi-worker, bid, proof, task, prompt-injection, and capability stress testing before any broader worker-discovery, reputation, marketplace, or self-serve rollout.

This task pack plans that work only. Do not implement the Round 4 protocol work inside the Responses workflow scaffold.
</goal>

<model_family_prompting_rules>
Prompts in this pack are written for the GPT-5.5 model family with outcome-first task statements, explicit constraints, narrow tool budgets, exact output structures, and concrete validation rules.

Use these operating principles in every stage:
- Put the target outcome, non-negotiable rules, and output shape before detailed context.
- Keep stage tasks outcome-oriented rather than process-heavy.
- Use explicit table headers and enum values where the output must be reviewable or machine-extractable.
- Use delimiters and section names to separate instructions from repository context.
- State what to do, not only what to avoid.
- Preserve validation and stopping conditions so the model can self-audit before emission.
- Keep static shared instructions early and stable so prompt caching can work across stages.
</model_family_prompting_rules>

<attachment_authority_order>
Among attached materials, follow the fixed `responses_runner_v2` authority order exactly:

1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Repository Files
4. Reference Context

If two sources conflict, prefer the higher-authority source. If a lower-authority source reveals a hard protocol contradiction, preserve the contradiction explicitly and do not silently merge the claims.
</attachment_authority_order>

<primary_job_input_contract>
The operator must provide exactly one primary job input at runtime.

That input should be the approved Round 4 focus memo or a materially equivalent approved brief. It must define:
- the primary proof point: first autonomous V2 verified-issue worker settlement
- the safety rail: audit-grade adversarial readiness
- Priority 0: V2 live-readiness pre-check
- explicit non-goals: broad worker discovery, reputation scoring, generalized composer, browser settlement controls, and AI-held wallet or secret authority
- the boundary between AI autonomy and runtime/operator settlement authority

If the primary input is absent or contradicts the Round 4 focus, Stage 1 must block rather than invent the focus from older repository context.
</primary_job_input_contract>

<grounding_rules>
- Treat the Primary Job Input as highest authority for the current Round 4 focus and the memo's priority ordering.
- Treat `docs/internal/2026-05-04_next_development_focus.md` as the repo-tracked copy of the focus memo when attached.
- Treat `AGENTS.md`, `docs/spec/OCH-1.1.md`, and `docs/spec/IMPLEMENTATION_BLUEPRINT.md` as the settlement, proof, lifecycle, DCT, TDD, and non-goal truth boundary.
- Treat the V2 proof-truth docs and closeouts as the authority on V1/V2 labels, native V2 public values, run-manifest non-circularity, verifier admission, and no-overclaim rules.
- Treat `docs/architecture/verifier_bundle_standard.md`, GitHub verified-issue receipt/product closeouts, and SDK/task-factory files as the authority on the narrow verified-issue product surface.
- Treat `docs/security/*`, DCT v2 docs, MCP/GitHub external evidence gates, and capability closeouts as the authority on non-settlement capability boundaries and redaction posture.
- Treat `ops/demo/delegatee_runtime/` as the runtime authority boundary: activation, signer boundary, proof generation/validation, submission, session evidence, and V2 adapter behavior remain runtime-owned.
- Treat `ops/demo/delegator_agent/` as the delegator/operator approval boundary: the model must not gain direct create/select, signer, or raw secret authority.
- Treat `docs/plan_orchestrator_v3/playbook-contract.md` and `automation/plan_orchestrator_v3/` as authoritative for the terminal `markdown_playbook_v1` format.
- Do not claim to have inspected files that were not attached in the current stage.
</grounding_rules>

<proof_truth_first_planning_rules>
- Preserve the order: V2 proof truth and pre-check before autonomous worker claims; autonomous harness contract before trials; local/fork trials before low-value live settlement; audit/adversarial readiness before discovery or self-serve expansion.
- Priority 0 is not optional. The final playbook must begin with V2 live-readiness pre-check work before building autonomous worker surfaces.
- V2 proof-truth blockers must precede productization or market claims.
- Capability routing must stay off the payout path and must not become proof public-values input.
- Manual gates occur only after technically clean work.
- External evidence is limited to facts local tests cannot generate, such as live V2 deployment/allowlist, OAuth/MCP/GitHub setup, organization approval, or the actual low-value live settlement transaction.
- The first autonomous run may fail, but the plan must make the failure useful through cost, reliability, refusal, and security telemetry.
</proof_truth_first_planning_rules>

<autonomous_worker_boundary_rules>
The AI worker may:
- read the verified-issue task package after the runtime has placed it in the authorized state
- propose edits inside the allowed workspace
- run or request only the allowed harness commands exposed by the runtime
- interpret deterministic failure output and attempt bounded retries
- produce patch, explanation, and telemetry artifacts under reviewed write roots

The AI worker must not:
- hold private keys, seed phrases, raw wallet credentials, OAuth client secrets, bearer tokens, GitHub App private keys, or decrypted private-resource contents
- call `activateTask`, `submitResult`, `claimTimeout`, `pause`, custody, or settlement functions directly
- mutate verifier admission, deployment allowlists, TaskSpec commitments, bundle manifests, capability policy, or security gates without deterministic tests and operator review
- widen scopes, request forbidden tools, bypass DCT/MCP policy, or treat prompt-injected issue text as instruction authority
</autonomous_worker_boundary_rules>

<explicit_deferrals>
The final playbook must defer:
- broad worker discovery
- public worker onboarding
- reputation scoring or rankings
- generalized task composer
- second verifier/template families
- browser settlement controls
- self-serve buyer lifecycle beyond readback/intake support
- AI-held wallet or secret authority
- broad GitHub write automation or merge judgment
- marketplace discovery dashboards
- multi-chain, non-USDC, subjective lanes, arbitration, or privacy/TEE lanes
</explicit_deferrals>

<tdd_and_verification_rules>
- Behavioral code, verifier, SDK, runtime, CLI, worker harness, task-factory, receipt, policy, or security-control rows require Red -> Green -> closeout discipline.
- A behavioral row must include at least one required verification command that would fail without the change.
- Docs-only, architecture-only, evidence-only, audit-freeze, or closeout rows must not invent fake red/green work.
- Worker harness rows must include negative tests for pre-activation execution refusal, out-of-scope file edits, prompt injection, raw-secret denial, capability denial, timeout/refusal, bounded retry behavior, and no direct settlement authority.
- Settlement rows must include proof/public-values, V2 receipt, direct-RPC or equivalent live/fork readback, fee/payout, and failure/abort evidence.
- Capability rows must include scope, audience, expiry, replay, redaction, and non-settlement-criticality negative tests.
</tdd_and_verification_rules>

<web_research_rules>
- Use web search only in stages whose tool profile enables it.
- Stage 2 web search is limited to official OpenAI documentation for Responses, Agents/agentic workflows, structured outputs, prompt guidance, and prompt caching. Use it only to calibrate the autonomous worker harness and prompt-contract surfaces; do not use it for product strategy or market research.
- Stage 4 web search is limited to official or primary GitHub, MCP, OAuth, and A2A security documentation needed to keep capability, audit, or external-evidence gate language current.
- Stages 1, 3, and 5 run with no tools and must rely on attached repo files plus approved handoffs.
- Web search never outranks the Primary Job Input, OCH-1.1, V2 proof truth, or reviewed stage handoffs.
</web_research_rules>

<stage_boundary_rules>
- Stage 1 locks the Round 4 scope, explicit non-goals, V2 live-readiness pre-check, autonomous-worker boundary, and Stage 2 constraints. It does not design the worker harness or final row order.
- Stage 2 freezes the autonomous worker authority split, harness contract, prompt/IO contract, failure/telemetry surfaces, and Stage 3 constraints. It does not design live settlement or audit rows.
- Stage 3 designs the local/fork trial protocol, low-value live settlement path, settlement evidence, reliability/cost metrics, and Stage 4 constraints. It does not reopen the worker authority split.
- Stage 4 freezes the audit target, adversarial readiness program, capability/security/external evidence gates, and Stage 5 constraints. It does not produce the final playbook.
- Stage 5 emits the final direct `markdown_playbook_v1` playbook. It may harden parser safety and row specificity, but it must not implement the protocol work itself.
</stage_boundary_rules>

<deliverable_contract>
The terminal Stage 5 response must be a direct `markdown_playbook_v1` artifact:
- begin with `## 1. Plan Context`
- include numbered H2 sections through `## 6. Immediate Next Actions`
- include exactly one `## 2. Ordered Execution Plan` table with the canonical authored header
- author no reserved columns such as `change_profile`, `execution_mode`, or `host_commands`
- use concrete repo-relative paths only
- author no `.local`, absolute, `.git`, `.codex`, `.claude`, `.mcp.json`, `ops/config`, or `secrets` paths
- keep `allowed_write_roots` narrow
- include at least one required verification command for every `requires_red_green=true` row
- keep manual and external gates narrow
- be parseable with `python3 automation/run_plan_orchestrator_v3.py list-items --playbook <final.md>`
</deliverable_contract>

<final_review_loop>
Before emitting any stage artifact, silently review the output from these roles:
1. protocol truth guardian
2. V2 proof/verifier architect
3. autonomous runtime authority reviewer
4. GitHub/MCP capability security reviewer
5. adversarial audit reviewer
6. plan_orchestrator_v3 parser reviewer

Do not mention this internal review loop in the stage output.
</final_review_loop>
```

### `automation/examples/round_four_autonomous_worker_v1/prompts/stage1_scope_lock_v2_precheck.md`

```md
Produce the Round 4 scope lock and V2 live-readiness pre-check.

Treat the Primary Job Input as the highest-authority Round 4 focus memo. Treat attached repository files as supporting evidence only.

Primary-input acceptance check:
- The Primary Job Input must identify the primary proof point as a first autonomous V2 verified-issue worker settlement.
- It must identify audit-grade adversarial readiness as a safety rail.
- It must include Priority 0 V2 live-readiness pre-check.
- It must exclude broad discovery, reputation scoring, generalized composer, browser settlement controls, and AI-held wallet or secret authority.
- If any of these elements are missing, begin with `## BLOCKED - Primary Input Incomplete`, list the missing elements, and do not continue to the normal output.

Non-negotiable rules:
- This stage locks scope and pre-checks only. Do not design the worker harness, trial protocol, adversarial test matrix, or final playbook rows.
- Preserve proof-truth-first ordering: V2 live readiness before autonomous worker claims.
- Preserve the AI/runtime/operator authority split from the memo.
- Treat V1/V2 proof truth, OCH-1.1, AGENTS.md, current runtime surfaces, and Round 3 closeouts as grounding constraints.
- Do not implement protocol work.
- Do not use web search in this stage.

Required output:

Begin directly with `## 1. Round 4 Scope Thesis`.

## 1. Round 4 Scope Thesis

State:
- the primary proof point
- the safety rail
- Priority 0 pre-check
- the current repo-grounded starting point
- what remains unproven
- why broad marketplace/discovery/reputation is deferred

## 2. V2 Live-Readiness Pre-Check Matrix

Use this exact table header:

| precheck_id | required_condition | source_of_truth | current_evidence_to_inspect | pass_condition | fail_or_blocked_state | downstream_effect |

Rules:
- Include Round 3 closeout completeness, canonical-branch status, V2 deployment or local-only status, verifier-family manifest completeness, controlled V2 settlement or fork-equivalent settlement, V1/V2 labeling, DCT/MCP external setup truth, no-secret scan, audit target freeze, autonomous authority boundary, and playbook parser safety.
- `precheck_id` must be unique lower_snake_case.
- `downstream_effect` must state whether the autonomous run is allowed, blocked, local-only, fork-only, or live-eligible.

## 3. Autonomous Worker Boundary Lock

Use this exact table header:

| boundary_surface | allowed_for_ai_worker | owned_by_runtime_or_operator | forbidden_for_ai_worker | evidence_or_test_needed | source_evidence |

Rules:
- Include patch generation, command execution, workspace writes, activation, proof generation, submit-result, signer use, secrets, DCT/MCP scopes, GitHub writes, failure retries, telemetry, and closeout evidence.
- Make the AI a constrained patch-producing worker, not a wallet or secret authority.

## 4. Proof-Truth And Product-Claim Ordering Lock

Use this exact table header:

| ordering_rule_id | must_precede | must_follow | why_required | violation_to_prevent | stage2_or_later_effect |

Rules:
- Include V2 proof truth before autonomous live claim, controlled V2 preflight before autonomous settlement, worker contract before trials, local/fork before live, audit/adversarial before discovery, and capability off-payout path before external-resource use.

## 5. Explicit Non-Goals And Deferrals

Use this exact table header:

| non_goal | why_deferred | allowed_later_trigger | risk_if_reintroduced_now |

Rules:
- Include broad worker discovery, reputation scoring, generalized composer, browser settlement controls, AI-held wallet or secret authority, broad GitHub writes, public worker onboarding, self-serve buyer lifecycle, additional verifier families, and marketplace launch.

## 6. Stage 2 Handoff Constraints

Provide a flat bullet list that locks:
- scope and proof point
- V2 pre-check requirements
- authority boundary requirements
- ordering constraints
- explicit non-goals
- what Stage 2 may design
- what Stage 2 must not reopen
```

### `automation/examples/round_four_autonomous_worker_v1/prompts/stage2_autonomous_worker_contract.md`

```md
Produce the autonomous worker authority and harness contract.

Treat the approved Stage 1 review bundle as binding for:
- Round 4 scope
- V2 live-readiness pre-check
- autonomous worker boundary
- proof-truth/product-claim ordering
- explicit non-goals and deferrals

Web search allowance:
- Use web search only for official OpenAI documentation about Responses, agentic workflows, structured outputs, prompt guidance, prompt caching, and tool-use safety.
- Use only domains enabled by the tool profile.
- Use web results to calibrate prompt/harness design principles, not to change the Round 4 scope.
- Do not search for market, competitor, discovery, reputation, buyer self-serve, or unrelated strategy topics.

Non-negotiable rules:
- This stage freezes the contract for a constrained autonomous coding worker.
- The AI worker may generate patches and interpret deterministic failures. It must not hold wallet authority, raw secrets, direct settlement authority, or capability-policy authority.
- Runtime controls activation, signer boundary, proof generation/validation, submit-result, session evidence, and failure readback.
- Operator controls live approvals, manual gates, external evidence, and custody boundaries.
- Do not design final playbook rows.
- Do not implement the worker.

Required output:

Begin directly with `## 1. Autonomous Worker Contract Thesis`.

## 1. Autonomous Worker Contract Thesis

State:
- the worker's purpose
- the autonomy allowed
- the authority withheld
- the runtime/operator split
- how the contract supports the first autonomous V2 verified-issue settlement

## 2. AI Runtime Operator Authority Split

Use this exact table header:

| contract_surface | ai_worker_may_do | runtime_must_own | operator_must_own | forbidden_authority | required_artifact_or_test |

Rules:
- Include issue/package read, patch proposal, allowed command execution, workspace mutation, retry loop, activation, signer use, proof generation, submit-result, DCT/MCP access, GitHub writes, telemetry, and closeout.
- `forbidden_authority` must be explicit, not implied.

## 3. Autonomous Worker Harness Contract

Use this exact table header:

| harness_surface | required_behavior | allowed_tools_or_inputs | denied_tools_or_inputs | telemetry_emitted | failure_mode | implementation_target |

Rules:
- Include task package intake, post-activation gating, sandbox/workspace, allowed paths, patch diff, harness command, bounded retries, timeout/refusal, prompt-injection handling, out-of-scope edit denial, secret denial, capability denial, and receipt/telemetry output.
- `implementation_target` must be repo-relative or `new_repo_output_to_be_planned`.

## 4. Prompt And I/O Contract

Use this exact table header:

| prompt_surface | instruction_rule | grounding_source | validation_rule | failure_if_violated |

Rules:
- Include system/developer instruction boundaries, issue text as untrusted content, attached repository authority order, no-secret echoing, structured telemetry extraction, and concise outcome-first worker prompts.
- Use official OpenAI docs only as lower-authority prompting context; do not use them to override OCH-1.1 or repo security boundaries.

## 5. Telemetry And Reliability Metrics Contract

Use this exact table header:

| metric_id | metric_name | capture_point | required_unit_or_shape | why_required | downstream_use |

Rules:
- Include solve rate, time to first patch, retries, tool calls, token/cost proxy, harness failures, proof/prover time, gas or fee, reward/bond adequacy, deadline slack, refusal mode, capability-denial mode, prompt-injection outcome, and secret-denial outcome.
- `metric_id` must be lower_snake_case.

## 6. Stage 3 Handoff Constraints

Provide a flat bullet list that locks:
- authority split
- harness surfaces
- prompt/I/O rules
- telemetry requirements
- failure cases Stage 3 must include
- what Stage 3 must not reopen
```

### `automation/examples/round_four_autonomous_worker_v1/prompts/stage3_trial_settlement_protocol.md`

```md
Produce the autonomous trial and settlement protocol design.

Treat the approved Stage 2 review bundle as binding for:
- AI/runtime/operator authority split
- worker harness contract
- prompt and I/O contract
- telemetry and reliability metrics
- failure cases required for meaningful autonomous execution

Treat the approved Stage 1 review bundle as binding for:
- V2 live-readiness pre-check
- proof-truth-first ordering
- explicit non-goals

Non-negotiable rules:
- This stage designs local/fork trials and the low-value live settlement path only. Do not reopen the worker authority contract.
- The first autonomous live run must not be the first unproven V2 path unless the pre-check explicitly allows fork-only or live-eligible status.
- Local/fork trial failures must produce useful failure, cost, and safety data.
- Live settlement remains low-value, operator-gated, and runtime-owned.
- Do not implement the trials or settlement.
- Do not use web search in this stage.

Required output:

Begin directly with `## 1. Trial And Settlement Thesis`.

## 1. Trial And Settlement Thesis

State:
- how local/fork trials de-risk the live autonomous settlement
- why controlled V2 preflight must precede live autonomous claims
- how failure remains useful
- how runtime authority is preserved during live settlement

## 2. Local And Fork Trial Matrix

Use this exact table header:

| trial_id | trial_type | task_shape | expected_outcome | required_fixture_or_artifact | verification_command | pass_condition | failure_data_to_capture |

Rules:
- Include easy success, expected failure, timeout, capability denial, prompt injection, out-of-scope edit, secret-like string, stale issue snapshot, mutable branch drift, stale proof, wrong public values, and no direct settlement authority.
- `trial_type` must be exactly one of:
  - `local`
  - `fork`
  - `local_or_fork`
- Every row must capture at least one failure or telemetry output.

## 3. Low-Value Live Settlement Path

Use this exact table header:

| settlement_step | runtime_or_operator_owner | precondition | action_boundary | evidence_artifact | manual_gate | failure_or_abort_rule |

Rules:
- Include final V2 readiness confirmation, task creation/selection boundary, activation, post-activation worker run, proof generation, submit-result, direct-RPC readback, fee/payout reconciliation, receipt, closeout, and abort/timeout path.
- `runtime_or_operator_owner` must be exactly one of:
  - `runtime`
  - `operator`
  - `runtime_with_operator_gate`
- `manual_gate` must be exactly `none`, `approval`, `operator_confirmation`, `security_review`, or `signoff`.

## 4. Cost Reliability And Failure Telemetry Packet

Use this exact table header:

| telemetry_surface | source | required_fields | redaction_rule | closeout_use |

Rules:
- Include all Stage 2 metrics and settlement-specific metrics.
- Keep raw secrets, private repo contents, proof witness material, and sensitive stdout/stderr out of telemetry.

## 5. Stage 5 Step-Family Contract For Trial And Settlement

Use this exact table header:

| step_family_id | phase_hint | atomic_action_family | prerequisites | artifact_families | requires_red_green | manual_gate_surface | external_evidence_surface | blocker_before_live_autonomous_claim |

Rules:
- `step_family_id` must be lower_snake_case.
- `requires_red_green` and `blocker_before_live_autonomous_claim` must be exactly `yes` or `no`.
- `prerequisites`, `artifact_families`, `manual_gate_surface`, and `external_evidence_surface` must be `none` or semicolon-separated stable ids.

## 6. Stage 4 Handoff Constraints

Provide a flat bullet list that locks:
- required trials
- live settlement sequence
- telemetry packet shape
- abort/failure rules
- rows that must be security-reviewed or audit-ready
- what Stage 4 must not reopen
```

### `automation/examples/round_four_autonomous_worker_v1/prompts/stage4_audit_adversarial_readiness.md`

```md
Produce the audit target freeze and adversarial readiness contract.

Treat the approved Stage 3 review bundle as binding for:
- local/fork trial matrix
- low-value live settlement path
- telemetry packet
- trial/settlement step-family contract

Treat Stage 1 and Stage 2 carry-forward artifacts as binding for:
- scope and non-goals
- V2 pre-check
- worker authority split
- harness and prompt/I/O contract

Web search allowance:
- Use web search only for official or primary GitHub, MCP, OAuth, and A2A security documentation needed to keep capability and external-evidence gate language current.
- Do not use web search for product strategy, marketplace discovery, reputation, pricing, or broad research.
- Web facts are lower authority than OCH-1.1, attached repo files, and approved handoffs.

Non-negotiable rules:
- This stage freezes the audit target and adversarial readiness design. It does not produce the final playbook.
- Audit scope must cover protocol proof/settlement surfaces, runtime/capability security, and market/adversarial stress.
- Capability routing must remain off payout truth.
- Manual audit gates must come after technically clean target-freeze artifacts.
- Do not implement audit fixtures or tests.

Required output:

Begin directly with `## 1. Audit And Adversarial Readiness Thesis`.

## 1. Audit And Adversarial Readiness Thesis

State:
- why audit/adversarial readiness runs in parallel with the autonomous proof point
- what must be frozen for audit
- which adversarial cases must be simulated before broader rollout
- which claims remain blocked until audit/security gates close

## 2. Audit Target Freeze Matrix

Use this exact table header:

| audit_surface | in_scope_components | frozen_artifacts | excluded_surfaces | required_reviewer_decision |

Rules:
- Include protocol audit, verifier/public-values audit, hub/registry audit, runtime/worker harness audit, DCT/MCP/GitHub/A2A capability audit, redaction audit, and external evidence audit.
- `excluded_surfaces` must explicitly exclude marketplace, reputation, generalized composer, browser settlement controls, and AI-held secrets where applicable.

## 3. Adversarial Stress Matrix

Use this exact table header:

| adversarial_case_id | attack_or_failure_class | fixture_or_simulation | expected_safe_behavior | required_test_or_packet | settlement_safety_rule |

Rules:
- Include stale bids, replay, nonce invalidation, bid floods, selection cancellation/reselection, activation timing, timeout race, invalid proof spam, wrong public-values length, wrong activation challenge, stale proof, multi-worker quote flood, prompt injection, capability denial, GitHub write denial, secret leakage attempt, private-resource denial, and relay spoofing if relay scaffolding is present.
- `adversarial_case_id` must be lower_snake_case.

## 4. Capability Security And External Evidence Gate Contract

Use this exact table header:

| security_surface | required_control | official_or_repo_source | negative_test_or_review | external_evidence_if_any |

Rules:
- Include DCT audience/scope/expiry/replay, MCP no-token-passthrough, GitHub read/write scope map, A2A metadata-only boundary, OAuth/protected-resource setup, secret redaction, private-resource redaction, and non-settlement-criticality.
- External evidence must be narrow and human-supplied.

## 5. Manual Gate And Claim Blocker Matrix

Use this exact table header:

| gate_id | gate_type | trigger | required_evidence | blocks_what_claim | unblocks_what_stage5_rows |

Rules:
- `gate_type` must be exactly one of:
  - `approval`
  - `operator_confirmation`
  - `security_review`
  - `signoff`
  - `external_evidence`
- Include V2 readiness gate, autonomous live settlement approval, audit target freeze, security review, live external setup evidence, and final closeout signoff.

## 6. Stage 5 Handoff Constraints

Provide a flat bullet list that locks:
- audit surfaces
- adversarial cases
- capability/external evidence gates
- manual gate ordering
- claim blockers
- what Stage 5 must preserve in the final playbook
```

### `automation/examples/round_four_autonomous_worker_v1/prompts/stage5_final_markdown_playbook.md`

```md
Produce the final parser-safe `markdown_playbook_v1` playbook for implementing Round 4.

This stage must emit direct playbook content. Do not emit a memo about a future playbook.

Treat the approved Stage 4 review bundle as binding for:
- audit target freeze
- adversarial stress matrix
- capability/security and external evidence gates
- manual gate and claim blocker matrix
- Stage 5 handoff constraints

Treat Stage 1, Stage 2, and Stage 3 carry-forward artifacts as binding for:
- Round 4 scope and V2 pre-check
- autonomous worker authority and harness contract
- trial and low-value live settlement protocol
- telemetry and failure packet requirements

Non-negotiable rules:
- Begin with `## 1. Plan Context`.
- Include canonical numbered H2 sections through `## 6. Immediate Next Actions`.
- In `## 2. Ordered Execution Plan`, use the exact table header below.
- Do not author reserved columns such as `change_profile`, `execution_mode`, or `host_commands`.
- Make every row one runnable item with concrete repo-relative paths.
- Do not use `.local`, absolute, `.git`, `.codex`, `.claude`, `.mcp.json`, `ops/config`, or `secrets` paths.
- Keep `allowed_write_roots` narrow.
- If `requires_red_green=true`, include at least one `required_verification_commands` entry.
- Keep manual gates after technically clean work.
- Keep external evidence to live/fork/deployment/OAuth/MCP/GitHub facts that local tests cannot generate.
- Do not widen into marketplace, reputation, generalized composer, browser settlement controls, public worker discovery, self-serve buyer lifecycle, or AI-held wallet/secret authority.
- The playbook must be suitable for `python3 automation/run_plan_orchestrator_v3.py list-items --playbook <final.md>`.

Required response:

Write the final markdown playbook content.

The playbook content must contain these exact sections:

## 1. Plan Context

Rules:
- State the Round 4 thesis.
- State the primary proof point and safety rail.
- State Priority 0 V2 pre-check.
- State the AI/runtime/operator authority split.
- State explicit non-goals.
- State success definition and claim boundaries.

## 2. Ordered Execution Plan

Use exactly this authored table header:

| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | manual_gate | manual_gate_reason | manual_gate_evidence | external_check | external_dependencies | consult_paths | required_verification_commands | suggested_verification_commands | required_verification_artifacts | notes |

Rules:
- `step_id` values must be strictly ordered zero-padded decimal strings.
- `prerequisites` must be `none`, comma-separated earlier step ids, or a numeric range.
- Use exact repo-relative paths in path cells.
- `deliverable` must contain at least one concrete repo-relative path.
- `requires_red_green` must be exactly `true` or `false`.
- `manual_gate` must be exactly `none`, `signoff`, `approval`, `operator_confirmation`, `security_review`, `presenter_review`, or `custom`.
- `external_check` must be exactly `none` or `human_supplied_evidence_required`.
- Use `none` where an optional surface does not apply.

## 3. Phase Details

Rules:
- Use H3 subsections whose normalized slugs match the `phase` values in Section 2.
- For each phase, explain objective, key repo surfaces, success checks, and likely failure modes.

## 4. Shared Guidance

Rules:
- Include proof-truth, autonomous authority, runtime custody, capability-security, secret-handling, adversarial-readiness, and non-settlement-criticality rules.
- Do not hide row-level work in this section.

## 5. Risks And Contingencies

Use this exact table header:

| risk_id | risk | trigger | impact | mitigation | contingency |

Rules:
- Carry forward real risks from approved stages.
- Keep mitigations concrete.
- Keep unresolved external evidence visible.

## 6. Immediate Next Actions

Provide a flat numbered list.
Do not create hidden runnable items outside Section 2.
```

### `automation/examples/round_four_autonomous_worker_v1/inputs/stage1.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "round-four-autonomous-worker-v1-stage1",
  "workflow_id": "round-four-autonomous-worker-v1",
  "stage_id": "scope_lock_v2_precheck",
  "description": "Stage 1 corpus for locking Round 4 scope, explicit non-goals, V2 live-readiness pre-check, and autonomous worker boundary. The operator-provided primary focus memo is added dynamically and outranks this repo-tracked memo if newer.",
  "primary_job_inputs": [],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "AGENTS.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/OCH-1.1.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/IMPLEMENTATION_BLUEPRINT.md",
      "kind": "file"
    },
    {
      "path": "docs/security/invariants.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/2026-05-04_next_development_focus.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_native_rehearsal.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_live_attestation.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_product_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/capability_security_review_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_external_issue_oauth_evidence_gate.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/external_mcp_oauth_setup_evidence_gate.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_public_values.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_claim_boundary.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_v1_v2_migration_truth.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/verifier_bundle_standard.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/dct_v2_schema_service_identity.md",
      "kind": "file"
    },
    {
      "path": "docs/security/capability_security_review_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/security/non_settlement_capability_boundary.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/control_plane.json",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/control_plane.json",
      "kind": "file"
    },
    {
      "path": "docs/plan_orchestrator_v3/playbook-contract.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/README.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/adapters/markdown_playbook.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/playbook_parser.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/validators.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/README.md",
      "kind": "file"
    }
  ],
  "reference_context": []
}
```

### `automation/examples/round_four_autonomous_worker_v1/inputs/stage2.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "round-four-autonomous-worker-v1-stage2",
  "workflow_id": "round-four-autonomous-worker-v1",
  "stage_id": "autonomous_worker_contract",
  "description": "Stage 2 corpus for freezing the autonomous worker authority split, runtime/operator boundaries, harness contract, prompt/I/O contract, and telemetry requirements. The approved Stage 1 review bundle is added dynamically.",
  "primary_job_inputs": [],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "AGENTS.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/OCH-1.1.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/IMPLEMENTATION_BLUEPRINT.md",
      "kind": "file"
    },
    {
      "path": "docs/security/invariants.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/2026-05-04_next_development_focus.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_native_rehearsal.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_live_attestation.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_public_values.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_claim_boundary.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_v1_v2_migration_truth.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/verifier_bundle_standard.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/control_plane.json",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/control_plane.json",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/repoPatchHarnessV2.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/githubVerifiedIssueReceipt.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/verifiedIssueTaskFactory.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/verifierBundle.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/repoSnapshotHarnessBinding.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/dctV2Capabilities.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/dctV2Runtime.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/a2aMetadataEnvelope.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/events.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/githubIssueBridge.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/specHash.ts",
      "kind": "file"
    },
    {
      "path": "docs/plan_orchestrator_v3/playbook-contract.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/README.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/adapters/markdown_playbook.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/playbook_parser.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/validators.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/README.md",
      "kind": "file"
    }
  ],
  "reference_context": []
}
```

### `automation/examples/round_four_autonomous_worker_v1/inputs/stage3.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "round-four-autonomous-worker-v1-stage3",
  "workflow_id": "round-four-autonomous-worker-v1",
  "stage_id": "trial_settlement_protocol",
  "description": "Stage 3 corpus for designing local/fork autonomous trials and the low-value live settlement evidence path. The approved Stage 2 review bundle is added dynamically and Stage 1 is carried as reference context.",
  "primary_job_inputs": [],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "AGENTS.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/OCH-1.1.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/IMPLEMENTATION_BLUEPRINT.md",
      "kind": "file"
    },
    {
      "path": "docs/security/invariants.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/2026-05-04_next_development_focus.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_native_rehearsal.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_live_attestation.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_product_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_public_values.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_claim_boundary.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_v1_v2_migration_truth.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/verifier_bundle_standard.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/control_plane.json",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/control_plane.json",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/repoPatchHarnessV2.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/githubVerifiedIssueReceipt.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/verifiedIssueTaskFactory.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/verifierBundle.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/repoSnapshotHarnessBinding.ts",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/plan_orchestrator_v3/playbook-contract.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/adapters/markdown_playbook.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/playbook_parser.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/validators.py",
      "kind": "file"
    }
  ],
  "reference_context": []
}
```

### `automation/examples/round_four_autonomous_worker_v1/inputs/stage4.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "round-four-autonomous-worker-v1-stage4",
  "workflow_id": "round-four-autonomous-worker-v1",
  "stage_id": "audit_adversarial_readiness",
  "description": "Stage 4 corpus for audit target freeze, adversarial readiness, capability/security gates, and external-evidence claim blockers. The approved Stage 3 review bundle is added dynamically; Stage 1 and Stage 2 are carried as reference context.",
  "primary_job_inputs": [],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "AGENTS.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/OCH-1.1.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/IMPLEMENTATION_BLUEPRINT.md",
      "kind": "file"
    },
    {
      "path": "docs/security/invariants.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/2026-05-04_next_development_focus.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_native_rehearsal.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_live_attestation.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_product_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/capability_security_review_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_external_issue_oauth_evidence_gate.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/external_mcp_oauth_setup_evidence_gate.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_public_values.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_claim_boundary.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_v1_v2_migration_truth.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/verifier_bundle_standard.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/dct_v2_schema_service_identity.md",
      "kind": "file"
    },
    {
      "path": "docs/security/capability_security_review_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/security/non_settlement_capability_boundary.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/control_plane.json",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/control_plane.json",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/dctV2Capabilities.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/dctV2Runtime.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/a2aMetadataEnvelope.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/events.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/githubIssueBridge.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/specHash.ts",
      "kind": "file"
    },
    {
      "path": "docs/architecture/verifier_bundle_standard.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_product_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/capability_security_review_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/plan_orchestrator_v3/playbook-contract.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/adapters/markdown_playbook.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/playbook_parser.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/validators.py",
      "kind": "file"
    }
  ],
  "reference_context": []
}
```

### `automation/examples/round_four_autonomous_worker_v1/inputs/stage5.input_manifest.json`

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "round-four-autonomous-worker-v1-stage5",
  "workflow_id": "round-four-autonomous-worker-v1",
  "stage_id": "final_markdown_playbook",
  "description": "Stage 5 corpus for final parser-safe markdown_playbook_v1 emission. The approved Stage 4 bundle is added dynamically; Stage 1, Stage 2, and Stage 3 outputs are carried as reference context.",
  "primary_job_inputs": [],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "AGENTS.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/OCH-1.1.md",
      "kind": "file"
    },
    {
      "path": "docs/spec/IMPLEMENTATION_BLUEPRINT.md",
      "kind": "file"
    },
    {
      "path": "docs/security/invariants.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/2026-05-04_next_development_focus.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_native_rehearsal.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/repo_patch_harness_v2_live_attestation.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_product_no_overclaim_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/capability_security_review_closeout.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/github_external_issue_oauth_evidence_gate.md",
      "kind": "file"
    },
    {
      "path": "docs/internal/production_protocol/external_mcp_oauth_setup_evidence_gate.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_public_values.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_harness_v2_claim_boundary.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/repo_patch_v1_v2_migration_truth.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/verifier_bundle_standard.md",
      "kind": "file"
    },
    {
      "path": "docs/architecture/dct_v2_schema_service_identity.md",
      "kind": "file"
    },
    {
      "path": "docs/security/capability_security_review_packet.md",
      "kind": "file"
    },
    {
      "path": "docs/security/non_settlement_capability_boundary.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegatee_runtime/control_plane.json",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/README.md",
      "kind": "file"
    },
    {
      "path": "ops/demo/delegator_agent/control_plane.json",
      "kind": "file"
    },
    {
      "path": "docs/plan_orchestrator_v3/playbook-contract.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/README.md",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/adapters/markdown_playbook.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/playbook_parser.py",
      "kind": "file"
    },
    {
      "path": "automation/plan_orchestrator_v3/validators.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/README.md",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/contracts.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/pack_loader.py",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/workflow_manifest.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/input_manifest.schema.json",
      "kind": "file"
    },
    {
      "path": "automation/responses_runner_v2/schemas/review_bundle.schema.json",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/repoPatchHarnessV2.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/githubVerifiedIssueReceipt.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/verifiedIssueTaskFactory.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/verifierBundle.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/repoSnapshotHarnessBinding.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/dctV2Capabilities.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/dctV2Runtime.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/a2aMetadataEnvelope.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/events.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/githubIssueBridge.ts",
      "kind": "file"
    },
    {
      "path": "packages/sdk/src/specHash.ts",
      "kind": "file"
    }
  ],
  "reference_context": []
}
```

### `automation/examples/round_four_autonomous_worker_v1/tools/no_tools.profile.json`

```json
{
  "tools": []
}
```

### `automation/examples/round_four_autonomous_worker_v1/tools/stage2_openai_agent_docs_web_search.profile.json`

```json
{
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "medium",
      "domains": [
        "developers.openai.com",
        "platform.openai.com",
        "help.openai.com",
        "openai.com",
        "github.com"
      ],
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
  "max_tool_calls": 48
}
```

### `automation/examples/round_four_autonomous_worker_v1/tools/stage4_capability_security_web_search.profile.json`

```json
{
  "tools": [
    {
      "type": "web_search",
      "search_context_size": "medium",
      "domains": [
        "docs.github.com",
        "github.com",
        "modelcontextprotocol.io",
        "oauth.net",
        "datatracker.ietf.org",
        "google.github.io"
      ],
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

### `automation/schemas/round_four_autonomous_worker_stage1.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://openclaw.local/automation/schemas/round_four_autonomous_worker_stage1.schema.json",
  "title": "Round Four Autonomous Worker Stage 1 Scope Lock",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "summary_version",
    "scope_thesis",
    "v2_precheck_rows",
    "worker_boundary_rows",
    "ordering_rules",
    "explicit_non_goals",
    "stage2_handoff_constraints"
  ],
  "properties": {
    "summary_version": {
      "type": "string",
      "const": "round_four_autonomous_worker_stage1.v1"
    },
    "scope_thesis": {
      "$ref": "#/$defs/non_empty_string"
    },
    "v2_precheck_rows": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "precheck_id",
          "required_condition",
          "source_of_truth",
          "current_evidence_to_inspect",
          "pass_condition",
          "fail_or_blocked_state",
          "downstream_effect"
        ],
        "properties": {
          "precheck_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "required_condition": {
            "$ref": "#/$defs/non_empty_string"
          },
          "source_of_truth": {
            "$ref": "#/$defs/non_empty_string"
          },
          "current_evidence_to_inspect": {
            "$ref": "#/$defs/non_empty_string"
          },
          "pass_condition": {
            "$ref": "#/$defs/non_empty_string"
          },
          "fail_or_blocked_state": {
            "$ref": "#/$defs/non_empty_string"
          },
          "downstream_effect": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "worker_boundary_rows": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "boundary_surface",
          "allowed_for_ai_worker",
          "owned_by_runtime_or_operator",
          "forbidden_for_ai_worker",
          "evidence_or_test_needed",
          "source_evidence"
        ],
        "properties": {
          "boundary_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "allowed_for_ai_worker": {
            "$ref": "#/$defs/non_empty_string"
          },
          "owned_by_runtime_or_operator": {
            "$ref": "#/$defs/non_empty_string"
          },
          "forbidden_for_ai_worker": {
            "$ref": "#/$defs/non_empty_string"
          },
          "evidence_or_test_needed": {
            "$ref": "#/$defs/non_empty_string"
          },
          "source_evidence": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "ordering_rules": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "ordering_rule_id",
          "must_precede",
          "must_follow",
          "why_required",
          "violation_to_prevent",
          "stage2_or_later_effect"
        ],
        "properties": {
          "ordering_rule_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "must_precede": {
            "$ref": "#/$defs/non_empty_string"
          },
          "must_follow": {
            "$ref": "#/$defs/non_empty_string"
          },
          "why_required": {
            "$ref": "#/$defs/non_empty_string"
          },
          "violation_to_prevent": {
            "$ref": "#/$defs/non_empty_string"
          },
          "stage2_or_later_effect": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "explicit_non_goals": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "non_goal",
          "why_deferred",
          "allowed_later_trigger",
          "risk_if_reintroduced_now"
        ],
        "properties": {
          "non_goal": {
            "$ref": "#/$defs/non_empty_string"
          },
          "why_deferred": {
            "$ref": "#/$defs/non_empty_string"
          },
          "allowed_later_trigger": {
            "$ref": "#/$defs/non_empty_string"
          },
          "risk_if_reintroduced_now": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "stage2_handoff_constraints": {
      "$ref": "#/$defs/non_empty_string_array"
    }
  },
  "$defs": {
    "non_empty_string": {
      "type": "string",
      "minLength": 1
    },
    "non_empty_string_array": {
      "type": "array",
      "minItems": 1,
      "items": {
        "$ref": "#/$defs/non_empty_string"
      }
    },
    "lower_snake_case_id": {
      "type": "string",
      "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
    },
    "yes_no": {
      "type": "string",
      "enum": [
        "yes",
        "no"
      ]
    },
    "true_check": {
      "type": "boolean",
      "const": true
    },
    "step_id": {
      "type": "string",
      "pattern": "^[0-9]{2,3}$"
    },
    "manual_gate": {
      "type": "string",
      "enum": [
        "none",
        "signoff",
        "approval",
        "operator_confirmation",
        "security_review",
        "presenter_review",
        "custom"
      ]
    },
    "external_check": {
      "type": "string",
      "enum": [
        "none",
        "human_supplied_evidence_required"
      ]
    }
  }
}
```

### `automation/schemas/round_four_autonomous_worker_stage2.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://openclaw.local/automation/schemas/round_four_autonomous_worker_stage2.schema.json",
  "title": "Round Four Autonomous Worker Stage 2 Worker Contract",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "summary_version",
    "contract_thesis",
    "authority_split_rows",
    "harness_surfaces",
    "prompt_surfaces",
    "telemetry_metrics",
    "stage3_handoff_constraints"
  ],
  "properties": {
    "summary_version": {
      "type": "string",
      "const": "round_four_autonomous_worker_stage2.v1"
    },
    "contract_thesis": {
      "$ref": "#/$defs/non_empty_string"
    },
    "authority_split_rows": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "contract_surface",
          "ai_worker_may_do",
          "runtime_must_own",
          "operator_must_own",
          "forbidden_authority",
          "required_artifact_or_test"
        ],
        "properties": {
          "contract_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "ai_worker_may_do": {
            "$ref": "#/$defs/non_empty_string"
          },
          "runtime_must_own": {
            "$ref": "#/$defs/non_empty_string"
          },
          "operator_must_own": {
            "$ref": "#/$defs/non_empty_string"
          },
          "forbidden_authority": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_artifact_or_test": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "harness_surfaces": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "harness_surface",
          "required_behavior",
          "allowed_tools_or_inputs",
          "denied_tools_or_inputs",
          "telemetry_emitted",
          "failure_mode",
          "implementation_target"
        ],
        "properties": {
          "harness_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_behavior": {
            "$ref": "#/$defs/non_empty_string"
          },
          "allowed_tools_or_inputs": {
            "$ref": "#/$defs/non_empty_string"
          },
          "denied_tools_or_inputs": {
            "$ref": "#/$defs/non_empty_string"
          },
          "telemetry_emitted": {
            "$ref": "#/$defs/non_empty_string"
          },
          "failure_mode": {
            "$ref": "#/$defs/non_empty_string"
          },
          "implementation_target": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "prompt_surfaces": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "prompt_surface",
          "instruction_rule",
          "grounding_source",
          "validation_rule",
          "failure_if_violated"
        ],
        "properties": {
          "prompt_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "instruction_rule": {
            "$ref": "#/$defs/non_empty_string"
          },
          "grounding_source": {
            "$ref": "#/$defs/non_empty_string"
          },
          "validation_rule": {
            "$ref": "#/$defs/non_empty_string"
          },
          "failure_if_violated": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "telemetry_metrics": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "metric_id",
          "metric_name",
          "capture_point",
          "required_unit_or_shape",
          "why_required",
          "downstream_use"
        ],
        "properties": {
          "metric_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "metric_name": {
            "$ref": "#/$defs/non_empty_string"
          },
          "capture_point": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_unit_or_shape": {
            "$ref": "#/$defs/non_empty_string"
          },
          "why_required": {
            "$ref": "#/$defs/non_empty_string"
          },
          "downstream_use": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "stage3_handoff_constraints": {
      "$ref": "#/$defs/non_empty_string_array"
    }
  },
  "$defs": {
    "non_empty_string": {
      "type": "string",
      "minLength": 1
    },
    "non_empty_string_array": {
      "type": "array",
      "minItems": 1,
      "items": {
        "$ref": "#/$defs/non_empty_string"
      }
    },
    "lower_snake_case_id": {
      "type": "string",
      "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
    },
    "yes_no": {
      "type": "string",
      "enum": [
        "yes",
        "no"
      ]
    },
    "true_check": {
      "type": "boolean",
      "const": true
    },
    "step_id": {
      "type": "string",
      "pattern": "^[0-9]{2,3}$"
    },
    "manual_gate": {
      "type": "string",
      "enum": [
        "none",
        "signoff",
        "approval",
        "operator_confirmation",
        "security_review",
        "presenter_review",
        "custom"
      ]
    },
    "external_check": {
      "type": "string",
      "enum": [
        "none",
        "human_supplied_evidence_required"
      ]
    }
  }
}
```

### `automation/schemas/round_four_autonomous_worker_stage3.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://openclaw.local/automation/schemas/round_four_autonomous_worker_stage3.schema.json",
  "title": "Round Four Autonomous Worker Stage 3 Trial Settlement Protocol",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "summary_version",
    "trial_thesis",
    "trial_rows",
    "settlement_steps",
    "telemetry_packet_rows",
    "step_family_contracts",
    "stage4_handoff_constraints"
  ],
  "properties": {
    "summary_version": {
      "type": "string",
      "const": "round_four_autonomous_worker_stage3.v1"
    },
    "trial_thesis": {
      "$ref": "#/$defs/non_empty_string"
    },
    "trial_rows": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "trial_id",
          "trial_type",
          "task_shape",
          "expected_outcome",
          "required_fixture_or_artifact",
          "verification_command",
          "pass_condition",
          "failure_data_to_capture"
        ],
        "properties": {
          "trial_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "trial_type": {
            "type": "string",
            "enum": [
              "local",
              "fork",
              "local_or_fork"
            ]
          },
          "task_shape": {
            "$ref": "#/$defs/non_empty_string"
          },
          "expected_outcome": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_fixture_or_artifact": {
            "$ref": "#/$defs/non_empty_string"
          },
          "verification_command": {
            "$ref": "#/$defs/non_empty_string"
          },
          "pass_condition": {
            "$ref": "#/$defs/non_empty_string"
          },
          "failure_data_to_capture": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "settlement_steps": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "settlement_step",
          "runtime_or_operator_owner",
          "precondition",
          "action_boundary",
          "evidence_artifact",
          "manual_gate",
          "failure_or_abort_rule"
        ],
        "properties": {
          "settlement_step": {
            "$ref": "#/$defs/non_empty_string"
          },
          "runtime_or_operator_owner": {
            "type": "string",
            "enum": [
              "runtime",
              "operator",
              "runtime_with_operator_gate"
            ]
          },
          "precondition": {
            "$ref": "#/$defs/non_empty_string"
          },
          "action_boundary": {
            "$ref": "#/$defs/non_empty_string"
          },
          "evidence_artifact": {
            "$ref": "#/$defs/non_empty_string"
          },
          "manual_gate": {
            "type": "string",
            "enum": [
              "none",
              "approval",
              "operator_confirmation",
              "security_review",
              "signoff"
            ]
          },
          "failure_or_abort_rule": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "telemetry_packet_rows": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "telemetry_surface",
          "source",
          "required_fields",
          "redaction_rule",
          "closeout_use"
        ],
        "properties": {
          "telemetry_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "source": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_fields": {
            "$ref": "#/$defs/non_empty_string"
          },
          "redaction_rule": {
            "$ref": "#/$defs/non_empty_string"
          },
          "closeout_use": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "step_family_contracts": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "step_family_id",
          "phase_hint",
          "atomic_action_family",
          "prerequisites",
          "artifact_families",
          "requires_red_green",
          "manual_gate_surface",
          "external_evidence_surface",
          "blocker_before_live_autonomous_claim"
        ],
        "properties": {
          "step_family_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "phase_hint": {
            "$ref": "#/$defs/non_empty_string"
          },
          "atomic_action_family": {
            "$ref": "#/$defs/non_empty_string"
          },
          "prerequisites": {
            "type": "array",
            "items": {
              "$ref": "#/$defs/non_empty_string"
            }
          },
          "artifact_families": {
            "type": "array",
            "items": {
              "$ref": "#/$defs/non_empty_string"
            }
          },
          "requires_red_green": {
            "$ref": "#/$defs/yes_no"
          },
          "manual_gate_surface": {
            "type": "array",
            "items": {
              "$ref": "#/$defs/non_empty_string"
            }
          },
          "external_evidence_surface": {
            "type": "array",
            "items": {
              "$ref": "#/$defs/non_empty_string"
            }
          },
          "blocker_before_live_autonomous_claim": {
            "$ref": "#/$defs/yes_no"
          }
        }
      }
    },
    "stage4_handoff_constraints": {
      "$ref": "#/$defs/non_empty_string_array"
    }
  },
  "$defs": {
    "non_empty_string": {
      "type": "string",
      "minLength": 1
    },
    "non_empty_string_array": {
      "type": "array",
      "minItems": 1,
      "items": {
        "$ref": "#/$defs/non_empty_string"
      }
    },
    "lower_snake_case_id": {
      "type": "string",
      "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
    },
    "yes_no": {
      "type": "string",
      "enum": [
        "yes",
        "no"
      ]
    },
    "true_check": {
      "type": "boolean",
      "const": true
    },
    "step_id": {
      "type": "string",
      "pattern": "^[0-9]{2,3}$"
    },
    "manual_gate": {
      "type": "string",
      "enum": [
        "none",
        "signoff",
        "approval",
        "operator_confirmation",
        "security_review",
        "presenter_review",
        "custom"
      ]
    },
    "external_check": {
      "type": "string",
      "enum": [
        "none",
        "human_supplied_evidence_required"
      ]
    }
  }
}
```

### `automation/schemas/round_four_autonomous_worker_stage4.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://openclaw.local/automation/schemas/round_four_autonomous_worker_stage4.schema.json",
  "title": "Round Four Autonomous Worker Stage 4 Audit Adversarial Readiness",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "summary_version",
    "audit_thesis",
    "audit_surfaces",
    "adversarial_cases",
    "security_surfaces",
    "manual_gates",
    "stage5_handoff_constraints"
  ],
  "properties": {
    "summary_version": {
      "type": "string",
      "const": "round_four_autonomous_worker_stage4.v1"
    },
    "audit_thesis": {
      "$ref": "#/$defs/non_empty_string"
    },
    "audit_surfaces": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "audit_surface",
          "in_scope_components",
          "frozen_artifacts",
          "excluded_surfaces",
          "required_reviewer_decision"
        ],
        "properties": {
          "audit_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "in_scope_components": {
            "$ref": "#/$defs/non_empty_string"
          },
          "frozen_artifacts": {
            "$ref": "#/$defs/non_empty_string"
          },
          "excluded_surfaces": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_reviewer_decision": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "adversarial_cases": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "adversarial_case_id",
          "attack_or_failure_class",
          "fixture_or_simulation",
          "expected_safe_behavior",
          "required_test_or_packet",
          "settlement_safety_rule"
        ],
        "properties": {
          "adversarial_case_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "attack_or_failure_class": {
            "$ref": "#/$defs/non_empty_string"
          },
          "fixture_or_simulation": {
            "$ref": "#/$defs/non_empty_string"
          },
          "expected_safe_behavior": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_test_or_packet": {
            "$ref": "#/$defs/non_empty_string"
          },
          "settlement_safety_rule": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "security_surfaces": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "security_surface",
          "required_control",
          "official_or_repo_source",
          "negative_test_or_review",
          "external_evidence_if_any"
        ],
        "properties": {
          "security_surface": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_control": {
            "$ref": "#/$defs/non_empty_string"
          },
          "official_or_repo_source": {
            "$ref": "#/$defs/non_empty_string"
          },
          "negative_test_or_review": {
            "$ref": "#/$defs/non_empty_string"
          },
          "external_evidence_if_any": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "manual_gates": {
      "type": "array",
      "minItems": 1,
      "items": {
        "type": "object",
        "additionalProperties": false,
        "required": [
          "gate_id",
          "gate_type",
          "trigger",
          "required_evidence",
          "blocks_what_claim",
          "unblocks_what_stage5_rows"
        ],
        "properties": {
          "gate_id": {
            "$ref": "#/$defs/lower_snake_case_id"
          },
          "gate_type": {
            "type": "string",
            "enum": [
              "approval",
              "operator_confirmation",
              "security_review",
              "signoff",
              "external_evidence"
            ]
          },
          "trigger": {
            "$ref": "#/$defs/non_empty_string"
          },
          "required_evidence": {
            "$ref": "#/$defs/non_empty_string"
          },
          "blocks_what_claim": {
            "$ref": "#/$defs/non_empty_string"
          },
          "unblocks_what_stage5_rows": {
            "$ref": "#/$defs/non_empty_string"
          }
        }
      }
    },
    "stage5_handoff_constraints": {
      "$ref": "#/$defs/non_empty_string_array"
    }
  },
  "$defs": {
    "non_empty_string": {
      "type": "string",
      "minLength": 1
    },
    "non_empty_string_array": {
      "type": "array",
      "minItems": 1,
      "items": {
        "$ref": "#/$defs/non_empty_string"
      }
    },
    "lower_snake_case_id": {
      "type": "string",
      "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
    },
    "yes_no": {
      "type": "string",
      "enum": [
        "yes",
        "no"
      ]
    },
    "true_check": {
      "type": "boolean",
      "const": true
    },
    "step_id": {
      "type": "string",
      "pattern": "^[0-9]{2,3}$"
    },
    "manual_gate": {
      "type": "string",
      "enum": [
        "none",
        "signoff",
        "approval",
        "operator_confirmation",
        "security_review",
        "presenter_review",
        "custom"
      ]
    },
    "external_check": {
      "type": "string",
      "enum": [
        "none",
        "human_supplied_evidence_required"
      ]
    }
  }
}
```

### `automation/schemas/round_four_autonomous_worker_stage5.schema.json`

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://openclaw.local/automation/schemas/round_four_autonomous_worker_stage5.schema.json",
  "title": "Round Four Autonomous Worker Stage 5 Final Playbook Summary",
  "type": "object",
  "additionalProperties": false,
  "required": [
    "summary_version",
    "playbook_contract",
    "execution_thesis",
    "sections_present",
    "phase_names",
    "step_count",
    "step_ids",
    "first_step_id",
    "last_step_id",
    "requires_red_green_steps",
    "manual_gate_steps",
    "external_evidence_steps",
    "playbook_validation_summary",
    "post_generation_validation_command"
  ],
  "properties": {
    "summary_version": {
      "type": "string",
      "const": "round_four_autonomous_worker_stage5.v1"
    },
    "playbook_contract": {
      "type": "string",
      "const": "markdown_playbook_v1"
    },
    "execution_thesis": {
      "$ref": "#/$defs/non_empty_string"
    },
    "sections_present": {
      "type": "array",
      "minItems": 6,
      "maxItems": 6,
      "items": {
        "type": "string",
        "enum": [
          "## 1. Plan Context",
          "## 2. Ordered Execution Plan",
          "## 3. Phase Details",
          "## 4. Shared Guidance",
          "## 5. Risks And Contingencies",
          "## 6. Immediate Next Actions"
        ]
      }
    },
    "phase_names": {
      "$ref": "#/$defs/non_empty_string_array"
    },
    "step_count": {
      "type": "integer",
      "minimum": 1
    },
    "step_ids": {
      "type": "array",
      "minItems": 1,
      "items": {
        "$ref": "#/$defs/step_id"
      }
    },
    "first_step_id": {
      "$ref": "#/$defs/step_id"
    },
    "last_step_id": {
      "$ref": "#/$defs/step_id"
    },
    "requires_red_green_steps": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/step_id"
      }
    },
    "manual_gate_steps": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/step_id"
      }
    },
    "external_evidence_steps": {
      "type": "array",
      "items": {
        "$ref": "#/$defs/step_id"
      }
    },
    "playbook_validation_summary": {
      "type": "object",
      "additionalProperties": false,
      "required": [
        "canonical_sections_present",
        "ordered_execution_header_present",
        "reserved_columns_absent",
        "forbidden_paths_absent",
        "prerequisites_parser_safe",
        "requires_red_green_commands_present",
        "manual_gates_after_technical_work",
        "external_evidence_narrow",
        "directly_usable_by_plan_orchestrator_v3"
      ],
      "properties": {
        "canonical_sections_present": {
          "$ref": "#/$defs/true_check"
        },
        "ordered_execution_header_present": {
          "$ref": "#/$defs/true_check"
        },
        "reserved_columns_absent": {
          "$ref": "#/$defs/true_check"
        },
        "forbidden_paths_absent": {
          "$ref": "#/$defs/true_check"
        },
        "prerequisites_parser_safe": {
          "$ref": "#/$defs/true_check"
        },
        "requires_red_green_commands_present": {
          "$ref": "#/$defs/true_check"
        },
        "manual_gates_after_technical_work": {
          "$ref": "#/$defs/true_check"
        },
        "external_evidence_narrow": {
          "$ref": "#/$defs/true_check"
        },
        "directly_usable_by_plan_orchestrator_v3": {
          "$ref": "#/$defs/true_check"
        }
      }
    },
    "post_generation_validation_command": {
      "type": "string",
      "const": "python3 automation/run_plan_orchestrator_v3.py list-items --playbook <final.md>"
    }
  },
  "$defs": {
    "non_empty_string": {
      "type": "string",
      "minLength": 1
    },
    "non_empty_string_array": {
      "type": "array",
      "minItems": 1,
      "items": {
        "$ref": "#/$defs/non_empty_string"
      }
    },
    "lower_snake_case_id": {
      "type": "string",
      "pattern": "^[a-z0-9]+(?:_[a-z0-9]+)*$"
    },
    "yes_no": {
      "type": "string",
      "enum": [
        "yes",
        "no"
      ]
    },
    "true_check": {
      "type": "boolean",
      "const": true
    },
    "step_id": {
      "type": "string",
      "pattern": "^[0-9]{2,3}$"
    },
    "manual_gate": {
      "type": "string",
      "enum": [
        "none",
        "signoff",
        "approval",
        "operator_confirmation",
        "security_review",
        "presenter_review",
        "custom"
      ]
    },
    "external_check": {
      "type": "string",
      "enum": [
        "none",
        "human_supplied_evidence_required"
      ]
    }
  }
}
```

### `automation/tests/test_round_four_autonomous_worker_v1_pack.py`

```py
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from automation.responses_runner_v2.pack_loader import (
    load_input_manifest,
    load_tool_profile,
    load_workflow_definition,
)


ROOT = Path(__file__).resolve().parents[2]
PACK_ROOT = "automation/examples/round_four_autonomous_worker_v1"
WORKFLOW_FILE = f"{PACK_ROOT}/workflows/round_four_autonomous_worker.workflow.json"

EXPECTED_STAGE_IDS = [
    "scope_lock_v2_precheck",
    "autonomous_worker_contract",
    "trial_settlement_protocol",
    "audit_adversarial_readiness",
    "final_markdown_playbook",
]

SCHEMA_FILES = [
    "automation/schemas/round_four_autonomous_worker_stage1.schema.json",
    "automation/schemas/round_four_autonomous_worker_stage2.schema.json",
    "automation/schemas/round_four_autonomous_worker_stage3.schema.json",
    "automation/schemas/round_four_autonomous_worker_stage4.schema.json",
    "automation/schemas/round_four_autonomous_worker_stage5.schema.json",
]

FORBIDDEN_MANIFEST_PATTERNS = [
    r"\.local/",
    r"\.local\\",
    r"(^|/)\.git(/|$)",
    r"(^|/)\.codex(/|$)",
    r"(^|/)\.claude(/|$)",
    r"(^|/)\.mcp\.json($|/)",
    r"(^|/)ops/config(/|$)",
    r"(^|/)secrets(/|$)",
]

OPENAI_STRUCTURED_OUTPUTS_UNSUPPORTED_SCHEMA_KEYWORDS = {
    "allOf",
    "contains",
    "dependentRequired",
    "dependentSchemas",
    "else",
    "if",
    "not",
    "prefixItems",
    "then",
    "uniqueItems",
}


def load_json(relative_path: str) -> dict:
    return json.loads((ROOT / relative_path).read_text(encoding="utf-8"))


class RoundFourAutonomousWorkerPackTests(unittest.TestCase):
    def test_workflow_manifest_loads_and_locks_five_stage_shape(self) -> None:
        workflow = load_workflow_definition(WORKFLOW_FILE, root=ROOT)

        self.assertEqual(workflow.workflow_id, "round-four-autonomous-worker-v1")
        self.assertEqual(workflow.workflow_mode, "custom_ordered")
        self.assertEqual([stage.stage_id for stage in workflow.stages], EXPECTED_STAGE_IDS)
        self.assertEqual(workflow.operator_requirements["minimum_primary_job_inputs"], 1)
        self.assertEqual(workflow.operator_requirements["maximum_primary_job_inputs"], 1)
        self.assertFalse(workflow.operator_requirements["allow_reference_context"])

        self.assertEqual(workflow.model_roles["primary_generation"].model, "gpt-5.5-pro")
        self.assertEqual(workflow.model_roles["primary_generation"].reasoning_effort, "xhigh")
        self.assertEqual(workflow.model_roles["primary_generation"].prompt_cache_retention, "24h")
        self.assertEqual(workflow.model_roles["structural_processing"].model, "gpt-5.5")
        self.assertEqual(workflow.model_roles["structural_processing"].reasoning_effort, "high")
        self.assertEqual(workflow.model_roles["structural_processing"].prompt_cache_retention, "24h")

        for stage in workflow.stages:
            self.assertEqual(stage.max_output_tokens, 128000)
            self.assertEqual(stage.model_role.value, "primary_generation")
            self.assertIsNotNone(stage.output.sidecar)

        self.assertEqual(workflow.stages[-1].gate.value, "terminal")
        for stage in workflow.stages[:-1]:
            self.assertEqual(stage.gate.value, "review_required")

        self.assertEqual(
            workflow.stages[1].carry_forward.review_bundle_from_stage_id,
            "scope_lock_v2_precheck",
        )
        self.assertEqual(
            workflow.stages[2].carry_forward.review_bundle_from_stage_id,
            "autonomous_worker_contract",
        )
        self.assertEqual(
            workflow.stages[3].carry_forward.review_bundle_from_stage_id,
            "trial_settlement_protocol",
        )
        self.assertEqual(
            workflow.stages[4].carry_forward.review_bundle_from_stage_id,
            "audit_adversarial_readiness",
        )

    def test_tool_profiles_are_well_formed_and_web_limited(self) -> None:
        self.assertEqual(load_tool_profile(f"{PACK_ROOT}/tools/no_tools.profile.json", root=ROOT), {})

        stage2_tools = load_tool_profile(
            f"{PACK_ROOT}/tools/stage2_openai_agent_docs_web_search.profile.json",
            root=ROOT,
        )
        self.assertEqual(stage2_tools["tools"][0]["type"], "web_search")
        self.assertEqual(
            stage2_tools["tools"][0]["filters"]["allowed_domains"],
            [
                "developers.openai.com",
                "platform.openai.com",
                "help.openai.com",
                "openai.com",
                "github.com",
            ],
        )
        self.assertEqual(stage2_tools["max_tool_calls"], 48)

        stage4_tools = load_tool_profile(
            f"{PACK_ROOT}/tools/stage4_capability_security_web_search.profile.json",
            root=ROOT,
        )
        self.assertEqual(stage4_tools["tools"][0]["type"], "web_search")
        self.assertEqual(
            stage4_tools["tools"][0]["filters"]["allowed_domains"],
            [
                "docs.github.com",
                "github.com",
                "modelcontextprotocol.io",
                "oauth.net",
                "datatracker.ietf.org",
                "google.github.io",
            ],
        )
        self.assertEqual(stage4_tools["max_tool_calls"], 64)

        workflow = load_json(WORKFLOW_FILE)
        stage_tool_files = {
            stage["stage_id"]: stage["tool_profile_file"] for stage in workflow["stages"]
        }
        self.assertEqual(stage_tool_files["scope_lock_v2_precheck"], "../tools/no_tools.profile.json")
        self.assertEqual(
            stage_tool_files["autonomous_worker_contract"],
            "../tools/stage2_openai_agent_docs_web_search.profile.json",
        )
        self.assertEqual(stage_tool_files["trial_settlement_protocol"], "../tools/no_tools.profile.json")
        self.assertEqual(
            stage_tool_files["audit_adversarial_readiness"],
            "../tools/stage4_capability_security_web_search.profile.json",
        )
        self.assertEqual(stage_tool_files["final_markdown_playbook"], "../tools/no_tools.profile.json")

    def test_input_manifests_reference_existing_repo_paths_and_no_forbidden_paths(self) -> None:
        for stage_number in range(1, 6):
            manifest_path = f"{PACK_ROOT}/inputs/stage{stage_number}.input_manifest.json"
            manifest_payload = load_json(manifest_path)
            serialized = json.dumps(manifest_payload, indent=2)

            for pattern in FORBIDDEN_MANIFEST_PATTERNS:
                self.assertIsNone(re.search(pattern, serialized), f"{manifest_path} contains {pattern}")

            manifest = load_input_manifest(manifest_path, root=ROOT)
            for field in ("primary_job_inputs", "reviewed_handoff_inputs", "attached_repository_files", "reference_context"):
                for entry in manifest[field]:
                    path = entry.path
                    self.assertFalse(Path(path).is_absolute(), path)
                    self.assertFalse(path.startswith("~"), path)
                    self.assertTrue((ROOT / path).exists(), path)

    def test_input_manifests_include_round_four_truth_anchors(self) -> None:
        def attached(stage_number: int) -> set[str]:
            return {
                entry["path"]
                for entry in load_json(f"{PACK_ROOT}/inputs/stage{stage_number}.input_manifest.json")[
                    "attached_repository_files"
                ]
            }

        self.assertTrue(
            {
                "docs/internal/2026-05-04_next_development_focus.md",
                "docs/internal/production_protocol/production_three_focus_closeout_packet.md",
                "docs/internal/production_protocol/repo_patch_harness_v2_no_overclaim_closeout.md",
                "docs/architecture/repo_patch_harness_v2_public_values.md",
                "docs/architecture/repo_patch_v1_v2_migration_truth.md",
            }.issubset(attached(1))
        )
        self.assertTrue(
            {
                "ops/demo/delegatee_runtime/control_plane.json",
                "ops/demo/delegator_agent/control_plane.json",
                "packages/sdk/src/repoPatchHarnessV2.ts",
                "packages/sdk/src/verifiedIssueTaskFactory.ts",
                "packages/sdk/src/dctV2Capabilities.ts",
            }.issubset(attached(2))
        )
        self.assertTrue(
            {
                "docs/internal/production_protocol/repo_patch_harness_v2_native_rehearsal.md",
                "docs/internal/production_protocol/repo_patch_harness_v2_live_attestation.md",
                "docs/architecture/repo_patch_harness_v2_claim_boundary.md",
                "packages/sdk/src/githubVerifiedIssueReceipt.ts",
            }.issubset(attached(3))
        )
        self.assertTrue(
            {
                "docs/security/capability_security_review_packet.md",
                "docs/security/non_settlement_capability_boundary.md",
                "docs/internal/production_protocol/external_mcp_oauth_setup_evidence_gate.md",
                "packages/sdk/src/a2aMetadataEnvelope.ts",
            }.issubset(attached(4))
        )
        self.assertTrue(
            {
                "docs/plan_orchestrator_v3/playbook-contract.md",
                "automation/plan_orchestrator_v3/adapters/markdown_playbook.py",
                "automation/plan_orchestrator_v3/playbook_parser.py",
                "automation/responses_runner_v2/schemas/workflow_manifest.schema.json",
            }.issubset(attached(5))
        )

    def test_shared_instructions_preserve_authority_order_and_deferrals(self) -> None:
        shared = (ROOT / PACK_ROOT / "shared_instructions.md").read_text(encoding="utf-8")
        authority_terms = [
            "Primary Job Inputs",
            "Reviewed Handoff Inputs",
            "Attached Repository Files",
            "Reference Context",
        ]
        positions = [shared.index(term) for term in authority_terms]
        self.assertEqual(positions, sorted(positions))

        for required in (
            "V2 live-readiness pre-check",
            "AI-held wallet or secret authority",
            "broad worker discovery",
            "reputation scoring",
            "browser settlement controls",
            "markdown_playbook_v1",
        ):
            self.assertIn(required, shared)

    def test_stage_prompts_include_required_output_structures(self) -> None:
        expected_headers = {
            "stage1_scope_lock_v2_precheck.md": [
                "| precheck_id | required_condition | source_of_truth | current_evidence_to_inspect | pass_condition | fail_or_blocked_state | downstream_effect |",
                "| boundary_surface | allowed_for_ai_worker | owned_by_runtime_or_operator | forbidden_for_ai_worker | evidence_or_test_needed | source_evidence |",
                "| non_goal | why_deferred | allowed_later_trigger | risk_if_reintroduced_now |",
            ],
            "stage2_autonomous_worker_contract.md": [
                "| contract_surface | ai_worker_may_do | runtime_must_own | operator_must_own | forbidden_authority | required_artifact_or_test |",
                "| harness_surface | required_behavior | allowed_tools_or_inputs | denied_tools_or_inputs | telemetry_emitted | failure_mode | implementation_target |",
                "| metric_id | metric_name | capture_point | required_unit_or_shape | why_required | downstream_use |",
            ],
            "stage3_trial_settlement_protocol.md": [
                "| trial_id | trial_type | task_shape | expected_outcome | required_fixture_or_artifact | verification_command | pass_condition | failure_data_to_capture |",
                "| settlement_step | runtime_or_operator_owner | precondition | action_boundary | evidence_artifact | manual_gate | failure_or_abort_rule |",
                "| step_family_id | phase_hint | atomic_action_family | prerequisites | artifact_families | requires_red_green | manual_gate_surface | external_evidence_surface | blocker_before_live_autonomous_claim |",
            ],
            "stage4_audit_adversarial_readiness.md": [
                "| audit_surface | in_scope_components | frozen_artifacts | excluded_surfaces | required_reviewer_decision |",
                "| adversarial_case_id | attack_or_failure_class | fixture_or_simulation | expected_safe_behavior | required_test_or_packet | settlement_safety_rule |",
                "| gate_id | gate_type | trigger | required_evidence | blocks_what_claim | unblocks_what_stage5_rows |",
            ],
            "stage5_final_markdown_playbook.md": [
                "## 1. Plan Context",
                "| step_id | phase | action | why_now | owner_type | prerequisites | repo_surfaces | deliverable | exit_criteria | allowed_write_roots | requires_red_green | manual_gate | manual_gate_reason | manual_gate_evidence | external_check | external_dependencies | consult_paths | required_verification_commands | suggested_verification_commands | required_verification_artifacts | notes |",
                "| risk_id | risk | trigger | impact | mitigation | contingency |",
            ],
        }
        for prompt_name, headers in expected_headers.items():
            text = (ROOT / PACK_ROOT / "prompts" / prompt_name).read_text(encoding="utf-8")
            for header in headers:
                self.assertIn(header, text, prompt_name)
            self.assertNotIn(".local/", text, prompt_name)

    def test_sidecar_schemas_are_strict_openai_compatible_objects(self) -> None:
        def walk_schema_objects(node, path, problems):
            if isinstance(node, dict):
                if node.get("type") == "object" and isinstance(node.get("properties"), dict):
                    props = set(node["properties"].keys())
                    if node.get("additionalProperties") is not False:
                        problems.append((".".join(path), "additionalProperties_not_false"))
                    required = node.get("required")
                    if not isinstance(required, list):
                        problems.append((".".join(path), "missing_required"))
                    else:
                        missing = sorted(props - set(required))
                        if missing:
                            problems.append((".".join(path), f"required_missing_{missing}"))
                if "$ref" in node and set(node) != {"$ref"}:
                    problems.append((".".join(path), "$ref_sibling_keywords"))
                for key, value in node.items():
                    if key in OPENAI_STRUCTURED_OUTPUTS_UNSUPPORTED_SCHEMA_KEYWORDS:
                        problems.append((".".join(path + [key]), key))
                    if key == "items" and value is False:
                        problems.append((".".join(path + [key]), "items_false"))
                    walk_schema_objects(value, path + [str(key)], problems)
            elif isinstance(node, list):
                for index, value in enumerate(node):
                    walk_schema_objects(value, path + [str(index)], problems)

        for schema_file in SCHEMA_FILES:
            schema = load_json(schema_file)
            self.assertFalse(schema.get("additionalProperties"), schema_file)
            self.assertIn("summary_version", schema["required"], schema_file)
            problems: list[tuple[str, str]] = []
            walk_schema_objects(schema, [], problems)
            self.assertEqual(problems, [], schema_file)

        terminal = load_json(SCHEMA_FILES[-1])
        self.assertEqual(terminal["properties"]["playbook_contract"]["const"], "markdown_playbook_v1")
        self.assertIn("playbook_validation_summary", terminal["required"])
        self.assertEqual(
            terminal["properties"]["post_generation_validation_command"]["const"],
            "python3 automation/run_plan_orchestrator_v3.py list-items --playbook <final.md>",
        )

    def test_no_forbidden_path_patterns_in_pack_manifests(self) -> None:
        manifest_files = [
            Path(WORKFLOW_FILE),
            *[Path(f"{PACK_ROOT}/inputs/stage{i}.input_manifest.json") for i in range(1, 6)],
        ]
        for relative_path in manifest_files:
            text = (ROOT / relative_path).read_text(encoding="utf-8")
            for pattern in FORBIDDEN_MANIFEST_PATTERNS:
                self.assertIsNone(re.search(pattern, text), f"{relative_path} contains {pattern}")


if __name__ == "__main__":
    unittest.main()
```

## Validation Notes

The test locks the five-stage order, model roles, 128000 token ceilings, prompt-cache retention, sidecar presence, web-tool limits, existing repo-relative attachments, strict sidecar schemas, prompt table headers, shared authority order, and forbidden-path exclusions.

[1]: https://developers.openai.com/api/docs/guides/prompt-guidance "https://developers.openai.com/api/docs/guides/prompt-guidance"
