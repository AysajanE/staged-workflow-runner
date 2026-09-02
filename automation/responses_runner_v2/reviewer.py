"""Single-reviewer gate for `reviewed` stages.

One reviewer CLI (Codex or Claude) reads the stage artifact plus the stage task and
handoff, and returns a small JSON verdict. The verdict and a markdown rendering of it
are written under the attempt's ``review/`` directory; the engine decides whether to
continue, request one primary-model revision, or stop for a human.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import (
    ReviewConfig,
    load_json,
    relpath,
    resolve_under_root,
    runner_now,
    sha256_file,
    write_json,
)
from .schema_validation import ContractValidationError, validate_contract

REVIEW_VERDICT_SCHEMA_VERSION = "responses_runner_v2.stage_review_verdict.v1"
REVIEW_VERDICT_SCHEMA_FILENAME = "stage_review_verdict.schema.json"
REVIEW_PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "stage_review.md"

# Subscription-authenticated `claude -p` must not see API-key style credentials.
CLAUDE_ENV_UNSET = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "CLAUDE_CODE_OAUTH_TOKEN",
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)

_VERDICT_ALIASES = {
    "approve": "approve",
    "approved": "approve",
    "approve_with_conditions": "approve",
    "accept": "approve",
    "accepted": "approve",
    "pass": "approve",
    "revise": "revise",
    "revision": "revise",
    "revise_required": "revise",
    "reject": "revise",
    "rejected": "revise",
    "do_not_approve": "revise",
    "blocked": "revise",
    "block": "revise",
    "fail": "revise",
}

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class ReviewError(SystemExit):
    """The reviewer could not produce a usable verdict."""


@dataclass(frozen=True)
class ReviewResult:
    verdict: dict[str, Any]
    verdict_path: str
    notes_path: str
    invocation_path: str

    @property
    def approved(self) -> bool:
        return self.verdict["verdict"] == "approve"


def default_runner(
    argv: list[str],
    *,
    input_text: str,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None,
) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(
        argv,
        input=input_text,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def build_review_job(
    *,
    root: Path,
    workflow_id: str,
    run_id: str,
    stage_id: str,
    stage_title: str,
    attempt_id: str,
    task_text: str,
    artifact_path: Path,
    input_manifest_markdown_path: Path,
    handoff_paths: list[Path],
    revision_of_attempt_id: str | None,
) -> dict[str, Any]:
    """The exact, bounded input the reviewer sees."""

    return {
        "schema_version": "responses_runner_v2.stage_review_job.v1",
        "workflow_id": workflow_id,
        "run_id": run_id,
        "stage_id": stage_id,
        "stage_title": stage_title,
        "attempt_id": attempt_id,
        "revision_of_attempt_id": revision_of_attempt_id,
        "objective": task_text,
        "artifact_path": relpath(root, artifact_path),
        "input_manifest_markdown_path": relpath(root, input_manifest_markdown_path),
        "handoff_paths": [relpath(root, path) for path in handoff_paths],
        "output_schema": REVIEW_VERDICT_SCHEMA_FILENAME,
    }


def compose_prompt(job: dict[str, Any]) -> str:
    template = REVIEW_PROMPT_PATH.read_text(encoding="utf-8").strip()
    return (
        f"{template}\n\n## Review job\n\n```json\n"
        f"{json.dumps(job, indent=2, sort_keys=True)}\n```\n"
    )


def build_command(
    config: ReviewConfig,
    *,
    prompt_path: Path,
    job_text: str,
    prompt_text: str,
) -> tuple[list[str], str, dict[str, str] | None]:
    """Return (argv, stdin_text, env) for the configured reviewer."""

    effort = config.effective_effort
    model = config.effective_model
    if config.reviewer == "codex":
        argv = [
            "codex",
            "exec",
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--ignore-user-config",
            "-c",
            f'model_reasoning_effort="{effort}"',
            "--output-schema",
            relpath_from_cwd(prompt_path.parent, REVIEW_VERDICT_SCHEMA_FILENAME),
        ]
        if model:
            argv += ["--model", model]
        argv.append("-")
        return argv, prompt_text, None
    if config.reviewer == "claude":
        argv = [
            "claude",
            "-p",
            "--model",
            model or "opus",
            "--effort",
            effort,
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
            str(prompt_path),
        ]
        env = {key: value for key, value in os.environ.items() if key not in CLAUDE_ENV_UNSET}
        return argv, f"Review job (JSON):\n{job_text}\n", env
    raise ReviewError(f"Unsupported reviewer {config.reviewer!r}.")


def relpath_from_cwd(review_dir: Path, schema_filename: str) -> str:
    """Path of the verdict schema as shipped with the engine."""

    del review_dir
    return str(Path(__file__).resolve().parent / "schemas" / schema_filename)


def _json_objects(text: str):
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", text):
        try:
            candidate, _ = decoder.raw_decode(text, match.start())
        except ValueError:
            continue
        if isinstance(candidate, dict):
            yield candidate


def extract_verdict(stdout: str) -> dict[str, Any]:
    """Find the verdict object in reviewer stdout, unwrapping CLI envelopes."""

    text = stdout.strip()
    outer: Any = None
    try:
        outer = json.loads(text)
    except ValueError:
        outer = None
    if isinstance(outer, dict):
        if "verdict" in outer:
            return outer
        structured = outer.get("structured_output")
        if isinstance(structured, dict) and "verdict" in structured:
            return structured
        result = outer.get("result")
        if isinstance(result, dict) and "verdict" in result:
            return result
        if isinstance(result, str):
            text = result
    for candidate in _json_objects(text):
        if "verdict" in candidate:
            return candidate
    raise ReviewError("Reviewer output did not contain a JSON object with a `verdict` field.")


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, sort_keys=True)


def normalize_verdict(raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a reviewer object onto the small verdict contract and validate it."""

    verdict_value = _VERDICT_ALIASES.get(_string(raw.get("verdict")).strip().lower())
    if verdict_value is None:
        raise ReviewError(f"Reviewer verdict {raw.get('verdict')!r} is not approve/revise.")
    findings: list[dict[str, str]] = []
    for index, item in enumerate(raw.get("blocking_findings") or [], start=1):
        if isinstance(item, str):
            item = {"description": item}
        if not isinstance(item, dict):
            continue
        findings.append(
            {
                "id": _string(item.get("id")) or f"finding_{index:02d}",
                "description": _string(item.get("description")),
                "evidence": _string(item.get("evidence")),
                "required_change": _string(item.get("required_change")),
            }
        )
    notes = [_string(item) for item in (raw.get("notes") or []) if _string(item)]
    if verdict_value == "revise" and not findings:
        findings.append(
            {
                "id": "finding_01",
                "description": _string(raw.get("summary")) or "Reviewer requested revision.",
                "evidence": "",
                "required_change": "Address the reviewer summary.",
            }
        )
    if verdict_value == "approve":
        findings = []
    payload = {
        "verdict": verdict_value,
        "summary": _string(raw.get("summary")),
        "blocking_findings": findings,
        "notes": notes,
    }
    try:
        validate_contract(payload, REVIEW_VERDICT_SCHEMA_FILENAME, label="stage review verdict")
    except ContractValidationError as exc:
        raise ReviewError(str(exc)) from exc
    return payload


def render_notes(verdict: dict[str, Any], *, stage_id: str, attempt_id: str) -> str:
    lines = [
        f"# Reviewer notes for stage `{stage_id}` ({attempt_id})",
        "",
        f"Verdict: **{verdict['verdict']}**",
        "",
        verdict["summary"].strip(),
        "",
    ]
    if verdict["blocking_findings"]:
        lines.append("## Blocking findings")
        lines.append("")
        for finding in verdict["blocking_findings"]:
            lines.append(f"- **{finding['id']}**: {finding['description']}")
            if finding["evidence"]:
                lines.append(f"  - Evidence: {finding['evidence']}")
            if finding["required_change"]:
                lines.append(f"  - Required change: {finding['required_change']}")
        lines.append("")
    if verdict["notes"]:
        lines.append("## Notes")
        lines.append("")
        for note in verdict["notes"]:
            lines.append(f"- {note}")
        lines.append("")
    return "\n".join(lines)


def _cost_fields(stdout: str, stderr: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    try:
        outer = json.loads(stdout.strip())
    except ValueError:
        outer = None
    if isinstance(outer, dict):
        for key in ("total_cost_usd", "num_turns", "duration_ms"):
            if key in outer:
                fields[key] = outer[key]
        if isinstance(outer.get("usage"), dict):
            fields["usage"] = outer["usage"]
    match = re.search(r"tokens used[:\s]+([\d,]+)", stderr, re.IGNORECASE)
    if match:
        fields["tokens_used"] = int(match.group(1).replace(",", ""))
    return fields


def run_review(
    *,
    root: Path,
    config: ReviewConfig,
    job: dict[str, Any],
    review_dir: Path,
    runner: Runner | None = None,
) -> ReviewResult:
    """Invoke the reviewer once and persist verdict, notes, and invocation evidence."""

    review_dir = resolve_under_root(root, review_dir, must_exist=False)
    review_dir.mkdir(parents=True, exist_ok=True)
    stamp = runner_now().strftime("%Y%m%dT%H%M%S_%fZ")
    prompt_text = compose_prompt(job)
    prompt_path = review_dir / f"prompt_{stamp}.md"
    prompt_path.write_text(prompt_text, encoding="utf-8")
    job_text = json.dumps(job, indent=2, sort_keys=True)
    argv, stdin_text, env = build_command(
        config, prompt_path=prompt_path, job_text=job_text, prompt_text=prompt_text
    )
    started_at = runner_now().isoformat()
    started = time.monotonic()
    run = runner or default_runner
    try:
        completed = run(
            argv,
            input_text=stdin_text,
            cwd=root,
            timeout=float(config.timeout_seconds),
            env=env,
        )
        exit_code = int(completed.returncode)
        stdout = str(completed.stdout or "")
        stderr = str(completed.stderr or "")
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = str(exc.stdout or "")
        stderr = str(exc.stderr or "") + f"\nreviewer timed out after {config.timeout_seconds}s"
    except FileNotFoundError as exc:
        exit_code = 127
        stdout = ""
        stderr = f"reviewer CLI could not be spawned: {argv[0]} ({exc})"
    duration_ms = int((time.monotonic() - started) * 1000)
    stdout_path = review_dir / f"stdout_{stamp}.txt"
    stderr_path = review_dir / f"stderr_{stamp}.txt"
    stdout_path.write_text(stdout, encoding="utf-8")
    stderr_path.write_text(stderr, encoding="utf-8")
    invocation = {
        "schema_version": "responses_runner_v2.review_invocation.v1",
        "reviewer": config.reviewer,
        "model": config.effective_model,
        "effort": config.effective_effort,
        "argv": argv,
        "cwd": str(root),
        "started_at": started_at,
        "completed_at": runner_now().isoformat(),
        "duration_ms": duration_ms,
        "exit_code": exit_code,
        "prompt_path": relpath(root, prompt_path),
        "stdout_path": relpath(root, stdout_path),
        "stderr_path": relpath(root, stderr_path),
        "artifact_sha256": sha256_file(resolve_under_root(root, job["artifact_path"], must_exist=True)),
        **_cost_fields(stdout, stderr),
    }
    invocation_path = review_dir / f"invocation_{stamp}.json"
    write_json(invocation_path, invocation)
    if exit_code != 0:
        raise ReviewError(
            f"Reviewer {config.reviewer} exited with code {exit_code}; see "
            f"{relpath(root, stderr_path)}. The stage stays completed with its review pending; "
            "run again to retry the review or supply --handoff-note to approve it yourself."
        )
    verdict = normalize_verdict(extract_verdict(stdout))
    verdict_path = review_dir / "verdict.json"
    notes_path = review_dir / "reviewer_notes.md"
    verdict_record = {
        "schema_version": REVIEW_VERDICT_SCHEMA_VERSION,
        **verdict,
        "reviewer": config.reviewer,
        "invocation_path": relpath(root, invocation_path),
        "artifact_sha256": invocation["artifact_sha256"],
        "recorded_at": runner_now().isoformat(),
    }
    write_json(verdict_path, verdict_record)
    notes_path.write_text(
        render_notes(verdict, stage_id=str(job["stage_id"]), attempt_id=str(job["attempt_id"])),
        encoding="utf-8",
    )
    return ReviewResult(
        verdict=verdict_record,
        verdict_path=relpath(root, verdict_path),
        notes_path=relpath(root, notes_path),
        invocation_path=relpath(root, invocation_path),
    )


def load_verdict(root: Path, verdict_path: str | Path) -> dict[str, Any]:
    return load_json(resolve_under_root(root, verdict_path, must_exist=True), "stage review verdict")
