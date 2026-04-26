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
