from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from . import telemetry
from .contracts import REVIEW_DECISION_SCHEMA_VERSION, relpath, resolve_under_root, runner_now, sha256_file, sha256_text
from .supervisor_artifacts import (
    SchemaValidationError,
    diff_snapshots,
    snapshot_workspace,
    validate_against_schema,
    write_diff,
    write_json_artifact,
    write_text_artifact,
)

INTERNAL_PACK_ROOT = "automation/task_packs/responses_runner_v2_supervisor_internal"

COMMAND_TEMPLATE_BY_ROLE = {
    "operator_codex": f"{INTERNAL_PACK_ROOT}/commands/operator_codex.command.json",
    "codex_review_agent": f"{INTERNAL_PACK_ROOT}/commands/codex_review_agent.command.json",
    "claude_review_agent": f"{INTERNAL_PACK_ROOT}/commands/claude_review_agent.command.json",
}

_CANONICAL_COMMAND_BY_ROLE = {
    "operator_codex": "codex exec",
    "codex_review_agent": "codex exec",
    "claude_review_agent": "claude -p",
}

_ALLOWED_ARGV_TEMPLATE_BY_ROLE = {
    "operator_codex": [
        "codex",
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--ephemeral",
        "--ignore-user-config",
        "-c",
        'model_reasoning_effort="high"',
        "--output-schema",
        "automation/responses_runner_v2/schemas/reviewer_output.schema.json",
        "-",
    ],
    "codex_review_agent": [
        "codex",
        "exec",
        "--model",
        "gpt-5.6-sol",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--ignore-user-config",
        "-c",
        'model_reasoning_effort="high"',
        "--output-schema",
        "automation/responses_runner_v2/schemas/reviewer_output.schema.json",
        "-",
    ],
    "claude_review_agent": [
        "claude",
        "-p",
        "--model",
        "opus",
        "--effort",
        "max",
        "--output-format",
        "json",
        "--tools",
        "Read,Grep,Glob",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
        "--setting-sources",
        "user",
        "--append-system-prompt-file",
        "{composed_prompt_file}",
    ],
}

_ALLOWED_CLAUDE_FALLBACK_ARGV_TEMPLATE = [
    *(_ALLOWED_ARGV_TEMPLATE_BY_ROLE["claude_review_agent"][:5]),
    "xhigh",
    *(_ALLOWED_ARGV_TEMPLATE_BY_ROLE["claude_review_agent"][6:]),
]

PROMPT_BY_ROLE = {
    "operator_codex": f"{INTERNAL_PACK_ROOT}/prompts/operator_codex.md",
    "codex_review_agent": f"{INTERNAL_PACK_ROOT}/prompts/codex_review.md",
    "claude_review_agent": f"{INTERNAL_PACK_ROOT}/prompts/claude_review.md",
}

PROMPT_BY_REVIEW_KIND = {
    "scaffold": f"{INTERNAL_PACK_ROOT}/prompts/scaffold_review.md",
    "stage_output": f"{INTERNAL_PACK_ROOT}/prompts/stage_output_review.md",
    "final_packet": f"{INTERNAL_PACK_ROOT}/prompts/final_packet_review.md",
    "recovery": f"{INTERNAL_PACK_ROOT}/prompts/recovery_review.md",
    "operator_acceptance": f"{INTERNAL_PACK_ROOT}/prompts/operator_acceptance.md",
    "consolidation": f"{INTERNAL_PACK_ROOT}/prompts/review_consolidation.md",
}

SHARED_PROMPT_PATH = f"{INTERNAL_PACK_ROOT}/shared_instructions.md"
REVIEWER_OUTPUT_SCHEMA_PATH = "automation/responses_runner_v2/schemas/reviewer_output.schema.json"
MODEL_OWNED_OUTPUT_KEYS = {
    "status",
    "approval_decision",
    "summary",
    "reviewed_artifacts",
    "missing_artifacts",
    "blocking_issues",
    "non_blocking_improvements",
    "recommendations",
    "unsupported_claims",
    "evidence",
    "validation_errors",
    "next_action",
}

# Exit codes that indicate the agent process was killed by an external
# signal (outer timeout, operator Ctrl-C, supervisor host shutdown) rather
# than the agent rejecting or failing the review on its own.
INTERRUPT_SIGNAL_BY_EXIT_CODE = {
    143: "SIGTERM",
    -15: "SIGTERM",
    130: "SIGINT",
    -2: "SIGINT",
}

# Exit code recorded when the agent CLI executable could not be spawned.
MISSING_CLI_EXIT_CODE = 127

# Decision statuses that describe a transport-layer delivery failure: the
# reviewer never produced a verdict. These sidecars carry empty
# blocking_issues (there is no reviewer finding to block on) and a
# validation_errors entry naming the transport cause, so downstream
# consumers can distinguish them from a real reviewer rejection and from
# malformed_output.
TRANSPORT_FAILURE_STATUSES = {"missing_cli", "interrupted"}

# Optional job field carrying verbatim source text of cited authority
# files: a list of {path, sha256, content} objects. The supervisor passes
# the field through to reviewers unmodified (inside the job JSON) so
# read-only reviewers can run grounding checks instead of blocking on
# missing source text. The passthrough is bounded so a single job cannot
# blow the agent CLI argument/stdin budget.
EMBEDDED_SOURCES_KEY = "embedded_sources"
MAX_EMBEDDED_SOURCES_BYTES = 512 * 1024


class AgentOutputError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentRunResult:
    command_id: str
    actor_role: str
    status: str
    approval_decision: str
    decision_path: str
    markdown_path: str
    stdout_path: str
    stderr_path: str
    command: dict[str, Any]
    read_only_check: dict[str, Any] | None
    fallback_used: bool = False
    usage_attempt_path: str | None = None


REVIEW_DECISION_TOP_LEVEL_KEYS = {
    "schema_version",
    "decision_id",
    "created_at",
    "supervisor_session_id",
    "workflow_id",
    "run_id",
    "stage_id",
    "review_cycle_id",
    "review_kind",
    "actor_role",
    "agent_command_id",
    "status",
    "approval_decision",
    "summary",
    "markdown_report_path",
    "json_report_path",
    "reviewed_artifacts",
    "missing_artifacts",
    "blocking_issues",
    "non_blocking_improvements",
    "recommendations",
    "unsupported_claims",
    "evidence",
    "command",
    "read_only_check",
    "validation_errors",
    "next_action",
}


def load_command_template(root: Path, actor_role: str) -> dict[str, Any]:
    path = resolve_under_root(root, COMMAND_TEMPLATE_BY_ROLE[actor_role], must_exist=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Command template must be an object: {path}")
    try:
        validate_against_schema(payload, "supervisor_command_template.schema.json", "supervisor command template")
    except SchemaValidationError as exc:
        raise SystemExit(str(exc)) from exc
    if payload.get("agent") != actor_role:
        raise SystemExit(f"Command template agent does not match {actor_role!r}: {path}")
    if payload.get("canonical_command") != _CANONICAL_COMMAND_BY_ROLE[actor_role]:
        raise SystemExit(f"Command template uses a non-allowlisted command: {path}")
    if payload.get("cwd") != "{workspace_root}":
        raise SystemExit(f"Command template cwd must be the active workspace root: {path}")
    argv_key = "primary_argv_template" if actor_role == "claude_review_agent" else "argv_template"
    if payload.get(argv_key) != _ALLOWED_ARGV_TEMPLATE_BY_ROLE[actor_role]:
        raise SystemExit(f"Command template argv posture is not allowlisted: {path}")
    if actor_role == "claude_review_agent" and payload.get(
        "fallback_argv_template"
    ) != _ALLOWED_CLAUDE_FALLBACK_ARGV_TEMPLATE:
        raise SystemExit(f"Claude fallback argv posture is not allowlisted: {path}")
    return payload


def _render_command_argv(
    template: dict[str, Any],
    *,
    actor_role: str,
    composed_prompt_path: Path,
    fallback: bool = False,
) -> list[str]:
    if actor_role == "claude_review_agent":
        key = "fallback_argv_template" if fallback else "primary_argv_template"
    else:
        if fallback:
            raise ValueError(f"{actor_role} has no command fallback")
        key = "argv_template"
    rendered: list[str] = []
    for raw in template[key]:
        value = str(raw).replace("{composed_prompt_file}", str(composed_prompt_path))
        if "{" in value or "}" in value:
            raise SystemExit(f"Unsupported command-template placeholder in {raw!r}.")
        rendered.append(value)
    return rendered


def _apply_operator_sandbox(
    argv: list[str],
    *,
    root: Path,
    job_payload: dict[str, Any],
) -> list[str]:
    """Grant write access only when an operator job declares exact write paths."""

    raw_paths = job_payload.get("allowed_write_paths")
    if raw_paths is None:
        mode = "read-only"
    else:
        if not isinstance(raw_paths, list) or any(not isinstance(item, str) or not item.strip() for item in raw_paths):
            raise SystemExit("Operator allowed_write_paths must be a list of non-empty workspace-relative paths.")
        for item in raw_paths:
            resolve_under_root(root, item, must_exist=False)
        mode = "workspace-write" if raw_paths else "read-only"
    return [*argv[:2], "--sandbox", mode, *argv[2:]]


def _compose_prompt(root: Path, actor_role: str, review_kind: str) -> tuple[list[str], str]:
    paths = [SHARED_PROMPT_PATH, PROMPT_BY_ROLE[actor_role], PROMPT_BY_REVIEW_KIND[review_kind]]
    sections: list[str] = []
    for path_value in paths:
        path = resolve_under_root(root, path_value, must_exist=True)
        sections.append(f"<!-- source: {relpath(root, path)} -->\n{path.read_text(encoding='utf-8').strip()}")
    sections.append(
        "## Transport Contract\n\n"
        "Emit only the model-owned JSON fields defined by "
        "automation/responses_runner_v2/schemas/reviewer_output.schema.json. "
        "The supervisor owns and will add all identity, command, timestamp, path, and read-only-check fields."
    )
    return paths, "\n\n".join(sections).rstrip() + "\n"


def _jsonable_job(job: dict[str, Any] | str | Path, *, root: Path) -> tuple[dict[str, Any], str]:
    if isinstance(job, dict):
        return job, json.dumps(job, indent=2, ensure_ascii=False)
    path = resolve_under_root(root, job, must_exist=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Agent job JSON must be an object: {path}")
    return payload, path.read_text(encoding="utf-8")


def _validated_embedded_sources(job_payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Validate optional job embedded_sources for bounded verbatim passthrough.

    Returns the entries unmodified. Fails closed (SystemExit) on a malformed
    shape or when total content exceeds MAX_EMBEDDED_SOURCES_BYTES, instead
    of silently dropping or truncating source text the reviewers will be
    told they can rely on.
    """

    raw = job_payload.get(EMBEDDED_SOURCES_KEY)
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise SystemExit("Job embedded_sources must be a list of {path, sha256, content} objects.")
    total_bytes = 0
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SystemExit(f"Job embedded_sources[{index}] must be an object with path, sha256, and content.")
        path = item.get("path")
        content = item.get("content")
        sha256 = item.get("sha256")
        if not isinstance(path, str) or not path.strip():
            raise SystemExit(f"Job embedded_sources[{index}].path must be a non-empty string.")
        if not isinstance(content, str):
            raise SystemExit(f"Job embedded_sources[{index}].content must be a string of verbatim source text.")
        if sha256 is not None and (not isinstance(sha256, str) or re.fullmatch(r"[a-f0-9]{64}", sha256) is None):
            raise SystemExit(f"Job embedded_sources[{index}].sha256 must be a 64-character lowercase hex digest.")
        total_bytes += len(content.encode("utf-8"))
        entries.append(item)
    if total_bytes > MAX_EMBEDDED_SOURCES_BYTES:
        raise SystemExit(
            f"Job embedded_sources content totals {total_bytes} bytes, which exceeds the "
            f"{MAX_EMBEDDED_SOURCES_BYTES}-byte bounded passthrough budget."
        )
    return entries


def _prompt_with_job(prompt_text: str, job_text: str, *, embedded_sources_count: int = 0) -> str:
    sources_note = ""
    if embedded_sources_count:
        sources_note = (
            f"The job object's `embedded_sources` array carries the verbatim text of {embedded_sources_count} "
            "cited authority file(s) as {path, sha256, content} objects. Treat each `content` value as the "
            "authoritative source text of the file at `path` and use it for grounding checks. Do not report "
            "these files as missing source text and do not block for lack of access to them.\n\n"
        )
    return (
        prompt_text.rstrip()
        + "\n\n## Supervisor Job JSON\n\n"
        + "Read the following job object as the only job-specific instruction payload. "
        + "Emit exactly the machine-ingestible JSON required by the prompt. Do not wrap stdout JSON in markdown fences.\n\n"
        + sources_note
        + "```json\n"
        + job_text.strip()
        + "\n```\n"
    )


def _declared_review_artifact_paths(job_payload: dict[str, Any]) -> list[str]:
    """Return only paths explicitly declared as review evidence by the job."""

    paths: list[str] = []
    for key in ("reviewed_artifacts", "required_artifacts", "artifacts"):
        value = job_payload.get(key)
        entries = value if isinstance(value, list) else []
        for entry in entries:
            if isinstance(entry, str) and entry.strip():
                paths.append(entry.strip())
            elif isinstance(entry, dict):
                path = entry.get("path") or entry.get("artifact_path")
                if isinstance(path, str) and path.strip():
                    paths.append(path.strip())
    manifest = job_payload.get("artifact_manifest")
    if isinstance(manifest, dict):
        entries = manifest.get("files")
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str) and entry["path"].strip():
                paths.append(entry["path"].strip())
    return list(dict.fromkeys(paths))


def _artifact_input_manifest(root: Path, paths: list[str]) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    for path_value in paths:
        path = resolve_under_root(root, path_value, must_exist=False)
        record: dict[str, Any] = {"path": relpath(root, path), "exists": path.exists()}
        if path.is_file():
            record.update({"sha256": sha256_file(path), "bytes": path.stat().st_size})
        manifest.append(record)
    return manifest


def _write_frozen_review_input(
    *,
    root: Path,
    output_path: Path,
    command_id: str,
    supervisor_session_id: str,
    review_cycle_id: str,
    review_kind: str,
    actor_role: str,
    prompt_text: str,
    job_payload: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    job_text = json.dumps(job_payload, indent=2, ensure_ascii=False, sort_keys=True)
    reviewed_paths = _declared_review_artifact_paths(job_payload)
    payload = {
        "schema_version": "responses_runner_v2.review_input.v1",
        "created_at": runner_now().isoformat(),
        "command_id": command_id,
        "supervisor_session_id": supervisor_session_id,
        "review_cycle_id": review_cycle_id,
        "review_kind": review_kind,
        "actor_role": actor_role,
        "job_sha256": sha256_text(job_text),
        "job_bytes": len(job_text.encode("utf-8")),
        "prompt_sha256": sha256_text(prompt_text),
        "prompt_bytes": len(prompt_text.encode("utf-8")),
        "reviewed_artifacts": _artifact_input_manifest(root, reviewed_paths),
        "job": job_payload,
    }
    review_input_path = output_path / f"{command_id}.review_input.json"
    write_json_artifact(
        root,
        review_input_path,
        payload,
        "review_input.schema.json",
        "frozen review input",
    )
    frozen_text = json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True)
    return payload, frozen_text, relpath(root, review_input_path)


def _load_cycle_review_input(
    *,
    root: Path,
    review_input: str | Path,
    supervisor_session_id: str,
    review_cycle_id: str,
    review_kind: str,
    job_payload: dict[str, Any],
) -> tuple[dict[str, Any], str, str]:
    path = resolve_under_root(root, review_input, must_exist=True)
    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid shared cycle review input: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit("Shared cycle review input must be a JSON object.")
    try:
        validate_against_schema(payload, "review_input.v2.schema.json", "shared cycle review input")
    except SchemaValidationError as exc:
        raise SystemExit(str(exc)) from exc
    expected_identity = {
        "supervisor_session_id": supervisor_session_id,
        "review_cycle_id": review_cycle_id,
        "review_kind": review_kind,
    }
    mismatches = [key for key, value in expected_identity.items() if payload.get(key) != value]
    if mismatches:
        raise SystemExit(f"Shared cycle review input identity mismatch: {', '.join(mismatches)}")

    job_path = resolve_under_root(root, payload["frozen_job_path"], must_exist=True)
    if sha256_file(job_path) != payload["frozen_job_sha256"] or job_path.stat().st_size != payload["frozen_job_bytes"]:
        raise SystemExit("Shared cycle review input frozen-job binding mismatch.")
    frozen_job = json.loads(job_path.read_text(encoding="utf-8"))
    if frozen_job != payload["job"] or frozen_job != job_payload:
        raise SystemExit("Shared cycle review input job does not match the invocation job.")

    manifest_path = resolve_under_root(root, payload["reviewed_artifact_manifest_path"], must_exist=True)
    if (
        sha256_file(manifest_path) != payload["reviewed_artifact_manifest_sha256"]
        or manifest_path.stat().st_size != payload["reviewed_artifact_manifest_bytes"]
    ):
        raise SystemExit("Shared cycle review input artifact-manifest binding mismatch.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or manifest.get("artifacts") != payload["reviewed_artifacts"]:
        raise SystemExit("Shared cycle review input artifact records do not match the frozen manifest.")
    for artifact in payload["reviewed_artifacts"]:
        artifact_path = resolve_under_root(root, artifact["path"], must_exist=True)
        if (
            not artifact_path.is_file()
            or artifact_path.stat().st_size != artifact["bytes"]
            or sha256_file(artifact_path) != artifact["sha256"]
        ):
            raise SystemExit(f"Shared cycle reviewed artifact changed: {artifact['path']}")
    return payload, text, relpath(root, path)


def _validate_model_owned_output(
    raw_decision: dict[str, Any],
    *,
    actor_role: str,
    review_kind: str,
) -> dict[str, Any]:
    canonical = _canonicalize_review_decision_shape(
        raw_decision,
        actor_role=actor_role,
        review_kind=review_kind,
    )
    model_owned = {key: canonical[key] for key in MODEL_OWNED_OUTPUT_KEYS if key in canonical}
    recommendation_keys = {
        "recommendation_id",
        "severity",
        "recommendation",
        "evidence",
        "affected_artifacts",
        "exact_change_needed",
        "rationale_for_no_change",
        "operator_decision",
        "decision_rationale",
        "rejected_reason",
        "changes_applied",
        "validation_evidence",
        "consolidation_recommendation",
    }
    projected_recommendations: list[dict[str, Any]] = []
    for recommendation in model_owned.get("recommendations", []):
        if not isinstance(recommendation, dict):
            continue
        projected = {key: value for key, value in recommendation.items() if key in recommendation_keys}
        if isinstance(projected.get("changes_applied"), list):
            projected["changes_applied"] = [
                {key: value for key, value in change.items() if key in {"path", "summary", "evidence"}}
                for change in projected["changes_applied"]
                if isinstance(change, dict)
            ]
        projected_recommendations.append(projected)
    model_owned["recommendations"] = projected_recommendations
    strict_payload = json.loads(json.dumps(model_owned))

    def complete_evidence(entries: Any) -> None:
        for entry in entries if isinstance(entries, list) else []:
            if isinstance(entry, dict):
                entry.setdefault("artifact_path", None)
                entry.setdefault("source", None)

    for artifact in strict_payload.get("reviewed_artifacts", []):
        if isinstance(artifact, dict):
            artifact.setdefault("sha256", None)
            artifact.setdefault("bytes", None)
    complete_evidence(strict_payload.get("evidence"))
    for recommendation in strict_payload.get("recommendations", []):
        if not isinstance(recommendation, dict):
            continue
        for key in (
            "exact_change_needed",
            "rationale_for_no_change",
            "operator_decision",
            "decision_rationale",
            "rejected_reason",
            "consolidation_recommendation",
        ):
            recommendation.setdefault(key, None)
        recommendation.setdefault("changes_applied", [])
        recommendation.setdefault("validation_evidence", [])
        complete_evidence(recommendation.get("evidence"))
        complete_evidence(recommendation.get("validation_evidence"))
        for change in recommendation.get("changes_applied", []):
            if isinstance(change, dict):
                complete_evidence(change.get("evidence"))
    try:
        validate_against_schema(strict_payload, "reviewer_output.schema.json", "reviewer model output")
    except SchemaValidationError as exc:
        raise AgentOutputError(str(exc)) from exc
    for recommendation in model_owned.get("recommendations", []):
        if isinstance(recommendation, dict):
            for key in list(recommendation):
                if recommendation[key] is None:
                    recommendation.pop(key)
    return model_owned


def _repair_input(original_input: str, raw_stdout: str, error: str) -> str:
    return (
        original_input.rstrip()
        + "\n\n## Single Format Repair\n\n"
        + "The prior command exited zero, but its stdout failed JSON parsing or reviewer-output schema validation. "
        + "Repair only the JSON transport shape. Do not change the substantive verdict or evidence. "
        + "Emit only one schema-valid JSON object.\n\n"
        + f"Validation error: {error}\n\n"
        + "Prior stdout:\n```text\n"
        + raw_stdout[:262144]
        + "\n```\n"
    )


def _parse_json_object_text(text: str, *, label: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        raise AgentOutputError(f"{label} did not contain JSON.")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise AgentOutputError(f"{label} was not valid JSON.")
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AgentOutputError(f"{label} contained malformed JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AgentOutputError(f"{label} JSON output must be an object.")
    return payload


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    payload = _parse_json_object_text(stdout, label="Agent stdout")
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        result_text = payload["result"].strip()
        try:
            nested = _parse_json_object_text(result_text, label="Agent result")
        except AgentOutputError:
            nested = None
        if isinstance(nested, dict):
            payload = nested
    if isinstance(payload, dict) and isinstance(payload.get("structured_output"), dict):
        payload = payload["structured_output"]
    return payload


def _stdout_is_empty_or_truncated(stdout: str) -> bool:
    """Return True when stdout carries no complete JSON object.

    Used to distinguish an externally killed reviewer (empty or cut-off
    stdout) from a process that exited on a signal-like code after still
    delivering a complete decision payload.
    """

    try:
        _parse_json_object_text(stdout, label="Agent stdout")
    except AgentOutputError:
        return True
    return False


def validate_review_decision(decision: dict[str, Any]) -> None:
    try:
        validate_against_schema(decision, "review_decision.schema.json", "review decision")
    except SchemaValidationError as exc:
        raise AgentOutputError(str(exc)) from exc

    actor_role = decision.get("actor_role")
    review_kind = decision.get("review_kind")
    if (
        actor_role in {"operator_codex", "codex_review_agent", "claude_review_agent"}
        and review_kind != "operator_acceptance"
        and not isinstance(decision.get("command"), dict)
    ):
        raise AgentOutputError("Agent-backed review decision must include command metadata.")
    if actor_role in {"codex_review_agent", "claude_review_agent"}:
        read_only_check = decision.get("read_only_check")
        if not isinstance(read_only_check, dict):
            raise AgentOutputError("Reviewer decision must include read_only_check.")
        if read_only_check.get("status") != "passed":
            raise AgentOutputError("Reviewer read_only_check did not pass.")
    if decision.get("approval_decision") == "approve" and decision.get("missing_artifacts"):
        raise AgentOutputError("Review decision cannot approve when missing_artifacts is non-empty.")
    if decision.get("status") == "succeeded" and decision.get("validation_errors"):
        raise AgentOutputError("Succeeded review decision cannot contain validation_errors.")
    if decision.get("review_kind") == "operator_acceptance":
        for rec in decision.get("recommendations", []):
            if not isinstance(rec, dict):
                raise AgentOutputError("operator_acceptance recommendations must be objects.")
            if not rec.get("operator_decision") or not rec.get("decision_rationale"):
                raise AgentOutputError("operator_acceptance recommendations require operator_decision and decision_rationale.")
            if rec.get("operator_decision") == "accepted":
                if not rec.get("evidence"):
                    raise AgentOutputError("Accepted operator recommendation must have evidence.")
                if not rec.get("changes_applied"):
                    raise AgentOutputError("Accepted operator recommendation must record changes_applied.")
                if not rec.get("validation_evidence"):
                    raise AgentOutputError("Accepted operator recommendation must record validation_evidence.")


def _render_markdown(decision: dict[str, Any]) -> str:
    lines = [
        f"# {decision.get('actor_role', 'agent')} review",
        "",
        f"- decision_id: {decision.get('decision_id')}",
        f"- status: {decision.get('status')}",
        f"- approval_decision: {decision.get('approval_decision')}",
        "",
        "## Summary",
        "",
        str(decision.get("summary", "")),
        "",
        "## Blocking Issues",
        "",
    ]
    blocking = decision.get("blocking_issues") if isinstance(decision.get("blocking_issues"), list) else []
    if not blocking:
        lines.append("None.")
    for issue in blocking:
        if isinstance(issue, dict):
            lines.append(f"- {issue.get('issue_id')}: {issue.get('description')}")
    lines.extend(["", "## Recommendations", ""])
    recommendations = decision.get("recommendations") if isinstance(decision.get("recommendations"), list) else []
    if not recommendations:
        lines.append("None.")
    for rec in recommendations:
        if isinstance(rec, dict):
            lines.append(f"- {rec.get('recommendation_id')}: {rec.get('recommendation')}")
    return "\n".join(lines).rstrip() + "\n"


def _minimal_blocked_decision(
    *,
    root: Path,
    output_dir: Path,
    command_id: str,
    actor_role: str,
    review_kind: str,
    review_cycle_id: str,
    supervisor_session_id: str,
    status: str,
    summary: str,
    validation_errors: list[str],
    command: dict[str, Any] | None,
    read_only_check: dict[str, Any] | None,
) -> dict[str, Any]:
    markdown_path = output_dir / f"{command_id}.md"
    json_path = output_dir / f"{command_id}.json"
    transport_failure = status in TRANSPORT_FAILURE_STATUSES
    blocking_issues: list[dict[str, Any]] = []
    if not transport_failure:
        blocking_issues = [
            {
                "issue_id": f"{command_id}_output_failure",
                "severity": "blocking",
                "description": summary,
                "evidence": validation_errors or [summary],
                "affected_artifacts": [],
            }
        ]
    payload = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "decision_id": command_id,
        "created_at": runner_now().isoformat(),
        "supervisor_session_id": supervisor_session_id,
        "workflow_id": None,
        "run_id": None,
        "stage_id": None,
        "review_cycle_id": review_cycle_id,
        "review_kind": review_kind,
        "actor_role": actor_role,
        "agent_command_id": command_id,
        "status": status,
        "approval_decision": "blocked",
        "summary": summary,
        "markdown_report_path": relpath(root, markdown_path),
        "json_report_path": relpath(root, json_path),
        "reviewed_artifacts": [],
        "missing_artifacts": [],
        "blocking_issues": blocking_issues,
        "non_blocking_improvements": [],
        "recommendations": [],
        "unsupported_claims": [],
        "evidence": [{"source": "supervisor_agents", "quote_or_summary": summary}],
        "command": command,
        "read_only_check": read_only_check,
        "validation_errors": validation_errors,
        "next_action": "blocked",
    }
    heading = "# Agent transport failure" if transport_failure else "# Agent invocation failed"
    write_text_artifact(root, markdown_path, heading + "\n\n" + summary + "\n")
    write_json_artifact(root, json_path, payload)
    return payload


def _safe_id(value: Any, fallback: str) -> str:
    raw = str(value or fallback).strip().lower()
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw)
    raw = raw.strip("._-")
    if not raw or not raw[0].isalnum():
        raw = fallback
    return raw[:128]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    return [text] if text else []


def _canonical_status(value: Any) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "complete": "succeeded",
        "completed": "succeeded",
        "ok": "succeeded",
        "pass": "succeeded",
        "passed": "succeeded",
        "success": "succeeded",
        "successful": "succeeded",
        "succeeded": "succeeded",
        "failure": "failed",
        "failed": "failed",
        "blocked": "blocked",
        "malformed_output": "malformed_output",
        "read_only_violation": "read_only_violation",
        "timeout": "timeout",
        "missing_cli": "missing_cli",
        "interrupted": "interrupted",
    }
    return mapping.get(raw, str(value or ""))


def _canonical_approval(value: Any) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "accept": "approve",
        "accepted": "approve",
        "approve": "approve",
        "approved": "approve",
        "ok": "approve",
        "pass": "approve",
        "passed": "approve",
        "approved_with_conditions": "approve_with_conditions",
        "approve_with_conditions": "approve_with_conditions",
        "conditional_approval": "approve_with_conditions",
        "reject": "do_not_approve",
        "rejected": "do_not_approve",
        "do_not_approve": "do_not_approve",
        "not_approved": "do_not_approve",
        "blocked": "blocked",
        "not_applicable": "not_applicable",
    }
    return mapping.get(raw, str(value or ""))


def _canonical_severity(value: Any) -> str:
    raw = str(value or "").strip().lower()
    mapping = {
        "critical": "critical",
        "blocker": "blocking",
        "blocking": "blocking",
        "high": "high",
        "medium": "medium",
        "med": "medium",
        "low": "low",
        "info": "low",
        "informational": "low",
        "note": "low",
        "none": "low",
    }
    return mapping.get(raw, "medium")


def _coerce_artifact_refs(value: Any, *, default_role: str = "reviewed_artifact") -> list[dict[str, Any]]:
    entries = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, Any]] = []
    for index, item in enumerate(entries, start=1):
        if isinstance(item, dict):
            path = str(item.get("path") or item.get("artifact_path") or item.get("artifact") or "").strip()
            role = str(item.get("role") or item.get("artifact") or item.get("name") or f"{default_role}_{index}").strip()
            if not path:
                continue
            entry: dict[str, Any] = {"path": path, "role": role or default_role}
            sha256 = str(item.get("sha256") or "").strip()
            if re.fullmatch(r"[a-f0-9]{64}", sha256):
                entry["sha256"] = sha256
            if isinstance(item.get("bytes"), int) and item["bytes"] >= 0:
                entry["bytes"] = item["bytes"]
            coerced.append(entry)
            continue
        path = str(item).strip()
        if path:
            coerced.append({"path": path, "role": f"{default_role}_{index}"})
    return coerced


def _coerce_missing_artifacts(value: Any) -> list[dict[str, Any]]:
    entries = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, Any]] = []
    for item in entries:
        if isinstance(item, dict):
            path = str(item.get("path") or item.get("artifact_path") or item.get("artifact") or "").strip()
            if not path:
                continue
            coerced.append({
                "path": path,
                "required": bool(item.get("required", True)),
                "reason": str(item.get("reason") or item.get("description") or "Required artifact was missing.").strip(),
            })
            continue
        path = str(item).strip()
        if path:
            coerced.append({"path": path, "required": True, "reason": "Required artifact was missing."})
    return coerced


def _coerce_evidence(value: Any, *, default_source: str, default_artifact_path: str | None = None) -> list[dict[str, str]]:
    entries = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, str]] = []
    for item in entries:
        if isinstance(item, dict):
            quote = str(
                item.get("quote_or_summary")
                or item.get("summary")
                or item.get("evidence_summary")
                or item.get("evidence")
                or item.get("detail")
                or item.get("rationale")
                or ""
            ).strip()
            source = str(item.get("source") or default_source).strip()
            artifact_path = str(item.get("artifact_path") or item.get("path") or "").strip()
            if not quote:
                quote = json.dumps(item, sort_keys=True)
            entry: dict[str, str] = {"quote_or_summary": quote}
            if artifact_path:
                entry["artifact_path"] = artifact_path
            else:
                entry["source"] = source
            coerced.append(entry)
            continue

        text = str(item).strip()
        if not text:
            continue
        entry = {"quote_or_summary": text}
        if default_artifact_path:
            entry["artifact_path"] = default_artifact_path
        else:
            entry["source"] = default_source
        coerced.append(entry)
    return coerced


def _coerce_affected_artifacts(rec: dict[str, Any]) -> list[Any]:
    affected = rec.get("affected_artifacts")
    if affected is None:
        affected = rec.get("affected_artifact")
    if affected is None:
        affected = rec.get("artifact_path")
    if affected is None:
        affected = rec.get("path")
    if isinstance(affected, list):
        normalized: list[str] = []
        for item in affected:
            if isinstance(item, dict):
                item = item.get("path") or item.get("artifact_path") or ""
            text = str(item).strip()
            if text:
                normalized.append(text)
        return normalized
    if affected is None:
        return []
    text = str(affected).strip()
    return [text] if text else []


def _coerce_issue_items(value: Any, *, default_prefix: str, include_severity: bool = True) -> list[dict[str, Any]]:
    entries = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, Any]] = []
    for index, item in enumerate(entries, start=1):
        if not isinstance(item, dict):
            text = str(item).strip()
            if not text:
                continue
            row: dict[str, Any] = {
                "issue_id": _safe_id(None, f"{default_prefix}-{index}"),
                "description": text,
                "evidence": [text],
                "affected_artifacts": [],
            }
            if include_severity:
                row["severity"] = "medium"
            coerced.append(row)
            continue
        row = dict(item)
        description = str(
            row.get("description")
            or row.get("title")
            or row.get("detail")
            or row.get("exact_change_needed")
            or row.get("rationale")
            or row.get("reason")
            or "Review note."
        ).strip()
        evidence = row.get("evidence")
        if evidence is None:
            evidence = row.get("evidence_summary") or row.get("detail") or description
        normalized: dict[str, Any] = {
            "issue_id": _safe_id(row.get("issue_id") or row.get("id"), f"{default_prefix}-{index}"),
            "description": description,
            "evidence": _string_list(evidence),
            "affected_artifacts": [str(item).strip() for item in _coerce_affected_artifacts(row) if str(item).strip()],
        }
        if include_severity:
            normalized["severity"] = _canonical_severity(row.get("severity"))
        coerced.append(normalized)
    return coerced


def _coerce_changes_applied(value: Any, *, default_source: str) -> list[dict[str, Any]]:
    changes = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, Any]] = []
    for item in changes:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        summary = str(item.get("summary") or item.get("change") or item.get("description") or "").strip()
        if not path or not summary:
            continue
        evidence = _coerce_evidence(
            item.get("evidence") or summary,
            default_source=default_source,
            default_artifact_path=path,
        )
        change = dict(item)
        change.pop("change", None)
        change["path"] = path
        change["summary"] = summary
        change["evidence"] = evidence
        coerced.append(change)
    return coerced


def _canonical_operator_decision(value: Any) -> str | None:
    """Normalize per-recommendation operator_decision spellings.

    Agents emit verb forms ("accept", "reject", "defer") for the
    past-participle enum; unknown spellings pass through untouched so the
    schema still rejects genuinely invalid values.
    """
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw:
        return None
    aliases = {
        "accept": "accepted",
        "accepted": "accepted",
        "approve": "accepted",
        "approved": "accepted",
        "reject": "rejected",
        "rejected": "rejected",
        "defer": "deferred",
        "deferred": "deferred",
        "already_satisfied": "already_satisfied",
        "satisfied": "already_satisfied",
        "out_of_scope": "out_of_scope",
    }
    return aliases.get(raw, raw)


def _coerce_recommendations(value: Any, *, actor_role: str, default_source: str) -> list[dict[str, Any]]:
    recommendations = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, Any]] = []
    for index, item in enumerate(recommendations, start=1):
        if not isinstance(item, dict):
            continue
        rec = dict(item)
        artifact_path = str(rec.get("affected_artifact") or "").strip() or None
        evidence = rec.get("evidence")
        if evidence is None:
            evidence = rec.get("supporting_evidence")
        rec["recommendation_id"] = _safe_id(
            rec.get("recommendation_id") or rec.get("id"),
            f"recommendation-{index}",
        )
        rec["source_agent"] = str(rec.get("source_agent") or actor_role)
        rec["severity"] = _canonical_severity(rec.get("severity"))
        rec["recommendation"] = str(
            rec.get("recommendation")
            or rec.get("exact_change_needed")
            or rec.get("rationale")
            or rec.get("title")
            or rec.get("detail")
            or rec.get("decision_rationale")
            or "Review recommendation."
        ).strip()
        rec["evidence"] = _coerce_evidence(
            evidence,
            default_source=default_source,
            default_artifact_path=artifact_path,
        )
        rec["affected_artifacts"] = _coerce_affected_artifacts(rec)
        if "exact_change_needed" not in rec and "rationale_for_no_change" not in rec:
            rationale = str(rec.get("decision_rationale") or rec.get("recommendation") or "").strip()
            if rationale:
                rec["rationale_for_no_change"] = rationale
        if "changes_applied" in rec:
            rec["changes_applied"] = _coerce_changes_applied(rec.get("changes_applied"), default_source=default_source)
        if "validation_evidence" in rec:
            rec["validation_evidence"] = _coerce_evidence(
                rec.get("validation_evidence"),
                default_source=default_source,
                default_artifact_path=artifact_path,
            )
        if "operator_decision" in rec:
            canonical_decision = _canonical_operator_decision(rec.get("operator_decision"))
            if canonical_decision is None:
                rec.pop("operator_decision", None)
            else:
                rec["operator_decision"] = canonical_decision
        rec.pop("id", None)
        rec.pop("supporting_evidence", None)
        rec.pop("affected_artifact", None)
        coerced.append(rec)
    return coerced


def _coerce_unsupported_claims(value: Any, *, default_source: str) -> list[dict[str, str]]:
    claims = value if isinstance(value, list) else ([] if value is None else [value])
    coerced: list[dict[str, str]] = []
    for item in claims:
        if isinstance(item, dict):
            claim = str(item.get("claim") or "").strip()
            source = str(item.get("source") or item.get("evidence") or default_source).strip()
            reason = str(item.get("reason") or item.get("rejected_reason") or item.get("operator_decision") or "").strip()
        else:
            claim = str(item).strip()
            source = default_source
            reason = "Claim was listed as unsupported by the review agent."
        if not claim:
            continue
        coerced.append({
            "claim": claim,
            "source": source or default_source,
            "reason": reason or "Claim was listed as unsupported by the review agent.",
        })
    return coerced


def _canonical_next_action(raw_action: Any, *, review_kind: str, approval_decision: str) -> str:
    action = str(raw_action or "").strip()
    allowed = {
        "proceed_to_consolidation",
        "proceed_to_operator_acceptance",
        "create_review_bundle",
        "create_final_bundle",
        "rerun_after_archive",
        "human_pause",
        "blocked",
    }
    if action in allowed:
        return action
    if approval_decision not in {"approve", "approve_with_conditions"}:
        return "blocked"
    if review_kind == "operator_acceptance":
        return "create_review_bundle"
    if review_kind == "consolidation":
        return "proceed_to_operator_acceptance"
    if review_kind == "final_packet":
        return "create_final_bundle"
    return "proceed_to_consolidation"


def _canonicalize_review_decision_shape(
    decision: dict[str, Any],
    *,
    actor_role: str,
    review_kind: str,
) -> dict[str, Any]:
    default_source = f"{actor_role}:{review_kind}"
    canonical = {key: value for key, value in decision.items() if key in REVIEW_DECISION_TOP_LEVEL_KEYS}
    if "status" in canonical:
        canonical["status"] = _canonical_status(canonical.get("status"))
    if "approval_decision" in canonical:
        canonical["approval_decision"] = _canonical_approval(canonical.get("approval_decision"))
    if "reviewed_artifacts" in canonical:
        canonical["reviewed_artifacts"] = _coerce_artifact_refs(canonical.get("reviewed_artifacts"))
    if "missing_artifacts" in canonical:
        canonical["missing_artifacts"] = _coerce_missing_artifacts(canonical.get("missing_artifacts"))
    if "blocking_issues" in canonical:
        canonical["blocking_issues"] = _coerce_issue_items(
            canonical.get("blocking_issues"),
            default_prefix="blocking-issue",
            include_severity=True,
        )
    if "non_blocking_improvements" in canonical:
        canonical["non_blocking_improvements"] = _coerce_issue_items(
            canonical.get("non_blocking_improvements"),
            default_prefix="non-blocking-improvement",
            include_severity=False,
        )
    if "recommendations" in canonical:
        canonical["recommendations"] = _coerce_recommendations(
            canonical.get("recommendations"),
            actor_role=actor_role,
            default_source=default_source,
        )
    if "unsupported_claims" in canonical:
        canonical["unsupported_claims"] = _coerce_unsupported_claims(
            canonical.get("unsupported_claims"),
            default_source=default_source,
        )
    if "evidence" in canonical:
        canonical["evidence"] = _coerce_evidence(canonical.get("evidence"), default_source=default_source)
    if "validation_errors" in canonical:
        canonical["validation_errors"] = _string_list(canonical.get("validation_errors"))
        # validation_errors is reserved for supervisor transport/schema reports.
        # Agents that file semantic findings there (with a succeeded status)
        # get them re-homed by verdict: rejections -> blocking_issues,
        # approvals -> non_blocking_improvements. The verdict stays untouched.
        if canonical.get("validation_errors") and _canonical_status(canonical.get("status")) == "succeeded":
            misfiled = canonical["validation_errors"]
            approval = _canonical_approval(canonical.get("approval_decision"))
            target_key = "blocking_issues" if approval in {"do_not_approve", "blocked"} else "non_blocking_improvements"
            rehomed = _coerce_issue_items(
                misfiled,
                default_prefix="misfiled-validation-finding",
                include_severity=target_key == "blocking_issues",
            )
            existing = canonical.get(target_key)
            canonical[target_key] = (existing if isinstance(existing, list) else []) + rehomed
            canonical["validation_errors"] = []
    if "approval_decision" in canonical or "next_action" in canonical:
        canonical["next_action"] = _canonical_next_action(
            canonical.get("next_action"),
            review_kind=review_kind,
            approval_decision=str(canonical.get("approval_decision") or ""),
        )
    return canonical


def _normalize_agent_decision(
    *,
    root: Path,
    output_dir: Path,
    command_id: str,
    actor_role: str,
    review_kind: str,
    review_cycle_id: str,
    supervisor_session_id: str,
    raw_decision: dict[str, Any],
    normalization_source: dict[str, Any] | None = None,
    command: dict[str, Any],
    read_only_check: dict[str, Any] | None,
    invocation_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    markdown_path = output_dir / f"{command_id}.md"
    json_path = output_dir / f"{command_id}.json"
    decision = _canonicalize_review_decision_shape(
        dict(raw_decision),
        actor_role=actor_role,
        review_kind=review_kind,
    )
    source_decision = normalization_source if normalization_source is not None else raw_decision
    normalized_fields = sorted(
        key
        for key in MODEL_OWNED_OUTPUT_KEYS
        if (key in source_decision) != (key in decision)
        or source_decision.get(key) != decision.get(key)
    )
    if normalized_fields:
        command = {
            **command,
            "output_normalization": {
                "changed": True,
                "fields": normalized_fields,
            },
        }
    decision["schema_version"] = decision.get("schema_version") or REVIEW_DECISION_SCHEMA_VERSION
    # Identity and transport metadata are supervisor-owned. Never accept an
    # agent's self-reported session, cycle, kind, role, or decision id.
    decision["decision_id"] = _safe_id(command_id, "review-decision")
    decision["created_at"] = runner_now().isoformat()
    decision["supervisor_session_id"] = _safe_id(supervisor_session_id, "supervisor-session")
    decision["review_cycle_id"] = _safe_id(review_cycle_id, "review-cycle")
    decision["review_kind"] = review_kind
    decision["actor_role"] = actor_role
    decision["agent_command_id"] = command_id
    decision["markdown_report_path"] = relpath(root, markdown_path)
    decision["json_report_path"] = relpath(root, json_path)
    decision["command"] = command
    decision["read_only_check"] = read_only_check
    identity = invocation_identity or {}
    for key in ("workflow_id", "run_id", "stage_id"):
        value = identity.get(key)
        decision[key] = _safe_id(value, key.replace("_", "-")) if value else None
    return decision


def _write_validated_decision(
    *,
    root: Path,
    output_dir: Path,
    command_id: str,
    actor_role: str,
    review_kind: str,
    review_cycle_id: str,
    supervisor_session_id: str,
    raw_decision: dict[str, Any],
    normalization_source: dict[str, Any] | None = None,
    command: dict[str, Any],
    read_only_check: dict[str, Any] | None,
    invocation_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    decision = _normalize_agent_decision(
        root=root,
        output_dir=output_dir,
        command_id=command_id,
        actor_role=actor_role,
        review_kind=review_kind,
        review_cycle_id=review_cycle_id,
        supervisor_session_id=supervisor_session_id,
        raw_decision=raw_decision,
        normalization_source=normalization_source,
        command=command,
        read_only_check=read_only_check,
        invocation_identity=invocation_identity,
    )
    validate_review_decision(decision)
    markdown_path = output_dir / f"{command_id}.md"
    json_path = output_dir / f"{command_id}.json"
    write_text_artifact(root, markdown_path, _render_markdown(decision))
    write_json_artifact(root, json_path, decision)
    return decision


def _claude_subscription_env() -> dict[str, str]:
    env = dict(os.environ)
    for key in (
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "CLAUDE_CODE_USE_FOUNDRY",
    ):
        env.pop(key, None)
    return env


def _run_subprocess(
    argv: list[str],
    *,
    root: Path,
    timeout_seconds: int,
    runner: Callable[..., Any] | None,
    input_text: str | None = None,
    env: dict[str, str] | None = None,
) -> Any:
    runner = runner or subprocess.run
    return runner(
        argv,
        cwd=str(root),
        text=True,
        capture_output=True,
        timeout=timeout_seconds,
        input=input_text,
        env=env,
    )


def _unsupported_effort(stderr: str, stdout: str) -> bool:
    combined = f"{stderr}\n{stdout}".lower()
    return "effort" in combined and ("unsupported" in combined or "invalid" in combined or "unknown" in combined)


def _command_model(argv: list[str]) -> str | None:
    try:
        value = argv[argv.index("--model") + 1]
    except (ValueError, IndexError):
        return None
    return value or None


def _invoke_agent(
    *,
    root: Path,
    actor_role: str,
    review_kind: str,
    review_cycle_id: str,
    supervisor_session_id: str,
    job: dict[str, Any] | str | Path,
    review_input: str | Path | None = None,
    output_dir: str | Path,
    runner: Callable[..., Any] | None = None,
    timeout_seconds: int | None = None,
) -> AgentRunResult:
    output_path = resolve_under_root(root, output_dir, must_exist=False)
    output_path_existed = output_path.exists()
    output_path.mkdir(parents=True, exist_ok=True)
    if not output_path_existed:
        os.chmod(output_path, 0o700)
    template = load_command_template(root, actor_role)
    timeout = int(timeout_seconds or template.get("timeout_seconds", 1800))
    command_id = _safe_id(
        f"cmd_{actor_role}_{review_cycle_id}_{runner_now().strftime('%H%M%S_%f')}",
        f"cmd-{actor_role}",
    )
    prompt_sources, prompt_text = _compose_prompt(root, actor_role, review_kind)
    prompt_path = output_path / f"{command_id}.composed_prompt.md"
    write_text_artifact(root, prompt_path, prompt_text)
    job_payload, _ = _jsonable_job(job, root=root)
    embedded_sources = _validated_embedded_sources(job_payload)
    shared_review_input = review_input is not None
    if shared_review_input:
        frozen_input, frozen_input_text, frozen_input_rel = _load_cycle_review_input(
            root=root,
            review_input=review_input,
            supervisor_session_id=supervisor_session_id,
            review_cycle_id=review_cycle_id,
            review_kind=review_kind,
            job_payload=job_payload,
        )
    else:
        frozen_input, frozen_input_text, frozen_input_rel = _write_frozen_review_input(
            root=root,
            output_path=output_path,
            command_id=command_id,
            supervisor_session_id=supervisor_session_id,
            review_cycle_id=review_cycle_id,
            review_kind=review_kind,
            actor_role=actor_role,
            prompt_text=prompt_text,
            job_payload=job_payload,
        )
    input_text = _prompt_with_job(
        prompt_text,
        frozen_input_text,
        embedded_sources_count=len(embedded_sources),
    )
    fallback_used = False
    env = None
    if actor_role in {"operator_codex", "codex_review_agent"}:
        argv = _render_command_argv(
            template,
            actor_role=actor_role,
            composed_prompt_path=prompt_path,
        )
        if actor_role == "operator_codex":
            argv = _apply_operator_sandbox(argv, root=root, job_payload=job_payload)
    elif actor_role == "claude_review_agent":
        argv = _render_command_argv(
            template,
            actor_role=actor_role,
            composed_prompt_path=prompt_path,
        )
        # Claude receives the frozen composed prompt through its configured
        # prompt file and the frozen review input through stdin exactly once.
        input_text = frozen_input_text
        env = _claude_subscription_env()
    else:
        raise SystemExit(f"Unsupported actor_role for agent invocation: {actor_role}")

    explicit_review_paths = [
        str(item["path"])
        for item in frozen_input["reviewed_artifacts"]
        if isinstance(item, dict) and item.get("path")
    ]
    protected_input_paths = list(explicit_review_paths)
    if shared_review_input:
        protected_input_paths.extend(
            [
                frozen_input_rel,
                str(frozen_input["frozen_job_path"]),
                str(frozen_input["reviewed_artifact_manifest_path"]),
            ]
        )
        protected_input_paths = list(dict.fromkeys(protected_input_paths))
    read_only_before = (
        snapshot_workspace(root, include_paths=protected_input_paths)
        if actor_role in {"codex_review_agent", "claude_review_agent"}
        else None
    )
    started_at = runner_now().isoformat()
    invocation_started = time.monotonic()
    missing_cli_error: str | None = None
    try:
        completed = _run_subprocess(
            argv,
            root=root,
            timeout_seconds=timeout,
            runner=runner,
            input_text=input_text,
            env=env,
        )
    except subprocess.TimeoutExpired as exc:
        completed = SimpleNamespace(returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "timeout")
    except FileNotFoundError as exc:
        missing_cli_error = f"Agent CLI executable could not be spawned: {argv[0]} ({exc})."
        completed = SimpleNamespace(returncode=MISSING_CLI_EXIT_CODE, stdout="", stderr=missing_cli_error)
    completed_at = runner_now().isoformat()

    if (
        actor_role == "claude_review_agent"
        and missing_cli_error is None
        and completed.returncode != 0
        and _unsupported_effort(str(completed.stderr), str(completed.stdout))
    ):
        fallback_used = True
        fallback_argv = _render_command_argv(
            template,
            actor_role=actor_role,
            composed_prompt_path=prompt_path,
            fallback=True,
        )
        started_at = runner_now().isoformat()
        try:
            completed = _run_subprocess(
                fallback_argv,
                root=root,
                timeout_seconds=timeout,
                runner=runner,
                input_text=input_text,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            completed = SimpleNamespace(returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "timeout")
        except FileNotFoundError as exc:
            missing_cli_error = f"Agent CLI executable could not be spawned: {fallback_argv[0]} ({exc})."
            completed = SimpleNamespace(returncode=MISSING_CLI_EXIT_CODE, stdout="", stderr=missing_cli_error)
        completed_at = runner_now().isoformat()
        argv = fallback_argv

    stdout_path = output_path / f"{command_id}.stdout.txt"
    stderr_path = output_path / f"{command_id}.stderr.txt"
    write_text_artifact(root, stdout_path, str(completed.stdout or ""))
    write_text_artifact(root, stderr_path, str(completed.stderr or ""))

    validation_errors: list[str] = []
    raw_decision: dict[str, Any] | None = None
    normalization_source: dict[str, Any] | None = None
    status = "succeeded"
    failure_summary = "Agent invocation failed or produced invalid output."
    returncode = int(completed.returncode)
    parse_or_schema_error: str | None = None
    if missing_cli_error is not None:
        status = "missing_cli"
        failure_summary = (
            "Agent CLI executable was not found, so the reviewer never ran and produced no verdict. "
            "This is a transport failure, not a reviewer rejection."
        )
        validation_errors.append(missing_cli_error)
    elif returncode == 124:
        status = "timeout"
        validation_errors.append("Agent command timed out.")
    elif returncode in INTERRUPT_SIGNAL_BY_EXIT_CODE and _stdout_is_empty_or_truncated(str(completed.stdout or "")):
        signal_name = INTERRUPT_SIGNAL_BY_EXIT_CODE[returncode]
        status = "interrupted"
        failure_summary = (
            "Agent process was interrupted by an external signal before emitting its review decision, "
            "so the reviewer produced no verdict. This is a transport failure, not a reviewer rejection."
        )
        validation_errors.append(
            f"Agent process was killed externally by {signal_name} (exit code {returncode}) "
            "with empty or truncated stdout before it could emit a review decision."
        )
    elif returncode != 0:
        status = "failed"
        validation_errors.append(f"Agent command exited with {returncode}.")
    else:
        try:
            parsed = _parse_stdout_json(str(completed.stdout or ""))
            normalization_source = parsed
            raw_decision = _validate_model_owned_output(
                parsed,
                actor_role=actor_role,
                review_kind=review_kind,
            )
        except AgentOutputError as exc:
            parse_or_schema_error = str(exc)

    repair_record: dict[str, Any] | None = None
    if parse_or_schema_error is not None:
        repair_started_at = runner_now().isoformat()
        repair_input = _repair_input(input_text, str(completed.stdout or ""), parse_or_schema_error)
        try:
            repaired = _run_subprocess(
                argv,
                root=root,
                timeout_seconds=timeout,
                runner=runner,
                input_text=repair_input,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            repaired = SimpleNamespace(returncode=124, stdout=exc.stdout or "", stderr=exc.stderr or "timeout")
        except FileNotFoundError as exc:
            repaired = SimpleNamespace(returncode=MISSING_CLI_EXIT_CODE, stdout="", stderr=str(exc))
        repair_completed_at = runner_now().isoformat()
        repair_stdout_path = output_path / f"{command_id}.repair.stdout.txt"
        repair_stderr_path = output_path / f"{command_id}.repair.stderr.txt"
        write_text_artifact(root, repair_stdout_path, str(repaired.stdout or ""))
        write_text_artifact(root, repair_stderr_path, str(repaired.stderr or ""))
        repair_record = {
            "attempted": True,
            "reason": parse_or_schema_error,
            "started_at": repair_started_at,
            "completed_at": repair_completed_at,
            "exit_code": int(repaired.returncode),
            "stdout_path": relpath(root, repair_stdout_path),
            "stderr_path": relpath(root, repair_stderr_path),
            "stdin_sha256": sha256_text(repair_input),
            "stdin_bytes": len(repair_input.encode("utf-8")),
        }
        if int(repaired.returncode) == 0:
            try:
                parsed = _parse_stdout_json(str(repaired.stdout or ""))
                normalization_source = parsed
                raw_decision = _validate_model_owned_output(
                    parsed,
                    actor_role=actor_role,
                    review_kind=review_kind,
                )
                status = "succeeded"
                validation_errors = []
                repair_record["status"] = "succeeded"
            except AgentOutputError as exc:
                status = "malformed_output"
                validation_errors = [parse_or_schema_error, f"Repair failed: {exc}"]
                repair_record["status"] = "failed"
        else:
            status = "malformed_output"
            validation_errors = [
                parse_or_schema_error,
                f"Repair command exited with {int(repaired.returncode)}.",
            ]
            repair_record["status"] = "failed"

    read_only_check = None
    if actor_role in {"codex_review_agent", "claude_review_agent"}:
        read_only_after = snapshot_workspace(root, include_paths=protected_input_paths)
        allowed_output_paths = {
            relpath(root, stdout_path),
            relpath(root, stderr_path),
            relpath(root, prompt_path),
            *([] if shared_review_input else [frozen_input_rel]),
            relpath(root, output_path / f"{command_id}.json"),
            relpath(root, output_path / f"{command_id}.md"),
            relpath(root, output_path / f"{command_id}.readonly.diff.md"),
            relpath(root, output_path / f"{command_id}.repair.stdout.txt"),
            relpath(root, output_path / f"{command_id}.repair.stderr.txt"),
            relpath(root, output_path / f"{command_id}.reviewer_usage_attempt.json"),
        }
        filtered_before = {
            path: digest
            for path, digest in (read_only_before or {}).items()
            if path not in allowed_output_paths
        }
        filtered_after = {
            path: digest
            for path, digest in read_only_after.items()
            if path not in allowed_output_paths
        }
        changes = diff_snapshots(filtered_before, filtered_after)
        diff_path = output_path / f"{command_id}.readonly.diff.md"
        diff_rel = write_diff(root, diff_path, changes)
        before_hash = sha256_text(json.dumps(filtered_before, sort_keys=True))
        after_hash = sha256_text(json.dumps(filtered_after, sort_keys=True))
        explicit_before = {
            path: digest
            for path, digest in filtered_before.items()
            if any(path == declared or path.startswith(f"{declared.rstrip('/')}/") for declared in explicit_review_paths)
        }
        explicit_after = {
            path: digest
            for path, digest in filtered_after.items()
            if any(path == declared or path.startswith(f"{declared.rstrip('/')}/") for declared in explicit_review_paths)
        }
        read_only_check = {
            "method": "process_restrictions_plus_workspace_snapshot_with_explicit_review_inputs",
            "before_hash": before_hash,
            "after_hash": after_hash,
            "diff_path": diff_rel,
            "status": "passed" if not changes else "failed",
            "changed_paths": changes,
            "reviewed_artifacts_before": explicit_before,
            "reviewed_artifacts_after": explicit_after,
        }

    command = {
        "command_id": command_id,
        "actor_role": actor_role,
        "argv": argv,
        "cwd": str(root),
        "started_at": started_at,
        "completed_at": completed_at,
        "exit_code": int(completed.returncode),
        "stdout_path": relpath(root, stdout_path),
        "stderr_path": relpath(root, stderr_path),
        "json_transport": "stdout_json_required_supervisor_sidecar",
        "expected_json_report_path": relpath(root, output_path / f"{command_id}.json"),
        "expected_markdown_report_path": relpath(root, output_path / f"{command_id}.md"),
        "prompt_sources": prompt_sources,
        "composed_prompt_path": relpath(root, prompt_path),
        "composed_prompt_sha256": sha256_file(prompt_path),
        "composed_prompt_bytes": prompt_path.stat().st_size,
        "review_input_path": frozen_input_rel,
        "review_input_sha256": sha256_file(resolve_under_root(root, frozen_input_rel, must_exist=True)),
        "review_input_bytes": resolve_under_root(root, frozen_input_rel, must_exist=True).stat().st_size,
        "job_sha256": frozen_input.get("frozen_job_sha256", frozen_input.get("job_sha256")),
        "job_bytes": frozen_input.get("frozen_job_bytes", frozen_input.get("job_bytes")),
        "reviewed_artifact_manifest_path": frozen_input.get("reviewed_artifact_manifest_path"),
        "reviewed_artifact_manifest_sha256": frozen_input.get("reviewed_artifact_manifest_sha256"),
        "stdin_mode": (
            "frozen_review_input"
            if actor_role == "claude_review_agent"
            else "composed_prompt_and_frozen_review_input"
        ),
        "stdin_sha256": sha256_text(input_text),
        "stdin_bytes": len(input_text.encode("utf-8")),
        "fallback_used": fallback_used,
        "embedded_sources_count": len(embedded_sources),
        "embedded_sources_bytes": sum(len(str(item.get("content") or "").encode("utf-8")) for item in embedded_sources),
        "repair_attempt": repair_record,
        **(
            {
                "auth_strategy": "subscription_oauth",
                "tools": ["Read", "Grep", "Glob"],
                "env_unset_for_subscription_oauth": [
                    "ANTHROPIC_API_KEY",
                    "ANTHROPIC_AUTH_TOKEN",
                    "CLAUDE_CODE_OAUTH_TOKEN",
                    "CLAUDE_CODE_USE_BEDROCK",
                    "CLAUDE_CODE_USE_VERTEX",
                    "CLAUDE_CODE_USE_FOUNDRY",
                ],
            }
            if actor_role == "claude_review_agent"
            else {}
        ),
    }

    if read_only_check is not None and read_only_check["status"] != "passed":
        status = "read_only_violation"
        failure_summary = "Agent invocation failed or produced invalid output."
        validation_errors.append("Reviewer modified workspace source files during read-only review.")

    if raw_decision is None or status != "succeeded":
        decision = _minimal_blocked_decision(
            root=root,
            output_dir=output_path,
            command_id=command_id,
            actor_role=actor_role,
            review_kind=review_kind,
            review_cycle_id=review_cycle_id,
            supervisor_session_id=supervisor_session_id,
            status=status,
            summary=failure_summary,
            validation_errors=validation_errors,
            command=command,
            read_only_check=read_only_check,
        )
    else:
        try:
            decision = _write_validated_decision(
                root=root,
                output_dir=output_path,
                command_id=command_id,
                actor_role=actor_role,
                review_kind=review_kind,
                review_cycle_id=review_cycle_id,
                supervisor_session_id=supervisor_session_id,
                raw_decision=raw_decision,
                normalization_source=normalization_source,
                command=command,
                read_only_check=read_only_check,
                invocation_identity={
                    "workflow_id": job_payload.get("workflow_id"),
                    "run_id": job_payload.get("run_id"),
                    "stage_id": job_payload.get("stage_id"),
                },
            )
        except AgentOutputError as exc:
            decision = _minimal_blocked_decision(
                root=root,
                output_dir=output_path,
                command_id=command_id,
                actor_role=actor_role,
                review_kind=review_kind,
                review_cycle_id=review_cycle_id,
                supervisor_session_id=supervisor_session_id,
                status="malformed_output",
                summary="Agent produced parseable JSON that failed review-decision schema validation.",
                validation_errors=[str(exc)],
                command=command,
                read_only_check=read_only_check,
            )

    command = decision.get("command") or command

    invocation_duration_ms = int((time.monotonic() - invocation_started) * 1000)
    usage_report = telemetry.build_usage_report(
        [
            {
                "attempt_id": command_id,
                "lane": "reviewer",
                "model": _command_model(argv),
                "status": decision["status"],
                "duration_ms": invocation_duration_ms,
                "request_wall_ms": None,
                "poll_wall_ms": None,
                "retry_count": int(fallback_used) + int(repair_record is not None),
                "upload_count": 0,
                "uploaded_bytes": 0,
                # Canonical Codex and Claude CLI transports do not guarantee
                # token counters. Keep the counters explicitly unknown.
                "usage": None,
            }
        ]
    )
    validate_against_schema(
        usage_report,
        "usage_report.schema.json",
        "reviewer usage report",
    )
    usage_attempt_path = output_path / f"{command_id}.reviewer_usage_attempt.json"
    write_json_artifact(root, usage_attempt_path, usage_report["attempts"][0])

    return AgentRunResult(
        command_id=command_id,
        actor_role=actor_role,
        status=str(decision["status"]),
        approval_decision=str(decision["approval_decision"]),
        decision_path=str(decision["json_report_path"]),
        markdown_path=str(decision["markdown_report_path"]),
        stdout_path=relpath(root, stdout_path),
        stderr_path=relpath(root, stderr_path),
        command=command,
        read_only_check=read_only_check,
        fallback_used=fallback_used,
        usage_attempt_path=relpath(root, usage_attempt_path),
    )


def invoke_operator_codex(**kwargs: Any) -> AgentRunResult:
    return _invoke_agent(actor_role="operator_codex", **kwargs)


def invoke_codex_review_agent(**kwargs: Any) -> AgentRunResult:
    return _invoke_agent(actor_role="codex_review_agent", **kwargs)


def invoke_claude_review_agent(**kwargs: Any) -> AgentRunResult:
    return _invoke_agent(actor_role="claude_review_agent", **kwargs)
