from __future__ import annotations

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
    build_prompt_cache_key,
    normalize_prompt_cache_retention,
    relpath,
    read_text,
    repo_root,
    validate_model_options,
)
from .pack_loader import load_schema_json


PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


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
    root = root or repo_root()
    schema = load_schema_json(schema_file, root=root)
    validate_model_options(
        model=structural_model,
        max_output_tokens=16000,
        prompt_cache_retention=prompt_cache_retention,
        text_format="json_schema",
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
        "background": False,
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
        "max_output_tokens": 16000,
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

    response_json = client.create_response(payload)
    if str(response_json.get("status")) not in {"completed", "failed", "cancelled", "incomplete"}:
        response_json = client.wait_for_terminal_response(
            str(response_json["id"]),
            poll_interval=2.0,
            max_wait_seconds=600.0,
        )
    cleaned_uploads = False
    try:
        structured_output = extract_structured_output(response_json, "json_schema")
        if structured_output is None:
            raise SystemExit("Sidecar extraction did not return structured output.")
        if delete_uploaded_files_on_complete:
            uploads_payload = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
            )
            cleaned_uploads = True
        sidecar_response_json_path.parent.mkdir(parents=True, exist_ok=True)
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
            __import__("json").dumps(structured_output, indent=2, ensure_ascii=False) + "\n",
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
