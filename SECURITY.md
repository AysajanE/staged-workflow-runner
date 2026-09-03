# Security Policy

## Supported Versions

Security fixes are handled on the current `main` branch until versioned releases are established.

## Reporting A Vulnerability

Please do not open a public issue with exploit details.

Use GitHub private vulnerability reporting or GitHub Security Advisories when available for this repository. If private reporting is not yet enabled, open a public issue that requests a private maintainer contact channel without including sensitive details.

Useful details to include privately:

- affected commit or release;
- affected command or workflow;
- whether subprocess execution, path traversal, API-key handling, uploaded files, or reviewer CLI invocation (`codex` or `claude`) is involved;
- minimal reproduction steps;
- expected impact.

We aim to acknowledge valid reports within 7 days and to provide a remediation plan or status update within 30 days.

## Security-Relevant Design Constraints

- Secrets belong in environment variables or ignored `.env` files, never in task packs or run artifacts intended for publication.
- Run outputs under `.local/` (`run_manifest.json`, per-attempt files such as `request_payload.json`, `uploads.json`, `response.final.json`, and `artifact.md`, `review/` evidence, and handoff notes) may contain request payloads, uploaded-file metadata, response content, and reviewer notes. Treat them as sensitive by default.
- Reviewer CLIs are invoked read-only (`codex exec --sandbox read-only --ephemeral`, `claude -p --tools Read,Grep,Glob`). Report any path where a reviewer can mutate tracked source without detection.
- All workflow, handoff-note, and run-artifact paths should remain under one exact workspace root.
