from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from .contracts import (
    RUN_MANIFEST_SCHEMA_VERSION,
    STAGE_CHECKPOINT_SCHEMA_VERSION,
    WorkflowDefinition,
    relpath,
    resolve_under_root,
    runner_now,
    sha256_file,
    sha256_text,
    timestamp_slug,
    write_json,
    write_text,
)
from .schema_validation import persisted_schema_filename, validate_contract


def create_run_dir(
    *,
    root: Path,
    output_root: Path,
    run_name: str,
    workflow_id: str,
    run_dir: Path | None = None,
) -> Path:
    if run_dir is not None:
        resolved = resolve_under_root(root, run_dir, must_exist=False)
        resolved.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(resolved, 0o700)
        return resolved
    resolved_output_root = resolve_under_root(root, output_root, must_exist=False)
    resolved_output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(resolved_output_root, 0o700)
    base_name = f"{timestamp_slug()}_{run_name}_{workflow_id}"
    for collision_number in range(100):
        suffix = "" if collision_number == 0 else f"_{uuid.uuid4().hex[:12]}"
        target = resolved_output_root / f"{base_name}{suffix}"
        try:
            target.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            continue
        return target
    raise RuntimeError(f"Unable to allocate a unique run directory under {resolved_output_root}")


def stage_root_path(run_dir: Path, stage_number: int, stage_id: str) -> Path:
    return run_dir / "stages" / f"{stage_number:02d}_{stage_id}"


def _attempt_id(attempt_number: int) -> str:
    if attempt_number < 1:
        raise ValueError("attempt_number must be at least 1")
    return f"attempt_{attempt_number:03d}"


def build_stage_paths(
    run_dir: Path,
    stage_number: int,
    stage_id: str,
    *,
    attempt_number: int | None = None,
    create: bool = True,
) -> dict[str, Path]:
    """Return stage artifact paths with an explicit v1 compatibility path.

    Omitting ``attempt_number`` retains the v1 layout for persisted runs and
    existing callers. New v2 submissions allocate an attempt and pass its
    number, placing all mutable evidence under ``attempt_NNN``.
    """

    stage_root = stage_root_path(run_dir, stage_number, stage_id)
    stage_dir = stage_root if attempt_number is None else stage_root / _attempt_id(attempt_number)
    if create:
        stage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(stage_dir, 0o700)
    return {
        "stage_root": stage_root,
        "stage_dir": stage_dir,
        "attempt_dir": stage_dir,
        "input_manifest_json": stage_dir / "input_manifest.json",
        "input_manifest_md": stage_dir / "input_manifest.md",
        "request_payload": stage_dir / "request_payload.json",
        "request_plan": stage_dir / "request_plan.json",
        "local_context_estimate": stage_dir / "local_context_estimate.json",
        "submission_intent": stage_dir / "submission.intent.json",
        "cancellation_intent": stage_dir / "cancellation.intent.json",
        "cancellation_result": stage_dir / "cancellation.result.json",
        "token_preflight": stage_dir / "token_preflight.json",
        "token_preflight_error": stage_dir / "token_preflight.error.json",
        "uploads_json": stage_dir / "uploads.json",
        "response_latest_json": stage_dir / "response.latest.json",
        "response_final_json": stage_dir / "response.final.json",
        "artifact_md": stage_dir / "artifact.md",
        "validator_report": stage_dir / "validator_report.json",
        "structured_output": stage_dir / "output.structured.json",
        "stage_checkpoint": stage_dir / "stage_checkpoint.json",
        "review_dir": stage_dir / "review",
        "review_verdict": stage_dir / "review" / "verdict.json",
        "reviewer_notes": stage_dir / "review" / "reviewer_notes.md",
    }


_ATTEMPT_DIRECTORY_PATTERN = re.compile(r"^attempt_(\d{3,})$")
_STATE_TRANSITION_FILE_PATTERN = re.compile(
    r"^state_transition\.revision_(\d{10,})\.intent\.json$"
)


def list_stage_attempts(run_dir: Path, stage_number: int, stage_id: str) -> list[int]:
    stage_root = stage_root_path(run_dir, stage_number, stage_id)
    if not stage_root.exists():
        return []
    attempts: list[int] = []
    for path in stage_root.iterdir():
        match = _ATTEMPT_DIRECTORY_PATTERN.fullmatch(path.name)
        if path.is_dir() and match:
            attempts.append(int(match.group(1)))
    return sorted(attempts)


def allocate_stage_attempt(
    run_dir: Path,
    stage_number: int,
    stage_id: str,
) -> tuple[int, dict[str, Path]]:
    """Exclusively allocate the next attempt without modifying older attempts.

    The caller should hold ``run_lock(run_dir)`` across eligibility checks,
    allocation, and durable submission-state publication. Exclusive directory
    creation is a second line of defense against accidental shared evidence.
    """

    stage_root = stage_root_path(run_dir, stage_number, stage_id)
    stage_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(stage_root, 0o700)
    candidate = max(list_stage_attempts(run_dir, stage_number, stage_id), default=0) + 1
    while True:
        attempt_dir = stage_root / _attempt_id(candidate)
        try:
            attempt_dir.mkdir(mode=0o700, exist_ok=False)
        except FileExistsError:
            candidate += 1
            continue
        return candidate, build_stage_paths(
            run_dir,
            stage_number,
            stage_id,
            attempt_number=candidate,
            create=False,
        )


def initialize_run_manifest(
    *,
    root: Path,
    workflow: WorkflowDefinition,
    run_id: str,
    run_name: str,
    run_dir: Path,
    operator_overrides: dict[str, Any],
) -> dict[str, Any]:
    stages = []
    for stage in workflow.stages:
        stage_paths = build_stage_paths(
            run_dir,
            stage.stage_number,
            stage.stage_id,
            create=False,
        )
        stages.append(
            {
                "stage_id": stage.stage_id,
                "stage_number": stage.stage_number,
                "gate": stage.gate.value,
                "stage_dir": relpath(root, stage_paths["stage_dir"]),
                "status": "prepared",
            }
        )
    now = runner_now().isoformat()
    return {
        "schema_version": RUN_MANIFEST_SCHEMA_VERSION,
        "run_id": run_id,
        "run_name": run_name,
        "workflow_id": workflow.workflow_id,
        "workflow_manifest_path": relpath(root, workflow.workflow_file),
        "workflow_manifest_sha256": sha256_file(workflow.workflow_file),
        "run_dir": relpath(root, run_dir),
        "started_at": now,
        "updated_at": now,
        "status": "created",
        "stage_order": [stage.stage_id for stage in workflow.stages],
        "operator_overrides": operator_overrides,
        "stages": stages,
    }


def run_manifest_path(run_dir: Path) -> Path:
    return run_dir / "run_manifest.json"


def load_run_manifest(root: Path, run_dir: Path) -> dict[str, Any]:
    payload = json.loads(
        resolve_under_root(root, run_manifest_path(run_dir), must_exist=True).read_text(
            encoding="utf-8"
        )
    )
    validate_contract(
        payload,
        persisted_schema_filename("run_manifest", payload.get("schema_version")),
        label="run manifest",
    )
    return payload


def write_run_manifest(run_dir: Path, manifest: dict[str, Any]) -> Path:
    manifest["updated_at"] = runner_now().isoformat()
    validate_contract(
        manifest,
        persisted_schema_filename("run_manifest", manifest.get("schema_version")),
        label="run manifest",
    )
    return write_json(run_manifest_path(run_dir), manifest)


def write_run_manifest_cas(
    *,
    root: Path,
    run_dir: Path,
    manifest: dict[str, Any],
    expected_revision: int,
    stage_id: str | None = None,
    expected_attempt_id: str | None = None,
    prepared: bool = False,
) -> Path:
    """Write only when the durable manifest is the caller's exact base revision.

    The caller must hold the run lock. Keeping this check next to the atomic
    write prevents a result from a remote call being applied to a newer stage
    attempt.
    """

    current = load_run_manifest(root, run_dir)
    require_run_manifest_revision(
        current,
        expected_revision=expected_revision,
        stage_id=stage_id,
        expected_attempt_id=expected_attempt_id,
    )
    if prepared:
        validate_contract(
            manifest,
            persisted_schema_filename("run_manifest", manifest.get("schema_version")),
            label="run manifest",
        )
        return write_json(run_manifest_path(run_dir), manifest)
    return write_run_manifest(run_dir, manifest)


def require_run_manifest_revision(
    current: dict[str, Any],
    *,
    expected_revision: int,
    stage_id: str | None = None,
    expected_attempt_id: str | None = None,
) -> None:
    """Check a manifest revision and optional current attempt without writing."""

    current_revision = int(current.get("revision", 0))
    if current_revision != expected_revision:
        raise SystemExit(
            "Run manifest revision conflict: "
            f"expected {expected_revision}, found {current_revision}."
        )
    if stage_id is not None:
        current_summary = find_stage_summary(current, stage_id)
        current_attempt_id = current_summary.get("current_attempt_id")
        if current_attempt_id != expected_attempt_id:
            raise SystemExit(
                f"Stage {stage_id} attempt conflict: expected {expected_attempt_id!r}, "
                f"found {current_attempt_id!r}."
            )


def find_stage_summary(run_manifest: dict[str, Any], stage_id: str) -> dict[str, Any]:
    for stage_summary in run_manifest["stages"]:
        if stage_summary["stage_id"] == stage_id:
            return stage_summary
    raise KeyError(stage_id)


def write_input_manifests(
    *,
    stage_paths: dict[str, Path],
    resolved_manifest: dict[str, Any],
    rendered_markdown: str,
) -> tuple[Path, Path]:
    write_json(stage_paths["input_manifest_json"], resolved_manifest)
    write_text(stage_paths["input_manifest_md"], rendered_markdown)
    return stage_paths["input_manifest_json"], stage_paths["input_manifest_md"]


def write_request_payload(
    *,
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    return write_json(stage_paths["request_payload"], payload)


def write_submission_intent(stage_paths: dict[str, Path], payload: dict[str, Any]) -> Path:
    return _write_immutable_text(
        stage_paths["submission_intent"],
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def write_cancellation_intent(stage_paths: dict[str, Path], payload: dict[str, Any]) -> Path:
    return _write_immutable_text(
        stage_paths["cancellation_intent"],
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def write_cancellation_result(stage_paths: dict[str, Path], payload: dict[str, Any]) -> Path:
    return write_json(stage_paths["cancellation_result"], payload)


def write_uploads_payload(stage_paths: dict[str, Path], uploads_payload: dict[str, Any]) -> Path:
    return write_json(stage_paths["uploads_json"], uploads_payload)


def write_token_preflight_success(
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    return write_json(stage_paths["token_preflight"], payload)


def write_token_preflight_error(
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    return write_json(stage_paths["token_preflight_error"], payload)


def write_response_latest(stage_paths: dict[str, Path], response_json: dict[str, Any]) -> Path:
    return write_json(stage_paths["response_latest_json"], response_json)


def json_file_sha256(payload: Any) -> str:
    """Hash the exact owner-only JSON representation written by ``write_json``."""

    return sha256_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def prepare_stage_checkpoint(checkpoint: dict[str, Any]) -> str:
    """Validate and return the hash of checkpoint bytes before publication."""

    checkpoint["schema_version"] = STAGE_CHECKPOINT_SCHEMA_VERSION
    validate_contract(
        checkpoint,
        persisted_schema_filename("stage_checkpoint", checkpoint.get("schema_version")),
        label="stage checkpoint",
    )
    return json_file_sha256(checkpoint)


def write_stage_checkpoint(stage_paths: dict[str, Path], checkpoint: dict[str, Any]) -> Path:
    prepare_stage_checkpoint(checkpoint)
    return write_json(stage_paths["stage_checkpoint"], checkpoint)


def load_stage_checkpoint(stage_paths: dict[str, Path]) -> dict[str, Any]:
    payload = json.loads(stage_paths["stage_checkpoint"].read_text(encoding="utf-8"))
    validate_contract(
        payload,
        persisted_schema_filename("stage_checkpoint", payload.get("schema_version")),
        label="stage checkpoint",
    )
    return payload


def stage_state_transition_path(
    stage_paths: dict[str, Path],
    target_revision: int,
) -> Path:
    if target_revision < 1:
        raise ValueError("target_revision must be positive")
    return (
        stage_paths["attempt_dir"]
        / f"state_transition.revision_{target_revision:010d}.intent.json"
    )


def _validate_stage_state_transition(payload: dict[str, Any], *, label: str) -> None:
    validate_contract(
        payload,
        "stage_state_transition.v1.schema.json",
        label=label,
    )
    checkpoint = payload["target_checkpoint"]
    target_manifest = payload["target_run_manifest"]
    if checkpoint.get("schema_version") != "responses_runner_v2.stage_checkpoint.v2":
        raise SystemExit(f"Invalid {label}: target checkpoint must use the v2 schema.")
    if target_manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
        raise SystemExit(f"Invalid {label}: target run manifest must use the v2 schema.")
    validate_contract(
        checkpoint,
        persisted_schema_filename("stage_checkpoint", checkpoint.get("schema_version")),
        label=f"{label} target checkpoint",
    )
    validate_contract(
        target_manifest,
        persisted_schema_filename("run_manifest", target_manifest.get("schema_version")),
        label=f"{label} target run manifest",
    )
    if json_file_sha256(checkpoint) != payload["target_checkpoint_sha256"]:
        raise SystemExit(f"Invalid {label}: target checkpoint hash mismatch.")
    if json_file_sha256(target_manifest) != payload["target_run_manifest_sha256"]:
        raise SystemExit(f"Invalid {label}: target run manifest hash mismatch.")


def write_stage_state_transition(
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
) -> Path:
    """Durably publish one immutable checkpoint/manifest transition intent."""

    _validate_stage_state_transition(payload, label="stage state transition")
    path = stage_state_transition_path(stage_paths, payload["target_manifest_revision"])
    return _write_immutable_text(
        path,
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
    )


def list_stage_state_transitions(run_dir: Path) -> list[Path]:
    """List only transition intents inside explicit v2 attempt directories."""

    stages_dir = run_dir / "stages"
    if not stages_dir.is_dir() or stages_dir.is_symlink():
        return []
    discovered: list[tuple[int, str, Path]] = []
    for stage_root in stages_dir.iterdir():
        if not stage_root.is_dir() or stage_root.is_symlink():
            continue
        for attempt_dir in stage_root.iterdir():
            if (
                not attempt_dir.is_dir()
                or attempt_dir.is_symlink()
                or _ATTEMPT_DIRECTORY_PATTERN.fullmatch(attempt_dir.name) is None
            ):
                continue
            for path in attempt_dir.iterdir():
                match = _STATE_TRANSITION_FILE_PATTERN.fullmatch(path.name)
                if path.is_file() and not path.is_symlink() and match is not None:
                    discovered.append((int(match.group(1)), path.as_posix(), path))
    return [item[2] for item in sorted(discovered)]


def load_stage_state_transition(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Invalid stage state transition {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SystemExit(f"Stage state transition must be a JSON object: {path}")
    _validate_stage_state_transition(payload, label=f"stage state transition {path}")
    return payload


def extract_output_text(response_json: dict[str, Any]) -> str:
    texts: list[str] = []
    output = response_json.get("output")
    if not isinstance(output, list):
        return ""
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                texts.append(part["text"])
            elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                texts.append(part["refusal"])
    return "\n\n".join(text.strip() for text in texts if text and text.strip()).strip()


def extract_structured_output(response_json: dict[str, Any], requested_text_format: str) -> Any | None:
    if requested_text_format != "json_schema":
        return None
    if response_json.get("output_parsed") is not None:
        return response_json["output_parsed"]
    output = response_json.get("output")
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("parsed") is not None:
                return part.get("parsed")
            if part.get("type") != "output_text" or not isinstance(part.get("text"), str):
                continue
            try:
                return json.loads(part["text"])
            except json.JSONDecodeError:
                continue
    return None


def _write_immutable_text(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if path.read_text(encoding="utf-8") == body:
            return path
        raise
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(body)
        handle.flush()
        os.fsync(handle.fileno())
    directory_descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_descriptor)
    finally:
        os.close(directory_descriptor)
    return path


def write_clean_artifact(path: Path, response_json: dict[str, Any]) -> Path:
    """Write immutable assistant output without runner envelope metadata."""

    output_text = extract_output_text(response_json)
    if not output_text:
        raise ValueError("Cannot create artifact.md without assistant output text.")
    body = output_text.rstrip() + "\n"
    return _write_immutable_text(path, body)


def write_response_final(
    *,
    json_path: Path,
    response_json: dict[str, Any],
    artifact_path: Path | None = None,
) -> Path:
    """Persist the raw terminal response and, when text exists, the clean artifact."""

    write_json(json_path, response_json)
    if artifact_path is not None:
        write_clean_artifact(artifact_path, response_json)
    return json_path
