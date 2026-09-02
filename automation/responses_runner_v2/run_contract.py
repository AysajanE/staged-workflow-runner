from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import attachments
from .contracts import (
    ASSURANCE_PROFILES,
    RuntimeOptions,
    RuntimeInputBinding,
    WorkflowDefinition,
    relpath,
    resolve_under_root,
    sha256_file,
    sha256_text,
    utc_now_iso,
    write_json,
)
from .schema_validation import validate_contract


RUN_CONTRACT_SCHEMA_VERSION = "responses_runner_v2.run_contract.v1"


def _asset_paths(workflow: WorkflowDefinition, runtime: RuntimeOptions, root: Path) -> list[tuple[str, Path]]:
    members: list[tuple[str, Path]] = [
        ("workflow_manifest", workflow.workflow_file),
        ("shared_instructions", workflow.shared_instructions_path),
    ]
    for stage in workflow.stages:
        prefix = f"stage:{stage.stage_id}"
        members.extend(
            [
                (f"{prefix}:task", stage.task_path),
                (f"{prefix}:input_manifest", stage.input_manifest_path),
            ]
        )
        for role, path in (
            (f"{prefix}:instructions", stage.stage_instructions_path),
            (f"{prefix}:tool_profile", stage.tool_profile_path),
            (f"{prefix}:output_schema", stage.output.schema_path),
        ):
            if path is not None:
                members.append((role, path))
        input_manifest = json.loads(stage.input_manifest_path.read_text(encoding="utf-8"))
        for field_name in (
            "primary_job_inputs",
            "reviewed_handoff_inputs",
            "attached_repository_files",
            "reference_context",
        ):
            for index, entry in enumerate(input_manifest.get(field_name, [])):
                path = resolve_under_root(root, str(entry["path"]), must_exist=True)
                role = f"{prefix}:input:{field_name}:{index}"
                exclude_globs = tuple(entry.get("exclude_globs", []))
                if entry.get("kind") == "workspace_inventory":
                    inventory = attachments._workspace_inventory(  # noqa: SLF001
                        root,
                        path,
                        exclude_globs=exclude_globs,
                    )
                    members.extend(
                        (f"{role}:{item['path']}", resolve_under_root(root, item["path"], must_exist=True))
                        for item in inventory["inventory_entries"]
                    )
                else:
                    members.extend(
                        _expand_member(
                            role,
                            path,
                            root=root,
                            exclude_globs=exclude_globs,
                        )
                    )
    for index, raw in enumerate(runtime.primary_job_inputs):
        path = resolve_under_root(root, raw, must_exist=True)
        members.extend(_expand_member(f"runtime:primary:{index}", path, root=root))
    for index, raw in enumerate(runtime.reference_context):
        path = resolve_under_root(root, raw, must_exist=True)
        members.extend(_expand_member(f"runtime:reference:{index}", path, root=root))
    for binding in runtime.input_bindings:
        path = resolve_under_root(root, binding.path, must_exist=True)
        members.extend(
            _expand_member(f"runtime:binding:{binding.binding_id}", path, root=root)
        )
    return members


def _expand_member(
    role: str,
    path: Path,
    *,
    root: Path,
    exclude_globs: tuple[str, ...] = (),
) -> list[tuple[str, Path]]:
    path = path.resolve()
    expanded = attachments.expand_attachment_target(
        root,
        path,
        exclude_globs=exclude_globs,
    )
    return [
        (
            role
            if path.is_file()
            else f"{role}:{child.relative_to(path).as_posix()}",
            child,
        )
        for child in expanded
    ]


def _runtime_payload(runtime: RuntimeOptions) -> dict[str, Any]:
    return {
        "primary_job_inputs": list(runtime.primary_job_inputs),
        "reference_context": list(runtime.reference_context),
        "input_bindings": [
            {
                "binding_id": binding.binding_id,
                "path": binding.path,
                "authority": binding.authority,
                "stage_ids": list(binding.stage_ids),
            }
            for binding in runtime.input_bindings
        ],
        "max_input_tokens": runtime.max_input_tokens,
        "skip_token_count": runtime.skip_token_count,
        "max_output_tokens": runtime.max_output_tokens,
        "file_expires_after": runtime.file_expires_after,
        "delete_uploaded_files_on_complete": runtime.delete_uploaded_files_on_complete,
        "primary_model": runtime.primary_model,
        "structural_model": runtime.structural_model,
        "service_tier": runtime.service_tier,
        "safety_identifier": runtime.safety_identifier,
        "prompt_cache_key_strategy": runtime.prompt_cache_key_strategy,
    }


def create_run_contract(
    *,
    root: Path,
    run_dir: Path,
    workflow: WorkflowDefinition,
    runtime: RuntimeOptions,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for role, path in _asset_paths(workflow, runtime, root):
        relative = relpath(root, path)
        if relative in seen_paths:
            continue
        seen_paths.add(relative)
        records.append(
            {
                "role": role,
                "path": relative,
                "sha256": sha256_file(path),
                "bytes": path.stat().st_size,
            }
        )
    canonical_members = json.dumps(records, sort_keys=True, separators=(",", ":"))
    runtime_payload = _runtime_payload(runtime)
    contract = {
        "schema_version": RUN_CONTRACT_SCHEMA_VERSION,
        "created_at": utc_now_iso(),
        "workflow_id": workflow.workflow_id,
        "assurance_profile": workflow.assurance_profile,
        "data_handling_policy": json.loads(
            json.dumps(ASSURANCE_PROFILES[workflow.assurance_profile]["data_handling"])
        ),
        "workflow_asset_set_hash": sha256_text(canonical_members),
        "effective_runtime": runtime_payload,
        "effective_runtime_sha256": sha256_text(
            json.dumps(runtime_payload, sort_keys=True, separators=(",", ":"))
        ),
        "members": records,
    }
    contract["contract_sha256"] = sha256_text(
        json.dumps(contract, sort_keys=True, separators=(",", ":"))
    )
    validate_contract(contract, "run_contract.schema.json", label="frozen run contract")
    path = run_dir / "run_contract.json"
    write_json(path, contract)
    return contract


def load_and_verify_run_contract(*, root: Path, run_dir: Path) -> dict[str, Any]:
    path = resolve_under_root(root, run_dir / "run_contract.json", must_exist=True)
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Invalid frozen run contract {path}: {exc}") from exc
    if contract.get("schema_version") != RUN_CONTRACT_SCHEMA_VERSION:
        raise SystemExit(
            f"Unsupported run contract {contract.get('schema_version')!r}; live v1 runs without "
            "a frozen contract cannot be resumed. Preserve their evidence and start a new v2 run."
        )
    validate_contract(contract, "run_contract.schema.json", label=f"frozen run contract {path}")
    expected_contract_hash = contract.get("contract_sha256")
    unsigned = dict(contract)
    unsigned.pop("contract_sha256", None)
    actual_contract_hash = sha256_text(
        json.dumps(unsigned, sort_keys=True, separators=(",", ":"))
    )
    if expected_contract_hash != actual_contract_hash:
        raise SystemExit("Frozen run-contract hash mismatch; refusing to continue.")
    for member in contract.get("members", []):
        member_path = resolve_under_root(root, str(member["path"]), must_exist=True)
        actual = sha256_file(member_path)
        if actual != member.get("sha256"):
            raise SystemExit(
                f"Frozen run-contract member drifted: {member['path']} "
                f"(expected {member.get('sha256')}, got {actual})."
            )
    return contract


def verify_effective_runtime(
    contract: dict[str, Any],
    runtime: RuntimeOptions,
    *,
    allow_stage_output_increase: bool = False,
) -> None:
    """Reject caller-supplied runtime drift for an already frozen run."""

    payload = _runtime_payload(runtime)
    # Review bundles are stage-gated handoffs created after the run contract;
    # their own hashes are validated when consumed. A narrowly authorized
    # output-limit increase may likewise be supplied for one unstarted stage
    # after a review gate; the exact effective value is frozen in that stage's
    # request plan and payload. All other request defaults and operator source
    # inputs remain frozen here.
    if allow_stage_output_increase:
        payload["max_output_tokens"] = contract["effective_runtime"].get(
            "max_output_tokens"
        )
    actual = sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    if actual != contract.get("effective_runtime_sha256"):
        raise SystemExit(
            "Caller effective runtime drifted from the frozen run contract; "
            "reuse the original runtime inputs and model/request settings."
        )


def runtime_from_contract(contract: dict[str, Any], **control: Any) -> RuntimeOptions:
    """Hydrate request-affecting runtime fields for resume/finalization paths."""

    payload = contract["effective_runtime"]
    return RuntimeOptions(
        primary_job_inputs=list(payload.get("primary_job_inputs", [])),
        reference_context=list(payload.get("reference_context", [])),
        input_bindings=[
            RuntimeInputBinding(
                binding_id=str(binding["binding_id"]),
                path=str(binding["path"]),
                authority=str(binding["authority"]),
                stage_ids=tuple(binding.get("stage_ids", [])),
            )
            for binding in payload.get("input_bindings", [])
        ],
        max_input_tokens=payload.get("max_input_tokens"),
        skip_token_count=bool(payload.get("skip_token_count", False)),
        max_output_tokens=payload.get("max_output_tokens"),
        file_expires_after=payload.get("file_expires_after"),
        delete_uploaded_files_on_complete=payload.get(
            "delete_uploaded_files_on_complete"
        ),
        primary_model=payload.get("primary_model"),
        structural_model=payload.get("structural_model"),
        service_tier=payload.get("service_tier"),
        safety_identifier=payload.get("safety_identifier"),
        prompt_cache_key_strategy=str(
            payload.get("prompt_cache_key_strategy", "legacy_stage_v1")
        ),
        **control,
    )
