# Supervised Self-Improvement Pack Design Summary

This document is the public summary of the four-stage self-improvement pack in `automation/task_packs/responses_runner_v2_supervised_end_to_end/`.

The full local drafting scaffold that produced this pack is development provenance, not operator documentation. It is intentionally kept out of the publishable tree.

## Purpose

The pack asks the runner to improve itself by designing and producing an additive supervisor lane around the existing `responses_runner_v2` engine.

The supervisor lane does not replace the core engine. The core engine continues to own workflow loading, request construction, Responses API submission, resume/refresh, artifact finalization, sidecar extraction, and review-bundle validation.

The supervisor owns session state, scaffold staging, scaffold dry-run gating, operator and reviewer invocation, deterministic consolidation, selective acceptance, failure classification, archive-before-rerun evidence, human-pause records, and final bundle assembly.

## Four-Stage Shape

1. `architecture_and_supervision_protocol`
   Locks the architecture, boundary between engine and supervisor, review topology, failure policy, and model posture.
2. `agent_review_protocol_and_package_contract`
   Locks command contracts, reviewer prompts, JSON transport, acceptance evidence, and the expected final package contract.
3. `draft_drop_in_packet`
   Produces a complete draft implementation packet without reopening locked architecture unless repository evidence requires it.
4. `final_drop_in_packet`
   Produces the hardened terminal packet with schema, tests, docs, and rollout instructions.

## Review Topology

The target supervisor protocol uses:

- accountable operator Codex lane;
- independent read-only Codex review lane through `codex exec`;
- independent read-only Claude review lane through subscription-authenticated `claude -p`;
- deterministic consolidation;
- separate operator selective acceptance with applied-change evidence.

The supervisor creates approved review bundles only after operator acceptance.

## Current Tool Posture

In the current four-stage workflow, Stage 3 intentionally has no `tool_profile_file`. Stage 4 uses high reasoning and no web-search profile. Do not reintroduce Stage 3 web search unless a later approved handoff provides concrete safety evidence and explicitly reopens that decision.

## Failure Policy

The resulting supervisor distinguishes:

- `completed_complete_artifact`;
- `failed_complete_artifact`;
- `failed_no_artifact`;
- `incomplete_output_limit`;
- `blocked_token_preflight`;
- `long_running_monitoring_anomaly`.

A failed stage with a complete substantive artifact is reviewable. A failed stage without a substantive artifact may be rerun as-is only after archive-before-rerun evidence. Output-limit incomplete outcomes must not auto-progress.
