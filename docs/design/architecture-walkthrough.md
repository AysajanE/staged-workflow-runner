# `staged-workflow-runner` — Comprehensive Architecture Walkthrough

## 1. What this repo is, in one sentence

A **manifest-driven runner for high-stakes staged OpenAI Responses workflows** that treats every artifact as evidence: schema-versioned, hashed, and confined to a single workspace root so a `tar` of that root constitutes a complete audit trail. It's optimized for *evidence chain-of-custody*, not throughput.

Two CLIs sit on top of one engine: a **generic runner** (`run_responses_v2.py`) executes one stage at a time against the OpenAI Responses API; a **supervisor** (`run_responses_supervisor_v2.py`) wraps that engine with policy — multi-agent review, failure classification, archive/rerun, and final-bundle assembly.

> **Insight**
> - "High-stakes" is the load-bearing adjective. Most LLM runners optimize for throughput and ergonomics. This one is built around the assumption that an answer might be wrong in a way that costs money or trust, so every claim has to be back-traceable to a specific attached file with a known hash.
> - The Responses API is *background-capable*: a request can run for hours with a `response_id` you poll. Almost the entire engine architecture (stage_checkpoint persistence, three resume modes, no-duplicate-submit guard) exists because the local Python process can die mid-run and the remote model is still working.

---

## 2. The Two-Lane Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│   SUPERVISOR LANE (policy)              automation/responses_…  │
│   ─────────────────────────              run_responses_supervisor│
│   - session state                        _v2.py + supervisor.py  │
│   - scaffold staging                                             │
│   - 3-agent review loop                                          │
│   - operator selective acceptance       Calls into ↓             │
│   - failure classification (6-way)                               │
│   - archive-before-rerun                                         │
│   - final implementation bundle                                  │
└──────────────────────┬──────────────────────────────────────────┘
                       │ run_workflow() / create_review_bundle()
                       ▼
┌─────────────────────────────────────────────────────────────────┐
│   ENGINE LANE (mechanism)               automation/responses_…  │
│   ─────────────────────────             run_responses_v2.py +   │
│   - workflow loading                    workflow.py + ...        │
│   - input-manifest expansion                                     │
│   - file uploads + role tagging                                  │
│   - request construction                                         │
│   - token preflight (fail-closed gate)                           │
│   - Responses API submission                                     │
│   - polling/resume/refresh                                       │
│   - artifact finalization                                        │
│   - sidecar structured extraction                                │
│   - review-bundle validation                                     │
└─────────────────────────────────────────────────────────────────┘
```

**The boundary is enforced** (AGENTS.md:65): "Do not rewrite `automation/responses_runner_v2/workflow.py` into the supervisor." The supervisor's import list — `from .workflow import run_workflow` and `from .review_bundle import create_review_bundle` — is the contract. It calls *into* the engine; it never reimplements submission/polling.

---

## 3. The Engine Lane — Internals

### 3.1 Foundation: `contracts.py`

This file is the **type system + invariants**. Everything else depends on it. Key elements:

| Concept | What it does |
|---|---|
| `RUNNER_VERSION = "responses_runner_v2.2026-03-17"` | Stamped into every run for replay |
| `WORKFLOW_SCHEMA_VERSION = "responses_runner_v2.workflow_manifest.v1"` (and 10 sibling versions) | Wire-protocol-style versioning per artifact type |
| `AUTHORITY_ORDER` | `Primary → Reviewed Handoff → Repository → Reference` — the model's hierarchy |
| `COMMON_RUNNER_INSTRUCTIONS` | The boilerplate prepended to every request, telling the model the authority order |
| `MODEL_CAPS` | Locked map: only `gpt-5.5` and `gpt-5.5-pro` accepted |
| `validate_model_options()` | `SystemExit`s on max_output_tokens overage, wrong cache retention, structured-output mismatch |
| `repo_root()` | Workspace-root resolver: explicit → env var → cwd |
| `resolve_under_root()` | **The single chokepoint that prevents path escape** — `SystemExit` if a path resolves outside root |
| `ResumeMode` enum | `FRESH_SUBMIT`, `RESUME_RESPONSE_ID`, `REFRESH_STATUS_ONLY` |
| `RESPONSES_CONTEXT_SUPPORTED_SUFFIXES` | The OpenAI-allowed file extensions; everything else needs the markdown wrapper |

> **Insight**
> - `resolve_under_root()` (contracts.py:452-462) does `path.resolve().relative_to(root.resolve())` — the resolve step follows symlinks, so even a symlink trick can't sneak files in/out. This makes the One-Root Policy *mechanical*, not just documented.
> - Schema versions are strings like `responses_runner_v2.foo.v1` rather than integers. This is the same practice as gRPC service names — letting you ship a v2 engine alongside v1 review bundles indefinitely.

### 3.2 Loading: `pack_loader.py`

Translates JSON workflow manifests into `WorkflowDefinition` dataclasses with **resolved, root-confined paths**. The validator catches misconfiguration *at load time*, not at request time:

- Refuses GPT-5.5-family profiles that don't set `prompt_cache_retention="24h"` (line 52-56)
- Refuses `background=True` with `store=False` (line 105-106) — the engine uses background mode to survive process death; without `store=true` you'd lose the response
- Validates stage uniqueness, ordering, and carry-forward references
- Validates `workflow_mode` matches stage count (`one_pass`=1, `two_pass`=2, `reviewed_three_stage`=3, or `custom_ordered`=any)

It also normalizes legacy tool profiles (e.g., `web_search_preview` → `web_search`, attaches `domains` as `filters.allowed_domains`).

### 3.3 Attachment Pipeline: `attachments.py`

This is where the model actually sees its inputs. The flow per stage:

1. **Resolve** — read static `input_manifest.json`, walk directories (skipping `.git`/`.local`/`node_modules`/...), apply `exclude_globs`, hash every file.
2. **Wrap** — for files whose extension isn't in `RESPONSES_CONTEXT_SUPPORTED_SUFFIXES` but are UTF-8 text, generate a markdown wrapper with the source path in front matter and the body in a fenced code block. **This is what lets the engine attach `.go`, `.rs`, `.toml`, `.tsx` files** even though OpenAI's API doesn't accept those extensions directly.
3. **Render** — produce `input_manifest.md`, the human-and-model-readable enumeration of every attached file with its role.
4. **Upload** — push every file (including the manifest itself) via the multipart `/files` endpoint.
5. **Build content blocks** — emit role-labeled text+file pairs in fixed authority order (manifest → primary → reviewed handoff → repository → reference).

> **Insight**
> - The 50MB request budget (`MAX_REQUEST_ATTACHMENT_BYTES`) and 50MB per-file cap (`MAX_SINGLE_FILE_BYTES`) are checked *during resolution*, not at upload time. So a misconfigured manifest fails before any HTTP traffic.
> - The rendered `input_manifest.md` contains short SHA256 prefixes for each file (`sha256=abc12345...`). The model can be asked to cite these as evidence, and the operator can verify by recomputing.

### 3.4 The Core: `workflow.py`

The 1300-line orchestrator. The entry is `run_workflow()`. The state machine flow:

```
load_workflow_definition
        ↓
load/create run_manifest.json
        ↓
_determine_next_stage  ← decides which stage to run (respects review gates)
        ↓
┌─────────────── stage loop ───────────────┐
│ 1. resolve attachments + render manifest  │
│ 2. write request_payload.json (DRY-RUN exits here)
│ 3. upload files                            │
│ 4. token preflight (fail-closed gate)      │
│ 5. POST /responses → response.latest.json  │
│ 6. write checkpoint #1 (immediate status)  │
│ 7. if --wait: poll until terminal          │
│    (re-write response.latest.json each poll)
│ 8. write response.final.{json,md}          │
│ 9. extract structured output if requested  │
│10. run sidecar pass if configured          │
│11. write checkpoint #2 (final status)      │
│12. if gate=auto AND --wait AND has next:   │
│    loop to next stage                      │
└────────────────────────────────────────────┘
```

Key behaviors:

- **Token preflight is a fail-closed gate** (workflow.py:422-531). It calls `POST /responses/input_tokens` first; an overage is *terminal* — never retried, no fallback even if API fails. Treats unbounded token spend as a safety property. The bypass is `--skip-token-count` (operator must explicitly accept the risk).
- **Two checkpoint writes** — one right after the API responds (which in background mode is usually `queued`), one after polling reaches terminal status. The first is enough to resume from.
- **Auto-progression is gated** (workflow.py:1144-1154). The engine only loops to the next stage when *all four* are true: stage completed, has next, gate=auto, `--wait` was passed, and the operator didn't pin a specific `--stage`. Anything else stops the run.
- **Three resume modes** correspond to operator intent:
  - `FRESH_SUBMIT` — first run of a stage
  - `RESUME_RESPONSE_ID` — `resume` subcommand: rehydrate the stored `response_id`, finalize artifacts
  - `REFRESH_STATUS_ONLY` — `refresh` subcommand: just poll the remote, don't finalize/cleanup. Used during monitoring.

### 3.5 OpenAI Client: `openai_client.py`

A 240-line **stdlib-only** HTTP client. No `requests`, no `httpx` — just `urllib`. Hand-rolled multipart uploads (lines 83-102). This means the core engine has zero third-party runtime dependencies.

Notable: retries on `{408, 409, 429, 500, 502, 503, 504}` with exponential backoff (capped at 30s). `wait_for_terminal_response` polls and writes a checkpoint on every poll via the `checkpoint_callback`, so even a polling-only run keeps the local `response.latest.json` current.

### 3.6 Sidecar Extraction: `sidecar.py`

When a stage configures `output.sidecar`, the engine runs a *second*, structured-output Responses call after the primary completes. It uploads the primary's markdown + raw response JSON as input, asks the structural model (`gpt-5.5`) to extract a strict-JSON-schema-conformant payload, and writes it to `output.structured.json` plus `sidecar.response.{json,md}`. This separates "creative generation" (primary) from "machine-ingestible structure" (sidecar) — the primary doesn't have to fight schema constraints while reasoning.

### 3.7 Review Bundle: `review_bundle.py`

The handoff contract between gated stages. A bundle JSON references files (paths + sha256 hashes) — primary markdown, response JSON, reviewer notes, and optional approved handoff markdown / structured artifact JSON. **`load_review_bundle()` rehashes every referenced file and refuses to load if any hash mismatches** (lines 138-160). That gives a stage downstream a cryptographic guarantee that what it's reading is what was approved.

### 3.8 Artifacts: `artifacts.py`

Owns the on-disk run layout. Creates `runs/{ts}_{run_name}_{workflow_id}/`, the `stages/NN_stage_id/` subdirectories, the run manifest, and the response markdown rendering. The markdown renderer (`write_response_pair`, lines 300-363) produces the human-friendly `response.final.md` with token usage, source citations, tool-call summaries, and uploads payload — alongside the raw `response.final.json`.

---

## 4. The Supervisor Lane — Internals

The supervisor is *additive policy*. It exists to answer: "what do I do with the engine's output?" especially when failure is messy or review is required.

### 4.1 Sessions: `supervisor_artifacts.py`

A supervisor session lives at `.local/automation/responses_runner_v2/supervisor_sessions/{session_id}/`. It contains:

```
supervisor_session.json   ← THE single source of truth, schema-validated
commands/                 ← captured stdout/stderr from agent CLIs
review_cycles/{cycle_id}/ ← per-cycle operator + reviewer + consolidation outputs
scaffolds/{version_id}/   ← staged scaffold copies + hash manifests
archives/{archive_id}/    ← archived failed-no-artifact attempts
final_bundle/             ← final implementation bundle output
human_pauses/             ← anomaly records that need a human decision
monitoring/               ← polling state for live response_ids
dry_runs/                 ← scaffold dry-run validations
```

Every write goes through `write_session()` (lines 225-235) which validates against `supervisor_session.schema.json` before atomically renaming the temp file. Schema invariance is mechanical.

The module also implements `snapshot_workspace()` (lines 314-338) — walks every file under root, hashes it, returns `{path: sha256}`. Combined with `diff_snapshots()`, this is the read-only enforcement primitive.

### 4.2 The Review Loop: `supervisor.py` + `supervisor_agents.py`

Three independent agents review every gated stage and the final packet:

| Role | CLI | Read-only? | Schema |
|---|---|---|---|
| **operator_codex** | `codex exec ...` | No (accountable, can apply changes) | `review_decision.v1` |
| **codex_review_agent** | `codex exec ...` | **Yes**, snapshot-enforced | `review_decision.v1` |
| **claude_review_agent** | `claude --bare -p --model opus --effort max --output-format json ...` | **Yes**, snapshot-enforced | `review_decision.v1` |

The flow (`supervisor.py:invoke_operator → invoke_reviewers → consolidate_reviews → accept_consolidated_review → create_approved_review_bundle`):

```
1. invoke_operator        → operator runs, writes provisional review_decision.json
2. invoke_reviewers       → Codex + Claude run independently, snapshot-validated
                            (REFUSED if no operator_provisional_record exists, for
                             review_kinds in REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL)
3. consolidate_reviews    → deterministic dedup by (recommendation text + affected_artifacts);
                            advisory only — emits "next_action: proceed_to_operator_acceptance"
4. accept_consolidated_review → operator picks IDs; supervisor REWRITES "accepted" → "rejected"
                                if applied_change_evidence lacks
                                {changes_applied[], validation_evidence[], operator_rationale}
5. create_approved_review_bundle → only if acceptance_record.approval_decision == "approve"
```

> **Insight**
> - **The supervisor will not synthesize evidence.** `accept_consolidated_review` (supervisor.py:548-554) rejects an accepted recommendation if `applied_change_evidence` doesn't supply concrete `changes_applied[]`, `validation_evidence[]`, and `operator_rationale`. The rationale string says "supervisor does not synthesize changes_applied" — that's the design refusing to let operators rubber-stamp recommendations.
> - **Read-only enforcement is a pre/post snapshot diff.** Reviewer agents may write to `command_stdout_path`, `command_stderr_path`, the JSON+markdown sidecars, and the readonly diff path itself — anything else is a `read_only_violation`. This catches an agent that prompts itself into editing a file.
> - **The `review_decision.v1` schema is the inter-agent protocol.** Every agent (operator, Codex reviewer, Claude reviewer, consolidation pass) emits the *same* schema. `actor_role` distinguishes them. The schema (`review_decision.schema.json:14-247`) requires: blocking_issues, recommendations with severity+evidence+(exact_change_needed OR rationale_for_no_change), unsupported_claims, command metadata, read_only_check. The shape ensures comparability.

### 4.3 Failure Classification: `supervisor_policies.py`

The runtime decision matrix. Six terminal classifications, each with an explicit `(reviewable, review_bundle_allowed, rerun_allowed)` triple:

| Classification | Reviewable | Bundle-able | Rerun | Recovery action |
|---|---|---|---|---|
| `completed_complete_artifact` | ✓ | ✓ | — | `review` (normal happy path) |
| `failed_complete_artifact` | ✓ | ✓ | — | `review_failed_artifact` (model produced substantive text but reported failure) |
| `failed_no_artifact` | ✗ | ✗ | ✓ | `archive_before_rerun` |
| `incomplete_output_limit` | ✗ | ✗ | ✗ | `block_and_recover` + human pause |
| `blocked_token_preflight` | ✗ | ✗ | ✗ | `human_pause` |
| `long_running_monitoring_anomaly` | ✗ | ✗ | ✗ | `monitor_without_duplicate_submit` + human pause |

`classify_stage_outcome()` (line 168) inspects the stage checkpoint, reads the response markdown, and applies rules:
- "Substantive markdown" = ≥40 chars and not literally `"No assistant text was returned."`
- A `failed` response with substantive markdown becomes `failed_complete_artifact` (still reviewable!)
- A `failed` response without it becomes `failed_no_artifact` (must archive first)
- An `incomplete` response always blocks; it cannot auto-progress (AGENTS.md:128)

### 4.4 Archive-Before-Rerun: `supervisor_artifacts.archive_attempt`

For `failed_no_artifact`, the operator can rerun *only after archiving*. The archive captures:

- The full request_payload, input_manifest (json + md), stage_checkpoint, and any partial response
- The workflow_manifest.sha256 + scaffold hash_manifest digest
- `request_hash` (deterministic SHA of the entire evidence object)
- `unchanged_input_evidence: { request_hash_before, scaffold_hash_before, rerun_requires_same_hashes: true }`

Before allowing the rerun, `can_rerun_failed_no_artifact()` checks the *current* request and scaffold hashes match the archive's `_before` hashes. **If the operator changed the prompt and tried to claim "fresh failure," the hashes won't match — rerun blocked.** This eliminates a whole class of "I quietly changed the inputs and pretended it was the same attempt" scenarios.

### 4.5 The Final Bundle: `create_final_implementation_bundle`

The supervisor's terminal artifact. Schema-validated payload (`final_implementation_bundle.schema.json`) requires:

- `file_inventory[]` and `emitted_files[]` whose paths must be **identical sets** — the supervisor literally compares `sorted(inventory_paths) == sorted(emitted_paths)`. This is the "no half-finished implementations" guarantee from AGENTS.md:140.
- `agent_reviews` containing all three: `operator_codex`, `codex_review_agent`, `claude_review_agent`.
- `operator_acceptance` with the decision_id of the approving acceptance record.
- Plus: `validation_evidence`, `model_migration_summary`, `failure_policy_summary`, `human_pause_summary`, `rollout_instructions`, `residual_risks`.

---

## 5. End-to-End Walkthrough

### Path A: Generic Engine (one-pass example)

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/.../one_pass.workflow.json \
  --skip-token-count \
  --wait
```

1. CLI parses args → resolves root → builds `RuntimeOptions` → instantiates `OpenAIClient.from_env(root)`.
2. `run_workflow()` loads the workflow JSON (validates schema_version + model caps), creates `runs/{ts}_..._{workflow_id}/`, writes initial `run_manifest.json` with `status="created"`.
3. `_determine_next_stage()` → finds stage 1, status `prepared`.
4. `_build_stage_runtime_manifest()` → resolves the input manifest (walks dirs, hashes files).
5. Files are uploaded. The rendered `input_manifest.md` is uploaded *first* and wired into the request as the manifest_file_id.
6. Request payload is built: instructions = `COMMON_RUNNER_INSTRUCTIONS + shared_instructions.md + stage_instructions.md (if any)`. Content blocks are role-labeled in authority order.
7. Token preflight unless skipped. Run with `--skip-token-count` only when an operator accepts that the model might consume more tokens than budgeted.
8. `POST /responses` → checkpoint #1 written. With `background: true`, the response comes back as `queued` immediately.
9. `--wait` polls until terminal. Each poll rewrites `response.latest.json`.
10. Once terminal: structured output extracted (if json_schema), sidecar runs (if configured), final markdown rendered, checkpoint #2 written, run_manifest updated.
11. If `gate=auto` and there's a next stage, the runner loops. Otherwise it returns the run_manifest path and exits.

### Path B: Supervisor Lane (full self-improvement loop)

This is the four-stage `responses_runner_v2_supervised_end_to_end_self_improvement` pack, all gated:

```
Stage 1: architecture_and_supervision_protocol  (review_required)
Stage 2: agent_review_protocol_and_package_contract  (review_required)
Stage 3: draft_drop_in_packet  (review_required)
Stage 4: final_drop_in_packet  (terminal, with sidecar)
```

End-to-end:

1. **Init**: human writes a `clarified_task_brief.md`. Operator runs `init-session`. Supervisor session JSON is created with `status: "clarified"`, `current_phase: "scaffold"`.
2. **Stage scaffold**: operator runs `stage-scaffold --scaffold-path ./scaffold_dir`. Files copied into `scaffolds/scaffold_001/source/`, hash_manifest generated.
3. **Dry-run**: `dry-run-scaffold --workflow-file ...`. Internally this calls `run_workflow(... dry_run=True ...)` — so the engine validates inputs, writes a request payload, but never hits the API.
4. **Scaffold review cycle** (review_kind = `scaffold`):
   - `invoke-operator` — writes operator's provisional decision JSON
   - `invoke-reviewers` — Codex + Claude review independently (workspace snapshot diff = read-only check)
   - `consolidate` — dedup; output is advisory (`next_action: proceed_to_operator_acceptance`)
   - `accept --accept-recommendation rec_001 --applied-change-evidence ./evidence.json` — operator selects which to accept; **must supply changes_applied + validation_evidence + rationale or it gets auto-rejected**
   - If approval: scaffold is `accepted`. `assert_scaffold_launch_allowed()` would now pass.
5. **Launch stage 1 via the engine** (`run_responses_v2.py run --workflow-file ...`). Same flow as Path A. Stage 1 output writes to `stages/01_architecture_and_supervision_protocol/response.final.{md,json}`.
6. **Classify stage 1**: `classify --run-dir ... --stage architecture_and_supervision_protocol`. Reads checkpoint, returns `outcome.v1` JSON. If `completed_complete_artifact` → reviewable.
7. **Stage 1 review cycle** (review_kind = `stage_output`): operator → reviewers → consolidate → accept.
8. **Bundle**: `create-bundle` with the acceptance_record. Bundle JSON includes file hashes; the bundle is what stage 2 will consume as `Reviewed Handoff Inputs`.
9. **Launch stage 2** with `--review-bundle stage1_bundle.json`. The engine validates the bundle (rehashes every file), rejects on mismatch, and lays out the bundle's contents under the `Reviewed Handoff Inputs` role.
10. Repeat steps 6-9 for stages 2, 3, 4.
11. **Final bundle**: `finalize-bundle --packet-json ./final_packet.json`. The schema validator checks that `file_inventory` and `emitted_files` are the same set. Session enters `status: "completed"`.

> **Insight**
> - The four-stage pack is *self-referential*: it asks the model to design a supervised end-to-end pack while running through one. This is why review_kind values include `final_packet` and the prompts reference `review_agent_requirements.md` — the artifact being authored is the same kind of artifact that's reviewing it.
> - Every artifact under the run directory has a stable hash chain: `workflow_manifest.sha256` → run_manifest → stage_checkpoint references response.final.json by sha256 → review_bundle's artifact_hashes → next stage's input_manifest references the bundle. A `tar` of the run + session is reconstructible.
> - Carry-forward is deliberately cheap: it just attaches the prior stage's `response.final.md` (or the review bundle's primary_artifact_markdown) under `reference_context`. No prompt-stuffing, no summarization — the model gets the actual approved text.

---

## 6. Run Output Layout

```
.local/automation/responses_runner_v2/
├── runs/{ts}_{run_name}_{workflow_id}/
│   ├── run_manifest.json                    ← top-level state
│   └── stages/{NN_stage_id}/
│       ├── input_manifest.{json,md}         ← resolved attachments + role labels
│       ├── request_payload.json             ← exact body sent to /responses
│       ├── token_preflight.{json|.error.json}
│       ├── uploads.json                     ← file_id ↔ source_path mapping
│       ├── response.latest.json             ← updated on each poll
│       ├── response.final.{json,md}         ← finalized after terminal status
│       ├── output.structured.json           ← if json_schema or sidecar configured
│       ├── sidecar.response.{json,md}       ← if sidecar configured
│       └── stage_checkpoint.json            ← lifecycle state, durable resume point
└── supervisor_sessions/{session_id}/
    ├── supervisor_session.json              ← schema-validated state
    ├── scaffolds/{version}/source/ + hash_manifest.json
    ├── review_cycles/{cycle_id}/{operator,agents}/*.{json,md,stdout.txt,stderr.txt,readonly.diff.md}
    ├── archives/{archive_id}/{supervisor_archive.json, artifacts/...}
    ├── monitoring/{stage_id}.monitoring.{json,human_pause.json}
    ├── human_pauses/...
    └── final_bundle/{final_implementation_bundle.json}
```

The `.local/` prefix is in `.gitignore` and `DIRECTORY_SKIP_NAMES` — these artifacts never get committed and are never re-attached as evidence to a future run.

---

## 7. Tests and Validation

The `automation/tests/` suite has 8 modules covering: contracts, example pack, supervisor flow, supervisor lane pack, review bundle, workflow, eval harness, model migration. Standard run:

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
```

CI (`.github/workflows/ci.yml`) runs the unittest suite plus dry-run smokes for the synthetic pack and the four-stage supervised pack on Python 3.10/3.11/3.12.

---

## 8. The Big Themes (what to internalize)

1. **One workspace root, mechanically enforced.** Not a convention — `resolve_under_root` SystemExits.
2. **Schema versions are wire protocols.** Every artifact carries `responses_runner_v2.<name>.v1`. v2 can co-exist without silent corruption.
3. **Authority Order is data, not docs.** Encoded as constants, iterated by attachments.py, baked into every request.
4. **Background responses change everything.** Three resume modes, two checkpoint writes per stage, no-duplicate-submit guard exist because a response can outlive the local process.
5. **Token preflight is fail-closed.** Unbounded spend is treated as a safety property.
6. **Separation of mechanism and policy.** Engine handles "how to talk to OpenAI." Supervisor handles "what to do with the answer."
7. **Three independent reviews + operator acceptance with applied-change evidence.** Reviewers don't write code; operator must supply concrete `changes_applied[]` or the supervisor auto-rejects.
8. **Read-only enforcement is mechanical.** Workspace snapshot diff before/after the agent runs.
9. **Failure has six discrete shapes.** Each maps to a distinct recovery path. Incomplete and blocked outcomes never auto-progress.
10. **Archive-before-rerun is mandatory** for `failed_no_artifact`. Same hashes required to rerun → no silent re-experimentation.
11. **Stdlib-only HTTP client.** Engine has zero third-party runtime deps; `jsonschema`/`pytest` are optional.
12. **Locked model posture.** `gpt-5.5-pro` / `gpt-5.5`, 24h cache, xhigh reasoning, 128000 max output. Validation refuses anything else at load time.
