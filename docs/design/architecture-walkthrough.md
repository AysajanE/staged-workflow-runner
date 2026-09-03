# `staged-workflow-runner` — Architecture Walkthrough

## 1. What this repo is, in one sentence

A **manifest-driven runner for high-stakes staged OpenAI Responses workflows** that treats every artifact as evidence: schema-versioned, hashed, and confined to a single workspace root so a `tar` of that root constitutes a complete audit trail. It is optimized for *evidence chain-of-custody*, not throughput.

There is one engine (`automation/responses_runner_v2/`) and one CLI (`automation/run_responses_v2.py`). A single `run` invocation chains a whole workflow through its stage gates; a gate may pause the run for a single independent reviewer CLI or for a human.

> **Insight**
> - "High-stakes" is the load-bearing adjective. Most LLM runners optimize for throughput and ergonomics. This one is built around the assumption that an answer might be wrong in a way that costs money or trust, so every claim has to be back-traceable to a specific attached file with a known hash.
> - The Responses API is *background-capable*: a request can run for hours with a `response_id` you poll. The durable run manifest, the `resume`/`refresh` paths, and the no-duplicate-submit guard exist because the local Python process can die mid-run while the remote model is still working.

---

## 2. Engine and Gates

```
automation/run_responses_v2.py            CLI: run | resume | refresh | cancel |
        │                                      recover-uploads
        ▼
automation/responses_runner_v2/
  contracts.py          constants, enums, dataclasses, path confinement, atomic writes
  workflow.py           run_workflow(): stage loop, gates, revisions, handoff notes
  pack_loader.py        workflow JSON -> WorkflowDefinition (root-confined paths)
  attachments.py        manifest expansion, markdown wrapping, uploads, role blocks
  openai_client.py      urllib transport, exact token count, polling
  reviewer.py           one reviewer CLI (codex | claude) -> JSON verdict
  artifacts.py          run/stage/attempt layout, run manifest
  validators.py         advisory post-output checks
  locking.py            .runner.lock: one process per run directory
  schema_validation.py  jsonschema checks for persisted manifests
```

Each stage declares a `gate` (`GateType`, contracts.py:270):

| Gate | What happens when the stage completes |
|---|---|
| `auto` | The run continues to the next stage. |
| `reviewed` | One reviewer CLI returns `approve` or `revise`. `approve` continues; `revise` triggers one revision attempt of the same stage; a second `revise` blocks the stage until a human supplies `--handoff-note`. |
| `human` | The run stops with the stage `waiting_for_review`. The operator reads `artifact.md` and continues with `run ... --run-dir <run_dir> --handoff-note <note.md>`. |
| `terminal` | Last stage. Nothing runs after it; `artifact.md` is the deliverable. |

A workflow that still spells a gate `review_required` is loaded as `human` (pack_loader.py:316-317).

`run` and `resume` wait in-process by default (poll every 20 s, `DEFAULT_POLL_INTERVAL` at contracts.py:23); `--no-wait` returns after submission. One waiting `run` chains through `auto` and `reviewed` gates, including revisions, until a `human` gate, a blocked review, the terminal stage, or an error (workflow.py:1982-2013).

---

## 3. The Engine — Internals

### 3.1 Foundation: `contracts.py`

This file is the **type system + invariants**. Everything else depends on it. Key elements:

| Concept | What it does |
|---|---|
| `RUNNER_VERSION` (line 16) | Stamped into every run for replay |
| `WORKFLOW_SCHEMA_VERSION` (line 30) and sibling artifact versions | Wire-protocol-style versioning per artifact type |
| `AUTHORITY_ORDER` (line 40) | `Primary Job Inputs → Reviewed Handoff Inputs → Attached Repository Files (alias: Attached Workspace Evidence) → Reference Context` |
| `COMMON_RUNNER_INSTRUCTIONS` (line 245) | Boilerplate prepended to every request, telling the model the authority order |
| `REVISION_INSTRUCTIONS` (line 258) | Prefixed to the task text on a revision attempt |
| `GateType` (line 270), `StageStatus` (line 300), `ALLOWED_STAGE_TRANSITIONS` (line 329) | Gate categories, per-stage lifecycle states (including `revision_requested`), and the legal transitions between them; `assert_stage_transition()` (line 351) `SystemExit`s on any other move |
| `ReviewConfig` (line 398) | `reviewer` (`codex` default, `claude`, `none`), `model`, `effort`, `timeout_seconds` (1800), `max_revisions` (1); defaults `gpt-5.6-sol`/`high` for codex and `opus`/`xhigh` for claude (lines 393-394) |
| `MODEL_CAPS` (line 183) | Capability map for durable `gpt-5.6` and explicit family variants |
| `validate_model_options()` (line 730) | `SystemExit`s on max_output_tokens overage, wrong cache retention, structured-output mismatch |
| `resolve_under_root()` (line 623) | **The single chokepoint that prevents path escape** — `SystemExit` if a path resolves outside root |

> **Insight**
> - `resolve_under_root()` (contracts.py:623-633) does `path.resolve().relative_to(root.resolve())`; the resolve step follows symlinks, so a symlink trick cannot sneak files in or out. This makes the One-Root Policy *mechanical*, not just documented.
> - Schema versions are strings like `responses_runner_v2.run_manifest.v2` rather than integers; `schema_validation.persisted_schema_filename()` maps each supported version to its schema file and rejects any other, so a persisted manifest always names the layout it was validated against.

### 3.2 Loading: `pack_loader.py`

Translates JSON workflow manifests into `WorkflowDefinition` dataclasses with **resolved, root-confined paths**. Misconfiguration fails *at load time*, not at request time:

- Refuses `background=true` with `store=false` (lines 163-164); background mode is what lets a response outlive the process, and that needs `store`.
- Validates stage uniqueness, ordering, and carry-forward references; `review_bundle_from_stage_id` is accepted only as a legacy alias for `handoff_from_stage_id`, and naming both with different values is rejected (lines 239-244).
- Validates `workflow_mode` against stage count (`one_pass`=1, `two_pass`=2, `reviewed_three_stage`=3, `custom_ordered`=any; lines 402-407).
- Parses `defaults.review` and per-stage `review` into `ReviewConfig`, rejecting unknown reviewers.
- Normalizes legacy tool profiles (e.g. `web_search_preview` → `web_search`).

### 3.3 Attachment Pipeline: `attachments.py`

This is where the model actually sees its inputs. Per stage:

1. **Resolve** — read the static `input_manifest.json`, walk directories (skipping `.git`/`.local`/`node_modules`/...), apply `exclude_globs`, hash every file.
2. **Wrap** — files whose extension the API does not accept but that are UTF-8 text get a markdown wrapper (`build_context_wrapper`, line 174) with the source path in front matter and the body in a fenced block. This is what lets `.go`, `.rs`, `.toml`, `.tsx` files travel.
3. **Render** — produce `input_manifest.md`, the human-and-model-readable enumeration of every attached file with its role and short SHA256 prefix.
4. **Upload** — push every file (including the manifest itself) via the multipart `/files` endpoint.
5. **Build content blocks** — `build_request_input_content` (line 952) emits role-labeled text+file pairs in fixed order: manifest → primary → reviewed handoff → repository files → reference context.

> **Insight**
> - The 50 MB request budget and 50 MB per-file cap (contracts.py:25-26) are checked during resolution, so a misconfigured manifest fails before any HTTP traffic.
> - `input_manifest.md` carries SHA256 prefixes for each file. The model can be asked to cite them, and the operator can verify by recomputing.

### 3.4 The Core: `workflow.py`

`run_workflow()` (line 1486) is the orchestrator. Per invocation:

```
load_workflow_definition + validate operator inputs
        ↓
load/create run_manifest.json
        ↓
apply --handoff-note if given            (_apply_handoff_note, line 776)
apply any pending reviewed-gate verdict  (_stage_review_pending, line 633)
        ↓
_determine_next_stage (line 215)         ← respects human/blocked gates
        ↓
┌─────────────── stage loop ─────────────────────────┐
│ 1. allocate attempt_NNN (revision_of_attempt_id     │
│    set when the stage is revision_requested)        │
│ 2. resolve attachments + render manifest            │
│ 3. stage upload copies under upload_inputs/ and     │
│    build the request payload                        │
│    (DRY-RUN writes request_payload.json and moves   │
│     to the next stage here)                         │
│ 4. upload files                                     │
│ 5. exact token preflight                            │
│ 6. POST /responses → response.latest.json           │
│ 7. if waiting: poll until terminal                  │
│ 8. finalize: artifact.md, response.final.json,      │
│    structured output, advisory validators           │
│ 9. if gate=reviewed: _apply_stage_gate (line 685)   │
│10. revision_requested → loop same stage             │
│    completed + auto/reviewed + next → next stage    │
│    anything else → return                           │
└─────────────────────────────────────────────────────┘
```

Key behaviors:

- **Dry-run renders every stage.** `run --dry-run` without `--stage` walks the whole workflow under `<run_dir>/dry_runs/`, writing `input_manifest.{json,md}`, `request_payload.json`, and the `upload_inputs/` staging directory per stage (lines 1569-1571 and 1679-1700). Handoffs from stages that have not run yet are satisfied by placeholder files under `dry_runs/stubs/<stage_id>/` (`_dry_run_stub`, line 447). This is the whole pre-launch check.
- **Token preflight.** The exact count (`POST /responses/input_tokens`, with the payload projected onto the fields that endpoint accepts, openai_client.py:36) is the only token check and fails closed when the count exceeds `max_input_tokens` or the model context window minus the requested output and a safety margin (`_token_preflight_state`, line 899), leaving the stage `blocked_preflight`. There is no local estimate. `--skip-token-count` disables the exact count.
- **Validators are advisory.** Post-output validators (`evidence_references_v1` and friends) write `validator_report.json` and set `validators_passed` / `validator_report_path` on the stage summary; a failed validator is a `WARNING`, never a block (`_run_stage_validators`, lines 1342-1383).
- **Auto-progression is gated** (lines 2003-2013): the engine only loops to the next stage when the stage completed, has a next stage, its gate is `auto` or `reviewed` (and approved), the invocation is waiting, and the operator did not pin `--stage`.
- **Crash recovery is driven by the run manifest.** Every attempt's `local_state` and `response_id` live in `run_manifest.json`, persisted by `_persist_stage_state` (line 1269) before and after each step. `resume` (`resume_stage`, line 2075) rehydrates the stored `response_id`, polls to terminal, finalizes, and applies a pending reviewed gate; `refresh` is the same path with `refresh_status_only`, which polls and records the remote status but never finalizes. `_determine_next_stage` refuses `run` on any live or uncertain stage (`LIVE_OR_UNCERTAIN_STAGE_STATES`, line 68). An ambiguous `POST /responses` failure (lines 1882-1911) writes `submission.error.json` and sets `submission_outcome_unknown`, a state with no outgoing transition: `run`, `resume`, `refresh` (line 2116), and `cancel` (line 2251) all refuse it, so the operator reconciles against the remote side and starts a new run. A `failed_no_artifact` or `blocked_preflight` stage may be rerun in place with `run --stage <id>` (`RERUNNABLE_STAGE_STATES`, line 67).
- **One durable record.** `run_manifest.json` (schema `run_manifest.v2.schema.json`) is the single durable record, rewritten atomically on every stage transition after `assert_stage_transition` accepts the move; the slimming is done. Everything else under an attempt directory is evidence, not state.

### 3.5 OpenAI Client: `openai_client.py`

A standard-library HTTP client (`urllib`); `jsonschema` is the one third-party dependency, used for contract validation. Retries with capped exponential backoff (30 s, line 99) apply only to safe methods (`GET`/`HEAD`/`OPTIONS`, line 25) on `{408, 409, 429, 500, 502, 503, 504}`; a `POST /responses` is never retried automatically, because a duplicate submission would be worse than an unknown outcome. `wait_for_terminal_response` (line 342) writes `response.latest.json` on every poll.

### 3.6 Artifacts: `artifacts.py`

Owns the on-disk run layout (`build_stage_paths`, line 63): `runs/{ts}_{run_name}_{workflow_id}/stages/NN_stage_id/attempt_NNN/`, the run manifest, and response artifacts. `artifact.md` is the clean assistant deliverable used downstream; `response.final.json` is the raw retained response; `output.structured.json` is written when the stage configures a structured output schema; `review/` holds reviewer evidence.

---

## 4. The Reviewed Gate

The `reviewed` gate replaces the former multi-agent supervisor lane with one reviewer, one verdict, and at most one revision. (The supervisor lane was removed because, on the real supervised run, 282 reviewer-agent minutes against 32 minutes of primary model time never changed the primary output; see AGENTS.md:54.)

### 4.1 Reviewer: `reviewer.py`

`_apply_stage_gate` (workflow.py:685) runs when a `reviewed` stage reaches `completed` and its `review_status` is not already `approved`, `human_approved`, or `not_required`.

1. `build_review_job` composes the bounded input: workflow/run/stage/attempt ids, the stage task text, and the root-relative paths of `artifact.md`, `input_manifest.md`, and the handoff inputs (`_reviewed_handoff_paths`, workflow.py:641). The reviewer reads only those files.
2. `compose_prompt` prepends `prompts/stage_review.md`; the prompt and the job are written under the attempt's `review/` directory as `prompt_<stamp>.md`.
3. `build_command` picks the CLI:
   - **codex**: `codex exec --sandbox read-only --ephemeral --ignore-user-config -c model_reasoning_effort="<effort>" --output-schema <stage_review_verdict.schema.json> [--model <model>] -` with the prompt on stdin.
   - **claude**: `claude -p --model <model|opus> --effort <effort> --output-format json --tools Read,Grep,Glob --permission-mode dontAsk --no-session-persistence --setting-sources user --append-system-prompt-file <prompt>` with the job on stdin and the API-key environment variables (`CLAUDE_ENV_UNSET`) stripped so subscription login is used.
   - **none**: no CLI; the engine writes an `approve` verdict with `disposition: not_required`.
4. `run_review` executes the command with `timeout_seconds`, writes `stdout_<stamp>.txt`, `stderr_<stamp>.txt`, and `invocation_<stamp>.json` (argv, duration, exit code, artifact SHA256, and cost/token fields when the CLI reports them), then `extract_verdict` unwraps CLI envelopes and `normalize_verdict` coerces synonyms (`approved`, `reject`, `blocked`, ...) onto the contract and validates it.
5. On success it writes `review/verdict.json` and `review/reviewer_notes.md`. A non-zero exit raises `ReviewError`; the stage stays `completed` with its review pending and the next `run` (or `resume`) retries the review.

The verdict contract (`schemas/stage_review_verdict.schema.json`) is deliberately small:

```json
{"verdict": "approve|revise", "summary": "...",
 "blocking_findings": [{"id", "description", "evidence", "required_change"}],
 "notes": ["..."]}
```

### 4.2 Verdict to stage status

Back in `_apply_stage_gate` (workflow.py:757-762):

| Verdict | Revisions so far vs `max_revisions` | `review_status` | Stage status |
|---|---|---|---|
| approve | any | `approved` | `completed` → run continues |
| revise | below limit | `revision_requested` | `revision_requested` → same stage reruns |
| revise | at limit | `blocked` | `waiting_for_review` → needs `--handoff-note` |

The disposition is stamped into `verdict.json` and the stage summary gains `review_verdict_path` and `reviewer_notes_path`.

### 4.3 The revision attempt

When the loop re-enters a `revision_requested` stage, the new attempt records `revision_of_attempt_id` (workflow.py:1585-1602). Two hooks change what the model sees:

- `_stage_task_text` (workflow.py:475) prefixes `REVISION_INSTRUCTIONS` to the stage task: resolve every blocking finding or argue against it with evidence, preserve untouched parts, output the full artifact.
- `_revision_handoff_entries` (workflow.py:489) attaches the prior attempt's `review/reviewer_notes.md` and `artifact.md` under **Reviewed Handoff Inputs**.

The reviewer runs again on the revised artifact with `revision_of_attempt_id` in its job.

### 4.4 Handoff to the next stage

`_gate_handoff_entries` (workflow.py:533) builds the Reviewed Handoff Inputs for a stage whose `carry_forward.handoff_from_stage_id` names an earlier stage:

- source gate `human`: requires `waiting_for_review` plus a recorded `handoff_note_path`; attaches the note and the artifact.
- source gate `reviewed`: attaches the reviewer notes and the artifact when `review_status` is `approved`; attaches the human note plus reviewer notes when a human unblocked it; attaches only the artifact for `not_required`; refuses to start while the review is pending or blocked.
- any other source gate: `SystemExit` — a handoff source must be `reviewed` or `human`.

In dry-run, missing sources are replaced by stubs under `dry_runs/stubs/`.

`_apply_handoff_note` (workflow.py:776) is the human side: given `--handoff-note <path>`, it finds the first `human` or `reviewed` stage in `waiting_for_review`, records `handoff_note_path`, and sets `review_status: human_approved`. `_determine_next_stage` then lets the following stage run.

`reference_context_from_stage_ids` (`_reference_context_from_stage_outputs`, workflow.py:407) is the cheaper, lower-authority carry-forward: it attaches earlier `artifact.md` files under **Reference Context** with no approval requirement.

> **Insight**
> - The reviewer is read-only by construction (`--sandbox read-only` for Codex; `Read,Grep,Glob` only for Claude) and returns a verdict, not a patch. The primary model does the rewriting, with the findings attached as evidence it must answer.
> - Two `revise` verdicts stop the run rather than looping. The human note that unblocks the stage is itself attached downstream, so the override is visible in the next stage's input manifest.

---

## 5. End-to-End Walkthrough

### Path A: one-pass run

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json
```

1. CLI parses args → resolves root → builds `RuntimeOptions` → instantiates `OpenAIClient.from_env(root)`.
2. `run_workflow()` loads the workflow (schema version, model caps), creates `runs/{ts}_..._{workflow_id}/`, writes `run_manifest.json` with `status="created"`.
3. `_determine_next_stage()` finds stage 1 in `prepared`.
4. Attachments are resolved and hashed; `input_manifest.{json,md}` are written and upload copies are staged under `upload_inputs/`.
5. Files are uploaded; exact token preflight runs.
6. The stage is persisted as `submitting` with the request's SHA-256, then one `POST /responses`. The response id is persisted immediately; an ambiguous POST failure becomes `submission_outcome_unknown` (with `submission.error.json`), never an automatic retry.
7. The run waits, rewriting `response.latest.json` on each poll.
8. Once terminal: `artifact.md`, `response.final.json`, structured output (when configured), and advisory validators are finalized before the final status is published.
9. The stage is `terminal`, so the run completes and the CLI prints the run manifest path.

### Path B: reviewed multi-stage run in one invocation

The real pack `automation/task_packs/gstack_design_to_po_playbook` has five stages: stages 1-4 are `reviewed`, each with `handoff_from_stage_id` pointing at the previous stage, and stage 5 is `terminal`. `defaults.review` is `{reviewer: codex, max_revisions: 1}`.

```bash
# pre-launch: render every stage's request with placeholders for later handoffs
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/runbooks/first-use-adaptation-example.md \
  --dry-run

# launch; waits in-process through every gate
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/runbooks/first-use-adaptation-example.md
```

What the single `run` does:

1. Stage 1 (`source_authority_map`) runs as in Path A and completes.
2. `_apply_stage_gate` invokes `codex exec` once on `artifact.md`. On `approve`, `review_status: approved` and the loop advances.
3. Stage 2 (`repo_grounding`) starts with stage 1's artifact and reviewer notes under Reviewed Handoff Inputs and stage 1's artifact again under Reference Context.
4. Suppose the stage 2 reviewer returns `revise`. The stage becomes `revision_requested`; the loop allocates `attempt_002` with `revision_of_attempt_id: attempt_001`, prefixes `REVISION_INSTRUCTIONS`, attaches `attempt_001/review/reviewer_notes.md` and `attempt_001/artifact.md`, and submits again. The reviewer runs on the revision.
5. On `approve`, stages 3 and 4 follow the same pattern. If any reviewer returns `revise` a second time for the same stage, the run stops with `review_status: blocked`; the operator reads the notes and continues with:

   ```bash
   python automation/run_responses_v2.py run --root . \
     --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
     --run-dir <run_dir> --handoff-note <note.md>
   ```

6. Stage 5 (`final_markdown_playbook`) is `terminal`; its `artifact.md` is the deliverable and the run manifest reports `completed`.

If the process dies after a stage completes but before its review is applied, the next `run` (or `resume --stage <id>`) applies the pending review first, then continues.

> **Insight**
> - The chain of custody is short: `workflow_manifest_sha256` → run manifest stage summary records `artifact_markdown_sha256` → `review/verdict.json` records `artifact_sha256` → the next stage's `input_manifest.md` lists the same file with its hash prefix.
> - Carry-forward is deliberately cheap: it attaches the prior stage's actual `artifact.md` (and the reviewer's or human's notes). No prompt-stuffing, no summarization.

---

## 6. Run Output Layout

```
.local/automation/responses_runner_v2/runs/{ts}_{run_name}_{workflow_id}/
├── run_manifest.json                        ← top-level state, stage summaries
├── dry_runs/                                ← --dry-run renders (+ stubs/<stage_id>/)
└── stages/{NN_stage_id}/
    └── attempt_NNN/
        ├── input_manifest.{json,md}         ← resolved attachments + role labels
        ├── upload_inputs/                   ← staged copies of the files prepared for upload
        ├── request_payload.json             ← exact body sent to /responses
        ├── token_preflight.{json|error.json}
        ├── uploads.json                     ← file_id ↔ source_path mapping
        ├── response.latest.json             ← updated on each poll
        ├── response.final.json              ← raw retained terminal response
        ├── artifact.md                      ← clean downstream deliverable
        ├── validator_report.json            ← advisory validator results (if configured)
        ├── output.structured.json           ← if a structured output schema is configured
        ├── submission.error.json            ← only if POST /responses failed
        ├── finalization.error.json          ← only if finalization raised
        └── review/                          ← reviewed gates only
            ├── verdict.json
            ├── reviewer_notes.md
            ├── prompt_<stamp>.md
            ├── stdout_<stamp>.txt / stderr_<stamp>.txt
            └── invocation_<stamp>.json
```

The `.local/` prefix is in `.gitignore` and `DIRECTORY_SKIP_NAMES` (contracts.py:59): these artifacts never get committed and are never re-attached as evidence to a future run. Stage state (`status`, `current_attempt_id`, each attempt's `local_state` and `response_id`) lives only in `run_manifest.json`.

---

## 7. Tests and Validation

The `automation/tests/` suite has 12 modules (105 tests): attachments, audit-gap closure, context quality, contracts, durability, eval harness, evidence-synthesis example, example pack, gstack pack, model migration, reviewed gates, workflow. Standard run:

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
```

CI (`.github/workflows/ci.yml`) runs the suite on Python 3.10/3.11/3.12, dry-runs the synthetic one-pass pack, and dry-runs every stage of the gstack playbook pack with `--primary-job-input docs/runbooks/first-use-adaptation-example.md`.

---

## 8. The Big Themes (what to internalize)

1. **One workspace root, mechanically enforced.** Not a convention: `resolve_under_root` `SystemExit`s.
2. **Schema versions are wire protocols.** Every persisted manifest carries `responses_runner_v2.<name>.vN` and is validated against the schema that version names.
3. **Authority Order is data, not docs.** Encoded as constants, iterated by `attachments.py`, baked into every request.
4. **Background responses shape the engine.** The single durable run manifest, `resume`/`refresh`, and the no-duplicate-submit guard exist because a response can outlive the local process.
5. **One invocation, many stages.** `run` waits and chains through `auto` and `reviewed` gates; only a `human` gate, a blocked review, the terminal stage, or an error hands control back.
6. **One reviewer, one verdict, one revision.** The reviewer is read-only and returns findings; the primary model does the revising with those findings attached; a second `revise` stops for a human.
7. **Human overrides are evidence too.** A `--handoff-note` is recorded on the stage and attached to the next stage's inputs.
8. **The exact token count is the gate; everything else is a warning.** There is no local estimate, and validators inform, they do not block.
9. **Dry-run is the pre-launch check.** One `--dry-run` renders every stage's request, with stubs standing in for unrun handoffs.
10. **Stdlib HTTP transport.** `urllib` only; `jsonschema` is the single required dependency.
11. **Locked model posture.** Durable `gpt-5.6`; primary `reasoning.mode=pro` at xhigh, structural standard mode at high or medium; implicit 30-minute cache TTL; 128000 max output. Verbosity and high/xhigh changes remain measurement-gated.
