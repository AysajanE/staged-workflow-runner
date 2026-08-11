from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import attachments
from .artifacts import (
    extract_structured_output,
    write_response_pair,
)
from .contracts import (
    DEFAULT_STRUCTURAL_MODEL,
    COMMON_RUNNER_INSTRUCTIONS,
    RUNNER_VERSION,
    TERMINAL_RESPONSE_STATUSES,
    base_model_name,
    build_prompt_cache_key,
    load_json,
    normalize_prompt_cache_retention,
    relpath,
    read_text,
    repo_root,
    sha256_file,
    validate_model_options,
    write_json,
)
from .pack_loader import load_schema_json


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
SIDECAR_MAX_OUTPUT_TOKENS = 128000
SIDECAR_MAX_WAIT_SECONDS = 1800.0
SIDECAR_POLL_INTERVAL_SECONDS = 2.0
SIDECAR_MAX_RETRYABLE_TERMINAL_RETRIES = 2
SIDECAR_RETRYABLE_ERROR_CODES = {
    "internal_server_error",
    "rate_limit_exceeded",
    "server_error",
    "service_unavailable",
    "timeout",
}


def _sidecar_companion_path(sidecar_response_json_path: Path, suffix: str) -> Path:
    return sidecar_response_json_path.with_suffix(f".{suffix}.json")


def _sidecar_extraction_failure_message(response_json: dict[str, Any]) -> str:
    response_id = str(response_json.get("id") or "<unknown>")
    status = str(response_json.get("status") or "<unknown>")
    details = response_json.get("incomplete_details")
    reason = None
    if isinstance(details, dict):
        reason = details.get("reason")
    if status == "incomplete" and reason == "max_output_tokens":
        budget = response_json.get("max_output_tokens")
        budget_text = f" at max_output_tokens={budget}" if budget is not None else ""
        return (
            f"Sidecar extraction response {response_id} was incomplete because it exhausted "
            f"its output token budget{budget_text}; no complete structured output was produced."
        )
    error = response_json.get("error")
    if isinstance(error, dict) and error.get("message"):
        return f"Sidecar extraction response {response_id} ended with status={status}: {error['message']}"
    return f"Sidecar extraction response {response_id} ended with status={status} and no structured output."


def _sidecar_error_code(response_json: dict[str, Any]) -> str | None:
    error = response_json.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return str(code) if code else None


def _sidecar_attempt_records(sidecar_attempts_json_path: Path) -> list[dict[str, Any]]:
    if not sidecar_attempts_json_path.exists():
        return []
    payload = load_json(sidecar_attempts_json_path, "sidecar retry attempts")
    records = payload.get("attempts")
    return records if isinstance(records, list) else []


def _sidecar_retryable_attempt_count(sidecar_attempts_json_path: Path) -> int:
    return sum(
        1
        for record in _sidecar_attempt_records(sidecar_attempts_json_path)
        if str(record.get("retry_reason", "")).startswith("retryable_terminal_")
    )


def _upsert_sidecar_attempt(
    sidecar_attempts_json_path: Path,
    response_json: dict[str, Any] | None,
    *,
    retry_reason: str | None,
    retry_count: int | None,
    submission_attempt_id: str | None = None,
    model: object = None,
    status: object = None,
    duration_ms: int | None = None,
    request_wall_ms: int | None = None,
    poll_wall_ms: int | None = None,
    upload_count: int | None = None,
    uploaded_bytes: int | None = None,
    error_type: str | None = None,
    error: str | None = None,
) -> None:
    records = _sidecar_attempt_records(sidecar_attempts_json_path)
    response_id = response_json.get("id") if response_json is not None else None
    existing = next(
        (
            record
            for record in records
            if (
                response_id is not None
                and record.get("response_id") == response_id
            )
            or (
                submission_attempt_id is not None
                and record.get("submission_attempt_id") == submission_attempt_id
            )
        ),
        None,
    )
    if existing is None:
        existing = {
            "attempt_id": f"sidecar_attempt_{len(records) + 1:03d}",
            "lane": "sidecar",
        }
        records.append(existing)
    if submission_attempt_id is not None:
        existing["submission_attempt_id"] = submission_attempt_id
    if response_json is not None:
        if response_id is not None:
            existing["response_id"] = response_id
        existing.update(
            {
                "error_code": _sidecar_error_code(response_json),
                "incomplete_details": response_json.get("incomplete_details"),
                "max_output_tokens": response_json.get("max_output_tokens"),
            }
        )
        model = response_json.get("model", model)
        status = response_json.get("status", status)
    for key, value in {"model": model, "status": status}.items():
        if value is not None or key not in existing:
            existing[key] = value
    if retry_reason is not None and (
        retry_reason != "terminal_attempt" or not existing.get("retry_reason")
    ):
        existing["retry_reason"] = retry_reason
    for key, value in {
        "duration_ms": duration_ms,
        "request_wall_ms": request_wall_ms,
        "poll_wall_ms": poll_wall_ms,
        "retry_count": retry_count,
        "upload_count": upload_count,
        "uploaded_bytes": uploaded_bytes,
    }.items():
        if value is not None or key not in existing:
            existing[key] = value
    if response_json is not None and (
        "usage" in response_json or "usage" not in existing
    ):
        existing["usage"] = response_json.get("usage")
    elif "usage" not in existing:
        existing["usage"] = None
    if error_type is not None:
        existing["error_type"] = error_type
    if error is not None:
        existing["error"] = error
    write_json(sidecar_attempts_json_path, {"attempts": records})


def _sidecar_response_retry_count(
    *,
    response_id: object,
    sidecar_submissions_json_path: Path,
    sidecar_attempts_json_path: Path,
) -> int | None:
    if isinstance(response_id, str) and response_id:
        submissions = _load_submission_journal(sidecar_submissions_json_path)["attempts"]
        for index, submission in enumerate(submissions):
            if isinstance(submission, dict) and submission.get("response_id") == response_id:
                return index
        for index, attempt in enumerate(_sidecar_attempt_records(sidecar_attempts_json_path)):
            if attempt.get("response_id") == response_id:
                value = attempt.get("retry_count")
                return value if isinstance(value, int) and not isinstance(value, bool) else index
    return None


def _new_sidecar_uploads_payload(
    *,
    delete_uploaded_files_on_complete: bool,
    file_expiration_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "delete_uploaded_files_on_complete": delete_uploaded_files_on_complete,
        "file_expiration_policy": file_expiration_policy,
        "files": [],
    }


def _upload_sidecar_source(
    *,
    root: Path,
    client: Any,
    uploads_payload: dict[str, Any],
    uploads_path: Path,
    source_path: Path,
    attachment_role: str,
    purpose: str,
    file_expiration_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    upload_sha256 = sha256_file(source_path)
    record: dict[str, Any] = {
        "attachment_role": attachment_role,
        "display_name": source_path.name,
        "source_path": relpath(root, source_path),
        "upload_filename": source_path.name,
        "wrapped_as_markdown": False,
        "bytes": source_path.stat().st_size,
        "upload_sha256": upload_sha256,
        "status": "uploading",
    }
    uploads_payload.setdefault("files", []).append(record)
    write_json(uploads_path, uploads_payload)
    try:
        upload = client.upload_file(
            source_path,
            purpose=purpose,
            file_expiration_policy=file_expiration_policy,
        )
        raw_file_id = upload.get("id") if isinstance(upload, dict) else None
        if not isinstance(raw_file_id, str) or not raw_file_id:
            raise ValueError("Sidecar file upload response did not include a non-empty id")
    except Exception as exc:
        record.update(
            {
                "status": "upload_outcome_unknown",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )
        write_json(uploads_path, uploads_payload)
        raise
    post_upload_sha256 = sha256_file(source_path)
    if post_upload_sha256 != upload_sha256:
        record.update(
            {
                "status": "upload_source_mutated",
                "file_id": raw_file_id,
                "post_upload_sha256": post_upload_sha256,
            }
        )
        write_json(uploads_path, uploads_payload)
        raise SystemExit(f"Sidecar upload source changed during upload: {source_path}")
    record.update(
        {
            "status": "uploaded",
            "file_id": raw_file_id,
            "purpose": upload.get("purpose", purpose),
            "created_at": upload.get("created_at"),
            "expires_at": upload.get("expires_at"),
        }
    )
    write_json(uploads_path, uploads_payload)
    return upload


def _load_submission_journal(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": "responses_runner_v2.sidecar_submission_journal.v1", "attempts": []}
    payload = load_json(path, "sidecar submission journal")
    if not isinstance(payload.get("attempts"), list):
        raise SystemExit(f"Sidecar submission journal has invalid attempts: {path}")
    return payload


def _assert_no_unknown_submission(path: Path, latest_response_path: Path) -> None:
    journal = _load_submission_journal(path)
    attempts = journal["attempts"]
    if not attempts or not isinstance(attempts[-1], dict):
        return
    for attempt in attempts[:-1]:
        if isinstance(attempt, dict) and attempt.get("status") in {
            "submitting",
            "submission_outcome_unknown",
        }:
            raise SystemExit(
                "Sidecar submission outcome is unknown; reconcile every durable submission "
                "journal entry before any new sidecar POST."
            )
    status = attempts[-1].get("status")
    if status == "submitting" and latest_response_path.exists():
        latest = load_json(latest_response_path, "latest sidecar response")
        response_id = latest.get("id")
        if isinstance(response_id, str) and response_id:
            attempts[-1].update({"status": "submitted", "response_id": response_id})
            write_json(path, journal)
            return
    if status in {"submitting", "submission_outcome_unknown"}:
        raise SystemExit(
            "Sidecar submission outcome is unknown; reconcile the durable submission journal "
            "before any new sidecar POST."
        )


def _assert_no_unknown_upload(uploads_payload: dict[str, Any]) -> None:
    for record in uploads_payload.get("files", []):
        if isinstance(record, dict) and record.get("status") in {
            "uploading",
            "upload_outcome_unknown",
        }:
            raise SystemExit(
                "Sidecar upload outcome is unknown; reconcile the durable upload journal "
                "before uploading the source again."
            )


def _begin_submission(path: Path, *, request_path: Path, request_sha256: str) -> tuple[dict[str, Any], int]:
    journal = _load_submission_journal(path)
    attempts = journal["attempts"]
    attempts.append(
        {
            "attempt_id": f"sidecar_submission_{len(attempts) + 1:03d}",
            "request_path": request_path.name,
            "request_sha256": request_sha256,
            "status": "submitting",
            "response_id": None,
            "request_wall_ms": None,
        }
    )
    write_json(path, journal)
    return journal, len(attempts) - 1


def _update_submission(
    path: Path,
    journal: dict[str, Any],
    index: int,
    **updates: Any,
) -> None:
    journal["attempts"][index].update(updates)
    write_json(path, journal)


def _shared_instructions() -> str:
    return read_text(
        PROMPTS_DIR / "structured_sidecar_shared_instructions.md",
        "structured sidecar shared instructions",
    ).strip()


def _task_prompt() -> str:
    return read_text(
        PROMPTS_DIR / "structured_sidecar_task.md",
        "structured sidecar task prompt",
    ).strip()


def run_sidecar_processing(
    *,
    root: Path | None,
    client: Any,
    workflow_id: str,
    run_id: str,
    stage_id: str,
    stage_number: int,
    structural_model: str,
    reasoning_effort: str,
    prompt_cache_retention: str | None,
    schema_file: str | Path,
    schema_name: str,
    response_markdown_path: Path,
    response_json_path: Path,
    sidecar_response_json_path: Path,
    sidecar_response_markdown_path: Path,
    structured_output_path: Path,
    service_tier: str | None,
    safety_identifier: str | None,
    file_expiration_policy: dict[str, Any] | None,
    delete_uploaded_files_on_complete: bool,
    store: bool = True,
    file_purpose: str = "user_data",
    reasoning_mode: str | None = None,
    prompt_cache_mode: str | None = None,
    prompt_cache_ttl: str | None = None,
    raw_recovery_reason: str | None = None,
) -> dict[str, Any]:
    """Run the framework-owned structured sidecar extraction pass.

    The workflow engine decides when to call this function. The sidecar keeps
    the existing public signature and behavior, while model migration is driven
    through DEFAULT_STRUCTURAL_MODEL and validate_model_options from contracts.
    """
    root = root or repo_root()
    sidecar_latest_json_path = _sidecar_companion_path(sidecar_response_json_path, "latest")
    sidecar_raw_json_path = _sidecar_companion_path(sidecar_response_json_path, "raw")
    sidecar_request_json_path = _sidecar_companion_path(sidecar_response_json_path, "request")
    sidecar_uploads_json_path = _sidecar_companion_path(sidecar_response_json_path, "uploads")
    sidecar_attempts_json_path = _sidecar_companion_path(sidecar_response_json_path, "attempts")
    sidecar_submissions_json_path = _sidecar_companion_path(sidecar_response_json_path, "submissions")

    if sidecar_response_json_path.exists() and structured_output_path.exists():
        response_json = load_json(sidecar_response_json_path, "sidecar response")
        structured_output = load_json(structured_output_path, "structured sidecar output")
        return {
            "response_json": response_json,
            "structured_output": structured_output,
            "sidecar_response_json_path": sidecar_response_json_path,
            "sidecar_response_markdown_path": sidecar_response_markdown_path,
            "structured_output_path": structured_output_path,
            "uploads_payload": None,
        }

    uploads_payload = (
        load_json(sidecar_uploads_json_path, "sidecar uploads payload")
        if sidecar_uploads_json_path.exists()
        else _new_sidecar_uploads_payload(
            delete_uploaded_files_on_complete=delete_uploaded_files_on_complete,
            file_expiration_policy=file_expiration_policy,
        )
    )
    _assert_no_unknown_submission(sidecar_submissions_json_path, sidecar_latest_json_path)
    _assert_no_unknown_upload(uploads_payload)

    schema = load_schema_json(schema_file, root=root)
    validate_model_options(
        model=structural_model,
        max_output_tokens=SIDECAR_MAX_OUTPUT_TOKENS,
        prompt_cache_retention=prompt_cache_retention,
        prompt_cache_ttl=prompt_cache_ttl,
        reasoning_mode=reasoning_mode,
        text_format="json_schema",
    )

    response_json: dict[str, Any] | None = None
    request_duration_ms: int | None = None
    poll_duration_ms: int | None = None
    response_retry_count: int | None = None
    attempt_upload_count: int | None = None
    attempt_uploaded_bytes: int | None = None
    created_response_this_call = False
    if sidecar_latest_json_path.exists():
        response_json = load_json(sidecar_latest_json_path, "latest sidecar response")

    retry_terminal_reason = None
    if response_json is None or str(response_json.get("status")) in TERMINAL_RESPONSE_STATUSES:
        if response_json is not None and str(response_json.get("status")) == "incomplete":
            incomplete_details = response_json.get("incomplete_details")
            if isinstance(incomplete_details, dict) and incomplete_details.get("reason") == "max_output_tokens":
                previous_request = (
                    load_json(sidecar_request_json_path, "sidecar request")
                    if sidecar_request_json_path.exists()
                    else {}
                )
                previous_budget = int(previous_request.get("max_output_tokens") or 0)
                if previous_budget < SIDECAR_MAX_OUTPUT_TOKENS:
                    retry_terminal_reason = "legacy_output_token_budget"
        if response_json is not None and str(response_json.get("status")) == "failed":
            error_code = _sidecar_error_code(response_json)
            if (
                error_code in SIDECAR_RETRYABLE_ERROR_CODES
                and _sidecar_retryable_attempt_count(sidecar_attempts_json_path)
                < SIDECAR_MAX_RETRYABLE_TERMINAL_RETRIES
            ):
                retry_terminal_reason = f"retryable_terminal_{error_code}"
        if response_json is None or retry_terminal_reason is not None:
            if response_json is not None and retry_terminal_reason is not None:
                _upsert_sidecar_attempt(
                    sidecar_attempts_json_path,
                    response_json,
                    retry_reason=retry_terminal_reason,
                    retry_count=_sidecar_response_retry_count(
                        response_id=response_json.get("id"),
                        sidecar_submissions_json_path=sidecar_submissions_json_path,
                        sidecar_attempts_json_path=sidecar_attempts_json_path,
                    ),
                )
            upload_start_index = len(uploads_payload.get("files", []))
            markdown_upload = _upload_sidecar_source(
                root=root,
                client=client,
                uploads_payload=uploads_payload,
                uploads_path=sidecar_uploads_json_path,
                source_path=response_markdown_path,
                attachment_role="Sidecar Source Markdown Artifact",
                purpose=file_purpose,
                file_expiration_policy=file_expiration_policy,
            )
            response_json_upload = None
            if raw_recovery_reason is not None:
                uploads_payload["raw_recovery_reason"] = raw_recovery_reason
                write_json(sidecar_uploads_json_path, uploads_payload)
                response_json_upload = _upload_sidecar_source(
                    root=root,
                    client=client,
                    uploads_payload=uploads_payload,
                    uploads_path=sidecar_uploads_json_path,
                    source_path=response_json_path,
                    attachment_role="Sidecar Source Response JSON Recovery",
                    purpose=file_purpose,
                    file_expiration_policy=file_expiration_policy,
                )
            attempt_uploads = uploads_payload.get("files", [])[upload_start_index:]
            attempt_upload_count = len(attempt_uploads)
            attempt_uploaded_bytes = sum(
                int(item.get("bytes", 0))
                for item in attempt_uploads
                if isinstance(item, dict)
            )

            content, _role_blocks = attachments.build_request_input_content(
                task_text=_task_prompt(),
                input_manifest_file_id=None,
                role_to_file_ids={},
            )
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": "Attachment role: Clean Source Artifact. The next file is the assistant output source of truth for sidecar extraction.",
                    },
                    {"type": "input_file", "file_id": str(markdown_upload["id"])},
                ]
            )
            if response_json_upload is not None:
                content.extend(
                    [
                        {
                            "type": "input_text",
                            "text": "Explicit recovery input: raw response JSON. Use only to recover structure missing from the clean artifact.",
                        },
                        {"type": "input_file", "file_id": str(response_json_upload["id"])},
                    ]
                )

            reasoning: dict[str, Any] = {"effort": reasoning_effort}
            if reasoning_mode is not None:
                reasoning["mode"] = reasoning_mode
            payload: dict[str, Any] = {
                "model": structural_model,
                "instructions": COMMON_RUNNER_INSTRUCTIONS.strip() + "\n\n" + _shared_instructions(),
                "input": [{"role": "user", "content": content}],
                "background": True,
                "store": store,
                "truncation": "disabled",
                "reasoning": reasoning,
                "text": {
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": schema,
                        "strict": True,
                    }
                },
                "max_output_tokens": SIDECAR_MAX_OUTPUT_TOKENS,
                "metadata": {
                    "workflow_id": workflow_id,
                    "run_id": run_id,
                    "stage_id": stage_id,
                    "stage_number": str(stage_number),
                    "kind": "sidecar",
                },
                "prompt_cache_key": build_prompt_cache_key(
                    f"stable:v1:{workflow_id}:{RUNNER_VERSION}:{base_model_name(structural_model)}",
                    "structural_processing",
                ),
            }
            normalized_retention = normalize_prompt_cache_retention(prompt_cache_retention)
            if normalized_retention:
                payload["prompt_cache_retention"] = normalized_retention
            cache_options = {
                key: value
                for key, value in {
                    "mode": prompt_cache_mode,
                    "ttl": prompt_cache_ttl,
                }.items()
                if value is not None
            }
            if cache_options:
                payload["prompt_cache_options"] = cache_options
            if service_tier:
                payload["service_tier"] = service_tier
            if safety_identifier:
                payload["safety_identifier"] = safety_identifier
            write_json(sidecar_request_json_path, payload)
            request_sha256 = sha256_file(sidecar_request_json_path)
            submission_journal, submission_index = _begin_submission(
                sidecar_submissions_json_path,
                request_path=sidecar_request_json_path,
                request_sha256=request_sha256,
            )
            response_retry_count = max(
                submission_index,
                len(_sidecar_attempt_records(sidecar_attempts_json_path)),
            )
            submission_attempt_id = str(
                submission_journal["attempts"][submission_index]["attempt_id"]
            )
            _upsert_sidecar_attempt(
                sidecar_attempts_json_path,
                None,
                retry_reason=None,
                retry_count=response_retry_count,
                submission_attempt_id=submission_attempt_id,
                model=structural_model,
                status="submitting",
                upload_count=attempt_upload_count,
                uploaded_bytes=attempt_uploaded_bytes,
            )

            request_started = time.monotonic()
            try:
                response_json = client.create_response(payload)
            except Exception as exc:
                request_duration_ms = int((time.monotonic() - request_started) * 1000)
                known_rejected = getattr(exc, "outcome_certainty", None) == "known_rejected"
                failed_status = (
                    "known_rejected" if known_rejected else "submission_outcome_unknown"
                )
                _update_submission(
                    sidecar_submissions_json_path,
                    submission_journal,
                    submission_index,
                    status=failed_status,
                    request_wall_ms=request_duration_ms,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                _upsert_sidecar_attempt(
                    sidecar_attempts_json_path,
                    None,
                    retry_reason=None,
                    retry_count=response_retry_count,
                    submission_attempt_id=submission_attempt_id,
                    model=structural_model,
                    status=failed_status,
                    request_wall_ms=request_duration_ms,
                    upload_count=attempt_upload_count,
                    uploaded_bytes=attempt_uploaded_bytes,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                raise
            request_duration_ms = int((time.monotonic() - request_started) * 1000)
            response_id = response_json.get("id") if isinstance(response_json, dict) else None
            if not isinstance(response_id, str) or not response_id:
                _update_submission(
                    sidecar_submissions_json_path,
                    submission_journal,
                    submission_index,
                    status="submission_outcome_unknown",
                    request_wall_ms=request_duration_ms,
                    error_type="InvalidResponse",
                    error="Sidecar create response did not include a non-empty id.",
                )
                _upsert_sidecar_attempt(
                    sidecar_attempts_json_path,
                    response_json if isinstance(response_json, dict) else None,
                    retry_reason=None,
                    retry_count=response_retry_count,
                    submission_attempt_id=submission_attempt_id,
                    model=structural_model,
                    status="submission_outcome_unknown",
                    request_wall_ms=request_duration_ms,
                    upload_count=attempt_upload_count,
                    uploaded_bytes=attempt_uploaded_bytes,
                    error_type="InvalidResponse",
                    error="Sidecar create response did not include a non-empty id.",
                )
                raise SystemExit("Sidecar create response did not include a non-empty id; outcome is unknown.")
            write_json(sidecar_latest_json_path, response_json)
            _update_submission(
                sidecar_submissions_json_path,
                submission_journal,
                submission_index,
                status="submitted",
                response_id=response_id,
                request_wall_ms=request_duration_ms,
            )
            _upsert_sidecar_attempt(
                sidecar_attempts_json_path,
                response_json,
                retry_reason=None,
                retry_count=response_retry_count,
                submission_attempt_id=submission_attempt_id,
                request_wall_ms=request_duration_ms,
                upload_count=attempt_upload_count,
                uploaded_bytes=attempt_uploaded_bytes,
            )
            created_response_this_call = True

    if str(response_json.get("status")) not in {"completed", "failed", "cancelled", "incomplete"}:
        poll_started = time.monotonic()
        response_json = client.wait_for_terminal_response(
            str(response_json["id"]),
            poll_interval=SIDECAR_POLL_INTERVAL_SECONDS,
            max_wait_seconds=SIDECAR_MAX_WAIT_SECONDS,
            checkpoint_callback=lambda polled: write_json(sidecar_latest_json_path, polled),
        )
        poll_duration_ms = int((time.monotonic() - poll_started) * 1000)
    elif created_response_this_call:
        poll_duration_ms = 0
    write_json(sidecar_latest_json_path, response_json)
    write_json(sidecar_raw_json_path, response_json)
    _upsert_sidecar_attempt(
        sidecar_attempts_json_path,
        response_json,
        retry_reason="terminal_attempt",
        retry_count=(
            response_retry_count
            if created_response_this_call
            else _sidecar_response_retry_count(
                response_id=response_json.get("id"),
                sidecar_submissions_json_path=sidecar_submissions_json_path,
                sidecar_attempts_json_path=sidecar_attempts_json_path,
            )
        ),
        duration_ms=(
            request_duration_ms + poll_duration_ms
            if request_duration_ms is not None and poll_duration_ms is not None
            else None
        ),
        request_wall_ms=request_duration_ms,
        poll_wall_ms=poll_duration_ms,
        upload_count=attempt_upload_count,
        uploaded_bytes=attempt_uploaded_bytes,
    )
    cleaned_uploads = False
    try:
        structured_output = extract_structured_output(response_json, "json_schema")
        if structured_output is None:
            raise SystemExit(_sidecar_extraction_failure_message(response_json))
        if delete_uploaded_files_on_complete:
            uploads_payload = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
                journal_callback=lambda payload: write_json(sidecar_uploads_json_path, payload),
            )
            write_json(sidecar_uploads_json_path, uploads_payload)
            cleaned_uploads = True
        write_response_pair(
            root=root,
            markdown_path=sidecar_response_markdown_path,
            json_path=sidecar_response_json_path,
            title="Responses Runner V2 Sidecar Response",
            workflow_id=workflow_id,
            run_id=run_id,
            stage_id=stage_id,
            stage_number=stage_number,
            response_json=response_json,
            requested_text_format="json_schema",
            structured_output=structured_output,
            uploads_payload=uploads_payload,
        )
        write_json(structured_output_path, structured_output)
    finally:
        if delete_uploaded_files_on_complete and not cleaned_uploads:
            uploads_payload = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
                journal_callback=lambda payload: write_json(sidecar_uploads_json_path, payload),
            )
            write_json(sidecar_uploads_json_path, uploads_payload)
    return {
        "response_json": response_json,
        "structured_output": structured_output,
        "sidecar_response_json_path": sidecar_response_json_path,
        "sidecar_response_markdown_path": sidecar_response_markdown_path,
        "structured_output_path": structured_output_path,
        "uploads_payload": uploads_payload,
    }


def default_structural_model() -> str:
    return DEFAULT_STRUCTURAL_MODEL
