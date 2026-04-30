# Security Policy

## Supported Versions

Security fixes are handled on the current `main` branch until versioned releases are established.

## Reporting A Vulnerability

Please do not open a public issue with exploit details.

Use GitHub private vulnerability reporting or GitHub Security Advisories when available for this repository. If private reporting is not yet enabled, open a public issue that requests a private maintainer contact channel without including sensitive details.

Useful details to include privately:

- affected commit or release;
- affected command or workflow;
- whether subprocess execution, path traversal, API-key handling, uploaded files, or supervisor review automation is involved;
- minimal reproduction steps;
- expected impact.

We aim to acknowledge valid reports within 7 days and to provide a remediation plan or status update within 30 days.

## Security-Relevant Design Constraints

- Secrets belong in environment variables or ignored `.env` files, never in task packs or run artifacts intended for publication.
- Run artifacts under `.local/` may contain request payloads, uploaded-file metadata, response artifacts, and review notes. Treat them as sensitive by default.
- Supervisor review agents are intended to be read-only. Report any path where a read-only reviewer can mutate tracked source without detection.
- All workflow and supervisor paths should remain under one exact workspace root.
