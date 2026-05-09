from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    HUMAN_PAUSE_SCHEMA_VERSION,
    STAGE_OUTCOME_SCHEMA_VERSION,
    load_json,
    relpath,
    resolve_under_root,
    runner_now,
)
from .supervisor_artifacts import write_json_artifact

OUTCOME_COMPLETED_COMPLETE = "completed_complete_artifact"
OUTCOME_FAILED_COMPLETE = "failed_complete_artifact"
OUTCOME_FAILED_NO_ARTIFACT = "failed_no_artifact"
OUTCOME_INCOMPLETE_OUTPUT_LIMIT = "incomplete_output_limit"
OUTCOME_BLOCKED_PREFLIGHT = "blocked_token_preflight"
OUTCOME_MONITORING_ANOMALY = "long_running_monitoring_anomaly"

REVIEWABLE_OUTCOMES = {OUTCOME_COMPLETED_COMPLETE, OUTCOME_FAILED_COMPLETE}


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _artifact_text(root: Path, path_value: str | None) -> str:
    if not path_value:
        return ""
    try:
        path = resolve_under_root(root, path_value, must_exist=True)
    except SystemExit:
        return ""
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _response_json(root: Path, checkpoint: dict[str, Any]) -> dict[str, Any] | None:
    artifacts_payload = checkpoint.get("artifacts")
    if not isinstance(artifacts_payload, dict):
        return None
    for key in ("response_final_json_path", "response_latest_json_path"):
        value = artifacts_payload.get(key)
        if isinstance(value, str) and value:
            try:
                return load_json(resolve_under_root(root, value, must_exist=True), key)
            except SystemExit:
                continue
    return None


def _response_markdown_path(checkpoint: dict[str, Any]) -> str | None:
    artifacts_payload = checkpoint.get("artifacts")
    if not isinstance(artifacts_payload, dict):
        return None
    value = artifacts_payload.get("response_final_markdown_path")
    return value if isinstance(value, str) else None


def _has_substantive_markdown(root: Path, checkpoint: dict[str, Any]) -> tuple[bool, str, str | None]:
    path_value = _response_markdown_path(checkpoint)
    text = _artifact_text(root, path_value)
    normalized = text.strip()
    if len(normalized) < 40:
        return False, "response markdown is missing or too short", path_value
    if "No assistant text was returned." in normalized:
        return False, "response markdown contains no assistant text", path_value
    return True, "response markdown contains substantive assistant text", path_value


def _checklist_item(
    *,
    item: str,
    passed: bool,
    detail: str,
    path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"item": item, "passed": passed, "detail": detail}
    if path is not None:
        payload["path"] = path
    return payload


def _sidecar_required_and_missing(root: Path, checkpoint: dict[str, Any]) -> bool:
    artifacts_payload = checkpoint.get("artifacts")
    if not isinstance(artifacts_payload, dict):
        return False
    sidecar_json = artifacts_payload.get("sidecar_response_json_path")
    structured = artifacts_payload.get("structured_output_path")
    if sidecar_json is None and structured is None:
        return False
    for value in (sidecar_json, structured):
        if isinstance(value, str) and value and not resolve_under_root(root, value, must_exist=False).exists():
            return True
    return False


def build_human_pause(
    *,
    root: Path,
    output_path: str | Path,
    pause_id: str,
    trigger: str,
    artifact_to_present: str,
    decision_required: str,
    safe_continuation_action: str,
    severity: str = "blocking",
    automation_may_resume: bool = False,
    blocks_review_bundle_creation: bool = True,
    related_outcome_id: str | None = None,
    related_review_cycle_id: str | None = None,
) -> dict[str, Any]:
    pause = {
        "schema_version": HUMAN_PAUSE_SCHEMA_VERSION,
        "human_pause_id": pause_id,
        "created_at": runner_now().isoformat(),
        "severity": severity,
        "trigger": trigger,
        "artifact_to_present": artifact_to_present,
        "decision_required": decision_required,
        "safe_continuation_action": safe_continuation_action,
        "automation_may_resume": automation_may_resume,
        "blocks_review_bundle_creation": blocks_review_bundle_creation,
        "related_outcome_id": related_outcome_id,
        "related_review_cycle_id": related_review_cycle_id,
    }
    write_json_artifact(root, output_path, pause, "human_pause.schema.json", "human pause")
    return pause


def _outcome_payload(
    *,
    root: Path,
    checkpoint_path: Path,
    checkpoint: dict[str, Any],
    classification: str,
    detection_signals: list[str],
    completeness_checklist: list[dict[str, Any]],
    action: str,
    reviewable: bool,
    review_bundle_allowed: bool,
    rerun_allowed: bool,
    human_pause_required: bool,
    human_pause: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response = checkpoint.get("response") if isinstance(checkpoint.get("response"), dict) else {}
    return {
        "schema_version": STAGE_OUTCOME_SCHEMA_VERSION,
        "outcome_id": f"outcome_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}",
        "created_at": runner_now().isoformat(),
        "run_id": str(checkpoint.get("run_id", "")),
        "stage_id": str(checkpoint.get("stage_id", "")),
        "response_id": response.get("id"),
        "response_status": response.get("status"),
        "classification": classification,
        "detection_signals": detection_signals,
        "completeness_checklist": completeness_checklist,
        "action": action,
        "reviewable": reviewable,
        "review_bundle_allowed": review_bundle_allowed,
        "rerun_allowed": rerun_allowed,
        "rerun_requires_archive": classification == OUTCOME_FAILED_NO_ARTIFACT,
        "human_pause_required": human_pause_required,
        "human_pause": human_pause,
        "checkpoint_path": relpath(root, checkpoint_path),
    }


def classify_stage_outcome(
    *,
    root: Path,
    checkpoint_path: str | Path,
    human_pause_output: str | Path | None = None,
) -> dict[str, Any]:
    resolved_checkpoint = resolve_under_root(root, checkpoint_path, must_exist=True)
    checkpoint = load_json(resolved_checkpoint, "stage checkpoint")
    status = str(checkpoint.get("status", ""))
    token_preflight = checkpoint.get("token_preflight") if isinstance(checkpoint.get("token_preflight"), dict) else {}

    if status == "blocked" or token_preflight.get("status") == "failed_closed":
        pause = None
        outcome_id = f"outcome_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}"
        if human_pause_output is not None:
            pause = build_human_pause(
                root=root,
                output_path=human_pause_output,
                pause_id=f"pause_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}_preflight",
                trigger="Token preflight failed closed or stage checkpoint is blocked.",
                artifact_to_present=relpath(root, resolved_checkpoint),
                decision_required="Decide whether to reduce context, alter stage scope, or adjust model/settings.",
                safe_continuation_action="Repair scaffold or input manifest, rerun dry-run validation, then relaunch only after approval.",
                related_outcome_id=outcome_id,
            )
        return _outcome_payload(
            root=root,
            checkpoint_path=resolved_checkpoint,
            checkpoint=checkpoint,
            classification=OUTCOME_BLOCKED_PREFLIGHT,
            detection_signals=[f"checkpoint.status={status}", f"token_preflight.status={token_preflight.get('status')}"],
            completeness_checklist=[{"item": "live_submission", "passed": False, "detail": "No reviewable live response exists."}],
            action="human_pause",
            reviewable=False,
            review_bundle_allowed=False,
            rerun_allowed=False,
            human_pause_required=True,
            human_pause=pause,
        )

    response_payload = _response_json(root, checkpoint) or {}
    response_status = str(response_payload.get("status") or checkpoint.get("response", {}).get("status") or "")
    substantive, substantive_reason, markdown_path = _has_substantive_markdown(root, checkpoint)
    final_json_present = bool(response_payload)
    sidecar_missing = _sidecar_required_and_missing(root, checkpoint)
    checklist = [
        _checklist_item(
            item="response_final_json",
            passed=final_json_present,
            detail="response JSON exists" if final_json_present else "response JSON missing",
        ),
        _checklist_item(
            item="response_final_markdown",
            passed=substantive,
            detail=substantive_reason,
            path=markdown_path,
        ),
        _checklist_item(
            item="required_sidecar",
            passed=not sidecar_missing,
            detail="required sidecar present or not configured",
        ),
    ]

    if response_status == "completed" and substantive and final_json_present and not sidecar_missing:
        return _outcome_payload(
            root=root,
            checkpoint_path=resolved_checkpoint,
            checkpoint=checkpoint,
            classification=OUTCOME_COMPLETED_COMPLETE,
            detection_signals=["response.status=completed", "complete markdown/json artifacts present"],
            completeness_checklist=checklist,
            action="review",
            reviewable=True,
            review_bundle_allowed=True,
            rerun_allowed=False,
            human_pause_required=False,
        )

    if response_status == "failed":
        if substantive and final_json_present and not sidecar_missing:
            return _outcome_payload(
                root=root,
                checkpoint_path=resolved_checkpoint,
                checkpoint=checkpoint,
                classification=OUTCOME_FAILED_COMPLETE,
                detection_signals=["response.status=failed", "substantive assistant artifact present"],
                completeness_checklist=checklist,
                action="review_failed_artifact",
                reviewable=True,
                review_bundle_allowed=True,
                rerun_allowed=False,
                human_pause_required=False,
            )
        return _outcome_payload(
            root=root,
            checkpoint_path=resolved_checkpoint,
            checkpoint=checkpoint,
            classification=OUTCOME_FAILED_NO_ARTIFACT,
            detection_signals=["response.status=failed", "no complete substantive assistant artifact"],
            completeness_checklist=checklist,
            action="archive_before_rerun",
            reviewable=False,
            review_bundle_allowed=False,
            rerun_allowed=True,
            human_pause_required=False,
        )

    if response_status == "incomplete" or status == "incomplete":
        incomplete_details = response_payload.get("incomplete_details") or checkpoint.get("incomplete_details") or {}
        reason = json.dumps(incomplete_details, sort_keys=True)
        pause = None
        outcome_id = f"outcome_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}"
        if human_pause_output is not None:
            pause = build_human_pause(
                root=root,
                output_path=human_pause_output,
                pause_id=f"pause_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}_incomplete",
                trigger=f"Response incomplete; details={reason}",
                artifact_to_present=relpath(root, resolved_checkpoint),
                decision_required="Decide whether to revise stage scope, max output, or model settings before rerun.",
                safe_continuation_action="Archive attempt, adjust approved scaffold or settings, rerun dry-run validation, and relaunch only after acceptance.",
                related_outcome_id=outcome_id,
            )
        return _outcome_payload(
            root=root,
            checkpoint_path=resolved_checkpoint,
            checkpoint=checkpoint,
            classification=OUTCOME_INCOMPLETE_OUTPUT_LIMIT,
            detection_signals=[f"response.status={response_status or status}", f"incomplete_details={reason}"],
            completeness_checklist=checklist,
            action="block_and_recover",
            reviewable=False,
            review_bundle_allowed=False,
            rerun_allowed=False,
            human_pause_required=True,
            human_pause=pause,
        )

    return _outcome_payload(
        root=root,
        checkpoint_path=resolved_checkpoint,
        checkpoint=checkpoint,
        classification=OUTCOME_FAILED_NO_ARTIFACT,
        detection_signals=[f"unrecognized terminal state status={status} response_status={response_status}"],
        completeness_checklist=checklist,
        action="archive_before_rerun",
        reviewable=False,
        review_bundle_allowed=False,
        rerun_allowed=True,
        human_pause_required=False,
    )


def detect_monitoring_anomaly(
    *,
    root: Path,
    checkpoint_path: str | Path,
    stale_after_seconds: float,
    human_pause_output: str | Path | None = None,
) -> dict[str, Any] | None:
    resolved_checkpoint = resolve_under_root(root, checkpoint_path, must_exist=True)
    checkpoint = load_json(resolved_checkpoint, "stage checkpoint")
    response = checkpoint.get("response")
    if not isinstance(response, dict):
        return None
    status = str(response.get("status", ""))
    if status not in {"queued", "in_progress"}:
        return None
    updated = _parse_datetime(str(checkpoint.get("updated_at", "")))
    age_seconds = stale_after_seconds + 1 if updated is None else (runner_now() - updated).total_seconds()
    if age_seconds < stale_after_seconds:
        return None

    outcome_id = f"outcome_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}"
    pause = None
    if human_pause_output is not None:
        pause = build_human_pause(
            root=root,
            output_path=human_pause_output,
            pause_id=f"pause_{checkpoint.get('run_id', 'unknown')}_{checkpoint.get('stage_id', 'unknown')}_monitoring",
            trigger=f"Live response {response.get('id')} remained {status} for {age_seconds:.1f}s.",
            artifact_to_present=relpath(root, resolved_checkpoint),
            decision_required="Decide whether to continue monitoring, cancel remotely, or wait for terminal status.",
            safe_continuation_action="Refresh/resume the existing response_id; do not submit duplicate work while it may complete.",
            related_outcome_id=outcome_id,
        )

    return _outcome_payload(
        root=root,
        checkpoint_path=resolved_checkpoint,
        checkpoint=checkpoint,
        classification=OUTCOME_MONITORING_ANOMALY,
        detection_signals=[
            f"response.status={status}",
            f"response_id={response.get('id')}",
            f"checkpoint_age_seconds={age_seconds:.1f}",
        ],
        completeness_checklist=[
            {"item": "terminal_response", "passed": False, "detail": "Response remains nonterminal."},
            {"item": "duplicate_submit_guard", "passed": True, "detail": "Existing response_id must be refreshed/resumed, not resubmitted."},
        ],
        action="monitor_without_duplicate_submit",
        reviewable=False,
        review_bundle_allowed=False,
        rerun_allowed=False,
        human_pause_required=True,
        human_pause=pause,
    )


def write_stage_outcome(root: Path, output_path: str | Path, outcome: dict[str, Any]) -> str:
    return write_json_artifact(root, output_path, outcome, "stage_outcome.schema.json", "stage outcome")


def can_rerun_failed_no_artifact(
    *,
    outcome: dict[str, Any],
    archive_manifest: dict[str, Any] | None,
    current_request_hash: str | None = None,
    current_scaffold_hash: str | None = None,
) -> bool:
    if outcome.get("classification") != OUTCOME_FAILED_NO_ARTIFACT:
        return False
    if not outcome.get("rerun_allowed"):
        return False
    if not archive_manifest:
        return False
    if archive_manifest.get("schema_version") != "responses_runner_v2.supervisor_archive.v1":
        return False
    if not archive_manifest.get("rerun_as_is_eligible"):
        return False
    evidence = archive_manifest.get("unchanged_input_evidence")
    if not isinstance(evidence, dict) or evidence.get("rerun_requires_same_hashes") is not True:
        return False
    if current_request_hash is not None and current_request_hash != evidence.get("request_hash_before"):
        return False
    if current_scaffold_hash is not None and current_scaffold_hash != evidence.get("scaffold_hash_before"):
        return False
    return True
