from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .contracts import REVIEW_DECISION_SCHEMA_VERSION, relpath, resolve_under_root, runner_now, sha256_text, write_json, write_text
from .supervisor_artifacts import SchemaValidationError, diff_snapshots, snapshot_workspace, validate_against_schema, write_diff

INTERNAL_PACK_ROOT = "automation/task_packs/responses_runner_v2_supervisor_internal"

COMMAND_TEMPLATE_BY_ROLE = {
    "operator_codex": f"{INTERNAL_PACK_ROOT}/commands/operator_codex.command.json",
    "codex_review_agent": f"{INTERNAL_PACK_ROOT}/commands/codex_review_agent.command.json",
    "claude_review_agent": f"{INTERNAL_PACK_ROOT}/commands/claude_review_agent.command.json",
}

PROMPT_BY_ROLE = {
    "operator_codex": f"{INTERNAL_PACK_ROOT}/prompts/operator_codex.md",
    "codex_review_agent": f"{INTERNAL_PACK_ROOT}/prompts/codex_review.md",
    "claude_review_agent": f"{INTERNAL_PACK_ROOT}/prompts/claude_review.md",
}


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


def load_command_template(root: Path, actor_role: str) -> dict[str, Any]:
    path = resolve_under_root(root, COMMAND_TEMPLATE_BY_ROLE[actor_role], must_exist=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Command template must be an object: {path}")
    return payload


def _read_prompt(root: Path, actor_role: str) -> tuple[str, str]:
    prompt_path = resolve_under_root(root, PROMPT_BY_ROLE[actor_role], must_exist=True)
    return relpath(root, prompt_path), prompt_path.read_text(encoding="utf-8")


def _jsonable_job(job: dict[str, Any] | str | Path, *, root: Path) -> tuple[dict[str, Any], str]:
    if isinstance(job, dict):
        return job, json.dumps(job, indent=2, ensure_ascii=False)
    path = resolve_under_root(root, job, must_exist=True)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise SystemExit(f"Agent job JSON must be an object: {path}")
    return payload, path.read_text(encoding="utf-8")


def _prompt_with_job(prompt_text: str, job_text: str) -> str:
    return (
        prompt_text.rstrip()
        + "\n\n## Supervisor Job JSON\n\n"
        + "Read the following job object as the only job-specific instruction payload. "
        + "Emit exactly the machine-ingestible JSON required by the prompt. Do not wrap stdout JSON in markdown fences.\n\n"
        + "```json\n"
        + job_text.strip()
        + "\n```\n"
    )


def _parse_stdout_json(stdout: str) -> dict[str, Any]:
    stripped = stdout.strip()
    if not stripped:
        raise AgentOutputError("Agent stdout did not contain JSON.")
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise AgentOutputError("Agent stdout was not valid JSON.")
        try:
            payload = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AgentOutputError(f"Agent stdout contained malformed JSON: {exc}") from exc
    if isinstance(payload, dict) and isinstance(payload.get("result"), str):
        result_text = payload["result"].strip()
        try:
            nested = json.loads(result_text)
        except json.JSONDecodeError:
            nested = None
        if isinstance(nested, dict):
            payload = nested
    if isinstance(payload, dict) and isinstance(payload.get("structured_output"), dict):
        payload = payload["structured_output"]
    if not isinstance(payload, dict):
        raise AgentOutputError("Agent JSON output must be an object.")
    return payload


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
        "blocking_issues": [
            {
                "issue_id": f"{command_id}_output_failure",
                "severity": "blocking",
                "description": summary,
                "evidence": validation_errors or [summary],
                "affected_artifacts": [],
            }
        ],
        "non_blocking_improvements": [],
        "recommendations": [],
        "unsupported_claims": [],
        "evidence": [{"source": "supervisor_agents", "quote_or_summary": summary}],
        "command": command,
        "read_only_check": read_only_check,
        "validation_errors": validation_errors,
        "next_action": "blocked",
    }
    write_text(markdown_path, "# Agent invocation failed\n\n" + summary + "\n")
    write_json(json_path, payload)
    return payload


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
    command: dict[str, Any],
    read_only_check: dict[str, Any] | None,
) -> dict[str, Any]:
    markdown_path = output_dir / f"{command_id}.md"
    json_path = output_dir / f"{command_id}.json"
    decision = dict(raw_decision)
    decision["schema_version"] = decision.get("schema_version") or REVIEW_DECISION_SCHEMA_VERSION
    decision["decision_id"] = decision.get("decision_id") or command_id
    decision["created_at"] = decision.get("created_at") or runner_now().isoformat()
    decision["supervisor_session_id"] = str(decision.get("supervisor_session_id") or supervisor_session_id)
    decision["review_cycle_id"] = str(decision.get("review_cycle_id") or review_cycle_id)
    decision["review_kind"] = str(decision.get("review_kind") or review_kind)
    decision["actor_role"] = actor_role
    decision["agent_command_id"] = command_id
    decision["markdown_report_path"] = relpath(root, markdown_path)
    decision["json_report_path"] = relpath(root, json_path)
    decision["command"] = command
    decision["read_only_check"] = read_only_check
    decision.setdefault("workflow_id", None)
    decision.setdefault("run_id", None)
    decision.setdefault("stage_id", None)
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
    command: dict[str, Any],
    read_only_check: dict[str, Any] | None,
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
        command=command,
        read_only_check=read_only_check,
    )
    validate_review_decision(decision)
    markdown_path = output_dir / f"{command_id}.md"
    json_path = output_dir / f"{command_id}.json"
    write_text(markdown_path, _render_markdown(decision))
    write_json(json_path, decision)
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


def _invoke_agent(
    *,
    root: Path,
    actor_role: str,
    review_kind: str,
    review_cycle_id: str,
    supervisor_session_id: str,
    job: dict[str, Any] | str | Path,
    output_dir: str | Path,
    runner: Callable[..., Any] | None = None,
    timeout_seconds: int | None = None,
) -> AgentRunResult:
    output_path = resolve_under_root(root, output_dir, must_exist=False)
    output_path.mkdir(parents=True, exist_ok=True)
    template = load_command_template(root, actor_role)
    timeout = int(timeout_seconds or template.get("timeout_seconds", 1800))
    command_id = f"cmd_{actor_role}_{review_cycle_id}_{runner_now().strftime('%H%M%S')}"
    prompt_rel, prompt_text = _read_prompt(root, actor_role)
    job_payload, job_text = _jsonable_job(job, root=root)

    fallback_used = False
    input_text = None
    env = None
    if actor_role in {"operator_codex", "codex_review_agent"}:
        argv = ["codex", "exec", _prompt_with_job(prompt_text, job_text)]
    elif actor_role == "claude_review_agent":
        prompt_path = resolve_under_root(root, prompt_rel, must_exist=True)
        argv = [
            "claude",
            "-p",
            "--model",
            "opus",
            "--effort",
            "max",
            "--output-format",
            "json",
            "--tools",
            "Read",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--setting-sources",
            "user",
            "--append-system-prompt-file",
            str(prompt_path),
        ]
        input_text = job_text
        env = _claude_subscription_env()
    else:
        raise SystemExit(f"Unsupported actor_role for agent invocation: {actor_role}")

    read_only_before = snapshot_workspace(root) if actor_role in {"codex_review_agent", "claude_review_agent"} else None
    started_at = runner_now().isoformat()
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
    completed_at = runner_now().isoformat()

    if actor_role == "claude_review_agent" and completed.returncode != 0 and _unsupported_effort(str(completed.stderr), str(completed.stdout)):
        fallback_used = True
        fallback_argv = list(argv)
        effort_index = fallback_argv.index("--effort") + 1
        fallback_argv[effort_index] = "xhigh"
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
        completed_at = runner_now().isoformat()
        argv = fallback_argv

    stdout_path = output_path / f"{command_id}.stdout.txt"
    stderr_path = output_path / f"{command_id}.stderr.txt"
    write_text(stdout_path, str(completed.stdout or ""))
    write_text(stderr_path, str(completed.stderr or ""))

    read_only_check = None
    if actor_role in {"codex_review_agent", "claude_review_agent"}:
        read_only_after = snapshot_workspace(root)
        allowed_output_paths = {
            relpath(root, stdout_path),
            relpath(root, stderr_path),
            relpath(root, output_path / f"{command_id}.json"),
            relpath(root, output_path / f"{command_id}.md"),
            relpath(root, output_path / f"{command_id}.readonly.diff.md"),
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
        read_only_check = {
            "method": "workspace_snapshot_excluding_local_artifacts",
            "before_hash": before_hash,
            "after_hash": after_hash,
            "diff_path": diff_rel,
            "status": "passed" if not changes else "failed",
            "changed_paths": changes,
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
        "prompt_file": prompt_rel,
        "fallback_used": fallback_used,
        "job_keys": sorted(job_payload.keys()),
        **(
            {
                "stdin_mode": "review_job_json",
                "auth_strategy": "subscription_oauth",
                "tools": ["Read"],
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

    validation_errors: list[str] = []
    raw_decision: dict[str, Any] | None = None
    status = "succeeded"
    if int(completed.returncode) == 124:
        status = "timeout"
        validation_errors.append("Agent command timed out.")
    elif int(completed.returncode) != 0:
        status = "failed"
        validation_errors.append(f"Agent command exited with {completed.returncode}.")
    else:
        try:
            raw_decision = _parse_stdout_json(str(completed.stdout or ""))
        except AgentOutputError as exc:
            status = "malformed_output"
            validation_errors.append(str(exc))

    if read_only_check is not None and read_only_check["status"] != "passed":
        status = "read_only_violation"
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
            summary="Agent invocation failed or produced invalid output.",
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
                command=command,
                read_only_check=read_only_check,
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
    )


def invoke_operator_codex(**kwargs: Any) -> AgentRunResult:
    return _invoke_agent(actor_role="operator_codex", **kwargs)


def invoke_codex_review_agent(**kwargs: Any) -> AgentRunResult:
    return _invoke_agent(actor_role="codex_review_agent", **kwargs)


def invoke_claude_review_agent(**kwargs: Any) -> AgentRunResult:
    return _invoke_agent(actor_role="claude_review_agent", **kwargs)
