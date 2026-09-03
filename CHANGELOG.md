# Changelog

## Unreleased

- Fixed the exact token preflight (`POST /responses/input_tokens`) by projecting the payload onto accepted fields.
- `run` and `resume` now wait in-process by default (poll every 20 s); `--no-wait` returns after submission. `run --dry-run` without `--stage` renders every stage's request under `<run_dir>/dry_runs/`, using placeholder handoffs under `dry_runs/stubs/`.
- Post-output validators (for example `evidence_references_v1`) are advisory: results go to `validator_report.json` and the manifest fields `validators_passed` / `validator_report_path`, and never block finalization.
- Added `reviewed` and `human` stage gates. A `reviewed` stage is judged by one reviewer CLI (`codex` default, or `claude`; configured by `defaults.review` / per-stage `review`, overridable with `--reviewer`) that returns a JSON verdict; `revise` triggers one primary-model revision attempt, and a second `revise` blocks the stage until a human supplies `--handoff-note`. A `human` stage stops the run until `--handoff-note` is supplied. Carry-forward gained `handoff_from_stage_id`. Run-manifest stage summaries gained `review_status`, `reviewer_notes_path`, `review_verdict_path`, `handoff_note_path`, `validators_passed`, `validator_report_path`, and `attempts[].revision_of_attempt_id`.
- Removed the supervisor lane (sessions, scaffold staging, operator Codex lane, three-agent review loop, consolidation, final bundles, human-pause and monitoring records) and its task packs and schemas: on the real supervised run it cost 282 reviewer-agent minutes against 32 minutes of primary model time and never changed the primary output. `review_required` is now loaded as a `human` gate; the review-bundle CLI (`automation/create_review_bundle_v2.py`) and the `--review-bundle` flag were removed.
- The gstack playbook pack now uses `reviewed` gates on stages 1-4 with `handoff_from_stage_id` and a `terminal` stage 5; CI dry-runs every stage of it.
- `run_manifest.json` is the single durable record per run, written atomically on every stage transition. Operator overrides are stored in it, so `resume` rebuilds runtime options from the manifest.
- Removed `run_contract.json`, `request_plan.json`, `local_context_estimate.json`, `stage_checkpoint.json`, submission intents, the stage-state transition journal, content-addressed sidecars, `response.final.md`, sidecar structured extraction, the `purge` and `usage-report` subcommands, and the v1 run/workflow manifest schemas.
- Removed the local byte-per-token estimate entirely; the exact `POST /responses/input_tokens` preflight is the only token check.
- Assurance profiles (`critical`, `reviewed`, `standard`, `fast`) now only set `fail_closed` and `require_input_budget`.
- Removed `docs/design/persisted-format-compatibility.md`.

## 0.1.0 - 2026-05-01

- Initial public-preparation release of the staged workflow runner, synthetic proof pack, and additive supervisor lane.
