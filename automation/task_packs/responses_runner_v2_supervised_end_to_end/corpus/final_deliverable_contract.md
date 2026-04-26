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
