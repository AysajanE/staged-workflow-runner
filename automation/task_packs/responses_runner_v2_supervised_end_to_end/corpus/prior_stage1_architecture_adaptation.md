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
