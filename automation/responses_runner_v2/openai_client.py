from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
from pathlib import Path
from collections.abc import Iterator
from typing import Any, Callable
from urllib import error, parse, request

from .contracts import (
    DEFAULT_API_BASE,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MAX_WAIT_SECONDS,
    DEFAULT_REQUEST_MAX_RETRIES,
    NONTERMINAL_RESPONSE_STATUSES,
    TERMINAL_RESPONSE_STATUSES,
    repo_root,
)


RETRYABLE_HTTP_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
SAFE_RETRY_METHODS = {"GET", "HEAD", "OPTIONS"}

OUTCOME_NOT_APPLICABLE = "not_applicable"
OUTCOME_KNOWN_REJECTED = "known_rejected"
OUTCOME_AMBIGUOUS = "ambiguous"
MULTIPART_CHUNK_BYTES = 1024 * 1024


# Fields accepted by POST /responses/input_tokens. Delivery options on the
# create payload (background, store, max_output_tokens, metadata, prompt cache
# routing, service_tier, safety_identifier) are rejected with HTTP 400.
INPUT_TOKEN_COUNT_FIELDS = (
    "model",
    "input",
    "instructions",
    "reasoning",
    "text",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "truncation",
    "previous_response_id",
    "conversation",
)


class ApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        outcome_certainty: str = OUTCOME_NOT_APPLICABLE,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.outcome_certainty = outcome_certainty

    @property
    def outcome_unknown(self) -> bool:
        return self.outcome_certainty == OUTCOME_AMBIGUOUS


def load_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            values[key] = value
    return values


def resolve_api_key(root: Path | None = None) -> tuple[str, str]:
    root = root or repo_root()
    env_value = os.environ.get("OPENAI_API_KEY")
    if env_value:
        return env_value, "environment"
    dotenv_value = load_dotenv(root / ".env").get("OPENAI_API_KEY")
    if dotenv_value:
        return dotenv_value, ".env"
    raise SystemExit("OPENAI_API_KEY is not set in the environment and was not found in .env.")


POLL_TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
MAX_CONSECUTIVE_POLL_FAILURES = 30


def _retry_delay_seconds(attempt: int) -> float:
    return float(min(2 ** (attempt - 1), 30))


def _decode_http_error(exc: error.HTTPError) -> str:
    body = exc.read()
    if not body:
        return f"{exc.code} {exc.reason}"
    text = body.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return f"{exc.code} {exc.reason}: {text}"
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and isinstance(err.get("message"), str):
            return f"{exc.code} {exc.reason}: {err['message']}"
    return f"{exc.code} {exc.reason}: {text}"


class _MultipartBody:
    """One-shot iterable body that never copies the uploaded file into memory."""

    def __init__(self, *, prefix: bytes, file_path: Path, suffix: bytes) -> None:
        self._prefix = prefix
        self._file_path = file_path
        self._suffix = suffix
        self.content_length = len(prefix) + file_path.stat().st_size + len(suffix)

    def __iter__(self) -> Iterator[bytes]:
        yield self._prefix
        with self._file_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(MULTIPART_CHUNK_BYTES), b""):
                yield chunk
        yield self._suffix


def _encode_multipart(
    fields: dict[str, str],
    file_field_name: str,
    file_path: Path,
) -> tuple[str, _MultipartBody]:
    boundary = f"----ResponsesRunnerV2Boundary{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode("utf-8")
        )
    mime_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    chunks.append(f"--{boundary}\r\n".encode("utf-8"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field_name}"; filename="{file_path.name}"\r\n'
            f"Content-Type: {mime_type}\r\n\r\n"
        ).encode("utf-8")
    )
    prefix = b"".join(chunks)
    suffix = b"\r\n" + f"--{boundary}--\r\n".encode("utf-8")
    return boundary, _MultipartBody(prefix=prefix, file_path=file_path, suffix=suffix)


class OpenAIClient:
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str = DEFAULT_API_BASE,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        request_max_retries: int = DEFAULT_REQUEST_MAX_RETRIES,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.request_max_retries = request_max_retries

    @classmethod
    def from_env(cls, *, root: Path | None = None, api_base: str = DEFAULT_API_BASE) -> "OpenAIClient":
        api_key, _source = resolve_api_key(root)
        return cls(api_key=api_key, api_base=api_base)

    def _raw_request(
        self,
        req: request.Request,
        *,
        max_retries: int | None = None,
    ) -> bytes:
        method = req.get_method().upper()
        retries_are_safe = method in SAFE_RETRY_METHODS
        attempts = max_retries if max_retries is not None else (
            self.request_max_retries if retries_are_safe else 1
        )
        attempts = max(1, attempts)
        for attempt in range(1, attempts + 1):
            try:
                with request.urlopen(req, timeout=self.timeout_seconds) as response:
                    return response.read()
            except error.HTTPError as exc:
                message = _decode_http_error(exc)
                if retries_are_safe and exc.code in RETRYABLE_HTTP_STATUS_CODES and attempt < attempts:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                certainty = OUTCOME_NOT_APPLICABLE
                if not retries_are_safe:
                    certainty = (
                        OUTCOME_AMBIGUOUS
                        if exc.code == 408 or exc.code >= 500
                        else OUTCOME_KNOWN_REJECTED
                    )
                raise ApiError(
                    message,
                    status_code=exc.code,
                    outcome_certainty=certainty,
                ) from exc
            except error.URLError as exc:
                if retries_are_safe and attempt < attempts:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                certainty = OUTCOME_NOT_APPLICABLE if retries_are_safe else OUTCOME_AMBIGUOUS
                raise ApiError(
                    f"Transport error: {exc.reason}",
                    outcome_certainty=certainty,
                ) from exc
            except (TimeoutError, ConnectionError) as exc:
                if retries_are_safe and attempt < attempts:
                    time.sleep(_retry_delay_seconds(attempt))
                    continue
                certainty = OUTCOME_NOT_APPLICABLE if retries_are_safe else OUTCOME_AMBIGUOUS
                raise ApiError(
                    f"Transport error: {exc}",
                    outcome_certainty=certainty,
                ) from exc
        raise AssertionError("unreachable")

    def json_request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        max_retries: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.api_base}{path}"
        body = None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=body, headers=headers, method=method)
        raw = self._raw_request(req, max_retries=max_retries)
        try:
            result = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            certainty = (
                OUTCOME_NOT_APPLICABLE
                if method.upper() in SAFE_RETRY_METHODS
                else OUTCOME_AMBIGUOUS
            )
            raise ApiError(
                f"Expected JSON response from {url}.",
                outcome_certainty=certainty,
            ) from exc
        if not isinstance(result, dict):
            certainty = (
                OUTCOME_NOT_APPLICABLE
                if method.upper() in SAFE_RETRY_METHODS
                else OUTCOME_AMBIGUOUS
            )
            raise ApiError(
                f"Expected JSON object response from {url}.",
                outcome_certainty=certainty,
            )
        return result

    def create_response(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.json_request("POST", "/responses", payload=payload, max_retries=1)

    def cancel_response(self, response_id: str) -> dict[str, Any]:
        quoted = parse.quote(response_id, safe="")
        return self.json_request("POST", f"/responses/{quoted}/cancel", max_retries=1)

    def retrieve_response(self, response_id: str) -> dict[str, Any]:
        quoted = parse.quote(response_id, safe="")
        return self.json_request("GET", f"/responses/{quoted}")

    def count_input_tokens_once(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Count input tokens for a create-response payload.

        The count endpoint accepts only the request-shaping subset of the
        create payload, so the payload is projected onto INPUT_TOKEN_COUNT_FIELDS
        before it is sent.
        """

        count_payload = {
            key: payload[key] for key in INPUT_TOKEN_COUNT_FIELDS if key in payload
        }
        return self.json_request(
            "POST", "/responses/input_tokens", payload=count_payload, max_retries=1
        )

    def delete_file(self, file_id: str) -> dict[str, Any]:
        quoted = parse.quote(file_id, safe="")
        return self.json_request("DELETE", f"/files/{quoted}")

    def upload_file(
        self,
        file_path: Path,
        *,
        purpose: str,
        file_expiration_policy: dict[str, Any] | None,
    ) -> dict[str, Any]:
        fields = {"purpose": purpose}
        if file_expiration_policy:
            if file_expiration_policy.get("anchor"):
                fields["expires_after[anchor]"] = str(file_expiration_policy["anchor"])
            if file_expiration_policy.get("seconds") is not None:
                fields["expires_after[seconds]"] = str(file_expiration_policy["seconds"])
        boundary, body = _encode_multipart(fields, "file", file_path)
        req = request.Request(
            f"{self.api_base}/files",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(body.content_length),
            },
            method="POST",
        )
        raw = self._raw_request(req, max_retries=1)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ApiError(
                "Expected JSON object response from file upload.",
                outcome_certainty=OUTCOME_AMBIGUOUS,
            ) from exc
        if not isinstance(payload, dict):
            raise ApiError(
                "Expected JSON object response from file upload.",
                outcome_certainty=OUTCOME_AMBIGUOUS,
            )
        return payload

    def wait_for_terminal_response(
        self,
        response_id: str,
        *,
        poll_interval: float,
        max_wait_seconds: float | None = DEFAULT_MAX_WAIT_SECONDS,
        checkpoint_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        start = time.monotonic()
        consecutive_failures = 0
        while True:
            try:
                response_json = self.retrieve_response(response_id)
            except ApiError as exc:
                # A background response keeps running through a polling outage;
                # keep waiting for transient failures instead of abandoning it.
                consecutive_failures += 1
                transient = exc.status_code is None or exc.status_code in POLL_TRANSIENT_STATUS_CODES
                elapsed = time.monotonic() - start
                if (
                    not transient
                    or consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES
                    or (max_wait_seconds is not None and elapsed >= max_wait_seconds)
                ):
                    raise
                time.sleep(poll_interval)
                continue
            consecutive_failures = 0
            if checkpoint_callback is not None:
                checkpoint_callback(response_json)
            status = str(response_json.get("status", "unknown"))
            if status in TERMINAL_RESPONSE_STATUSES:
                return response_json
            if status not in NONTERMINAL_RESPONSE_STATUSES:
                return response_json
            elapsed = time.monotonic() - start
            if max_wait_seconds is not None and elapsed >= max_wait_seconds:
                raise ApiError(
                    f"Response {response_id} did not reach a terminal state within {max_wait_seconds:.1f}s "
                    f"(last_status={status})."
                )
            time.sleep(poll_interval)
