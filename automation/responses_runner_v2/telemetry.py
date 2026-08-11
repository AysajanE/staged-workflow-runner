from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .contracts import load_json, relpath, resolve_under_root, sha256_file


USAGE_SCHEMA_VERSION = "responses_runner_v2.normalized_usage.v1"
USAGE_REPORT_SCHEMA_VERSION = "responses_runner_v2.usage_report.v1"
USAGE_COUNTER_FIELDS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_input_tokens",
    "cache_write_input_tokens",
    "reasoning_output_tokens",
)


def _counter(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return 0


def _nullable_counter(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and value >= 0:
        return int(value)
    return None


def _nullable_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def normalize_response_usage(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize Responses usage keys, retaining Chat-era fallbacks for old artifacts."""

    usage = _mapping(payload.get("usage")) if "usage" in payload else payload
    if usage.get("schema_version") == USAGE_SCHEMA_VERSION:
        return {
            "schema_version": USAGE_SCHEMA_VERSION,
            **{field: _counter(usage.get(field)) for field in USAGE_COUNTER_FIELDS},
        }
    input_details = _mapping(
        usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
    )
    output_details = _mapping(
        usage.get("output_tokens_details") or usage.get("completion_tokens_details")
    )
    input_tokens = _counter(usage.get("input_tokens", usage.get("prompt_tokens")))
    output_tokens = _counter(usage.get("output_tokens", usage.get("completion_tokens")))
    explicit_total = usage.get("total_tokens")
    total_tokens = (
        _counter(explicit_total) if explicit_total is not None else input_tokens + output_tokens
    )
    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_input_tokens": _counter(input_details.get("cached_tokens")),
        "cache_write_input_tokens": _counter(input_details.get("cache_write_tokens")),
        "reasoning_output_tokens": _counter(output_details.get("reasoning_tokens")),
    }


def _unknown_usage() -> dict[str, Any]:
    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        **{field: None for field in USAGE_COUNTER_FIELDS},
    }


def _normalize_attempt_usage(value: object) -> dict[str, Any]:
    """Preserve explicitly unavailable usage instead of reporting invented zeroes."""

    if value is None:
        return _unknown_usage()
    usage = _mapping(value)
    if not usage:
        # An empty API usage object reports no counters; it is not evidence of zero usage.
        return _unknown_usage()
    if usage.get("schema_version") == USAGE_SCHEMA_VERSION and any(
        usage.get(field) is None for field in USAGE_COUNTER_FIELDS
    ):
        return {
            "schema_version": USAGE_SCHEMA_VERSION,
            **{field: _nullable_counter(usage.get(field)) for field in USAGE_COUNTER_FIELDS},
        }
    return normalize_response_usage(usage)


def aggregate_usage(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Sum usage only when every supplied attempt exposes the corresponding counter."""

    totals = {field: 0 for field in USAGE_COUNTER_FIELDS}
    unavailable = {field: False for field in USAGE_COUNTER_FIELDS}
    attempt_count = 0
    for record in records:
        source: object = record.get("usage") if "usage" in record else record
        normalized = _normalize_attempt_usage(source)
        attempt_count += 1
        for field in USAGE_COUNTER_FIELDS:
            value = normalized.get(field)
            if value is None:
                unavailable[field] = True
            else:
                totals[field] += _counter(value)
    return {
        "attempt_count": attempt_count,
        **{
            field: None if unavailable[field] else totals[field]
            for field in USAGE_COUNTER_FIELDS
        },
    }


def build_usage_report(attempts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """Build lane totals only from the durable attempts actually supplied."""

    normalized_attempts: list[dict[str, Any]] = []
    by_lane_records: dict[str, list[dict[str, Any]]] = {}
    for index, attempt in enumerate(attempts, start=1):
        lane = str(attempt.get("lane") or "primary")
        uploaded_files = _nullable_counter(
            attempt.get("uploaded_files", attempt.get("upload_count"))
        )
        normalized = {
            "attempt_id": str(attempt.get("attempt_id") or f"attempt_{index:03d}"),
            "lane": lane,
            "model": _nullable_text(attempt.get("model")),
            "status": _nullable_text(attempt.get("status")),
            "duration_ms": _nullable_counter(attempt.get("duration_ms")),
            "request_wall_ms": _nullable_counter(attempt.get("request_wall_ms")),
            "poll_wall_ms": _nullable_counter(attempt.get("poll_wall_ms")),
            "retry_count": _nullable_counter(attempt.get("retry_count")),
            "upload_count": uploaded_files,
            "uploaded_files": uploaded_files,
            "uploaded_bytes": _nullable_counter(attempt.get("uploaded_bytes")),
            "usage": _normalize_attempt_usage(attempt.get("usage")),
        }
        normalized_attempts.append(normalized)
        by_lane_records.setdefault(lane, []).append(normalized)
    return {
        "schema_version": USAGE_REPORT_SCHEMA_VERSION,
        "attempts": normalized_attempts,
        "by_lane": {
            lane: aggregate_usage(records) for lane, records in sorted(by_lane_records.items())
        },
        "totals": aggregate_usage(normalized_attempts),
    }


def write_supervisor_usage_report(
    *,
    root: Path,
    session_ref: str | Path,
) -> dict[str, Any]:
    """Aggregate durable reviewer attempts without changing run-level usage totals."""

    from . import supervisor_artifacts

    session_path = supervisor_artifacts.session_dir(root, session_ref)
    session = supervisor_artifacts.load_session(root, session_path)
    attempts: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    for invocation in session.get("review_agent_invocations", []):
        if not isinstance(invocation, Mapping):
            continue
        path_value = invocation.get("usage_attempt_path")
        if not isinstance(path_value, str) or not path_value:
            continue
        path = resolve_under_root(root, path_value, must_exist=True)
        if not path.is_relative_to(session_path):
            raise SystemExit(f"Supervisor usage attempt is outside its recorded session: {path}")
        if path in seen_paths:
            raise SystemExit(f"Supervisor usage attempt is recorded more than once: {path}")
        seen_paths.add(path)
        expected_sha256 = invocation.get("usage_attempt_sha256")
        if not isinstance(expected_sha256, str) or sha256_file(path) != expected_sha256:
            raise SystemExit(f"Supervisor usage attempt hash mismatch: {path}")
        payload = load_json(path, "reviewer usage attempt")
        if payload.get("lane") != "reviewer":
            raise SystemExit(f"Supervisor usage attempt has non-reviewer lane: {path}")
        if payload.get("attempt_id") != invocation.get("command_id"):
            raise SystemExit(f"Supervisor usage attempt command identity mismatch: {path}")
        attempts.append(payload)
    report = build_usage_report(attempts)
    report_path = session_path / "reviewer_usage_report.json"
    supervisor_artifacts.write_json_artifact(
        root,
        report_path,
        report,
        schema_filename="usage_report.schema.json",
        label="supervisor reviewer usage report",
    )
    return {
        "supervisor_session_id": session["supervisor_session_id"],
        "usage_report_path": relpath(root, report_path),
        "attempt_count": len(attempts),
    }
