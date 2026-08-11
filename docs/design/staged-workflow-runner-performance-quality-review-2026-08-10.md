# Staged Workflow Runner: Consolidated Architecture, Performance, and Quality Review

- Date: 2026-08-10
- Repository: `staged-workflow-runner`
- Scope: standalone runner engine, supervisor/review lane, task packs, examples, schemas, evals, runbooks, tests, and representative local run evidence
- Goal: make the runner a trustworthy, focused, measurable, general-purpose first-class staged workflow system while preserving its high-stakes critical profile
- Review posture: code and artifact review only; no live Responses API calls and no source implementation changes

## Executive verdict

The runner has a strong architectural design: a one-root policy, explicit input authority, ordered stages, durable-evidence intent, hash-validated handoffs, resumable Responses jobs, optional structured sidecars, an additive supervisor, independent reviews, deterministic consolidation, and separate operator acceptance. Those are the right primitives for difficult staged work. The current system-temp staging and non-atomic state writes mean the one-root and durability properties are not yet fully enforced.

The team's main diagnosis is directionally right: context pollution, an incomplete revision loop, and missing measurement are important quality constraints. The implementation evidence, however, changes the order of work. Several documented safety properties are not enforced at the transition where they matter:

1. an explicitly selected stage can be submitted again even when its existing response is still live;
2. remote completion can be recorded as run completion before required local artifacts or sidecars are finalized;
3. missing reviewer verdicts and unresolved blocking issues can disappear between review, consolidation, and acceptance;
4. review decisions, acceptance, bundles, and finalization are not bound tightly enough to the exact session, cycle, subject, and artifact hashes;
5. Codex review is not process-sandboxed read-only, and the snapshot excludes the `.local/` artifacts being reviewed.

For a high-stakes runner, these are P0 state and gate-integrity defects. They should precede cache, reviewer-parallelism, and model-attention optimizations. Once those are fixed, clean carry-forward artifacts, authority-aware deduplication, stage-scoped inputs, telemetry, deterministic validators, and a real revision loop are the highest-return quality improvements.

### Recommended priority order

| Priority | Outcome | Why it comes here |
|---|---|---|
| P0 | Make submission, finalization, review, acceptance, and bundle transitions truthful and hash-bound | Prevent duplicate work and false progression/completion |
| P0 | Enforce immutable review inputs and actual read-only reviewer execution | Make independent review evidence trustworthy |
| P1 | Produce clean model-facing artifacts and remove duplicate/conflicting context | Largest focus improvement per unit of work |
| P1 | Add correct telemetry, input budgets, and representative evals | Establish the measurement loop needed to validate every later optimization |
| P1 | Add deterministic output validators, faithful dry-run planning, and stronger grounding | Move mechanical work out of expensive model passes and catch failures earlier |
| P1 | Add an acceptance-aware primary-model revision loop | Ensure supported findings improve the artifact itself |
| P2 | Repair reviewer JSON once, compose prompts, parallelize reviewers, and add a resumable composite command | Improve reliability and wall-clock time after gates are sound |
| P2 | Introduce domain-neutral profiles, evidence vocabulary, and final-delivery bundles | Turn the critical coding lane into one profile of a general-purpose product |

## 1. What the system is and how it works end to end

The core Python package is 7,991 lines across thirteen modules. Responsibilities are intentionally split rather than concentrated in a single orchestrator.

### 1.1 Engine components

| Component | Responsibility | Key evidence |
|---|---|---|
| `automation/run_responses_v2.py` | CLI for run, resume, and refresh; converts operator flags into `RuntimeOptions` | `automation/run_responses_v2.py:41-129` |
| `contracts.py` | Constants, dataclasses, model caps, authority roles, root/path rules, state enums, hashes, common instructions | `automation/responses_runner_v2/contracts.py:15-27`, `:189-220`, `:362-430`, `:473-520` |
| `pack_loader.py` | Loads workflow, stage, input-manifest, tool, and output definitions; resolves pack assets | `automation/responses_runner_v2/pack_loader.py:41-164`, `:167-366`, `:400-460` |
| `attachments.py` | Expands and hashes inputs, renders input manifests, wraps/bundles text, uploads files, emits role-labelled request content, cleans uploads | `automation/responses_runner_v2/attachments.py:127-242`, `:245-446`, `:479-538` |
| `workflow.py` | Selects stages, combines static/runtime/handoff context, builds requests, token-counts, submits/polls, finalizes, sidecars, checkpoints, resume/refresh | `automation/responses_runner_v2/workflow.py:127-192`, `:727-874`, `:877-1166`, `:1201-1347` |
| `openai_client.py` | Raw HTTP, retry behavior, Responses CRUD/polling, token count, upload, deletion | `automation/responses_runner_v2/openai_client.py:23-24`, `:83-102`, `:124-190` |
| `artifacts.py` | Run/stage layout, manifests, request/upload artifacts, response rendering, response text/tool/source extraction | `automation/responses_runner_v2/artifacts.py:21-125`, `:174-363` |
| `sidecar.py` | Optional structural pass over a text artifact; owns its upload, request, polling, retry, response, and structured output files | `automation/responses_runner_v2/sidecar.py:155-369` |
| `review_bundle.py` | Creates, loads, hash-validates, and expands approved handoffs | `automation/responses_runner_v2/review_bundle.py:19-80`, `:164-220`, `:255-303` |

The intended live flow is:

```text
workflow + pack assets + runtime inputs
                |
                v
load/validate definitions -> select eligible stage -> resolve/hash role inputs
                |                                      |
                |                                      v
                |                             input_manifest.{json,md}
                v                                      |
prepare/wrap/bundle/upload files -> build request -> exact token preflight
                |                                      |
                +---------------------> POST /responses
                                               |
                                      persist response_id/status
                                               |
                                        poll or resume/refresh
                                               |
                                    response.final.{json,md}
                                               |
                                  optional structured sidecar
                                               |
                               checkpoint + run-manifest transition
                                               |
                         auto next stage OR review bundle OR terminal
```

Input authority is explicit and useful:

1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Repository Files
4. Reference Context

The runtime generates `input_manifest.md` so the model can distinguish those roles and cite only attached evidence. This is a strong design choice, provided duplicate content is not placed at conflicting tiers.

### 1.2 Supervisor components

The supervisor is additive, matching the repository's architectural rule that the engine continues to own workflow execution.

| Component | Responsibility | Key evidence |
|---|---|---|
| `automation/run_responses_supervisor_v2.py` | CLI for session, scaffold, examination, dry run, reviews, consolidation, acceptance, recovery, archive, and final bundle | `automation/run_responses_supervisor_v2.py:41-288` |
| `supervisor.py` | Session workflow and high-level transition functions | `automation/responses_runner_v2/supervisor.py:97-163`, `:330-680`, `:719-1177`, `:1194-1400` |
| `supervisor_agents.py` | Operator/Codex/Claude process invocation, stdout parsing, coercion, schema validation, read-only checks | `automation/responses_runner_v2/supervisor_agents.py:17-57`, `:312-373`, `:394-890`, `:936-1209` |
| `supervisor_artifacts.py` | Supervisor schemas, session storage, snapshots, diffs, archive and bundle support | `automation/responses_runner_v2/supervisor_artifacts.py:27-30`, `:145-237`, `:318-425` |
| `supervisor_policies.py` | Stage-outcome classification, reviewability, rerun guidance, monitoring anomaly detection | `automation/responses_runner_v2/supervisor_policies.py:181-386` |

The intended reviewed flow is:

```text
clarified brief -> staged scaffold -> static examination -> engine dry run
       -> operator provisional review
       -> Codex reviewer + Claude reviewer
       -> deterministic consolidation (advisory)
       -> operator selective acceptance (authoritative)
       -> approved review bundle
       -> engine next stage
```

Recovery adds classification, archive-before-rerun evidence, retry budgets, and human pauses. Finalization accepts a final packet and records a final implementation bundle.

### 1.3 What is already strong

- The engine/supervisor boundary is clear and should remain.
- One-root path resolution rejects explicitly resolved paths outside the workspace (`contracts.py:480-490`).
- Resolved inputs carry paths, hashes, sizes, and authority roles.
- Raw Responses JSON is preserved separately from rendered Markdown.
- Review bundles validate workflow/run/stage identity and primary response paths/hashes when the engine consumes them (`review_bundle.py:164-220`).
- Resume and refresh use a known `response_id`; the design correctly discourages blind duplicate submission.
- Sidecar extraction is a separate structural lane rather than forcing the primary model into machine JSON.
- Consolidation and acceptance are separate concepts.
- The repository has 115 passing unit tests across Python 3.10-3.12 in CI, plus pack dry runs.

The important distinction is that the architecture describes stronger invariants than several current implementations enforce.

## 2. P0 findings: fix before calling the runner high-stakes trustworthy

### P0-1. Submission and finalization are not one coherent state machine

This is a family of related duplicate-execution and false-completion defects.

#### Evidence

- Normal automatic selection stops on `submitted` or `in_progress`, but the explicit `--stage` branch checks only earlier stages and returns the requested stage without checking its own status (`workflow.py:150-177`). A repeated explicit invocation can submit the same stage again.
- Generic HTTP retry applies to `POST /responses` for 408, 409, 429, 5xx, and all `URLError` failures (`openai_client.py:23`, `:124-173`). A timeout after server acceptance can create a second response.
- The first durable `response_id` write happens only after `create_response()` returns (`workflow.py:1046-1064`). Resume/refresh cannot reconcile an ambiguous create failure without an ID (`workflow.py:1201-1223`).
- A max-input overage writes a blocked checkpoint and then raises before updating the run manifest; other fail-closed preflight errors can raise without a blocked checkpoint (`workflow.py:453-530`). The run can still look `prepared` and eligible.
- Response/checkpoint/run status is persisted before local finalization (`workflow.py:1046-1081`). If rendering or sidecar extraction fails later, the manifest may already say completed or waiting for review.
- Refresh intentionally skips finalization but still writes terminal stage/run status (`workflow.py:1238-1320`). The test at `automation/tests/test_responses_runner_v2_workflow.py:542-588` explicitly accepts `completed` with no final Markdown, sidecar, or structured output.
- Supervisor classification falls through unrecognized states, including queued/in-progress in the wrong entry path, to `failed_no_artifact` with `rerun_allowed=true` (`supervisor_policies.py:320-331`).
- Every stage has one fixed evidence directory, not attempt-specific evidence (`artifacts.py:40-58`), so a duplicate or rerun overwrites the same paths.

#### Required design

Use an explicit, durable state machine such as:

```text
prepared
  -> staging_inputs
  -> uploading
  -> preflight_passed
  -> submitting
  -> submitted(response_id)
  -> remote_terminal_pending_finalization
  -> finalized
  -> waiting_for_review | completed | failed_complete | failed_no_artifact | cancelled

Ambiguous POST outcome -> submission_outcome_unknown (never implicitly resubmit)
Preflight failure      -> blocked_preflight (transactionally persisted)
Controlled rerun       -> archived attempt + new attempt_NNN directory
```

Implementation rules:

- Apply the same own-state guard to automatic and explicit stage selection.
- Hold a per-run submission lock (or equivalent compare-and-swap lease) across eligible-stage selection, attempt allocation, and durable submission-state publication. Allocate collision-resistant run/attempt IDs with exclusive creation; an in-process status check alone cannot prevent two processes from posting concurrently.
- Separate remote status from local artifact readiness. Only `finalized` can become `completed` or `waiting_for_review`.
- Persist a pre-submit intent with request hash and attempt ID before the POST.
- Do not automatically retry ambiguous create failures. Use an idempotency key only if the endpoint's documented contract supports it; otherwise block for reconciliation.
- Make blocked-preflight persistence transactional: error artifact, checkpoint, stage summary, and run status before raising.
- Require archive evidence and a new attempt directory for every rerun.
- Make supervisor classification understand every live, unknown, incomplete, and finalization state; a live response is never rerunnable.
- Add an intent-journaled, idempotent cancel command for a known live `response_id`, followed by refresh/finalization and upload cleanup. Cancellation is not a reconciliation mechanism for an unknown submission outcome.

#### Acceptance tests

- Re-running a queued, in-progress, completed, or waiting-for-review explicit stage fails before `create_response()`.
- Two barrier-started processes targeting one prepared stage produce exactly one POST and one clean lock/CAS conflict; same-time run creation cannot reuse a directory.
- Timeout-after-accept simulation produces one `submission_outcome_unknown` attempt and never a second POST.
- Remote completion plus injected sidecar failure remains non-progressable until a later successful finalization.
- Refresh of a terminal response records the remote terminal state but not local completion.
- Every preflight failure leaves matching run, stage, and diagnostic state.
- A permitted rerun creates `attempt_002` without modifying `attempt_001`.
- Cancel tests cover a live response, an already-terminal response, repeated cancellation, and refusal to cancel an unknown submission outcome.

### P0-2. Required reviewer failures and genuine blockers can become approval

#### Evidence

- `_minimal_blocked_decision()` deliberately leaves `blocking_issues=[]` for `missing_cli` and `interrupted`, while setting `approval_decision=blocked` and validation errors (`supervisor_agents.py:312-369`). Tests lock that representation (`test_responses_runner_v2_supervisor.py:749-819`).
- Consolidation concatenates issue arrays but discards each source's status, approval decision, validation errors, and read-only result. Its approval is based only on whether the merged `blocking_issues` list is nonempty (`supervisor.py:878-920`). A missing verdict can therefore look clean.
- Acceptance does not carry `consolidated["blocking_issues"]` forward. It constructs blockers only from rejected critical/blocking recommendations and missing filesystem artifacts (`supervisor.py:1086-1124`). A reviewer's genuine blocking issue can disappear even when consolidation retained it.
- `_require_operator_provisional()` checks for a non-empty path, not a successful, matching operator verdict (`supervisor.py:712-716`).

#### Required design

- Require exactly one successful operator provisional decision and one delivered, schema-valid decision from each required reviewer for the exact cycle.
- Treat transport success, schema validity, and read-only success as supervisor gate conditions distinct from reviewer semantic approval.
- Preserve every unresolved blocking issue into acceptance.
- Clear a blocker only through an explicit resolution record containing issue ID, affected artifact hash, applied change, validation evidence, and operator rationale.
- Encode role/status/approval conditional invariants in validation code or schema. Absence is never equivalent to approval.

#### Acceptance tests

- Missing CLI, timeout, interrupt, malformed JSON, schema failure, and read-only violation each block consolidation/acceptance progression.
- One genuine reviewer blocker remains blocking even when there are no recommendations.
- A blocker cannot be cleared by omission or by rejecting a recommendation; only a hash-bound resolution can clear it.
- All three required decisions must match the recorded review cycle and subject.

### P0-3. Review, acceptance, and final completion are insufficiently bound to their subject

#### Evidence

- Agent normalization forces actor role and review kind, but agent-supplied session and cycle IDs are retained when present rather than required to match the invocation (`supervisor_agents.py:839-860`).
- Consolidation accepts caller-supplied decision paths and validates only their schema. It does not require equality with the cycle's recorded output paths, expected roles, session, cycle, workflow, run, stage, job hash, or artifact manifest (`supervisor.py:819-853`).
- Acceptance likewise accepts an arbitrary schema-valid consolidation path (`supervisor.py:1034-1049`).
- Scaffold acceptance updates `session["scaffold_versions"][-1]`, not the scaffold version actually reviewed (`supervisor.py:1169-1175`).
- Approved bundle creation checks only `review_kind=operator_acceptance` and `approval_decision=approve`, not session/workflow/run/stage/cycle linkage (`supervisor.py:1301-1347`). The acceptance record path is stored only in a free-form note, not as a hash-bound bundle member.
- Finalization checks keys and path-list equality, schema-validates, and marks the session complete (`supervisor.py:1350-1400`). `reviewRef` requires only non-empty paths and decision text (`schemas/final_implementation_bundle.schema.json:317-340`); files and hashes need not be real. The existing finalization test uses nonexistent review paths (`test_responses_runner_v2_supervisor.py:1246-1286`).

#### Required design

Every transition should bind this immutable subject tuple:

```text
session_id
review_cycle_id
review_kind
scaffold_version + scaffold_hash
workflow_id + workflow_asset_set_hash
run_id + stage_id + attempt_id
checkpoint_hash
reviewed_artifact_manifest_hash
review_job_hash
```

Then:

- derive downstream decision paths from cycle state instead of accepting arbitrary paths;
- hash raw stdout/stderr, normalized decisions, consolidation, resolution records, acceptance, and bundle;
- validate exact actor/session/cycle/subject linkage at every boundary;
- update the bound scaffold version, not the latest one;
- load and verify all final review references, deliverable files, and hashes before setting `completed`;
- add supervisor-owned gated launch/rerun operations that register engine runs in the session.

#### Acceptance tests

- Cross-session, cross-cycle, wrong-role, wrong-stage, and stale-scaffold decisions are rejected.
- Changing a reviewed byte invalidates consolidation and acceptance.
- An acceptance for scaffold v1 cannot approve scaffold v2.
- Missing review files, wrong hashes, fabricated decisions, or stale acceptance cannot finalize a session.
- Unregistered runs cannot be classified, bundled, or advanced in supervised mode.

### P0-4. Independent review is not actually immutable/read-only

#### Evidence

- Codex review runs as bare `codex exec <large prompt>` with ambient sandbox, model, effort, approvals, and tools (`supervisor_agents.py:960-962`).
- Before/after snapshots are the primary mutation check (`supervisor_agents.py:988`, `:1040-1072`).
- Snapshotting excludes every gitignored path (`supervisor_artifacts.py:318-404`). `.gitignore:1-3` ignores `.local/`, while run and supervisor artifacts are intentionally stored there (`contracts.py:17`; `supervisor_artifacts.py:27`). The artifacts being reviewed can therefore be modified without detection.
- A before/after snapshot also cannot detect edit-then-restore behavior.
- The supervisor rereads a job for each agent (`supervisor_agents.py:953-955`), so reviewers need not see identical bytes if it changes between serial calls.

#### Required design

- Invoke Codex with explicit read-only sandbox, ephemeral state, pinned/auditable model and effort, and output schema.
- Give Claude the minimum read/search tools only after its filesystem posture and immutable target set are enforced.
- Build one immutable review-input manifest once per cycle and pass the same frozen job bytes to both agents.
- Hash every declared reviewed artifact before and after, including ignored `.local` files.
- Allow writes only to role-specific command output paths. Use snapshots as defense in depth, not the primary barrier.

#### Acceptance tests

- Mutation of a `.local` reviewed artifact is prevented or reported as a read-only violation.
- Writes outside exact reviewer output paths fail.
- Both reviewers' records contain the same review job and artifact-manifest hashes.
- An edit-and-restore attempt cannot succeed.

## 3. Consolidated improvement backlog

### P1: quality, focus, durability, and measurement

| ID | Opportunity | Concrete implementation | Success signal |
|---|---|---|---|
| P1-1 | Clean model-facing artifact | Add immutable `artifact.md` containing only assistant output. Keep `response.final.md/json` as evidence. Add artifact path/hash to checkpoint, run summary, bundle schema, and backward-compatible readers. | Carry-forward bytes/tokens fall; no envelope metadata appears in downstream citations |
| P1-2 | Authority-aware context dedup | If a review bundle from stage X supplies its primary artifact, omit X's generated reference carry-forward. Detect same path/hash across roles and fail or require explicit precedence. | Zero unacknowledged cross-tier duplicates |
| P1-3 | Stage-scoped runtime inputs | Replace “all runtime primary/reference inputs in every stage” with named workflow-scoped or stage-scoped bindings, frozen at run creation. | Inputs absent from stages that do not declare them; lower input tokens |
| P1-4 | Sidecar input hygiene | Default to clean Markdown only. Attach raw response JSON only for an explicit recovery condition. Journal each sidecar upload/attempt and cleanup result. | Lower sidecar amplification; no lost file IDs |
| P1-5 | Correct usage telemetry | Normalize `usage.input_tokens_details` and `usage.output_tokens_details`, with legacy fallbacks. Persist every primary/sidecar attempt, retries, duration, uploads, cache reads, and aggregate totals. | Run-level `usage-report`; renderer no longer shows false `None` values |
| P1-6 | Cache-key experiment | Use stable keys per cache-compatible workflow/version/model role or lane, not per stage. Fix 64-character overflow. A/B against current keys. | Higher `cached_tokens/input_tokens` where prefixes actually match; measured latency/cost delta |
| P1-7 | Pre-upload context/spend budget | Add conservative local estimate before upload; add verified model context windows; enforce input + requested output + safety margin. Keep exact API count when available. Require a real max for critical packs. | Oversized jobs fail before upload; no late opaque context errors |
| P1-8 | Durable upload lifecycle | Journal each successful upload immediately; cleanup on pre-submit failure; add idempotent recovery command. Verify hash immediately before upload. | Every remote file ID is recoverable and byte-bound |
| P1-9 | Frozen run contract | Snapshot/hash workflow, prompts, shared instructions, manifests, tools, schemas, effective runtime options, and upload-ready bytes. Fail closed or explicitly migrate on drift. | Resume/later stages use the exact original contract |
| P1-10 | Schema/topology enforcement | Make one standards-compliant Draft 2020-12 validator a core dependency and validate raw manifests before coercion. Require stage numbers `1..N`, backward-only dependencies, manifest identity match, and runtime/schema parity. | Same accept/reject result in minimal and dev installations |
| P1-11 | Concurrency-safe persistence | Beyond the P0 submission lock, add collision-resistant command IDs, `exist_ok=False`, atomic fsync-and-replace, per-session lock, revision/CAS, and nonempty-directory rejection. | Parallel starts never share evidence; interrupted writes remain valid |
| P1-12 | Faithful dry run | Factor one pure request planner. Dry run emits symbolic content-addressed file handles and runs wrapper, bundle, count, size, duplicate, and validator planning. | Normalized dry/live requests differ only by remote IDs/timestamps |
| P1-13 | Deterministic validators | Add typed/versioned post-output validators with trusted registry IDs, structured results, timeouts, artifact hashes, and gate policy. Start with `markdown_playbook_v1`. | Mechanical failures are caught before model review/bundle creation |
| P1-14 | Grounding inventory | Add a bounded, deterministic `workspace_inventory` attachment projection, then attach selected package/CI/test/config/source contents. Fix gstack Stage 2. | Stage can substantiate paths and commands without attaching the whole tree |
| P1-15 | Acceptance-aware revision | Consolidation -> evidence-supported revision directive -> primary-model revision -> fresh full review -> operator acceptance. Bundle revised artifact and acceptance as hashed members. | Accepted substantive findings are visible in the artifact itself |
| P1-16 | Safer directory inputs | Respect gitignore by default, reject/flag secret filenames and symlink escapes, require explicit audited override for sensitive/unknown binaries. | No accidental `.env`, key, or outside-root upload |
| P1-17 | Persisted-format compatibility | Version every changed run/checkpoint/session/bundle contract and publish a migration matrix: resume unchanged, migrate from backup, or fail closed with recovery instructions. Test frozen v1 fixtures. | Existing live evidence is never silently reinterpreted or stranded |
| P1-18 | Data handling and retention | Add assurance-profile policy for local permissions, raw artifact/reviewer-output retention, redaction, API `store`, remote file expiry/deletion, and an evidence-preserving purge/tombstone operation. | Sensitive data has an explicit lifecycle, including under permissive umask |

Two implementation details deserve special attention:

1. `is_probably_utf8_text()` currently calls `read_bytes()[:4096]`, reading an entire file to sample 4 KiB (`attachments.py:33-41`). Wrappers, bundles, and multipart encoding make additional full-size copies (`attachments.py:51-119`; `openai_client.py:83-102`). Stream these paths and memoize classification.
2. Live staging uses the system temporary directory (`workflow.py:994-1001`), outside the promised one root. Wrapper names flatten `/` to `__` and can collide, and fixed triple fences can be escaped by source content (`attachments.py:51-119`). Use a content-addressed, stage-local upload-input area under the run root and a safe length-delimited or dynamically fenced representation.

### P2: reviewer reliability and throughput

| ID | Opportunity | Concrete implementation | Guardrail |
|---|---|---|---|
| P2-1 | Schema-first reviewer output | Define a smaller model-owned decision schema; supervisor supplies IDs, timestamps, paths, command metadata, and read-only proof. | Do not ask the model to generate deterministic envelope fields |
| P2-2 | One format repair | For parse/schema failure only, invoke the same agent once with raw stdout and exact errors. Preserve both attempts and lineage. | Never repair timeout, interrupt, nonzero exit, missing CLI, read-only violation, or semantic rejection |
| P2-3 | Prompt composition | Generate enum/shape guardrails from schema; compose shared policy + role prompt + review-kind supplement. Wire or remove dormant prompt assets. | One source of truth; hash effective prompt |
| P2-4 | Typed command posture | Validate allowlisted command templates and honor explicit model, effort, prompt, schema, tools, sandbox, and timeout. Move Codex job to stdin and store only hash/size. | No shell interpolation or repository-defined arbitrary executable |
| P2-5 | Focused Claude search | Add read-only Grep/Glob after sandbox/immutable-input work. | Tools expand search, not write authority |
| P2-6 | Deterministic recommendation grouping | Normalize whitespace/case/punctuation and artifact lists; keep every source and severity disagreement. Mark looser matches `possibly_related`. | Semantic clustering is advisory and cannot delete provenance |
| P2-7 | Parallel reviewers | Run independent reviewers concurrently against one frozen input manifest; persist each result independently; run one shared post-integrity check. | Any missing/invalid reviewer blocks the cycle |
| P2-8 | Composite pre-acceptance command | Resumable `review-cycle`: operator provisional -> parallel reviewers -> consolidation. Checkpoint each step. | Acceptance remains a separate deliberate action |
| P2-9 | Upload reuse and streaming | Run-local content-hash registry keyed by bytes, purpose, expiry, and deletion policy; stream large files. | Never reuse expired/deleted or policy-incompatible files |
| P2-10 | Reasoning-summary experiment | If enabled, store model-generated summary only as diagnostic raw evidence and measure extra tokens. | It is not the model's private rationale, factual evidence, or gate authority |

## 4. Verification of every team recommendation

Verdict legend:

- **Confirmed**: premise and implementation direction are supported.
- **Partially confirmed**: the core issue is real, but a factual premise or proposed implementation needs correction.
- **Experiment**: plausible optimization without evidence of net quality improvement yet.
- **Not confirmed**: repository evidence contradicts the claim or existing coverage already satisfies it.

| Team item | Verdict | Verification, correction, and disposition |
|---|---|---|
| 1a. Clean carry-forward artifacts | **Partially confirmed** | `response.final.md` contains metadata, usage, tool/source summaries, upload lifecycle, and sometimes direct structured output (`artifacts.py:300-362`), and it is used for carry-forward/bundles (`workflow.py:301-325`; `review_bundle.py:255-303`). Correction: sidecar-produced structured JSON is not appended to primary Markdown; only direct `json_schema` output uses that block. Implement a separate `artifact.md` with backward-compatible schema fields. |
| 1b. Remove double attachment | **Confirmed** | `_build_stage_runtime_manifest()` concatenates review-bundle inputs and prior-stage reference context without dedup (`workflow.py:737-769`). Gstack stages 2-4 and the synthetic reviewed pack name the immediate predecessor both ways. Auto-remove only the unambiguous engine-generated duplicate; lint all other cross-authority duplicates. |
| 1c. Stop routine sidecar raw JSON upload | **Partially confirmed** | Sidecar always uploads/attaches Markdown and raw JSON even though JSON is labelled recovery-only (`sidecar.py:218-277`). Correction: `_response_supports_sidecar_processing()` does not already identify missing text for completed responses (`workflow.py:551-569`). Make recovery explicit. Keep `previous_response_id` as a low-priority experiment: official documentation says chained prior input tokens are still billed, so it is not an obvious cost win ([Conversation state](https://developers.openai.com/api/docs/guides/conversation-state)). |
| 1d. Default review-bundle raw JSON to false | **Confirmed** | Dataclass and parser default to true (`contracts.py:309-315`; `pack_loader.py:187-203`); all three committed task-pack workflows set false, while the synthetic reviewed example inherits true. Change all code/schema/example expectations together. |
| 1e. Lower intermediate verbosity | **Experiment** | Gstack inherits high verbosity for every primary stage, and stage overrides exist. But stages 3-4 carry substantive tables/corrections. A/B per stage and preserve contract completion; do not blanket-change to medium. |
| 2a. Add a revision pass | **Partially confirmed** | Findings reach downstream primary stages through notes/handoff, but there is no conditional same-artifact revision. The synthetic proposal/revision/final shape does not prove acceptance-driven revision: its test writes `# approved` and directly creates a bundle (`test_responses_runner_v2_example_pack.py:83-122`). Current acceptance also requires changes to be already applied (`supervisor.py:960-986`, `:1053-1077`), so “accept then revise” is backwards. Use a pre-acceptance revision directive, revise, re-review, then accept. |
| 2b. Reviewer repair and shared enums | **Partially confirmed** | One format-only repair is justified; current parse/schema failures immediately block (`supervisor_agents.py:1141-1195`) and the coercion layer is large. Four of seven prompt files contain some enum guardrail, but only three role prompts are loaded; review-kind prompts and declared shared instructions are dormant. Compose active prompts first, then single-source guardrails. |
| 2c. Claude search tools and command templates | **Confirmed** | Claude is hardcoded to `--tools Read`; templates contribute only timeout while argv/posture are hardcoded (`supervisor_agents.py:950-986`). Add Grep/Glob only after immutable read-only enforcement. Render a typed allowlisted posture rather than executing arbitrary template argv. |
| 2d. Better recommendation dedup | **Confirmed** | `_recommendation_key()` is exact text + artifact-list matching (`supervisor.py:825-832`). Add deterministic normalization and advisory related clusters, preserving originals and provenance. |
| 3a. Deterministic playbook validator | **Confirmed** | Stage 4 spends model attention on exact columns, IDs, paths, pipes, and nonempty cells (`stage4_gate_and_contract_review.md:5-19`), all suited to code. Add a generic validator hook and a pack-specific built-in; keep semantic judgments with reviewers. |
| 3b. Fix gstack Stage 2 grounding | **Partially confirmed** | The defect is fully real: the prompt demands source/tests/docs/config/package/CI and commands but attaches only `README.md` and the playbook contract (`inputs/stage2.input_manifest.json:9-19`) with no tool. A tree alone is insufficient to verify behavior/commands. Add bounded inventory plus selected file contents or a constrained read/search tool. |
| 3c. Expand scaffold lints | **Partially confirmed** | Prompt semantic lint is mostly `<80` words (`supervisor.py:400-410`), so contract, token, topology, duplicate, orphan, and Markdown checks are valuable. Correction: a five-backtick close validly closes a four-backtick opening in three prompts. The actual malformed case is `corpus/final_deliverable_contract.md:111-121`. Use a CommonMark-aware parser. A primary prompt need not promise machine JSON when a separate sidecar owns extraction; lint source-field coverage instead. Orphan tools should warn, not auto-delete. |
| 4a. Usage/cache/cost telemetry | **Confirmed** | Usage is rendered but not aggregated (`artifacts.py:317-341`; `workflow.py:669-705`). Additionally, the renderer incorrectly reads Chat-style `prompt_tokens_details`/`completion_tokens_details`; Responses reports `input_tokens_details`/`output_tokens_details`. Persist per-attempt and run totals; version any pricing estimate. |
| 4b. Fix prompt-cache keys | **Partially confirmed** | Stage-specific keys prevent deliberate cache affinity (`workflow.py:398-401`; `sidecar.py:304`). Official guidance says exact prefix matches and a shared key improve routing for requests with common prefixes ([Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching)). Use a stable key per compatible lane/model/version, not one blind key for primary and sidecar. Instrument first. Current official model pages show cached-input discounts for `gpt-5.5` but not `gpt-5.5-pro`, so the primary-pro benefit is latency, not necessarily money ([GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5), [GPT-5.5 Pro](https://developers.openai.com/api/docs/models/gpt-5.5-pro)). |
| 4c. Deterministic citation validity | **Partially confirmed** | The grounding rule exists (`contracts.py:202-208`), but regexing every path-like string will misclassify proposed deliverables and commands. Introduce explicit evidence references or a structured claim/source sidecar, then validate paths/hashes against the resolved manifest. |
| 4c. Rubric grader | **Confirmed as optional eval** | Current evals check shape/substrings and the freeze gate trusts existence plus top-level `passed` (`run_responses_v2_eval.py:42-165`, `:175-231`). Add a pinned strict-schema judge with human calibration and paired A/B use. Do not make one nondeterministic self-grader the sole production gate. |
| 5. Token preflight bypass | **Confirmed, understated** | README and multiple runbooks prescribe `--skip-token-count`; committed task packs enable preflight but omit `max_input_tokens`, so count success is observational and retryable service failure may continue. The Northset item is a runbook scaffold, not a runnable pack, though it sets preflight false. Add local pre-upload context fit plus explicit critical-pack budgets. |
| 5. `create_response` retry can duplicate | **Confirmed, proposed recovery corrected** | Generic retries cover ambiguous POST failures and no response ID is persisted until the call returns. Simply routing to resume/refresh cannot work without an ID. Add submit intent, method-specific retry, documented idempotency if available, and `submission_outcome_unknown`. |
| 5. Parallel reviewers/incremental snapshots | **Partially confirmed** | Reviewers are serial and each does before/after hashing (`supervisor.py:780-795`; `supervisor_agents.py:988-1072`). Current template timeouts are 3600s, not 1800s, and snapshots exclude ignored paths, so “four full-workspace snapshots” is inaccurate. Parallelize after integrity fixes. Do not make size+mtime the trust boundary; always hash declared immutable inputs. |
| 5. Composite review-cycle command | **Confirmed** | Current CLI requires separate operator, reviewers, and consolidation calls. Add a resumable composite through consolidation only; acceptance remains separate. |
| 5 minor. Move Codex job to stdin | **Confirmed** | Full prompt and up to 512 KiB embedded source ride in argv and argv is copied into session state (`supervisor_agents.py:50-57`, `:960-962`; `supervisor.py:746-758`, `:800-813`). Move to stdin; record only hash/size and effective posture. |
| 5 minor. Add gstack CI fixture | **Not confirmed** | CI has no named gstack step, but it runs full unittest discovery, and `test_responses_runner_v2_gstack_pack.py:51-68` already constructs a fixture and calls dry-run. A named step could improve visibility, but the functional gap does not exist. Improve dry-run fidelity instead. |
| 5 minor. Request reasoning summaries | **Experiment** | Current requests set effort only. A summary is model-generated diagnostic context, not the model's private reasoning or durable factual evidence. Add only behind config and telemetry; never put it in clean carry-forward or failure authority. |
| 5 minor. E2E terminal high vs xhigh | **Confirmed contradiction** | Workflow default is xhigh but Stage 4 overrides high (`four_stage.workflow.json:13-19`, `:111-119`). The pack calls all stages high-stakes, while its design summary documents the high exception. Resolve with measured evidence and consistent docs/config; do not silently flip it. |

## 5. General-purpose product gaps

The engine can run more than coding tasks, but its first-class contracts still assume a critical repository implementation workflow.

### 5.1 Vocabulary and input model

`COMMON_RUNNER_INSTRUCTIONS` calls every job a “high-stakes repository task,” defines evidence as repo-local files, and requires repository-relative citations (`contracts.py:202-212`). `Attached Repository Files` is a hardcoded role.

Recommendation for a v2-compatible evolution:

- Preserve the four-level authority order.
- Introduce domain-neutral `Attached Workspace Evidence` as the core concept, with `repository_file` as one evidence kind.
- Make citation policy explicit per output contract: repository path, document/page, URL, record ID, dataset row, or mixed structured evidence reference.
- Keep current labels as aliases for v1 pack compatibility.

### 5.2 Stage-scoped inputs

Every operator-supplied primary input and reference is appended to every stage (`workflow.py:749-769`). That wastes attention and prevents a later stage from receiving a new authoritative input without repeating it everywhere.

Add named bindings:

```json
{
  "binding_id": "approved_brief",
  "path": "brief.md",
  "authority": "primary_job_input",
  "scope": {"type": "stages", "stage_ids": ["intake", "synthesis"]}
}
```

Freeze bindings at run creation; require an explicit amendment record to add or change one mid-run.

### 5.3 Assurance profiles, not one hardcoded worldview

Scaffold examination currently requires exact model defaults and every non-terminal stage to be review-gated (`supervisor.py:356-398`). Those are appropriate for the current critical lane but too rigid for all general-purpose use.

Add explicit profiles:

| Profile | Intended use | Default gate posture |
|---|---|---|
| `critical` | High-stakes or irreversible work | Existing three-agent review, fail-closed, xhigh where configured, deterministic validators, full evidence |
| `reviewed` | Important deliverables needing independent review | Configurable reviewer quorum, evidence and validators, operator acceptance |
| `standard` | Ordinary staged work | Pack-defined gates, one optional review lane, normal budgets |
| `fast` | Low-risk drafts/transformations | Minimal deterministic validation, explicit non-critical label |

Existing packs should default to `critical`; reduced profiles must record their assurance level and must not masquerade as critical.

### 5.4 Generic final delivery

`final_implementation_bundle.v1` requires emitted implementation files, red/green phases, model migration, failure-policy cases, and rollout instructions (`schemas/final_implementation_bundle.schema.json:6-21`, `:43-188`). Non-coding work must invent false implementation fields.

Create `final_delivery_bundle.v1` with:

- subject and workflow lineage;
- typed deliverables and their hashes/URIs;
- input/evidence manifest hash;
- deterministic validation results;
- review decisions and acceptance hashes;
- assurance profile;
- residual risks and next actions.

Then make `final_implementation_bundle` a stricter extension/profile with emitted files, red/green evidence, migration, and rollout fields.

### 5.5 A real non-coding proof pack

The synthetic pack is still repository/implementation-oriented. Add an offline evidence-synthesis pack using several documents with conflicting claims:

```text
intake -> source map -> evidence reconciliation -> draft -> independent review
       -> revision directive -> revised delivery -> final generic bundle
```

It should demonstrate page/document citations, stage-scoped inputs, deterministic source-reference validation, accepted/rejected recommendations, and no implementation-file fiction.

### 5.6 Data handling is part of the assurance contract

A general-purpose runner will receive more than source code. It may handle customer documents, strategy, policy, financial records, or other sensitive inputs. Today it durably records request payloads, raw responses, manifests, sidecars, reviewer stdout/stderr, and—on the Codex path—the full argv, using ordinary process-default file permissions. Pack-controlled API `store`, uploaded-file expiry, and cleanup behavior are not unified under an assurance policy.

Add a data-handling block to every assurance profile:

- sensitivity classification and allowed evidence kinds;
- owner-only local file/directory permissions;
- whether raw request, response, sidecar, reviewer, and reasoning-summary artifacts may be retained;
- redaction rules that preserve a hash-bound sealed original when audit evidence is required;
- allowed API `store` behavior, file purpose, expiry, deletion, and zero-retention constraints;
- an evidence-preserving purge operation that writes a tombstone manifest of deleted artifact hashes rather than silently erasing lineage.

This must be enforced by the request planner and persistence layer, not left only to pack prose. Test secret fixtures, permissive umask, reviewer command records, remote cleanup failures, and purge/recovery behavior.

## 6. Measurement and eval design

### 6.1 Fix telemetry correctness first

The renderer currently reads `prompt_tokens_details` and `completion_tokens_details` (`artifacts.py:317-335`), but Responses usage uses `input_tokens_details` and `output_tokens_details`. A local completed response records cached tokens `0` and reasoning tokens `6458` in JSON while its Markdown header shows both as `None`.

Add a normalized attempt record:

```json
{
  "lane": "primary|sidecar|reviewer",
  "attempt_id": "...",
  "model": "...",
  "input_tokens": 0,
  "cached_tokens": 0,
  "reasoning_tokens": 0,
  "output_tokens": 0,
  "cache_write_tokens": 0,
  "uploaded_files": 0,
  "uploaded_bytes": 0,
  "request_wall_ms": 0,
  "poll_wall_ms": 0,
  "retry_count": 0,
  "status": "..."
}
```

Keep primary, sidecar, and reviewer usage separate as well as aggregated. Monetary cost must use a dated/versioned price table and be labelled an estimate.

### 6.2 Current evidence baseline

One historical four-stage local run under `.local/automation/responses_runner_v2/runs/2026-04-26_224624_...` provides a useful, non-generalizable baseline:

| Stage | `response.final.md` bytes | Assistant-body characters | Markdown overhead |
|---|---:|---:|---:|
| 1 | 99,219 | 50,699 | 48,520 |
| 2 | 100,321 | 72,973 | 27,348 |
| 3 | 379,079 | 350,473 | 28,606 |
| 4 | 408,483 | 363,501 | 44,982 |
| Total | 987,102 | 837,646 | 149,456 (15.1%) |

Stage 1's model-facing Markdown was almost twice its assistant body because evidence metadata was appended. The Stage 2 resolved manifest also attached Stage 1's `response.final.md` once as Reviewed Handoff and once as Reference Context. All four responses reported different prompt-cache keys and `cached_tokens=0`. This does not prove causality, but it is sufficient to justify instrumentation and an A/B test.

### 6.3 Metrics that matter

#### Integrity metrics

- duplicate submissions: exactly zero;
- progression without finalized required artifacts: exactly zero;
- cycles advancing with missing/invalid required verdicts: exactly zero;
- unresolved blockers lost across transitions: exactly zero;
- final bundles with missing/hash-mismatched references: exactly zero.

#### Focus and quality metrics

- model-facing input tokens and bytes by authority role;
- duplicated bytes/tokens by path and content hash;
- envelope-to-body and sidecar-input amplification ratios;
- deterministic validator pass rate by rule;
- grounded evidence-reference precision/recall;
- human-rated correctness, completeness, focus, unsupported-claim rate, and revision incorporation;
- reviewer agreement and blocker resolution rate.

#### Performance metrics

- stage and review-cycle wall time;
- primary/sidecar/reviewer input, cached, reasoning, and output tokens;
- upload count/bytes/reuse, retry count, and cleanup success;
- cache hit ratio for cache-compatible shared prefixes;
- estimated cost using a pinned pricing snapshot.

### 6.4 Representative eval lane

Build a frozen corpus spanning:

1. critical coding/implementation;
2. repository planning;
3. multi-document research/evidence synthesis;
4. policy or operational decision support;
5. document/report generation;
6. ordinary low-risk staged transformation.

For each change, run paired A/B trials on identical frozen inputs and seeds/settings where applicable:

- deterministic contract and citation checks first;
- blinded human comparison for correctness/focus/completeness;
- pinned strict-schema rubric judge as advisory corroboration;
- store judge model, prompt, rubric, input, output, and hashes;
- calibrate judge scores against a human-labelled gold subset before using them for release decisions.

The current freeze gate must also verify expected workflow/case IDs, result schemas, artifact hashes, and all required cases; it should not trust any file with `passed: true`.

## 7. Implementation sequence

Persisted-state compatibility is a cross-cutting exit gate, not cleanup work. Before the first schema/state change, freeze representative v1 run, checkpoint, review-bundle, and supervisor-session fixtures and publish a format matrix for each PR: readable unchanged, migrated from an immutable backup, or deliberately blocked with recovery instructions. Never silently load old bytes under new semantics.

### PR 1: State-safety regression suite and engine transition fix

- Freeze v1 run/checkpoint fixtures and implement the migration-or-explicit-rejection path for every state/directory change in this PR before changing live formats.
- Reproduce explicit-stage duplicate submission, ambiguous submission, preflight partial persistence, refresh-without-finalization, and queued classifier behavior.
- Introduce attempt IDs/directories and remote-vs-local status.
- Add collision-safe run/attempt allocation and a per-run submission lock/CAS spanning stage selection through durable submission state.
- Make state writes atomic and fail closed.
- Add an intent-journaled cancel operation for known live response IDs, followed by refresh/finalization and cleanup.

Exit gate: affected v1 runs/checkpoints follow the published compatibility path; no path, including two concurrent processes, can issue a second POST while an attempt is live or unknown; no run is complete before required artifacts exist; cancellation is idempotent and cannot conceal an unknown submission.

### PR 2: Supervisor gate truth and provenance

- Require successful exact-cycle decisions.
- Preserve blockers and add explicit resolution records.
- Bind job, subject, decisions, consolidation, acceptance, and bundles by hashes.
- Verify final bundle references and emitted/delivered artifact hashes.
- Add supervisor-owned `launch-stage` and `rerun-stage` operations that enforce scaffold launch eligibility, register runs in the session, apply recovery policy, and refuse direct unregistered progression in supervised mode.

Exit gate: every negative/absent reviewer fixture blocks; stale or foreign artifacts cannot advance or finalize; supervised launch/rerun cannot bypass the session gate.

### PR 3: Read-only enforcement, frozen inputs, and concurrency-safe state

- Explicit reviewer sandbox and immutable `.local` target hashes.
- One frozen job for both reviewers.
- Freeze workflow assets/runtime/upload bytes.
- Atomic fsync/replace, locks/CAS, collision-resistant IDs.
- Move all staging under the workspace root.
- Make standards-compliant raw-schema validation a required runtime dependency and validate before dataclass coercion; test minimal and development installations for identical behavior.
- Introduce the minimal assurance/data-policy schema here, defaulting every existing pack to `critical`; broader `reviewed`, `standard`, and `fast` behavior remains PR 8 work.
- Define profile-owned local permissions, artifact retention/redaction, and migration behavior for every changed persisted contract.

Exit gate: review and run results are byte-reproducible; concurrent operations conflict safely; v1 fixtures are either safely migrated or rejected with explicit recovery; raw manifests have one accept/reject truth.

### PR 4: Clean context and telemetry

- `artifact.md` and schema migration.
- Immediate-predecessor dedup and stage-scoped inputs.
- Raw JSON defaults false; sidecar Markdown-only normal path.
- Usage normalizer, attempt telemetry, report command, prompt-cache key cap fix.
- Resolve the end-to-end pack's terminal `high` versus documented `xhigh` reasoning posture through a measured comparison, then synchronize workflow, docs, and tests.

Exit gate: A/B shows lower input with no deterministic/human quality regression; telemetry accounts for every attempt.

### PR 5: Input budgets, uploads, and request planner

- Local pre-upload estimate and context-window registry.
- Required critical input budgets.
- Per-file upload journal, cleanup recovery command, streaming.
- One pure planner for dry and live modes.
- Secret/symlink/binary safeguards.
- Enforce assurance-profile API `store`, remote file expiry/deletion, raw-artifact retention, and evidence-preserving purge/tombstone policy.

Exit gate: every live request shape is previewed offline; no uploaded file ID is lost.

### PR 6: Validators, grounding, and evals

- Typed validator hook and `markdown_playbook_v1` validator.
- Workspace inventory + gstack Stage 2 selected contents.
- CommonMark/topology/identity/duplicate/orphan lints.
- Explicit evidence-reference validation and strengthened freeze gate.
- Calibrated optional rubric lane.

Exit gate: known-bad structural artifacts fail deterministically; representative packs have usable grounding.

### PR 7: Primary-model revision and reviewer reliability

- Revision directive, primary revision, fresh review, then acceptance.
- Smaller reviewer output schema and one repair.
- Prompt composition and typed command templates.
- Codex stdin; Claude search tools.
- Deterministic grouping.

Exit gate: accepted findings change the artifact; rejected findings do not; all lineage is preserved.

### PR 8: Throughput and general-purpose product layer

- Parallel reviewers and resumable composite pre-acceptance command.
- Generic evidence vocabulary, expand assurance profiles beyond the already-enforced `critical` default, and add `final_delivery_bundle.v1`.
- Non-coding proof pack.
- Upload reuse after privacy/lifecycle tests.

Exit gate: the non-coding pack completes without repository/implementation fiction, and existing critical packs retain their fail-closed posture.

## 8. Acceptance-test catalog

In addition to the PR-specific gates above, the final program should include these enduring regression families:

1. **State model:** every state/event pair has an explicit allowed or rejected transition; crash injection at each durable write leaves a recoverable state.
2. **Submission:** explicit and automatic selection share the same guard; a barrier-start concurrency test permits exactly one POST; create retries are method-aware; unknown submission cannot rerun.
3. **Finalization:** required artifact contracts are stored before submission and checked before completion/reviewability.
4. **Review quorum:** every transport/schema/read-only failure blocks; exact subject identity is mandatory.
5. **Blocker lifecycle:** blocker resolution is explicit, evidence-backed, and hash-bound.
6. **Bundle truth:** every referenced path exists under root and every claimed hash matches.
7. **Root/inputs:** symlink escape, system-temp staging, secret-directory input, and hash-to-upload mutation all fail.
8. **Concurrency:** barrier-started processes get distinct attempts; CAS prevents lost session updates.
9. **Schema:** behavior is identical with the supported installation; strings are not coerced to booleans; unknown keys fail.
10. **Topology:** non-contiguous stage numbers, self/future handoffs, wrong manifest identity, and unacknowledged authority duplicates fail.
11. **Dry/live parity:** normalized request plans are identical except remote identifiers and time.
12. **Context:** clean artifact excludes all envelope sections; double attachment and out-of-scope runtime input tests are exact.
13. **Telemetry:** Responses usage keys render and aggregate correctly; retries and sidecars cannot disappear.
14. **Cache:** long IDs stay within the key cap; same compatible lane shares a key; incompatible lanes do not.
15. **Validators:** valid and invalid playbook fixtures cover columns/order, IDs, prerequisites, paths, pipes, required verification, and policy gates.
16. **Markdown lint:** four-open/five-close is accepted; the actual unclosed nested fence is rejected.
17. **Grounding:** explicit evidence citations resolve to manifest paths/hashes while proposed paths and commands are not misclassified.
18. **Revision:** one accepted and one rejected recommendation flow through revision and fresh review correctly.
19. **Eval freeze:** wrong workflow/case, missing case, wrong hash, forged `passed`, or schema-invalid result fails.
20. **Profiles:** a non-coding standard/reviewed task completes generically; critical packs remain unchanged unless explicitly migrated.
21. **Migration:** frozen v1 runs, checkpoints, bundles, and sessions are read unchanged, migrated from a verified backup, or rejected with exact recovery guidance according to the published matrix.
22. **Data lifecycle:** owner-only permissions survive a permissive umask; profile-disallowed raw/remote retention fails planning; purge leaves a hash-bound tombstone; cleanup can resume safely.
23. **Cancellation:** live, terminal, repeated, and unknown-submission cancel cases preserve truthful state, final artifacts, and upload lifecycle.
24. **Model posture:** the measured end-to-end terminal effort choice matches workflow configuration, documentation, and tests.

## 9. Revised “only do five things” list

If implementation capacity is limited, do these five packages in order:

1. **Truthful state machine:** explicit-stage guard, per-run submission lock/CAS, attempt namespaces, unknown-submission handling, cancellation, transactional preflight, and finalization-before-completion.
2. **Truthful review gate:** exact reviewer quorum, blocker preservation/resolution, subject lineage, supervisor-owned launch/rerun, and verified final bundle.
3. **Immutable inputs/review:** explicit read-only sandbox, `.local` target hashing, frozen job/assets/runtime, in-root staging, concurrency-safe writes, persisted-format migration, and profile-owned data lifecycle.
4. **Clean context plus measurement:** clean artifact, authority dedup, stage-scoped inputs, correct telemetry, and cache A/B.
5. **Deterministic quality loop:** validators, better grounding, faithful dry run, representative evals, and revision-before-final-acceptance.

This supersedes the team's proposed top five only in ordering and implementation detail. Its context, telemetry, validator, reviewer-repair, and revision recommendations remain valuable after the P0 transition guarantees are restored.

## 10. Evidence base and caveats

Review coverage included:

- all thirteen core engine/supervisor Python modules (7,991 lines);
- all twelve committed runner schemas;
- all six workflow manifests, their referenced prompts/manifests/tools/schemas, and the internal supervisor prompt/command pack;
- all ten committed documentation/runbook files;
- the eval CLI and dataset;
- the CI workflow and 64 test/fixture files;
- representative ignored local run artifacts for real usage/context evidence;
- recent git history around reviewer-output normalization;
- official OpenAI documentation for prompt caching, conversation chaining, and current GPT-5.5-family capabilities.

Verification performed during this review:

```text
python -m unittest discover -s automation/tests -p 'test_*.py'
Ran 115 tests in 2.033s
OK
```

No live model submission was needed. Performance recommendations based on cache keys, verbosity, rubric judging, upload reuse, and reasoning summaries remain hypotheses until measured on the representative A/B lane. The P0 state/gate findings are direct code-path findings and do not depend on model behavior.
