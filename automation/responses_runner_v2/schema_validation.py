from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from .contracts import schema_dir


class ContractValidationError(SystemExit):
    """Raised when a persisted or operator-supplied contract is invalid."""


@lru_cache(maxsize=32)
def load_contract_schema(filename: str) -> dict[str, Any]:
    path = schema_dir() / filename
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractValidationError(f"Cannot load contract schema {path}: {exc}") from exc
    try:
        Draft202012Validator.check_schema(payload)
    except SchemaError as exc:
        raise ContractValidationError(f"Invalid Draft 2020-12 schema {path}: {exc.message}") from exc
    return payload


def validate_contract(payload: Any, filename: str, *, label: str) -> None:
    """Validate raw JSON before any coercion or dataclass construction."""

    validator = Draft202012Validator(load_contract_schema(filename))
    errors = sorted(validator.iter_errors(payload), key=lambda error: list(error.absolute_path))
    if not errors:
        return
    rendered: list[str] = []
    for error in errors[:20]:
        location = "$"
        for part in error.absolute_path:
            location += f"[{part}]" if isinstance(part, int) else f".{part}"
        rendered.append(f"{location}: {error.message}")
    if len(errors) > len(rendered):
        rendered.append(f"... and {len(errors) - len(rendered)} more error(s)")
    raise ContractValidationError(f"Invalid {label}: " + "; ".join(rendered))


def workflow_schema_filename(schema_version: object) -> str:
    if schema_version == "responses_runner_v2.workflow_manifest.v1":
        return "workflow_manifest.schema.json"
    if schema_version == "responses_runner_v2.workflow_manifest.v2":
        return "workflow_manifest.v2.schema.json"
    raise ContractValidationError(
        f"Unsupported workflow schema_version: {schema_version!r}. "
        "Use a documented v1 compatibility path or a v2 workflow."
    )


def persisted_schema_filename(kind: str, schema_version: object) -> str:
    supported = {
        ("run_manifest", "responses_runner_v2.run_manifest.v1"): "run_manifest.schema.json",
        ("run_manifest", "responses_runner_v2.run_manifest.v2"): "run_manifest.v2.schema.json",
        ("stage_checkpoint", "responses_runner_v2.stage_checkpoint.v1"): "stage_checkpoint.schema.json",
        ("stage_checkpoint", "responses_runner_v2.stage_checkpoint.v2"): "stage_checkpoint.v2.schema.json",
        ("input_manifest", "responses_runner_v2.input_manifest.v1"): "input_manifest.schema.json",
    }
    filename = supported.get((kind, schema_version))
    if filename is None:
        raise ContractValidationError(f"Unsupported {kind} schema_version: {schema_version!r}")
    return filename
