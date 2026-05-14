# Stage Workflow Runner — Operator Onboarding Guide

> **Who this is for:** a new teammate who will *operate* the Stage Workflow Runner —
> launch workflows, supervise them, review stage outputs, recover from failures, and
> assemble final deliverables. You do not need to modify the engine to operate it.
> You *do* need to understand what every command does, what every artifact means,
> and which guardrails will stop you (on purpose) when something is off.
>
> **How to read this:** Sections 1–4 are the mental model — read them once, slowly.
> Sections 5–10 are the working reference — skim now, return when operating.
> Sections 11–13 are playbooks — use them at the keyboard.

---

## Table of contents

1. [The one-sentence model](#1-the-one-sentence-model)
2. [First principles (everything derives from these)](#2-first-principles)
3. [The two-lane architecture](#3-the-two-lane-architecture)
4. [Core vocabulary](#4-core-vocabulary)
5. [The engine lane in depth](#5-the-engine-lane-in-depth)
6. [Anatomy of a single stage](#6-anatomy-of-a-single-stage)
7. [What happens *before* the first stage](#7-what-happens-before-the-first-stage)
8. [The supervisor lane in depth](#8-the-supervisor-lane-in-depth)
9. [The workflows: modes and packs](#9-the-workflows-modes-and-packs)
10. [The run output layout (your evidence trail)](#10-the-run-output-layout)
11. [End-to-end walkthroughs](#11-end-to-end-walkthroughs)
12. [Command reference](#12-command-reference)
13. [Operator playbooks](#13-operator-playbooks)
14. [Guardrails you cannot bypass](#14-guardrails-you-cannot-bypass)
15. [Where to look when something is wrong](#15-where-to-look-when-something-is-wrong)
16. [Reading order & next steps](#16-reading-order--next-steps)

---

## 1. The one-sentence model

**The Stage Workflow Runner is a manifest-driven runner for high-stakes, staged OpenAI
Responses workflows that treats every artifact as evidence — schema-versioned, hashed,
and confined to a single workspace root — so that a `tar` of that root is a complete,
replayable audit trail.**

Read that sentence again. Three load-bearing phrases:

- **"high-stakes"** — the entire design assumes an answer could be wrong in a way that
  costs money or trust. So every claim must trace back to a specific attached file with a
  known hash. The runner is optimized for *chain-of-custody*, not throughput.
- **"staged"** — work is broken into ordered **stages**. Between stages there can be
  **review gates**: a stage's output is not allowed to flow into the next stage until it
  has been reviewed and packaged into an approved **review bundle**.
- **"manifest-driven"** — you do *not* write Python to define a workflow. You write JSON
  **manifests** and Markdown **prompts** in a **task pack**. The engine is generic; the
  task pack is the workflow.

```
   A TASK PACK (JSON + Markdown, no code)        THE ENGINE (generic Python)
   ├── workflows/*.workflow.json   ───────┐
   ├── inputs/*.input_manifest.json       │
   ├── prompts/*.md                       ├──►  reads the manifests, talks to OpenAI,
   ├── tools/*.profile.json                │     writes durable hashed artifacts
   ├── schemas/*.schema.json              │
   └── shared_instructions.md     ────────┘
```

---

## 2. First principles

Everything else in this guide is a consequence of these six ideas. If you internalize
only one section, make it this one.

### 2.1 One workspace root, mechanically enforced

Every invocation operates against **one exact workspace root**. Workflow files, task-pack
assets, uploaded attachments, run outputs, supervisor sessions, archives, and review
bundles must *all* live under that one root.

Root resolution order (the same everywhere):

1. explicit `--root`
2. the `RESPONSES_RUNNER_V2_ROOT` environment variable
3. the current working directory, used as-is

This is not a convention you are trusted to follow — it is **mechanical**. Every path the
runner touches passes through one function, `resolve_under_root()`. It calls
`path.resolve()` (which follows symlinks) and then `.relative_to(root)`. If the path lands
outside the root, the process exits. A symlink trick cannot sneak a file in or out.

> **Why it matters to you:** if you ever see `Path must stay under workspace root: ...`,
> you pointed something outside the root. The fix is never "force it" — it is to move the
> asset under the root or correct the `--root` you passed.

### 2.2 Every artifact is evidence

Every file the runner writes is:

- **schema-versioned** — it carries a string like `responses_runner_v2.run_manifest.v1`.
  Versions are treated like wire protocols, so a v2 engine can coexist with v1 artifacts
  without silent corruption.
- **hashed** — SHA-256 of files and of payloads. The run manifest records the workflow
  manifest's hash. Review bundles record hashes of every file they reference. Stage
  summaries record hashes of their response artifacts.
- **confined** — written only under the workspace root, under `.local/...`, which is
  git-ignored and never re-attached to a future run as evidence.

The payoff: claims are back-traceable. The rendered `input_manifest.md` lists a short
SHA-256 for every attached file. The model can be asked to cite those paths; you can
recompute the hash to verify it cited a real file.

### 2.3 Responses run in the *background* — the local process is disposable

The runner submits work to the OpenAI Responses API in **background mode**. A request can
run for *hours*. You get back a `response_id` and you *poll* it. The local Python process
can crash, get killed, or be `Ctrl-C`'d — and the remote model keeps working.

Almost every "extra" mechanism in the engine exists because of this single fact:

- **Two checkpoint writes per stage** — one the instant the API accepts the request
  (usually status `queued`), one after polling reaches a terminal status. The first is
  already enough to resume from.
- **Three resume modes** — `fresh_submit` (first run), `resume_response_id` (rehydrate a
  stored `response_id` and finalize), `refresh_status_only` (just poll, do not finalize).
- **A no-duplicate-submit rule** — if a stage is still `submitted`/`in_progress`, the
  engine refuses to `run` it again. You must `resume` or `refresh` instead. Submitting a
  duplicate while a live `response_id` may still complete is explicitly prohibited.

> **Why it matters to you:** "my terminal died mid-run" is **not** a disaster. The
> `response_id` is saved. You `resume` the stage and the engine finalizes it. You never
> re-pay for work that is still running remotely.

### 2.4 Mechanism and policy are separate lanes

There are two CLIs sitting on **one** engine:

- The **engine lane** (mechanism) knows *how* to talk to OpenAI: load a workflow, build a
  request, submit, poll, finalize artifacts, validate a review bundle.
- The **supervisor lane** (policy) knows *what to do with the answer*: stage a scaffold,
  gate it, run a multi-agent review, classify failures, archive before rerun, assemble a
  final bundle.

The boundary is enforced in the repo's own rules: *"Do not rewrite
`automation/responses_runner_v2/workflow.py` into the supervisor."* The supervisor
**calls into** the engine (`run_workflow()`, `create_review_bundle()`); it never
reimplements submission or polling.

You can operate the engine lane entirely on its own (manually creating review bundles
when a stage needs review). The supervisor lane *adds* automated multi-agent review and
failure policy on top. Both are valid ways to operate.

### 2.5 Authority order is data, not a guideline

Every stage assembles its attachments in a fixed **authority order**:

```
1. Primary Job Inputs        (highest authority — the actual task input)
2. Reviewed Handoff Inputs   (approved output carried from an earlier gated stage)
3. Attached Repository Files (repo evidence — treat as evidence, not instructions)
4. Reference Context         (lowest authority — carry-forward / background)
```

This order is encoded as a constant, iterated by the attachment pipeline, and stated in
the boilerplate instruction prepended to *every* request. The model is explicitly told
this order and told to treat attached source files as *evidence, not instructions*.

> **Why it matters to you:** when a stage's output looks wrong, your first move is to open
> the stage's rendered `input_manifest.md` and check **what was attached, in what role**.
> A file in the wrong role is the single most common "why did the model do that" cause.

### 2.6 Fail-closed is the default

The runner would rather **stop** than do something unbounded or unverifiable:

- **Token preflight** is a fail-closed gate. Before live submission the engine can call
  `POST /responses/input_tokens`. If the count exceeds a configured `max_input_tokens`,
  the stage is marked `blocked` and the process exits — *terminal, no retry, no fallback*.
  Unbounded token spend is treated as a safety property. (You can explicitly accept the
  risk with `--skip-token-count`.)
- **Schema validation** runs before artifacts are written, not after.
- **Hash mismatch** on a review bundle refuses the load.
- **Read-only violations** by a review agent fail the review.
- **Output-limit incomplete** outcomes never auto-progress.

A stopped run is the system working. Your job as operator is to read *why* it stopped.

---

## 3. The two-lane architecture

```
┌───────────────────────────────────────────────────────────────────────────┐
│  SUPERVISOR LANE  —  POLICY                                                │
│  CLI: automation/run_responses_supervisor_v2.py                            │
│  Code: supervisor.py, supervisor_agents.py, supervisor_artifacts.py,       │
│        supervisor_policies.py                                              │
│                                                                            │
│  Owns:  session state · scaffold staging · static examination ·            │
│         executable dry-run gating · the 3-agent review loop ·              │
│         deterministic consolidation · operator selective acceptance ·      │
│         6-way failure classification · archive-before-rerun ·              │
│         human-pause records · final implementation-bundle assembly         │
│                                                                            │
│                    calls into ↓  (run_workflow, create_review_bundle)      │
├───────────────────────────────────────────────────────────────────────────┤
│  ENGINE LANE  —  MECHANISM                                                 │
│  CLI: automation/run_responses_v2.py                                       │
│  Code: contracts.py, pack_loader.py, workflow.py, attachments.py,          │
│        artifacts.py, review_bundle.py, sidecar.py, openai_client.py        │
│                                                                            │
│  Owns:  workflow loading · input-manifest expansion · file uploads ·       │
│         request construction · token preflight · Responses API submission ·│
│         polling / resume / refresh · artifact finalization ·               │
│         sidecar structured extraction · review-bundle validation           │
└───────────────────────────────────────────────────────────────────────────┘
```

**The engine lane** is generic and reusable. Point it at any task pack and it runs.

**The supervisor lane** is *additive*. It does not replace the engine — it wraps it with
the policy you need when failure is messy or review is mandatory. When the supervisor
needs a stage executed, it calls the engine's `run_workflow()`. When it needs a review
bundle, it calls the engine's `create_review_bundle()`.

There are **four** CLI entry points (also installed as console scripts via
`pyproject.toml`):

| Script file | Console script | Purpose |
|---|---|---|
| `automation/run_responses_v2.py` | `staged-workflow-run` | Generic engine: run / resume / refresh a stage |
| `automation/run_responses_supervisor_v2.py` | `staged-workflow-supervisor` | Supervisor: session, scaffold, review loop, classify, finalize |
| `automation/create_review_bundle_v2.py` | `staged-workflow-create-bundle` | Build an approved review bundle by hand |
| `automation/run_responses_v2_eval.py` | `staged-workflow-eval` | Score artifacts against an eval dataset; check a freeze gate |

---

## 4. Core vocabulary

Learn these twelve terms. The rest of the guide uses them constantly.

| Term | What it is |
|---|---|
| **Task pack** | A directory of JSON + Markdown that *defines a workflow*: workflow manifest, per-stage input manifests, prompts, tool profiles, output schemas, shared instructions. No Python. |
| **Workflow manifest** | The `*.workflow.json` file. Declares workflow id, mode, model roles, request defaults, the ordered list of stages, each stage's gate, carry-forward rules, and optional sidecar extraction. |
| **Stage** | One unit of work = one Responses API call (plus an optional sidecar call). Stages are ordered by `stage_number`. |
| **Gate** | A stage's progression rule: `auto` (engine may continue to the next stage), `review_required` (stop — output must be reviewed and bundled before the next stage), `terminal` (last stage). |
| **Input manifest** | The per-stage `*.input_manifest.json`. Declares the *static* attachment set for the stage, grouped into the four authority roles. The engine merges it with operator-supplied inputs and carry-forward artifacts at runtime, then renders a human-readable `input_manifest.md`. |
| **Review bundle** | The approval contract passed *between* gated stages. A JSON file that references the approved markdown, the raw response JSON, reviewer notes (and optionally an approved handoff markdown + structured JSON) — each with its SHA-256. Loading it re-hashes everything and refuses on mismatch. |
| **Run manifest** | `run_manifest.json` — the top-level durable state of one run: the workflow id, the workflow manifest's hash, the ordered stage list, and a per-stage summary. |
| **Stage checkpoint** | `stage_checkpoint.json` — the durable, resumable state of one stage: status, the `response_id`, token-preflight result, and pointers to every artifact. Written twice per stage. |
| **Sidecar** | An optional *second* Responses call that runs after a stage completes. It re-reads the stage's markdown output and extracts a strict-JSON-schema payload using the structural model. Keeps "creative generation" and "machine-ingestible structure" as separate passes. |
| **Supervisor session** | The supervisor's single source of truth: a schema-validated `supervisor_session.json` plus a directory of review cycles, scaffolds, archives, monitoring records, and the final bundle. |
| **Scaffold** | A task pack staged *into* a supervisor session and hashed, so the supervisor can gate it (examine, dry-run, review) before any live stage runs. |
| **Review cycle** | One pass of the supervisor's review loop on a scaffold, a stage output, a final packet, or a recovery path: operator → 2 independent reviewers → consolidation → operator acceptance. |

---

## 5. The engine lane in depth

Eight modules. You will rarely edit them, but you must know what each *produces* because
that is what you read when operating.

```
contracts.py ──────► the type system + invariants. Schema versions, AUTHORITY_ORDER,
                     MODEL_CAPS, the enums (GateType, StageStatus, ResumeMode...),
                     repo_root(), resolve_under_root(), validate_model_options().
                     Everything imports this.

pack_loader.py ────► turns workflow/input-manifest JSON into validated dataclasses with
                     resolved, root-confined paths. Catches misconfiguration at LOAD time:
                     bad model posture, background+no-store, duplicate stage ids,
                     mis-ordered stages, dangling carry-forward references, mode↔count
                     mismatch. Also normalizes tool profiles.

workflow.py ───────► THE orchestrator (~1350 lines). run_workflow() / resume_stage() /
                     refresh_stage(). The stage state machine lives here.

attachments.py ────► resolves files+directories into a concrete attachment list, hashes
                     each file, wraps unsupported text files as Markdown, enforces the
                     50MB limits and the 100-file cap, renders input_manifest.md,
                     uploads everything, builds the role-labeled request content.

artifacts.py ──────► the on-disk run layout. create_run_dir(), build_stage_paths(),
                     run-manifest + checkpoint writers, and the response.final.md
                     renderer (token usage, sources, tool calls, uploads).

review_bundle.py ──► the handoff contract. create_review_bundle(), load_review_bundle()
                     (re-hashes everything), validate_review_bundle_for_stage()
                     (cross-checks against the source stage's recorded artifacts).

sidecar.py ────────► the framework-owned structured-extraction pass. Uploads the primary
                     markdown + raw JSON, runs the structural model against a strict
                     JSON schema, writes output.structured.json + sidecar.response.*.
                     Has its own retry logic for output-limit and transient errors.

openai_client.py ──► a ~240-line, STANDARD-LIBRARY-ONLY HTTP client (urllib, not requests
                     or httpx). Hand-rolled multipart upload. Retries 408/409/429/5xx with
                     capped exponential backoff. The engine has zero third-party runtime
                     dependencies.
```

### The engine's stage state machine

```
load_workflow_definition          ← validates schema_version + model caps at load time
        │
        ▼
load or create run_manifest.json  ← one per run directory
        │
        ▼
_determine_next_stage             ← picks the first prepared/blocked stage whose
        │                            predecessor is completed OR has an approved
        │                            review handoff. Refuses to run a nonterminal stage.
        ▼
┌───────────────── per-stage loop ──────────────────────────────────────┐
│ 1. resolve attachments  (static manifest + operator inputs +          │
│                          review-bundle inputs + carry-forward)        │
│ 2. render + write input_manifest.{json,md}                            │
│ 3. write request_payload.json        ◄── --dry-run RETURNS HERE       │
│ 4. upload every file (input_manifest.md uploaded first)               │
│ 5. token preflight  (fail-closed gate, unless --skip-token-count)     │
│ 6. POST /responses  →  write response.latest.json                     │
│ 7. write stage_checkpoint #1   (status usually "queued")              │
│ 8. if --wait: poll until terminal, rewriting response.latest.json     │
│ 9. on terminal: write response.final.{json,md}                        │
│10. extract structured output (if json_schema) / run sidecar (if any)  │
│11. write stage_checkpoint #2   (final status) + update run_manifest   │
│12. if gate=auto AND --wait AND no pinned --stage AND has next stage:  │
│       loop to the next stage                                          │
└────────────────────────────────────────────────────────────────────────┘
```

A few behaviors worth memorizing as an operator:

- **`--dry-run` stops at step 3.** It builds and writes the exact request payload but
  uploads nothing and calls no API. It is your cheapest validation: it proves the workflow
  loads, the manifests resolve, and the request is well-formed.
- **Auto-progression needs *all five*:** the stage completed, there is a next stage, the
  stage's gate is `auto`, you passed `--wait`, and you did **not** pin a specific
  `--stage`. Anything else stops the run after one stage.
- **A `review_required` stage that has a next stage ends in status
  `waiting_for_review`** — not `completed`. The run status becomes `waiting_for_review`.
  The next `run` of that workflow will refuse to proceed until you supply
  `--review-bundle`.

---

## 6. Anatomy of a single stage

Zoom into one stage. This is what every stage does, generic-engine or supervisor-driven.

### 6.1 Building the request

The request that goes to OpenAI is assembled from three text pieces plus role-labeled
file attachments:

```
instructions  =  COMMON_RUNNER_INSTRUCTIONS      (the authority-order boilerplate)
              +  shared_instructions.md           (task-pack-wide instructions)
              +  stage_instructions.md            (optional, per stage)

input content =  [ stage task prompt text ]
              +  [ "Attachment role: Stage Input Manifest"      → input_manifest.md ]
              +  [ "Attachment role: Primary Job Inputs"        → files... ]
              +  [ "Attachment role: Reviewed Handoff Inputs"   → files... ]
              +  [ "Attachment role: Attached Repository Files" → files... ]
              +  [ "Attachment role: Reference Context"         → files... ]
```

The content blocks are emitted in **authority order**, each preceded by a text label that
tells the model what role the next files play. The `input_manifest.md` is uploaded
*first* and wired in as the manifest file — it is the model's table of contents.

### 6.2 The attachment pipeline

For each attachment entry the engine: **resolves** it (walks directories, skipping
`.git`, `.local`, `node_modules`, `__pycache__`, …; applies `exclude_globs`; hashes every
file), **wraps** it if needed (a `.go`/`.rs`/`.tsx`/`.sql` file is not a Responses-API
context type, so if it is UTF-8 text the engine generates a Markdown wrapper with the
source path in front matter and the body in a fenced block — *this is how the runner can
attach any text file*), **renders** `input_manifest.md`, **uploads** every file, and
**builds** the content blocks.

Limits enforced *during resolution* (before any HTTP traffic):

- 50 MB per single file
- 50 MB combined per request
- 100 attached files per request — if exceeded, the engine bundles a whole authority role
  into one deterministic Markdown bundle before giving up.

### 6.3 The two checkpoint writes

```
POST /responses returns  ──►  checkpoint #1   status: "queued" (background mode)
                                   │            ↑ already enough to resume from
        --wait polls...            │
   terminal status reached  ──►  checkpoint #2   status: "completed" / "failed" / ...
```

### 6.4 Stage status vs. response status

The OpenAI **response** has statuses: `queued`, `in_progress`, `completed`, `failed`,
`cancelled`, `incomplete`. The engine maps those to a **stage** status:

| Response status | Stage status | Note |
|---|---|---|
| `completed` (gate `review_required`, has next) | `waiting_for_review` | Output must be bundled before the next stage |
| `completed` (otherwise) | `completed` | |
| `failed` | `failed` | May still have a substantive artifact — see §8.4 |
| `cancelled` | `cancelled` | |
| `incomplete` | `incomplete` | Never auto-progresses |
| `queued` / `in_progress` | `in_progress` | Use `resume`/`refresh`, never re-`run` |

### 6.5 The sidecar pass

If a stage configures `output.sidecar`, then *after* the primary response is terminal the
engine runs a second Responses call: it uploads the primary's `response.final.md` and
`response.final.json`, asks the **structural model** (`gpt-5.5`) to produce a payload that
strictly conforms to the sidecar JSON schema, and writes `output.structured.json` plus
`sidecar.response.{json,md}`. Creative generation (primary) and machine-ingestible
structure (sidecar) are deliberately kept as separate passes so the primary model never
has to fight schema constraints while reasoning.

> **Operator note:** sidecar artifacts are only written on the *terminal-artifact path* —
> `run --wait` or `resume --wait`. If `run_manifest.json` shows a stage `completed` but
> `output.structured.json` / `sidecar.response.*` are missing, the stage was *refreshed*
> but not *finalized*. Run `resume` on that stage to backfill them.

---

## 7. What happens *before* the first stage

This is the part most newcomers miss: in the supervisor lane, **a great deal happens
before Stage 1 ever runs, and none of it touches OpenAI.** The pre-stage sequence exists
to make sure the workflow you are about to run live has been clarified, staged, statically
examined, dry-run, and reviewed.

```
                  ┌─────────────────────────────────────────────────┐
   STEP 0         │  HUMAN CLARIFICATION GATE                       │
   (human)        │  The ONLY mandatory human step.                 │
                  │  1. human supplies a task                       │
                  │  2. operator asks clarifying questions if needed│
                  │  3. human accepts a clarified_task_brief.md     │
                  └───────────────────────┬─────────────────────────┘
                                          │  clarified_task_brief.md (accepted)
                                          ▼
   STEP 1         init-session            Creates the supervisor session JSON.
   (supervisor)                           status: "clarified"  phase: "scaffold"
                                          Records the brief's path + SHA-256.
                                          ▼
   STEP 2         stage-scaffold          Copies the task pack INTO the session
   (supervisor)                           (scaffolds/scaffold_001/source/) and
                                          writes a hash_manifest.json of every file.
                                          status: "scaffold_staged"
                                          ▼
   STEP 3         examine-scaffold        STATIC pre-launch gate. Validates WITHOUT
   (supervisor)                           constructing a Stage 1 request:
                                            • workflow manifest schema
                                            • model posture matches the locked defaults
                                            • exactly one terminal stage, and it is last
                                            • every non-terminal stage is review-gated
                                            • sidecar/output schemas use no unsupported
                                              keywords (if/then/oneOf/allOf/not/...)
                                            • tool profiles + attachment inventory
                                            • each stage prompt is substantive
                                          Emits scaffold_examination.{json,md}.
                                          Blocking issues → session "blocked".
                                          ▼
   STEP 4         dry-run-scaffold        EXECUTABLE gate. Calls the engine's
   (supervisor)                           run_workflow(dry_run=True): the engine
                                          resolves inputs and WRITES a request payload,
                                          but never calls the API. Proves the request
                                          can actually be constructed.
                                          ▼
   STEP 5         SCAFFOLD REVIEW CYCLE   The full 3-agent loop, applied to the scaffold
   (supervisor)                           itself (review_kind = "scaffold"):
                                            invoke-operator → invoke-reviewers →
                                            consolidate → accept
                                          ▼
                  assert_scaffold_launch_allowed  ── passes ONLY when the latest scaffold
                                                     version is "accepted" AND an accepted
                                                     scaffold review cycle exists.
                                          ▼
   ════════════════ NOW, AND ONLY NOW, STAGE 1 MAY RUN LIVE ════════════════
```

So "between stages, prior to the first stage" the sequence is:
**clarification → init-session → stage-scaffold → examine-scaffold → dry-run-scaffold →
scaffold review cycle → launch-allowed.** Two of those steps are *gates* you can fail
(`examine-scaffold`, `dry-run-scaffold`) and one is a full review loop. The first thing
that ever reaches OpenAI is Stage 1's request — and by then the workflow has been
validated four different ways.

> **If you only operate the generic engine lane** (no supervisor), the pre-stage sequence
> collapses to: *make sure the task pack is under the root, then `run --dry-run`.* The
> dry-run is your single pre-flight check. Everything else above is supervisor policy.

---

## 8. The supervisor lane in depth

### 8.1 The supervisor session

A session lives at
`.local/automation/responses_runner_v2/supervisor_sessions/{session_id}/`:

```
supervisor_session.json   ← THE single source of truth, schema-validated on every write
commands/                 ← captured stdout/stderr from agent CLI invocations
review_cycles/{id}/        ← per-cycle operator + reviewer + consolidation + acceptance
scaffolds/{version}/       ← staged scaffold copies + hash_manifest.json
examinations/              ← scaffold_examination.{json,md}
dry_runs/                  ← scaffold dry-run validations
archives/{id}/             ← archived failed-no-artifact attempts (+ hashes)
monitoring/                ← polling state + monitoring-anomaly human pauses
human_pauses/              ← anomaly records that need a human decision
final_bundle/              ← the final implementation bundle
```

Every write to `supervisor_session.json` goes through one function that **validates the
payload against the session JSON schema, then atomically renames a temp file over it**.
The session is never half-written and never schema-invalid on disk.

### 8.2 The 3-agent review loop

For every scaffold and every non-terminal stage, three independent agents review the same
materials:

| Role | CLI invoked | Read-only? | Accountable? |
|---|---|---|---|
| **operator_codex** | `codex exec "<prompt + job JSON>"` | No — may apply changes | **Yes** — the accountable lane |
| **codex_review_agent** | `codex exec "<prompt + job JSON>"` | **Yes** — snapshot-enforced | No — advisory |
| **claude_review_agent** | `claude -p --model opus --effort max --output-format json --tools Read ...` | **Yes** — snapshot-enforced | No — advisory |

The loop, in order:

```
1. invoke-operator       The accountable operator Codex runs first and writes a
                         PROVISIONAL review decision. (For scaffold/stage_output/
                         final_packet/recovery cycles, this provisional record is
                         REQUIRED before reviewers may run.)

2. invoke-reviewers      The Codex reviewer and the Claude reviewer run INDEPENDENTLY
                         and READ-ONLY. The supervisor snapshots every workspace source
                         file (hashes) before and after each reviewer. Any change to a
                         file other than the agent's own output sidecars is a
                         "read_only_violation" and fails that review.

3. consolidate           Deterministic merge of operator + 2 reviewer findings.
                         De-duplicates recommendations by (text + affected artifacts).
                         ADVISORY ONLY — its next_action is "proceed_to_operator_
                         acceptance". It does not accept anything.

4. accept                The operator selects which recommendation IDs to accept and
                         supplies an applied-change-evidence JSON file. The supervisor
                         REWRITES any "accepted" recommendation to "rejected" unless its
                         evidence supplies concrete changes_applied[], validation_
                         evidence[], and operator_rationale. The supervisor does NOT
                         synthesize evidence.

5. create-bundle         An approved review bundle is created ONLY if the acceptance
                         record's approval_decision == "approve".
```

Two things to burn in:

- **The inter-agent protocol is one schema: `review_decision.v1`.** Every agent
  (operator, both reviewers, *and* the consolidation pass) emits the *same* JSON shape;
  `actor_role` distinguishes them. That uniformity is what makes the outputs comparable.
- **Read-only enforcement is mechanical.** It is a before/after hash snapshot of the
  whole workspace (excluding `.local` and the usual skip dirs). A reviewer that prompts
  itself into editing a file is caught automatically.

The Claude lane deliberately does **not** use `--bare`: bare mode skips OAuth/keychain
credentials. The supervisor strips `ANTHROPIC_API_KEY` and related provider env vars
before invoking `claude` so the local subscription OAuth login is used. If `--effort max`
is unsupported locally, it retries once with `--effort xhigh` and records
`fallback_used=true`.

### 8.3 Consolidation is advisory; acceptance is the real decision

This separation is the heart of the supervisor's integrity model:

```
consolidation  ──►  "here is every distinct recommendation, de-duplicated, with
                     provenance. I am NOT approving anything."

operator        ──►  "I accept rec_003 and rec_007. Here is the evidence file showing
acceptance           what I changed, how I validated it, and why."

supervisor      ──►  re-checks the evidence. rec_003's evidence is concrete → stays
(mechanical)         accepted. rec_007's evidence is missing changes_applied → REWRITTEN
                     to rejected. A rejected BLOCKING recommendation becomes a blocking
                     issue → approval_decision becomes "do_not_approve".
```

The operator cannot rubber-stamp. An accepted recommendation without
`changes_applied[]` + `validation_evidence[]` + `operator_rationale` is auto-rejected by
the supervisor itself.

### 8.4 The 6-way failure classification

When a stage reaches a terminal state, the supervisor's `classify` command inspects the
checkpoint + response markdown and assigns exactly one of six classifications. Each maps
to a distinct recovery path:

| Classification | Reviewable | Bundle-able | Rerun | Recovery action |
|---|:--:|:--:|:--:|---|
| `completed_complete_artifact` | ✓ | ✓ | — | `review` — the normal happy path |
| `failed_complete_artifact` | ✓ | ✓ | — | `review_failed_artifact` — model reported failure but produced substantive text; still reviewable |
| `failed_no_artifact` | ✗ | ✗ | ✓ | `archive_before_rerun` — must archive (with hashes) before any rerun |
| `incomplete_output_limit` | ✗ | ✗ | ✗ | `block_and_recover` — human pause; never auto-progresses |
| `blocked_token_preflight` | ✗ | ✗ | ✗ | `human_pause` — repair the scaffold/input set before relaunch |
| `long_running_monitoring_anomaly` | ✗ | ✗ | ✗ | `monitor_without_duplicate_submit` — refresh/resume the existing `response_id`, never re-submit |

The dividing line between the two `failed_*` outcomes is **"substantive markdown"**: the
response markdown must be at least 40 characters and not literally
`"No assistant text was returned."` A failed response *with* substantive text is still
reviewable; *without* it, you must archive before rerun.

### 8.5 Archive-before-rerun (the anti-"quietly changed the inputs" guard)

A `failed_no_artifact` stage may be rerun **only after archiving the attempt**. The
archive captures the full request payload, input manifests, checkpoint, the workflow
manifest's hash, the scaffold's hash-manifest digest, a deterministic `request_hash`, and
an `unchanged_input_evidence` block.

Before the rerun is allowed, the supervisor checks the *current* request and scaffold
hashes against the archive's `_before` hashes. **If you changed the prompt and tried to
claim a "fresh failure," the hashes will not match and the rerun is blocked.** This
eliminates an entire class of "I quietly re-experimented and pretended it was the same
attempt" scenarios. Rerunning is also subject to a retry budget (`failed_no_artifact: 1`
by default).

### 8.6 Human pauses

After the clarification gate, human pauses are *exception paths only*. A human-pause
artifact must state: the **trigger**, the **artifact to present**, the **decision
required**, the **safe continuation action**, whether automation may resume, and whether
review-bundle creation is blocked. Typical triggers: output-limit incomplete, blocked
preflight, a repeated monitoring anomaly, a missing review-agent CLI, or an evidence
conflict the operator cannot resolve.

### 8.7 The final implementation bundle

The supervisor's terminal artifact. `finalize-bundle` validates a packet against the
final-bundle schema. The schema requires, among other things, that **`file_inventory[]`
and `emitted_files[]` are the identical set of paths** (`sorted(...) == sorted(...)`) —
this is the "no half-finished implementations, no hidden files" guarantee. It also
requires all three agent reviews, an approving `operator_acceptance` record, validation
evidence, a model-migration summary, a failure-policy summary, a human-pause summary,
rollout instructions, and residual risks. On success the session status becomes
`completed`.

---

## 9. The workflows: modes and packs

### 9.1 Workflow modes

A workflow manifest declares a `workflow_mode`. The loader enforces the stage count:

| Mode | Stages | Typical gate pattern |
|---|---|---|
| `one_pass` | exactly 1 | `terminal` |
| `two_pass` | exactly 2 | `auto` → `terminal` |
| `reviewed_three_stage` | exactly 3 | `review_required` → `review_required` → `terminal` |
| `custom_ordered` | any number | any combination |

### 9.2 The bundled packs

**`automation/examples/responses_runner_v2_synthetic/`** — the **synthetic proof pack**.
Small, bounded, business-agnostic. It exists so you can verify the *engine* without
adopting a real workflow. It ships three workflows that together exercise one-pass +
sidecar, automatic two-pass carry-forward, and reviewed three-stage gating:

```
one_pass.workflow.json            1 stage  (terminal, sidecar)
two_pass.workflow.json            stage 1 auto  →  stage 2 terminal+sidecar
reviewed_three_stage.workflow.json  proposal (review_required)
                                  → revision (review_required, carries proposal's bundle)
                                  → final_delivery (terminal+sidecar, carries revision's bundle)
```

**`automation/task_packs/responses_runner_v2_supervised_end_to_end/`** — the current
**four-stage self-improvement pack** (`custom_ordered`). It asks the runner to improve
*itself* by designing the supervisor lane around the existing engine:

```
Stage 1  architecture_and_supervision_protocol      gate: review_required
Stage 2  agent_review_protocol_and_package_contract gate: review_required
                                                    (carries Stage 1's review bundle)
Stage 3  draft_drop_in_packet                       gate: review_required
                                                    (carries Stage 2's review bundle)
Stage 4  final_drop_in_packet                       gate: terminal + sidecar
                                                    (carries Stage 3's review bundle)
```

**`automation/task_packs/responses_runner_v2_supervisory_lane/`** — a legacy three-stage
pack kept as historical regression coverage. Do not run it for new work.

**`automation/task_packs/responses_runner_v2_supervisor_internal/`** — *not* a normal
workflow pack. It is the supervisor's own prompt + command-template library: the prompts
for operator Codex, the Codex reviewer, and the Claude reviewer, and the `*.command.json`
templates that define exactly how each agent CLI is invoked. The supervisor CLI depends
on these files existing.

### 9.3 Carry-forward: how a stage sees the previous stage

A stage's `carry_forward` block can pull from earlier stages two ways:

- **`reference_context_from_stage_ids`** — attaches the prior stage's `response.final.md`
  under the lowest-authority *Reference Context* role. Cheap, no review required.
- **`review_bundle_from_stage_id`** — requires you to supply that stage's *approved
  review bundle* with `--review-bundle`. The bundle's contents (approved markdown,
  reviewer notes, optionally an approved handoff markdown and structured JSON) are laid
  out under the *Reviewed Handoff Inputs* role. This is the gated path.

Carry-forward is deliberately cheap — it attaches the *actual approved text*, not a
summary. No prompt-stuffing, no lossy compression.

### 9.4 The locked model posture

New runners, supervisors, examples, workflows, and tests use a **locked** model posture.
The loader rejects anything else *at load time*:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`
- committed GPT-5.5-family prompt cache retention: `24h` (the loader refuses a GPT-5.5
  profile that omits this)
- high-stakes primary reasoning effort: `xhigh`; structural: `high` or `medium`
- locked max output tokens for high-stakes self-improvement stages: `128000`

Do not introduce legacy 5.4-family identifiers as runtime defaults.

---

## 10. The run output layout

Everything lands under `.local/` — git-ignored, never committed, never re-attached as
evidence to a future run.

```
.local/automation/responses_runner_v2/
├── runs/{timestamp}_{run_name}_{workflow_id}/
│   ├── run_manifest.json                  ← top-level run state + per-stage summary
│   └── stages/{NN_stage_id}/
│       ├── input_manifest.json            ← resolved attachments (machine)
│       ├── input_manifest.md              ← resolved attachments (human + model TOC)
│       ├── request_payload.json           ← the EXACT body sent to /responses
│       ├── token_preflight.json           ← or token_preflight.error.json
│       ├── uploads.json                   ← file_id ↔ source_path mapping + lifecycle
│       ├── response.latest.json           ← rewritten on every poll
│       ├── response.final.json            ← finalized raw response after terminal status
│       ├── response.final.md              ← human-readable render (usage, sources, tools)
│       ├── output.structured.json         ← if json_schema OR sidecar configured
│       ├── sidecar.response.json/.md      ← if sidecar configured
│       └── stage_checkpoint.json          ← durable, resumable stage state
└── supervisor_sessions/{session_id}/
    ├── supervisor_session.json            ← schema-validated single source of truth
    ├── scaffolds/{version}/source/ + hash_manifest.json
    ├── examinations/  dry_runs/  review_cycles/  archives/
    ├── monitoring/  human_pauses/  commands/
    └── final_bundle/final_implementation_bundle.json
```

### The evidence / hash chain

This is what makes a `tar` of the root a complete audit trail:

```
workflow_manifest.sha256
        │  recorded in
        ▼
run_manifest.json ──► stage summary records response.final.{md,json} by SHA-256
        │
        ▼
stage_checkpoint.json ──► points at every artifact + the response_id
        │
        ▼
review_bundle.json ──► artifact_hashes{} for every referenced file;
        │              load() re-hashes and refuses on mismatch
        ▼
next stage's input_manifest.json ──► references the bundle under Reviewed Handoff Inputs
```

Every link is a hash. Break any link and the runner refuses to proceed.

---

## 11. End-to-end walkthroughs

### Path A — generic engine, one-pass (the fastest way to see it work)

```bash
# 1. Dry-run: build the request, touch no API. Your cheapest validation.
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
#    → writes run_manifest.json + stages/01_draft_summary/{input_manifest.*,request_payload.json,stage_checkpoint.json}

# 2. Live run, wait for completion.
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --skip-token-count \
  --wait
#    → uploads files, POSTs /responses, polls to terminal, writes response.final.* ,
#      runs the sidecar, writes output.structured.json, prints the run_manifest path
```

What happened internally: CLI resolved the root → built `RuntimeOptions` →
`OpenAIClient.from_env(root)` → `run_workflow()` loaded + validated the workflow → created
the run dir → `_determine_next_stage()` found stage 1 → resolved + uploaded attachments →
(token preflight skipped) → `POST /responses` → checkpoint #1 → polled to `completed` →
wrote `response.final.*` → ran the sidecar → checkpoint #2 → returned.

### Path A′ — generic engine, a `review_required` workflow (manual review)

```bash
# Stage 1 runs and ends in status "waiting_for_review".
python automation/run_responses_v2.py run --root . \
  --workflow-file .../reviewed_three_stage.workflow.json --skip-token-count --wait

# You inspect stages/01_proposal/response.final.md, then write reviewer notes,
# then build an approved bundle by hand:
python automation/create_review_bundle_v2.py --root . \
  --output review_bundle_stage1.json \
  --workflow-id synthetic_reviewed_three_stage \
  --source-stage-id proposal \
  --source-run-id <run_id> \
  --primary-artifact-markdown <run_dir>/stages/01_proposal/response.final.md \
  --response-artifact-json   <run_dir>/stages/01_proposal/response.final.json \
  --reviewer-notes notes.md

# Continue the SAME run, supplying the bundle. Stage 2 now sees it as Reviewed Handoff Input.
python automation/run_responses_v2.py run --root . \
  --workflow-file .../reviewed_three_stage.workflow.json \
  --run-dir <run_dir> --review-bundle review_bundle_stage1.json --skip-token-count --wait
```

### Path B — supervisor lane, full loop (the four-stage self-improvement pack)

```bash
# STEP 0  (human)  accept a clarified_task_brief.md

# STEP 1  init the session
python automation/run_responses_supervisor_v2.py init-session --root . \
  --clarified-task-brief docs/clarified_task_brief.md \
  --summary "One-sentence accepted task summary"

# STEP 2  stage the task pack into the session (copies + hashes it)
python automation/run_responses_supervisor_v2.py stage-scaffold --root . \
  --session <session_id> \
  --scaffold-path automation/task_packs/responses_runner_v2_supervised_end_to_end

# STEP 3  STATIC examination gate
python automation/run_responses_supervisor_v2.py examine-scaffold --root . \
  --session <session_id> \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json

# STEP 4  EXECUTABLE dry-run gate
python automation/run_responses_supervisor_v2.py dry-run-scaffold --root . \
  --session <session_id> \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json

# STEP 5  scaffold review cycle: operator → reviewers → consolidate → accept
python automation/run_responses_supervisor_v2.py invoke-operator   --root . --session <id> --review-cycle scaffold_001 --review-kind scaffold --job-json <op_job.json>
python automation/run_responses_supervisor_v2.py invoke-reviewers  --root . --session <id> --review-cycle scaffold_001 --review-kind scaffold --job-json <review_job.json>
python automation/run_responses_supervisor_v2.py consolidate       --root . --session <id> --review-cycle scaffold_001 --codex-review <...> --claude-review <...> --operator-review <...> --output <consolidated.json>
python automation/run_responses_supervisor_v2.py accept            --root . --session <id> --review-cycle scaffold_001 --consolidated-review <consolidated.json> --accept-recommendation rec_001 --applied-change-evidence <evidence.json> --output <acceptance.json>

# --- scaffold is now "accepted"; launch is allowed ---

# STEP 6  launch Stage 1 LIVE via the engine
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --skip-token-count --wait

# STEP 7  classify Stage 1's outcome
python automation/run_responses_supervisor_v2.py classify --root . \
  --session <session_id> --run-dir <run_dir> --stage architecture_and_supervision_protocol
#    → if completed_complete_artifact: reviewable

# STEP 8  Stage 1 review cycle (review_kind = stage_output), then create-bundle
python automation/run_responses_supervisor_v2.py create-bundle --root . --session <id> \
  --output bundle_stage1.json --workflow-id <id> --source-stage-id architecture_and_supervision_protocol \
  --source-run-id <run_id> --primary-artifact-markdown <...> --response-artifact-json <...> \
  --reviewer-notes <...> --acceptance-record <acceptance.json>

# STEP 9  launch Stage 2 with --review-bundle bundle_stage1.json ... repeat 7-9 for stages 2, 3, 4

# STEP 10  finalize
python automation/run_responses_supervisor_v2.py finalize-bundle --root . \
  --session <session_id> --packet-json <final_packet.json> --output final_implementation_bundle.json
#    → schema-validates; file_inventory must equal emitted_files; session status → "completed"
```

---

## 12. Command reference

### `run_responses_v2.py` — the generic engine CLI

| Subcommand | What it does | Key flags |
|---|---|---|
| `run` | Launch the next eligible stage (or continue a run). | `--workflow-file` (req), `--root`, `--run-name`, `--run-dir`, `--stage`, `--primary-job-input` (repeatable), `--reference-context` (repeatable), `--review-bundle` (repeatable), `--dry-run`, `--wait`, `--skip-token-count`, `--max-input-tokens`, `--max-output-tokens`, `--primary-model`, `--structural-model`, `--poll-interval`, `--max-wait-seconds`, `--service-tier`, `--file-expires-after`, `--delete-uploaded-files-on-complete` |
| `resume` | Rehydrate a stored `response_id`, poll to terminal, finalize artifacts. | `--run-dir` (req), `--stage` (req), `--wait`, `--poll-interval`, `--max-wait-seconds` |
| `refresh` | Poll remote status only — does **not** finalize or clean up. | `--run-dir` (req), `--stage` (req) |

`run` vs `resume` vs `refresh`: use **`run`** to start a fresh stage; **`resume`** when a
stage was already submitted and you want the engine to finalize it (write
`response.final.*`, structured output, sidecar); **`refresh`** when you only want the
latest remote status recorded locally. `refresh` will *not* backfill final artifacts.

### `run_responses_supervisor_v2.py` — the supervisor CLI

| Subcommand | What it does |
|---|---|
| `init-session` | Create a session from an accepted `--clarified-task-brief` + `--summary`. |
| `stage-scaffold` | Copy a `--scaffold-path` task pack into the session and hash it. |
| `examine-scaffold` | Static pre-launch gate over a `--workflow-file` (no Stage 1 request built). |
| `dry-run-scaffold` | Executable gate — runs the engine in `dry_run=True` mode. |
| `invoke-operator` | Run the accountable operator Codex job for a `--review-cycle` / `--review-kind`. |
| `invoke-reviewers` | Run the independent read-only Codex + Claude reviewers. |
| `consolidate` | Deterministically merge `--operator-review` + `--codex-review` + `--claude-review`. |
| `accept` | Operator selective acceptance — needs `--accept-recommendation` IDs + `--applied-change-evidence`. |
| `create-bundle` | Create an approved review bundle — only with an approving `--acceptance-record`. |
| `classify` | Classify a stage outcome (`--run-dir` + `--stage`) into one of the 6 classes. |
| `monitor` | Record monitoring state; emit a human-pause if a stage is stale. |
| `archive-attempt` | Archive a `failed_no_artifact` attempt (with hashes) before a rerun. |
| `finalize-bundle` | Validate + record the final implementation bundle from `--packet-json`. |
| `validate-session` | Validate `supervisor_session.json` against its schema. |

### `create_review_bundle_v2.py` — build a bundle by hand

`--output`, `--workflow-id`, `--source-stage-id`, `--source-run-id`,
`--primary-artifact-markdown`, `--response-artifact-json`, `--reviewer-notes` are
required; `--approved-handoff-markdown`, `--structured-artifact-json`,
`--locked-decision`, `--open-dependency`, `--note` are optional.

### `run_responses_v2_eval.py` — the eval / freeze-gate helper

`--dataset-file` + `--list-cases`; or `--dataset-file --case-id --artifact
[--structured-artifact]` to grade one case; or `--freeze-gate-file` to check a freeze
gate. Supported checks: required JSON keys, JSON array minimum length, JSON path equals,
structured-output required keys, required text substrings.

---

## 13. Operator playbooks

### "I want to prove the runner works at all"

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json --dry-run
```

The unittest suite (8 modules) plus a clean dry-run is the same gate CI runs on Python
3.10/3.11/3.12.

### "My terminal died / I `Ctrl-C`'d during `--wait`"

Not a disaster — the `response_id` is in `stage_checkpoint.json`. Run:

```bash
python automation/run_responses_v2.py resume --root . --run-dir <run_dir> --stage <stage_id> --wait
```

The engine retrieves the response, polls to terminal, and finalizes artifacts.

### "The stage shows `completed` but `output.structured.json` is missing"

It was *refreshed*, not *finalized*. `resume` it (`resume` finalizes; `refresh` does not).

### "Token preflight blocked the stage"

The input token count exceeded the configured `max_input_tokens`. This is *terminal* and
intentional. Do **not** retry blindly. Either reduce the input manifest scope (fewer/
smaller attachments), raise the limit deliberately, or — if you accept the spend risk —
re-run with `--skip-token-count`. In the supervisor lane this surfaces as
`blocked_token_preflight` → a human pause.

### "A stage failed"

Run `classify`. If `failed_complete_artifact`, it is still reviewable — proceed through
the review loop. If `failed_no_artifact`, you must `archive-attempt` first, and the rerun
is only allowed if the request and scaffold hashes are unchanged and retry budget remains.
If `incomplete_output_limit`, it will not auto-progress — you get a human pause and must
decide on scope/model/budget.

### "A stage has been `in_progress` for hours"

Do **not** re-`run` it (no-duplicate-submit). Use `refresh` to record the latest status,
or `resume --wait` to keep polling. In the supervisor lane, `monitor` will detect a stale
stage and emit a `long_running_monitoring_anomaly` human pause; the safe action is always
refresh/resume the existing `response_id`.

### "I'm using this checkout against a different project"

Keep the runner checkout wherever you like; put the task pack + all referenced assets
under the *target* workspace root; invoke with `--root /path/to/target-workspace`. The
`--workflow-file` is interpreted under that root, and the target root is also where `.env`
is looked up. There is no dual-root mode in this release.

---

## 14. Guardrails you cannot bypass

These are mechanical. They are not warnings — they stop the process. Knowing them turns
"why did it fail" into a five-second diagnosis.

| Guardrail | Trips when | Where |
|---|---|---|
| **One-root** | any path resolves outside the workspace root | `resolve_under_root()` |
| **Model caps** | `max_output_tokens` over the model cap, wrong cache retention, structured output on an unsupported model | `validate_model_options()` |
| **GPT-5.5 cache posture** | a GPT-5.5 model role omits `prompt_cache_retention="24h"` | `pack_loader` |
| **background + store** | a workflow sets `background=true` with `store=false` | `pack_loader` |
| **Stage shape** | duplicate stage ids, mis-ordered stage numbers, mode↔count mismatch, dangling carry-forward reference | `pack_loader` |
| **No-duplicate-submit** | you `run` a stage that is still `submitted`/`in_progress` | `_determine_next_stage` |
| **Review gate** | you try to run past a `review_required` stage without `--review-bundle` | `_determine_next_stage` |
| **Token preflight** | input tokens exceed `max_input_tokens` (fail-closed, terminal) | `workflow.py` token preflight |
| **Bundle hash** | any file a review bundle references has a different hash than recorded | `load_review_bundle()` |
| **Bundle ↔ source match** | a bundle's artifact path/hash does not match the source stage's recorded summary | `validate_review_bundle_for_stage()` |
| **Schema validation** | any supervisor artifact fails its JSON schema | `supervisor_artifacts` (validates before atomic write) |
| **Read-only review** | a read-only reviewer modifies any workspace source file | `supervisor_agents` snapshot diff |
| **Operator-provisional-first** | reviewers are invoked before the operator provisional record exists | `invoke_reviewers` |
| **Evidence-or-reject** | an accepted recommendation lacks `changes_applied`/`validation_evidence`/`operator_rationale` | `accept_consolidated_review` |
| **Scaffold launch** | Stage 1 launch attempted before the scaffold is `accepted` with an accepted review cycle | `assert_scaffold_launch_allowed` |
| **Archive-before-rerun** | a `failed_no_artifact` rerun without a matching archive (same hashes) | `can_rerun_failed_no_artifact()` |
| **Final-bundle completeness** | `file_inventory` ≠ `emitted_files`, or a missing agent review | `finalize-bundle` |

---

## 15. Where to look when something is wrong

```
Question                              First file to open
──────────────────────────────────────────────────────────────────────────
"Why did the model produce that?"  →  stages/NN_stage/input_manifest.md
                                      (check WHAT was attached, in WHICH role)

"What exactly did we send?"        →  stages/NN_stage/request_payload.json

"What is the current stage state?" →  stages/NN_stage/stage_checkpoint.json
                                      (status, response_id, terminal?, resume_mode)

"What is the overall run state?"   →  run_manifest.json
                                      (per-stage summary, statuses, artifact hashes)

"Did token preflight pass?"        →  stages/NN_stage/token_preflight.json
                                      or token_preflight.error.json

"What did the model actually say?" →  stages/NN_stage/response.final.md
                                      (usage, sources, tool calls — human-readable)

"What does the supervisor think?"  →  supervisor_sessions/<id>/supervisor_session.json
                                      (status, current_phase, errors[], human_pauses[])

"Why did a review fail?"           →  review_cycles/<id>/agents/*.readonly.diff.md
                                      and the *.stderr.txt next to it

"Why was a rerun blocked?"         →  archives/<id>/supervisor_archive.json
                                      (compare request_hash / scaffold_hash)
```

A `SystemExit` message from any CLI is *designed* to tell you exactly which guardrail
tripped. Read it literally before changing anything.

---

## 16. Reading order & next steps

Once this guide makes sense, read the source in this order — it is the same order the
repo's own `DEVELOPING.md` recommends, and each file builds on the previous:

1. `AGENTS.md` — the repo-level rules every automation agent (and you) must follow.
2. `DEVELOPING.md` — the developer mental model and the "do not reopen casually" list.
3. `docs/runbooks/responses-runner-v2.md` — the day-to-day operator runbook.
4. `automation/responses_runner_v2/contracts.py` — the type system; read it slowly.
5. `automation/responses_runner_v2/pack_loader.py` — what makes a workflow *valid*.
6. `automation/responses_runner_v2/workflow.py` — the orchestrator; the one file that
   shows the whole engine flow end to end.
7. `automation/responses_runner_v2/attachments.py` — how the model actually sees inputs.
8. `automation/responses_runner_v2/review_bundle.py` and `sidecar.py` — the handoff
   contract and the structured-extraction pass.
9. `automation/examples/responses_runner_v2_synthetic/README.md` — then dry-run all three
   synthetic workflows.
10. `automation/tests/test_responses_runner_v2_workflow.py` — the executable spec.

Then, for the supervisor lane: `docs/design/supervised-self-improvement-pack.md`,
`automation/responses_runner_v2/supervisor.py`, and the
`automation/task_packs/responses_runner_v2_supervisor_internal/` prompt + command library.

**The single fastest way to build intuition:** dry-run the synthetic one-pass workflow,
then open every file it wrote under `.local/automation/responses_runner_v2/runs/`. The
engine's whole contract is visible in those artifacts.

---

*Companion file: `docs/stage-workflow-runner-interactive.html` — an interactive,
click-through version of these diagrams for building the same mental model visually.*
