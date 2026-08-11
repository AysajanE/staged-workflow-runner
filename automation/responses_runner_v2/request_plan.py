from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


REQUEST_PLAN_SCHEMA_VERSION = "responses_runner_v2.request_plan.v1"
DEFAULT_SAFETY_MARGIN_TOKENS = 4096
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


def symbolic_file_handle(sha256: str) -> str:
    """Return the same deterministic handle in dry and live request planning."""

    digest = sha256.casefold()
    if _SHA256_RE.fullmatch(digest) is None:
        raise ValueError("symbolic file handles require a full hexadecimal sha256")
    return f"file_sha256_{digest}"


def conservative_token_estimate(byte_count: int) -> int:
    """Use one token per byte as a deliberately conservative pre-upload bound."""

    if byte_count < 0:
        raise ValueError("byte_count must be non-negative")
    return byte_count


def normalized_request_sha256(payload: Mapping[str, Any]) -> str:
    """Hash a symbolic request with stable JSON normalization."""

    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request_file_items(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    messages = payload.get("input")
    if not isinstance(messages, list):
        raise ValueError("complete request payload requires an input message list")
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("request input messages must be objects")
        content = message.get("content")
        if not isinstance(content, list):
            raise ValueError("request input message content must be a list")
        for item in content:
            if isinstance(item, dict) and item.get("type") == "input_file":
                file_id = item.get("file_id")
                if not isinstance(file_id, str) or not file_id:
                    raise ValueError("request input_file items require non-empty file_id values")
                items.append(item)
    return items


def materialize_request_payload(
    symbolic_request_payload: Mapping[str, Any],
    provider_file_ids: Sequence[str],
) -> dict[str, Any]:
    """Substitute provider file IDs positionally into one symbolic complete request."""

    payload = copy.deepcopy(dict(symbolic_request_payload))
    file_items = _request_file_items(payload)
    if len(file_items) != len(provider_file_ids):
        raise ValueError(
            "provider file ID count does not match symbolic complete-request attachments"
        )
    for item, provider_file_id in zip(file_items, provider_file_ids, strict=True):
        if not isinstance(provider_file_id, str) or not provider_file_id:
            raise ValueError("provider file IDs must be non-empty strings")
        item["file_id"] = provider_file_id
    return payload


def normalize_materialized_request(
    materialized_request_payload: Mapping[str, Any],
    symbolic_file_ids: Sequence[str],
) -> dict[str, Any]:
    """Replace provider IDs with the plan's positional symbolic IDs."""

    payload = copy.deepcopy(dict(materialized_request_payload))
    file_items = _request_file_items(payload)
    if len(file_items) != len(symbolic_file_ids):
        raise ValueError(
            "materialized request attachment count does not match the symbolic plan"
        )
    for item, symbolic_file_id in zip(file_items, symbolic_file_ids, strict=True):
        if not symbolic_file_id.startswith("file_sha256_") or _SHA256_RE.fullmatch(
            symbolic_file_id.removeprefix("file_sha256_")
        ) is None:
            raise ValueError("normalized request requires symbolic sha256 file IDs")
        item["file_id"] = symbolic_file_id
    return payload


def verify_materialized_request(
    plan: Mapping[str, Any],
    materialized_request_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail if live request structure differs from the symbolic request beyond file IDs."""

    symbolic_request = plan.get("symbolic_request_payload")
    expected_hash = plan.get("normalized_request_sha256")
    files = plan.get("files")
    if not isinstance(symbolic_request, dict) or not isinstance(expected_hash, str):
        raise ValueError("request plan is missing its symbolic complete request or normalized hash")
    if not isinstance(files, list):
        raise ValueError("request plan files must be a list")
    symbolic_file_ids = [str(item["symbolic_file_id"]) for item in files]
    normalized = normalize_materialized_request(
        materialized_request_payload,
        symbolic_file_ids,
    )
    if normalized != symbolic_request:
        raise ValueError(
            "live request differs from the symbolic complete request beyond provider file IDs"
        )
    if normalized_request_sha256(normalized) != expected_hash:
        raise ValueError("symbolic complete-request normalized hash mismatch")
    return normalized


def build_request_plan(
    *,
    text_parts: Sequence[str],
    files: Sequence[Mapping[str, Any]],
    context_window: int,
    max_output_tokens: int,
    safety_margin_tokens: int = DEFAULT_SAFETY_MARGIN_TOKENS,
    data_handling_policy: Mapping[str, Any] | None = None,
    request_store: bool | None = None,
    file_purpose: str | None = None,
    delete_uploaded_files_on_complete: bool | None = None,
    symbolic_request_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic request plan without remote IDs, clocks, or filesystem I/O."""

    if context_window <= 0 or max_output_tokens < 0 or safety_margin_tokens < 0:
        raise ValueError("context_window must be positive; other limits must be non-negative")
    planned_files: list[dict[str, Any]] = []
    hash_roles: dict[str, set[str]] = {}
    attachment_bytes = 0
    for index, descriptor in enumerate(files, start=1):
        digest = str(descriptor.get("sha256") or "").casefold()
        handle = symbolic_file_handle(digest)
        size = descriptor.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ValueError(f"files[{index - 1}].bytes must be a non-negative integer")
        role = str(descriptor.get("authority") or descriptor.get("role") or "unspecified")
        path = str(descriptor.get("path") or descriptor.get("display_name") or f"file_{index:03d}")
        attachment_bytes += size
        hash_roles.setdefault(digest, set()).add(role)
        planned_files.append(
            {
                "symbolic_file_id": handle,
                "path": path,
                "sha256": digest,
                "bytes": size,
                "authority": role,
            }
        )

    text_bytes = sum(len(part.encode("utf-8")) for part in text_parts)
    model_facing_bytes = text_bytes + attachment_bytes
    estimated_input_tokens = conservative_token_estimate(model_facing_bytes)
    required_context_tokens = estimated_input_tokens + max_output_tokens + safety_margin_tokens
    duplicate_hashes = [
        {"sha256": digest, "authorities": sorted(roles)}
        for digest, roles in sorted(hash_roles.items())
        if len(roles) > 1
    ]
    data_handling: dict[str, Any] | None = None
    if data_handling_policy is not None:
        if request_store is None or file_purpose is None or delete_uploaded_files_on_complete is None:
            raise ValueError("data-handling planning requires store, file purpose, and deletion settings")
        if request_store and not bool(data_handling_policy.get("api_store_allowed")):
            raise ValueError("assurance profile does not allow API store=true")
        required_purpose = str(data_handling_policy.get("file_purpose") or "")
        if file_purpose != required_purpose:
            raise ValueError(
                f"assurance profile requires file purpose {required_purpose!r}, got {file_purpose!r}"
            )
        if (
            bool(data_handling_policy.get("delete_uploaded_files_on_complete"))
            and not delete_uploaded_files_on_complete
        ):
            raise ValueError("assurance profile requires uploaded-file deletion on completion")
        data_handling = {
            "status": "passed",
            "sensitivity": str(data_handling_policy.get("sensitivity") or "unspecified"),
            "retain_raw_request": bool(data_handling_policy.get("retain_raw_request")),
            "retain_raw_response": bool(data_handling_policy.get("retain_raw_response")),
            "retain_reviewer_output": bool(data_handling_policy.get("retain_reviewer_output")),
            "retain_reasoning_summary": bool(data_handling_policy.get("retain_reasoning_summary")),
            "request_store": request_store,
            "file_purpose": file_purpose,
            "delete_uploaded_files_on_complete": delete_uploaded_files_on_complete,
        }

    plan = {
        "schema_version": REQUEST_PLAN_SCHEMA_VERSION,
        "files": planned_files,
        "duplicate_content_across_authorities": duplicate_hashes,
        "estimate": {
            "text_bytes": text_bytes,
            "attachment_bytes": attachment_bytes,
            "model_facing_bytes": model_facing_bytes,
            "estimated_input_tokens": estimated_input_tokens,
            "max_output_tokens": max_output_tokens,
            "safety_margin_tokens": safety_margin_tokens,
            "required_context_tokens": required_context_tokens,
            "context_window": context_window,
            "fits_context": required_context_tokens <= context_window,
            "method": "one_token_per_utf8_byte",
        },
    }
    if data_handling is not None:
        plan["data_handling"] = data_handling
    if symbolic_request_payload is not None:
        symbolic_request = copy.deepcopy(dict(symbolic_request_payload))
        request_file_ids = [item["file_id"] for item in _request_file_items(symbolic_request)]
        planned_file_ids = [item["symbolic_file_id"] for item in planned_files]
        if request_file_ids != planned_file_ids:
            raise ValueError(
                "symbolic complete-request file order does not match planned attachment order"
            )
        plan["symbolic_request_payload"] = symbolic_request
        plan["normalized_request_sha256"] = normalized_request_sha256(symbolic_request)
    return plan
