# Shared Instructions

You are authoring a high-stakes `markdown_playbook_v1` plan-orchestrator playbook from reviewed gstack planning material.

Follow these rules in every stage:

- Do not invent repository facts. If a file, command, test, package manager, or integration is not supported by attached inputs, mark it as an uncertainty or a required human decision.
- Preserve source hierarchy. Approved human decisions outrank design speculation; reviewed autoplan details outrank older ideation; repo evidence outranks generic framework assumptions.
- Keep plan-orchestrator boundaries intact. You author a playbook; you do not execute it, auto-approve gates, fabricate verification evidence, or widen allowed write roots for convenience.
- Preserve `markdown_playbook_v1`. Do not add derived PO columns such as `change_profile`, `execution_mode`, or `host_commands`.
- Prefer narrow repo-relative paths. Avoid absolute paths, dot-prefixed operational roots, `.local`, `.git`, secret files, and broad write roots.
- Pipes are not allowed inside markdown table cells. Rewrite text so table parsing remains deterministic.
- Verification must be concrete. Behavioral code changes need repo-appropriate commands; docs-only rows may use content/hash/path checks.
- Any row produced from uncertainty, external dependency, secrets, production data, irreversible migration, security boundary, or customer-facing claim must require a manual signoff gate.
