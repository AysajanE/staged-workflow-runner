# Prompting Guidance Reference

This file records the official-source prompting constraints that this task pack must preserve.

## OpenAI GPT-5.5 Guidance

Official source URLs:

- https://developers.openai.com/api/docs/guides/latest-model
- https://developers.openai.com/api/docs/guides/prompt-guidance
- https://developers.openai.com/api/docs/models/gpt-5.5
- https://developers.openai.com/api/docs/models/gpt-5.5-pro
- https://developers.openai.com/codex/noninteractive
- https://developers.openai.com/codex/cli/reference

Design implications for this task:

- Prompt for outcome, success criteria, constraints, allowed side effects, evidence rules, output format, and stopping conditions.
- Do not copy older process-heavy prompt stacks blindly.
- Use `gpt-5.5-pro` for high-stakes long-running primary generation.
- Use `gpt-5.5` for structural or lower-latency processing when sufficient.
- Use `reasoning_effort: xhigh` only where maximum intelligence matters more than latency/cost.
- Use `codex exec` as the canonical non-interactive Codex CLI command.
- Treat `codex -exec` as a local compatibility alias only if the team's wrapper supports it.

## Anthropic Claude Guidance

Official source URLs:

- https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/settings

Design implications for this task:

- Claude prompts should be clear and direct.
- Use XML tags to separate instructions, context, inputs, and output contract.
- Give Claude a role.
- For non-interactive automation, use `claude -p` or `claude --print`.
- Prefer `--bare` for scripted calls when deterministic context loading is important.
- Use `--output-format json` and `--json-schema` when machine-ingestible output is required.
- Use `--append-system-prompt-file` for additional role/task instructions while preserving Claude Code's built-in behavior, unless the implementation has a strong reason to replace the system prompt.
- Use `--effort max` for the highest available Opus reasoning mode when supported; otherwise fall back explicitly to `--effort xhigh`.

## Required Prompt Style For Final Implementation

The final implementation must produce prompt artifacts that are:

- concise enough for GPT-5.5 and Claude Opus to follow;
- explicit enough for non-interactive operation;
- structured enough to prevent review drift;
- grounded in artifacts and repo paths;
- stable under command-line execution;
- compatible with JSON sidecar extraction.
