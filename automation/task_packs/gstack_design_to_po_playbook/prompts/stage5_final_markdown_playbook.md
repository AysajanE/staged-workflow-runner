# Stage 5 - Final Markdown Playbook

Emit only the final `markdown_playbook_v1` markdown document.

Requirements:

- Use the required sections and execution table columns from the contract summary.
- Do not include commentary before or after the playbook.
- Do not include code fences around the playbook.
- Do not use pipe characters inside cell content except as markdown table delimiters.
- Include explicit phase details for manual gates and verification commands.
- Include immediate next actions that start with saving the artifact under `docs/playbooks/<slug>.playbook.md`, then running PO `list-items`, then running PO `doctor --playbook`.
- Do not claim PO verification has passed unless the operator provided actual PO output as an input.
