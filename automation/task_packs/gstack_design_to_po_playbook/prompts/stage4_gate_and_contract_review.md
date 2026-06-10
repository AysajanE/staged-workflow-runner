# Stage 4 - Gate And Contract Review

Harden the candidate rows into a contract-compliant playbook plan.

Review and correct:

1. `markdown_playbook_v1` section structure and table columns;
2. sequential step ids and prerequisite references;
3. absolute paths, dot-prefixed paths, `.local`, `.git`, secret paths, and broad write roots;
4. pipes inside table cells;
5. docs-only rows that accidentally imply behavioral approval;
6. behavioral rows without concrete verification;
7. rows that need manual signoff gates;
8. missing PO post-output checks.

The corrected execution table must include `required_verification_commands`.
Verification commands in prose or phase details are useful context but are not
contract-compliant substitutes for non-empty row cells. If the draft rows omit
the column, add it here before approving the preflight packet.

Output a final preflight packet: corrected rows, section outline, gate list, verification list, and any residual warnings. Do not execute PO.
