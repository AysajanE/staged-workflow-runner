# Responses Runner V2 Supervisor Internal Pack

This internal pack contains prompt and command artifacts used by the supervised end-to-end lane for `responses_runner_v2`.

It is not a normal paid-stage workflow pack. It is the supervisor's local instruction and command-template library for:

- accountable operator Codex jobs;
- read-only Codex review-agent jobs;
- read-only Claude review-agent jobs;
- deterministic consolidation;
- scaffold authoring/improvement;
- intermediate stage-output review;
- final implementation-packet review.

## Model Defaults

Supervisor-generated workflows and scaffolds use:

- primary generation: `gpt-5.5-pro`;
- structural processing: `gpt-5.5`;
- committed GPT-5.5-family prompt cache retention: `24h`;
- high-stakes primary reasoning effort: `xhigh`;
- structural reasoning effort: `high` or `medium`;
- max output for locked high-stakes self-improvement stages: `128000`.

## Files

- `shared_instructions.md` — shared authority, evidence, side-effect, and output rules.
- `prompts/operator_codex.md` — accountable operator Codex prompt.
- `prompts/codex_review.md` — independent read-only Codex review prompt.
- `prompts/claude_review.md` — independent read-only Claude review prompt with XML structure.
- `prompts/review_consolidation.md` — consolidation policy.
- `prompts/scaffold_author_improver.md` — scaffold author/improver guidance.
- `prompts/stage_output_review.md` — intermediate stage-output review guidance.
- `prompts/final_packet_review.md` — final packet review guidance.
- `commands/operator_codex.command.json` — canonical `codex exec` operator template.
- `commands/codex_review_agent.command.json` — canonical read-only `codex exec` reviewer template.
- `commands/claude_review_agent.command.json` — canonical subscription-authenticated `claude -p` reviewer template.
- `commands/consolidation.command.json` — deterministic supervisor consolidation command template.

## JSON Transport

Agent commands use stdout JSON as the canonical transport. The supervisor captures stdout/stderr, parses stdout as a JSON review decision, validates it against `automation/responses_runner_v2/schemas/review_decision.schema.json`, then writes the validated JSON sidecar and markdown report under the supervisor session directory.

Missing stdout JSON, malformed JSON, schema-invalid JSON, mismatched output paths, nonzero exit, timeout, or read-only violation is a hard failure.

## Read-Only Review Enforcement

Codex and Claude review agents are review-only. They may not edit repository files or produce patches. The supervisor takes a workspace snapshot before and after each reviewer command, excluding `.local` supervisor-owned artifacts, and fails the review if source files change. The Claude lane intentionally does not use `--bare`, because bare mode skips OAuth/keychain reads; the supervisor strips higher-precedence API credential environment variables so subscription OAuth from local `claude` login is used.

## Review Sequence

For every scaffold and every non-terminal stage:

1. operator Codex prepares a provisional review and bundle;
2. Codex review agent independently reviews;
3. Claude review agent independently reviews;
4. consolidation classifies findings without final acceptance;
5. operator Codex accepts only supported recommendations with applied-change evidence;
6. supervisor creates the approved review bundle or blocks progression.

The same topology applies to final packet review, except that the terminal output is a final implementation bundle rather than a next-stage bundle.

## Human Pauses

After the initial clarification gate, human pauses are exception paths only. A pause artifact must state:

- exact trigger;
- artifact to present;
- decision required;
- safe continuation action;
- whether automation may resume;
- whether review-bundle creation is blocked.
