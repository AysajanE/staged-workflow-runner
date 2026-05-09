from __future__ import annotations

import json
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
    TERMINAL_RESPONSE_STATUSES,
    build_prompt_cache_key,
    load_json,
    normalize_prompt_cache_retention,
    relpath,
    read_text,
    repo_root,
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


def _record_sidecar_terminal_attempt(
    sidecar_attempts_json_path: Path,
    response_json: dict[str, Any],
    *,
    retry_reason: str,
) -> None:
    records = _sidecar_attempt_records(sidecar_attempts_json_path)
    records.append(
        {
            "response_id": response_json.get("id"),
            "status": response_json.get("status"),
            "error_code": _sidecar_error_code(response_json),
            "incomplete_details": response_json.get("incomplete_details"),
            "max_output_tokens": response_json.get("max_output_tokens"),
            "retry_reason": retry_reason,
        }
    )
    write_json(sidecar_attempts_json_path, {"attempts": records})


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
        else None
    )

    schema = load_schema_json(schema_file, root=root)
    validate_model_options(
        model=structural_model,
        max_output_tokens=SIDECAR_MAX_OUTPUT_TOKENS,
        prompt_cache_retention=prompt_cache_retention,
        text_format="json_schema",
    )

    response_json: dict[str, Any] | None = None
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
                _record_sidecar_terminal_attempt(
                    sidecar_attempts_json_path,
                    response_json,
                    retry_reason=retry_terminal_reason,
                )
            markdown_upload = client.upload_file(
                response_markdown_path,
                purpose="user_data",
                file_expiration_policy=file_expiration_policy,
            )
            response_json_upload = client.upload_file(
                response_json_path,
                purpose="user_data",
                file_expiration_policy=file_expiration_policy,
            )
            uploads_payload = {
                "delete_uploaded_files_on_complete": delete_uploaded_files_on_complete,
                "file_expiration_policy": file_expiration_policy,
                "files": [
                    {
                        "attachment_role": "Sidecar Source Markdown Artifact",
                        "display_name": response_markdown_path.name,
                        "source_path": relpath(root, response_markdown_path),
                        "upload_filename": response_markdown_path.name,
                        "wrapped_as_markdown": False,
                        "bytes": response_markdown_path.stat().st_size,
                        "file_id": str(markdown_upload["id"]),
                        "purpose": markdown_upload.get("purpose", "user_data"),
                        "created_at": markdown_upload.get("created_at"),
                        "expires_at": markdown_upload.get("expires_at"),
                    },
                    {
                        "attachment_role": "Sidecar Source Response JSON",
                        "display_name": response_json_path.name,
                        "source_path": relpath(root, response_json_path),
                        "upload_filename": response_json_path.name,
                        "wrapped_as_markdown": False,
                        "bytes": response_json_path.stat().st_size,
                        "file_id": str(response_json_upload["id"]),
                        "purpose": response_json_upload.get("purpose", "user_data"),
                        "created_at": response_json_upload.get("created_at"),
                        "expires_at": response_json_upload.get("expires_at"),
                    },
                ],
            }
            write_json(sidecar_uploads_json_path, uploads_payload)

            content, _role_blocks = attachments.build_request_input_content(
                task_text=_task_prompt(),
                input_manifest_file_id=None,
                role_to_file_ids={},
            )
            content.extend(
                [
                    {
                        "type": "input_text",
                        "text": "Attachment role: Source Markdown Artifact. The next attached file is the markdown source of truth for sidecar extraction.",
                    },
                    {"type": "input_file", "file_id": str(markdown_upload["id"])},
                    {
                        "type": "input_text",
                        "text": "Attachment role: Source Response JSON. The next attached file is raw response metadata for recovery only.",
                    },
                    {"type": "input_file", "file_id": str(response_json_upload["id"])},
                ]
            )

            payload: dict[str, Any] = {
                "model": structural_model,
                "instructions": COMMON_RUNNER_INSTRUCTIONS.strip() + "\n\n" + _shared_instructions(),
                "input": [{"role": "user", "content": content}],
                "background": True,
                "store": True,
                "truncation": "disabled",
                "reasoning": {"effort": reasoning_effort},
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
                "prompt_cache_key": build_prompt_cache_key(f"{workflow_id}:{run_id}:sidecar", stage_id),
            }
            normalized_retention = normalize_prompt_cache_retention(prompt_cache_retention)
            if normalized_retention:
                payload["prompt_cache_retention"] = normalized_retention
            if service_tier:
                payload["service_tier"] = service_tier
            if safety_identifier:
                payload["safety_identifier"] = safety_identifier
            write_json(sidecar_request_json_path, payload)

            response_json = client.create_response(payload)
            write_json(sidecar_latest_json_path, response_json)

    if str(response_json.get("status")) not in {"completed", "failed", "cancelled", "incomplete"}:
        response_json = client.wait_for_terminal_response(
            str(response_json["id"]),
            poll_interval=SIDECAR_POLL_INTERVAL_SECONDS,
            max_wait_seconds=SIDECAR_MAX_WAIT_SECONDS,
            checkpoint_callback=lambda polled: write_json(sidecar_latest_json_path, polled),
        )
    write_json(sidecar_latest_json_path, response_json)
    write_json(sidecar_raw_json_path, response_json)
    cleaned_uploads = False
    try:
        structured_output = extract_structured_output(response_json, "json_schema")
        if structured_output is None:
            raise SystemExit(_sidecar_extraction_failure_message(response_json))
        if delete_uploaded_files_on_complete:
            uploads_payload = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
            )
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
        structured_output_path.write_text(
            json.dumps(structured_output, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    finally:
        if delete_uploaded_files_on_complete and not cleaned_uploads:
            attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
            )
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
