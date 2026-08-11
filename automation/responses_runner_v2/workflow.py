from __future__ import annotations

import copy
import json
import time
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from . import attachments, artifacts, review_bundle, sidecar
from .request_plan import (
    build_request_plan,
    materialize_request_payload,
    symbolic_file_handle,
    verify_materialized_request,
)
from .contracts import (
    ASSURANCE_PROFILES,
    COMMON_RUNNER_INSTRUCTIONS,
    DEFAULT_OUTPUT_ROOT,
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_STRUCTURAL_MODEL,
    GateType,
    ModelRole,
    RUNNER_VERSION,
    ResumeMode,
    RunStatus,
    RuntimeOptions,
    StageDefinition,
    StageStatus,
    TERMINAL_RESPONSE_STATUSES,
    assert_stage_transition,
    build_prompt_cache_key,
    base_model_name,
    load_json,
    model_max_output_tokens,
    model_context_window,
    new_run_id,
    normalize_prompt_cache_retention,
    normalize_slug,
    parse_duration_seconds,
    read_text,
    relpath,
    repo_root,
    resolve_under_root,
    runner_now,
    sha256_file,
    sha256_text,
    unique_strings,
    validate_model_options,
    write_json,
)
from .locking import RunLockError, run_lock
from .openai_client import ApiError, OpenAIClient
from .pack_loader import (
    load_input_manifest,
    load_schema_json,
    load_text_asset,
    load_tool_profile,
    load_workflow_definition,
    validate_operator_inputs,
)
from .run_contract import (
    create_run_contract,
    load_and_verify_run_contract,
    runtime_from_contract,
    verify_effective_runtime,
)
from .schema_validation import validate_contract
from .telemetry import build_usage_report
from .validators import run_validator

REVIEWABLE_APPROVED_SOURCE_STATUSES = {
    StageStatus.WAITING_FOR_REVIEW.value,
    StageStatus.FAILED.value,
    StageStatus.FAILED_COMPLETE.value,
}

RUNNABLE_STAGE_STATES = {StageStatus.PREPARED.value}
LIVE_OR_UNCERTAIN_STAGE_STATES = {
    StageStatus.STAGING_INPUTS.value,
    StageStatus.UPLOADING.value,
    StageStatus.PREFLIGHT_PASSED.value,
    StageStatus.SUBMITTING.value,
    StageStatus.SUBMISSION_OUTCOME_UNKNOWN.value,
    StageStatus.SUBMITTED.value,
    StageStatus.IN_PROGRESS.value,
    StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value,
    StageStatus.CANCELLING.value,
}


def _entry_from_path(path: str | Path, *, notes: str | None = None) -> attachments.AttachmentEntry:
    from .contracts import AttachmentEntry

    resolved = Path(path)
    kind = "directory" if resolved.suffix == "" and resolved.exists() and resolved.is_dir() else "file"
    return AttachmentEntry(path=str(path), kind=kind, notes=notes)


def _build_operator_entries(paths: list[str], *, notes: str | None = None) -> list[attachments.AttachmentEntry]:
    from .contracts import AttachmentEntry

    entries: list[AttachmentEntry] = []
    for raw in paths:
        entries.append(AttachmentEntry(path=str(raw), kind="file", notes=notes))
    return entries


def _load_review_bundles(root: Path, review_bundle_paths: list[str]) -> dict[str, dict[str, Any]]:
    bundles: dict[str, dict[str, Any]] = {}
    for bundle_path in review_bundle_paths:
        bundle = review_bundle.load_review_bundle(root=root, bundle_path=bundle_path)
        bundles[str(bundle["source_stage_id"])] = bundle
    return bundles


def _operator_overrides(runtime: RuntimeOptions) -> dict[str, Any]:
    return {
        "primary_job_inputs": list(runtime.primary_job_inputs),
        "reference_context": list(runtime.reference_context),
        "review_bundles": list(runtime.review_bundles),
        "input_bindings": [
            {
                "binding_id": binding.binding_id,
                "path": binding.path,
                "authority": binding.authority,
                "stage_ids": list(binding.stage_ids),
            }
            for binding in runtime.input_bindings
        ],
        "skip_token_count": runtime.skip_token_count,
        "max_input_tokens": runtime.max_input_tokens,
        "max_output_tokens": runtime.max_output_tokens,
        "file_expires_after": runtime.file_expires_after,
        "delete_uploaded_files_on_complete": runtime.delete_uploaded_files_on_complete,
        "primary_model": runtime.primary_model,
        "structural_model": runtime.structural_model,
        "service_tier": runtime.service_tier,
        "safety_identifier": runtime.safety_identifier,
        "prompt_cache_key_strategy": runtime.prompt_cache_key_strategy,
    }


RUN_INITIALIZATION_INTENT_FILENAME = "run_initialization.intent.json"


def _run_initialization_intent_path(run_dir: Path) -> Path:
    return run_dir / RUN_INITIALIZATION_INTENT_FILENAME


def _validate_partial_run_initialization(
    *,
    root: Path,
    run_dir: Path,
    workflow,
    runtime: RuntimeOptions,
    run_name: str,
    intent: dict[str, Any],
) -> dict[str, Any]:
    """Validate an immutable pre-manifest initialization intent and its partial files."""

    if set(intent) != {
        "schema_version",
        "created_at",
        "target_manifest",
        "target_manifest_sha256",
    } or intent.get("schema_version") != "responses_runner_v2.run_initialization_intent.v1":
        raise SystemExit("Invalid run-initialization intent; refusing partial recovery.")
    target = intent.get("target_manifest")
    if not isinstance(target, dict) or artifacts.json_file_sha256(target) != intent.get(
        "target_manifest_sha256"
    ):
        raise SystemExit("Run-initialization intent manifest hash mismatch.")
    expected_stages = [
        {
            "stage_id": stage.stage_id,
            "stage_number": stage.stage_number,
            "gate": stage.gate.value,
            "stage_dir": relpath(
                root,
                artifacts.stage_root_path(run_dir, stage.stage_number, stage.stage_id),
            ),
            "status": "prepared",
        }
        for stage in workflow.stages
    ]
    expected_fixed = {
        "schema_version": "responses_runner_v2.run_manifest.v2",
        "run_name": run_name,
        "workflow_id": workflow.workflow_id,
        "workflow_manifest_path": relpath(root, workflow.workflow_file),
        "workflow_manifest_sha256": sha256_file(workflow.workflow_file),
        "run_dir": relpath(root, run_dir),
        "status": "created",
        "stage_order": [stage.stage_id for stage in workflow.stages],
        "operator_overrides": _operator_overrides(runtime),
        "stages": expected_stages,
    }
    if any(target.get(key) != value for key, value in expected_fixed.items()):
        raise SystemExit(
            "Run-initialization intent does not match the requested workflow/runtime binding."
        )
    if not isinstance(target.get("run_id"), str) or not target["run_id"]:
        raise SystemExit("Run-initialization intent is missing its run identity.")
    if not isinstance(target.get("started_at"), str) or not target["started_at"]:
        raise SystemExit("Run-initialization intent is missing its start timestamp.")

    allowed_files = {
        run_dir / ".runner.lock",
        _run_initialization_intent_path(run_dir),
        run_dir / "run_contract.json",
    }
    allowed_dirs = {run_dir / "stages"} | {
        artifacts.stage_root_path(run_dir, stage.stage_number, stage.stage_id)
        for stage in workflow.stages
    }
    unexpected = [
        path
        for path in run_dir.rglob("*")
        if (path.is_file() and path not in allowed_files)
        or (path.is_dir() and path not in allowed_dirs)
    ]
    if unexpected:
        raise SystemExit(
            "Refusing partial run initialization with unexpected evidence: "
            + ", ".join(relpath(root, path) for path in sorted(unexpected))
        )
    return dict(target)


def _verify_initial_run_contract(
    *,
    root: Path,
    run_dir: Path,
    workflow,
    runtime: RuntimeOptions,
) -> dict[str, Any]:
    contract = load_and_verify_run_contract(root=root, run_dir=run_dir)
    if (
        contract.get("workflow_id") != workflow.workflow_id
        or contract.get("assurance_profile") != workflow.assurance_profile
    ):
        raise SystemExit("Partial run contract does not match the requested workflow.")
    workflow_member = next(
        (
            member
            for member in contract.get("members", [])
            if member.get("role") == "workflow_manifest"
        ),
        None,
    )
    if (
        workflow_member is None
        or workflow_member.get("path") != relpath(root, workflow.workflow_file)
        or workflow_member.get("sha256") != sha256_file(workflow.workflow_file)
    ):
        raise SystemExit("Partial run contract workflow binding mismatch.")
    verify_effective_runtime(contract, runtime)
    return contract


def _load_or_create_run_manifest(
    *,
    root: Path,
    workflow,
    runtime: RuntimeOptions,
) -> tuple[Path, dict[str, Any]]:
    output_root = runtime.output_root or resolve_under_root(root, DEFAULT_OUTPUT_ROOT, must_exist=False)
    run_name = normalize_slug(runtime.run_name or workflow.workflow_id)
    run_dir = artifacts.create_run_dir(
        root=root,
        output_root=output_root,
        run_name=run_name,
        workflow_id=workflow.workflow_id,
        run_dir=runtime.run_dir,
    )
    manifest_path = artifacts.run_manifest_path(run_dir)
    try:
        with run_lock(run_dir):
            if manifest_path.exists():
                manifest = artifacts.load_run_manifest(root, run_dir)
                if manifest["workflow_id"] != workflow.workflow_id:
                    raise SystemExit(
                        f"Run directory workflow mismatch: expected {workflow.workflow_id}, "
                        f"got {manifest['workflow_id']}"
                    )
                if manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
                    raise SystemExit(
                        "This is a frozen v1 run. Its terminal evidence remains readable, but it cannot "
                        "be continued under v2 semantics because its assets and attempts were not frozen. "
                        "Archive it and start a new v2 run."
                    )
                manifest = _reconcile_stage_state_transitions(
                    root=root,
                    run_dir=run_dir,
                    run_manifest=manifest,
                )
                contract = load_and_verify_run_contract(root=root, run_dir=run_dir)
                workflow_member = next(
                    (
                        member
                        for member in contract.get("members", [])
                        if member.get("role") == "workflow_manifest"
                    ),
                    None,
                )
                caller_workflow_path = relpath(root, workflow.workflow_file)
                if (
                    workflow_member is None
                    or workflow_member.get("path") != caller_workflow_path
                    or workflow_member.get("sha256") != sha256_file(workflow.workflow_file)
                ):
                    raise SystemExit(
                        "Caller workflow manifest does not match the frozen run contract; "
                        "resume with the original workflow file."
                    )
                verify_effective_runtime(contract, runtime)
                return run_dir, manifest
            intent_path = _run_initialization_intent_path(run_dir)
            if intent_path.exists():
                intent = load_json(intent_path, "run-initialization intent")
                manifest = _validate_partial_run_initialization(
                    root=root,
                    run_dir=run_dir,
                    workflow=workflow,
                    runtime=runtime,
                    run_name=run_name,
                    intent=intent,
                )
            else:
                unexpected_entries = [
                    path for path in run_dir.iterdir() if path.name != ".runner.lock"
                ]
                if unexpected_entries:
                    raise SystemExit(
                        f"Refusing to initialize nonempty run directory without run_manifest.json: {run_dir}"
                    )
                manifest = artifacts.initialize_run_manifest(
                    root=root,
                    workflow=workflow,
                    run_id=new_run_id(),
                    run_name=run_name,
                    run_dir=run_dir,
                    operator_overrides=_operator_overrides(runtime),
                )
                intent = {
                    "schema_version": "responses_runner_v2.run_initialization_intent.v1",
                    "created_at": runner_now().isoformat(),
                    "target_manifest": manifest,
                    "target_manifest_sha256": artifacts.json_file_sha256(manifest),
                }
                write_json(intent_path, intent)

            for stage in workflow.stages:
                artifacts.build_stage_paths(
                    run_dir,
                    stage.stage_number,
                    stage.stage_id,
                )
            contract_path = run_dir / "run_contract.json"
            if contract_path.exists():
                contract = _verify_initial_run_contract(
                    root=root,
                    run_dir=run_dir,
                    workflow=workflow,
                    runtime=runtime,
                )
            else:
                create_run_contract(
                    root=root,
                    run_dir=run_dir,
                    workflow=workflow,
                    runtime=runtime,
                )
                contract = _verify_initial_run_contract(
                    root=root,
                    run_dir=run_dir,
                    workflow=workflow,
                    runtime=runtime,
                )
            manifest["revision"] = 1
            manifest["assurance_profile"] = workflow.assurance_profile
            manifest["run_contract_path"] = relpath(root, contract_path)
            manifest["run_contract_sha256"] = sha256_file(contract_path)
            manifest["workflow_asset_set_hash"] = contract["workflow_asset_set_hash"]
            artifacts.write_run_manifest(run_dir, manifest)
            return run_dir, manifest
    except RunLockError as exc:
        raise SystemExit(str(exc)) from exc


def _stage_summary_map(run_manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["stage_id"]: item for item in run_manifest["stages"]}


def _determine_next_stage(
    *,
    workflow,
    run_manifest: dict[str, Any],
    review_bundles: dict[str, dict[str, Any]],
    explicit_stage_id: str | None,
    rerun_authorized_stage_id: str | None = None,
) -> StageDefinition:
    def approved_review_handoff_exists(previous_stage, previous_summary: dict[str, Any]) -> bool:
        previous_status = str(previous_summary.get("status", ""))
        return (
            previous_stage.gate == GateType.REVIEW_REQUIRED
            and previous_status in REVIEWABLE_APPROVED_SOURCE_STATUSES
            and (
                previous_stage.stage_id in review_bundles
                or (
                    bool(previous_summary.get("review_approved"))
                    and isinstance(previous_summary.get("review_bundle_path"), str)
                    and bool(previous_summary.get("review_bundle_path"))
                )
            )
        )

    stage_summaries = _stage_summary_map(run_manifest)
    if explicit_stage_id:
        stage = workflow.stage(explicit_stage_id)
        own_status = str(stage_summaries[stage.stage_id].get("status", ""))
        if own_status not in RUNNABLE_STAGE_STATES and not (
            own_status == StageStatus.FAILED_NO_ARTIFACT.value
            and rerun_authorized_stage_id == stage.stage_id
        ):
            action = "resume" if own_status in LIVE_OR_UNCERTAIN_STAGE_STATES else "controlled rerun"
            raise SystemExit(
                f"Stage {stage.stage_id} is {own_status!r}, not prepared; use {action} instead of run."
            )
        for previous_stage in workflow.stages:
            if previous_stage.stage_number >= stage.stage_number:
                break
            previous_summary = stage_summaries[previous_stage.stage_id]
            previous_status = previous_summary["status"]
            if previous_status == StageStatus.COMPLETED.value:
                continue
            if approved_review_handoff_exists(previous_stage, previous_summary):
                continue
            if previous_status == StageStatus.WAITING_FOR_REVIEW.value:
                raise SystemExit(
                    f"Stage {stage.stage_id} requires a review bundle from stage {previous_stage.stage_id}."
                )
            if previous_status != StageStatus.COMPLETED.value:
                raise SystemExit(
                    f"Stage {stage.stage_id} cannot run before stage {previous_stage.stage_id} completes."
                )
        return stage

    for stage in workflow.stages:
        summary = stage_summaries[stage.stage_id]
        status = summary["status"]
        if status in LIVE_OR_UNCERTAIN_STAGE_STATES:
            raise SystemExit(
                f"Stage {stage.stage_id} is nonterminal. Use resume or refresh instead of run."
            )
        if status not in RUNNABLE_STAGE_STATES:
            continue
        if stage.stage_number == 1:
            return stage
        previous_stage = workflow.stages[stage.stage_number - 2]
        previous_summary = stage_summaries[previous_stage.stage_id]
        if previous_summary["status"] == StageStatus.COMPLETED.value:
            return stage
        if approved_review_handoff_exists(previous_stage, previous_summary):
            return stage
        if previous_summary["status"] == StageStatus.WAITING_FOR_REVIEW.value:
            raise SystemExit(
                f"Run is waiting for review after stage {previous_stage.stage_id}. Supply --review-bundle."
            )
    raise SystemExit("No eligible stage was found for this run.")


def _validate_rerun_authorization(
    *,
    root: Path,
    run_dir: Path,
    run_manifest: dict[str, Any],
    stage_id: str,
    archive_manifest: str | None,
) -> dict[str, Any] | None:
    """Bind one archived failed attempt to one new immutable attempt."""

    if archive_manifest is None:
        return None
    summary = artifacts.find_stage_summary(run_manifest, stage_id)
    if summary.get("status") != StageStatus.FAILED_NO_ARTIFACT.value:
        raise SystemExit(
            "An archive rerun is allowed only for the current failed_no_artifact stage."
        )
    archive_path = resolve_under_root(root, archive_manifest, must_exist=True)
    archive = load_json(archive_path, "supervisor archive")
    validate_contract(
        archive,
        "supervisor_archive.schema.json",
        label="supervisor rerun archive",
    )
    if not archive.get("rerun_as_is_eligible"):
        raise SystemExit("Supervisor archive is not eligible for an as-is rerun.")
    source = archive.get("source", {})
    if source.get("run_dir") != relpath(root, run_dir) or source.get("stage_id") != stage_id:
        raise SystemExit("Supervisor archive does not bind this run and stage.")
    if source.get("run_id") not in {None, run_manifest["run_id"]}:
        raise SystemExit("Supervisor archive run identity does not match this run.")
    evidence = archive.get("unchanged_input_evidence", {})
    if (
        evidence.get("rerun_requires_same_hashes") is not True
        or evidence.get("request_hash_before") != archive.get("request_hash")
    ):
        raise SystemExit("Supervisor archive lacks unchanged request-hash evidence.")
    for item in archive.get("included_artifacts", []):
        source_path = resolve_under_root(root, str(item["source_path"]), must_exist=True)
        archived_path = resolve_under_root(root, str(item["archive_path"]), must_exist=True)
        expected_hash = str(item["sha256"])
        if sha256_file(source_path) != expected_hash or sha256_file(archived_path) != expected_hash:
            raise SystemExit(
                f"Supervisor archive evidence drifted for {item['source_path']}."
            )
    archive_hash = sha256_file(archive_path)
    for attempt in summary.get("attempts", []):
        rerun = attempt.get("rerun_authorization")
        if isinstance(rerun, dict) and rerun.get("archive_sha256") == archive_hash:
            raise SystemExit("Supervisor archive authorization has already been consumed.")
    return {
        "archive_manifest_path": relpath(root, archive_path),
        "archive_sha256": archive_hash,
        "request_hash": str(archive["request_hash"]),
        "scaffold_hash": str(archive.get("scaffold_hash") or ""),
        "prior_attempt_id": summary.get("current_attempt_id"),
        "authorized_at": runner_now().isoformat(),
    }


def _effective_model(workflow, stage: StageDefinition, runtime: RuntimeOptions) -> str:
    if stage.model_role == ModelRole.PRIMARY_GENERATION:
        return runtime.primary_model or workflow.model_roles["primary_generation"].model
    return runtime.structural_model or workflow.model_roles["structural_processing"].model


def _effective_reasoning(workflow, stage: StageDefinition) -> str:
    profile = workflow.model_roles[stage.model_role.value]
    return stage.reasoning_effort or profile.reasoning_effort


def _effective_reasoning_mode(workflow, stage: StageDefinition) -> str | None:
    return workflow.model_roles[stage.model_role.value].reasoning_mode


def _effective_verbosity(workflow, stage: StageDefinition) -> str:
    profile = workflow.model_roles[stage.model_role.value]
    return stage.verbosity or profile.verbosity


def _effective_prompt_cache_retention(workflow, stage: StageDefinition) -> str | None:
    profile = workflow.model_roles[stage.model_role.value]
    return profile.prompt_cache_retention


def _effective_prompt_cache_options(workflow, stage: StageDefinition) -> dict[str, str] | None:
    profile = workflow.model_roles[stage.model_role.value]
    if profile.prompt_cache_mode is None and profile.prompt_cache_ttl is None:
        return None
    options: dict[str, str] = {}
    if profile.prompt_cache_mode is not None:
        options["mode"] = profile.prompt_cache_mode
    if profile.prompt_cache_ttl is not None:
        options["ttl"] = profile.prompt_cache_ttl
    return options


def _effective_max_output_tokens(workflow, stage: StageDefinition, runtime: RuntimeOptions) -> int:
    if runtime.max_output_tokens is not None:
        return runtime.max_output_tokens
    if stage.max_output_tokens is not None:
        return stage.max_output_tokens
    model = _effective_model(workflow, stage, runtime)
    return model_max_output_tokens(model) or 32000


def _effective_max_input_tokens(stage: StageDefinition, runtime: RuntimeOptions) -> int | None:
    if runtime.max_input_tokens is not None:
        return runtime.max_input_tokens
    return stage.max_input_tokens


def _effective_service_tier(workflow, runtime: RuntimeOptions) -> str | None:
    return runtime.service_tier or workflow.request_defaults.service_tier


def _effective_safety_identifier(workflow, runtime: RuntimeOptions) -> str | None:
    return runtime.safety_identifier or workflow.request_defaults.safety_identifier


def _effective_expiration_policy(workflow, runtime: RuntimeOptions) -> dict[str, Any] | None:
    seconds = parse_duration_seconds(runtime.file_expires_after)
    if seconds is None:
        seconds = workflow.request_defaults.file_uploads.expires_after_seconds
    if seconds is None:
        return None
    return {"anchor": "created_at", "seconds": seconds}


def _delete_uploads_on_complete(workflow, runtime: RuntimeOptions) -> bool:
    if runtime.delete_uploaded_files_on_complete is not None:
        return runtime.delete_uploaded_files_on_complete
    return workflow.request_defaults.file_uploads.delete_on_completion


def _build_instructions(workflow, stage: StageDefinition) -> str:
    pieces = [COMMON_RUNNER_INSTRUCTIONS.strip(), load_text_asset(workflow.shared_instructions_path).strip()]
    if stage.stage_instructions_path is not None:
        pieces.append(load_text_asset(stage.stage_instructions_path).strip())
    return "\n\n".join(part for part in pieces if part)


def _build_text_config(
    *,
    root: Path,
    workflow,
    stage: StageDefinition,
    runtime: RuntimeOptions,
) -> dict[str, Any]:
    model = _effective_model(workflow, stage, runtime)
    prompt_cache_retention = _effective_prompt_cache_retention(workflow, stage)
    max_output_tokens = _effective_max_output_tokens(workflow, stage, runtime)
    validate_model_options(
        model=model,
        max_output_tokens=max_output_tokens,
        prompt_cache_retention=prompt_cache_retention,
        prompt_cache_ttl=workflow.model_roles[stage.model_role.value].prompt_cache_ttl,
        reasoning_mode=_effective_reasoning_mode(workflow, stage),
        text_format=stage.output.primary_format,
    )
    if stage.output.primary_format == "text":
        return {"format": {"type": "text"}, "verbosity": _effective_verbosity(workflow, stage)}
    schema = load_schema_json(stage.output.schema_path, root=root)
    return {
        "format": {
            "type": "json_schema",
            "name": stage.output.schema_name,
            "schema": schema,
            "strict": True,
        }
    }


def _resolve_tool_settings(root: Path, workflow, stage: StageDefinition) -> dict[str, Any]:
    profile = load_tool_profile(stage.tool_profile_path, root=root) if stage.tool_profile_path else {}
    tools = profile.get("tools")
    if not isinstance(tools, list) or not tools:
        return {}
    resolved = dict(profile)
    resolved.setdefault("parallel_tool_calls", workflow.request_defaults.parallel_tool_calls)
    resolved.setdefault("max_tool_calls", workflow.request_defaults.max_tool_calls)
    return resolved


def _reference_context_from_stage_outputs(
    *,
    workflow,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
) -> list[attachments.AttachmentEntry]:
    from .contracts import AttachmentEntry

    summaries = _stage_summary_map(run_manifest)
    entries: list[AttachmentEntry] = []
    for source_stage_id in stage.carry_forward.reference_context_from_stage_ids:
        summary = summaries[source_stage_id]
        response_markdown_path = summary.get("artifact_markdown_path")
        if not response_markdown_path and run_manifest.get("schema_version") == "responses_runner_v2.run_manifest.v1":
            response_markdown_path = summary.get("response_markdown_path")
        if not response_markdown_path:
            raise SystemExit(
                f"Stage {stage.stage_id} cannot carry forward {source_stage_id}; no response markdown was recorded."
            )
        entries.append(
            AttachmentEntry(
                path=response_markdown_path,
                kind="file",
                notes=f"carry-forward markdown from stage {source_stage_id}",
            )
        )
    return entries


def _review_handoff_entries(
    *,
    root: Path,
    workflow,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    review_bundles: dict[str, dict[str, Any]],
) -> tuple[list[attachments.AttachmentEntry], str | None]:
    source_stage_id = stage.carry_forward.review_bundle_from_stage_id
    if source_stage_id is None:
        return [], None
    if source_stage_id not in review_bundles:
        raise SystemExit(
            f"Stage {stage.stage_id} requires a review bundle from stage {source_stage_id}."
        )
    source_summary = artifacts.find_stage_summary(run_manifest, source_stage_id)
    source_status = str(source_summary.get("status", ""))
    if source_status not in REVIEWABLE_APPROVED_SOURCE_STATUSES:
        raise SystemExit(
            f"Stage {stage.stage_id} cannot consume a review bundle from stage {source_stage_id} "
            f"with status {source_status!r}."
        )
    bundle = review_bundles[source_stage_id]
    review_bundle.validate_review_bundle_for_stage(
        bundle,
        workflow_id=workflow.workflow_id,
        expected_source_stage_id=source_stage_id,
        expected_source_run_id=run_manifest["run_id"],
        root=root,
        source_stage_summary=source_summary,
    )
    source_summary["review_approved"] = True
    source_summary["approved_from_status"] = source_status
    source_summary["review_bundle_path"] = bundle["bundle_path"]
    return review_bundle.expand_review_bundle_inputs(
        bundle,
        include_response_artifact_json=stage.carry_forward.review_bundle_include_response_artifact_json,
    ), str(bundle["bundle_path"])


def _build_request_payload(
    *,
    workflow,
    stage: StageDefinition,
    run_manifest: dict[str, Any],
    runtime: RuntimeOptions,
    text_config: dict[str, Any],
    content: list[dict[str, Any]],
    role_blocks: list[dict[str, Any]],
    tool_settings: dict[str, Any],
) -> dict[str, Any]:
    model = _effective_model(workflow, stage, runtime)
    reasoning: dict[str, Any] = {"effort": _effective_reasoning(workflow, stage)}
    reasoning_mode = _effective_reasoning_mode(workflow, stage)
    if reasoning_mode is not None:
        reasoning["mode"] = reasoning_mode
    if runtime.prompt_cache_key_strategy == "stable_lane_v1":
        cache_prefix = (
            f"stable:v1:{workflow.workflow_id}:{RUNNER_VERSION}:"
            f"{base_model_name(model)}:{stage.model_role.value}"
        )
        cache_lane = stage.model_role.value
    else:
        cache_prefix = f"legacy:v1:{workflow.workflow_id}:{run_manifest['run_id']}"
        cache_lane = stage.stage_id
    payload: dict[str, Any] = {
        "model": model,
        "instructions": _build_instructions(workflow, stage),
        "input": [{"role": "user", "content": content}],
        "background": workflow.request_defaults.background,
        "store": workflow.request_defaults.store,
        "truncation": "disabled",
        "reasoning": reasoning,
        "text": text_config,
        "max_output_tokens": _effective_max_output_tokens(workflow, stage, runtime),
        "metadata": {
            "workflow_id": workflow.workflow_id,
            "run_id": run_manifest["run_id"],
            "run_name": run_manifest["run_name"],
            "stage_id": stage.stage_id,
            "stage_number": str(stage.stage_number),
            "runner_version": RUNNER_VERSION,
        },
        "prompt_cache_key": build_prompt_cache_key(
            cache_prefix,
            cache_lane,
        ),
    }
    normalized_retention = normalize_prompt_cache_retention(
        _effective_prompt_cache_retention(workflow, stage)
    )
    if normalized_retention:
        payload["prompt_cache_retention"] = normalized_retention
    cache_options = _effective_prompt_cache_options(workflow, stage)
    if cache_options:
        payload["prompt_cache_options"] = cache_options
    if workflow.request_defaults.temperature is not None:
        payload["temperature"] = workflow.request_defaults.temperature
    service_tier = _effective_service_tier(workflow, runtime)
    safety_identifier = _effective_safety_identifier(workflow, runtime)
    if service_tier:
        payload["service_tier"] = service_tier
    if safety_identifier:
        payload["safety_identifier"] = safety_identifier
    for key in ("tools", "tool_choice", "include", "max_tool_calls", "parallel_tool_calls"):
        if key in tool_settings:
            payload[key] = tool_settings[key]
    return payload


def _local_context_estimate(
    *,
    workflow,
    stage: StageDefinition,
    runtime: RuntimeOptions,
    resolved_manifest: dict[str, Any],
    rendered_manifest_md: str,
) -> dict[str, Any]:
    """Compute a conservative pre-upload bound without making a remote call."""

    instruction_bytes = len(_build_instructions(workflow, stage).encode("utf-8"))
    task_bytes = len(load_text_asset(stage.task_path).encode("utf-8"))
    manifest_bytes = len(rendered_manifest_md.encode("utf-8"))
    attachment_bytes = 0
    for field_name in (
        "primary_job_inputs",
        "reviewed_handoff_inputs",
        "attached_repository_files",
        "reference_context",
    ):
        for entry in resolved_manifest.get(field_name, []):
            resolved = entry.get("resolved", {})
            for expanded in resolved.get("expanded_paths", []):
                value = expanded.get("bytes")
                if isinstance(value, int) and value >= 0:
                    attachment_bytes += value
    # One input byte per token is deliberately conservative for offline
    # planning. Exact API counting remains a second pre-submit gate.
    estimated_input_tokens = (
        instruction_bytes + task_bytes + manifest_bytes + attachment_bytes
    )
    requested_output_tokens = _effective_max_output_tokens(workflow, stage, runtime)
    context_window = model_context_window(_effective_model(workflow, stage, runtime))
    safety_margin_tokens = max(4096, int((context_window or 0) * 0.02))
    configured_input_limit = _effective_max_input_tokens(stage, runtime)
    within_input_limit = (
        configured_input_limit is None or estimated_input_tokens <= configured_input_limit
    )
    within_context_window = (
        context_window is not None
        and estimated_input_tokens + requested_output_tokens + safety_margin_tokens
        <= context_window
    )
    return {
        "schema_version": "responses_runner_v2.local_context_estimate.v1",
        "method": "utf8_bytes_upper_bound_v1",
        "instruction_bytes": instruction_bytes,
        "task_bytes": task_bytes,
        "manifest_bytes": manifest_bytes,
        "attachment_bytes": attachment_bytes,
        "estimated_input_tokens": estimated_input_tokens,
        "configured_input_limit": configured_input_limit,
        "requested_output_tokens": requested_output_tokens,
        "safety_margin_tokens": safety_margin_tokens,
        "context_window": context_window,
        "within_input_limit": within_input_limit,
        "within_context_window": within_context_window,
        "passed": within_input_limit and within_context_window,
    }


def _token_preflight_state(
    *,
    root: Path,
    client: OpenAIClient,
    workflow,
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    payload: dict[str, Any],
    runtime: RuntimeOptions,
) -> dict[str, Any]:
    hard_limit = _effective_max_input_tokens(stage, runtime)
    if runtime.skip_token_count or not workflow.request_defaults.token_preflight.enabled:
        return {"status": "skipped_by_operator", "attempts": 0}
    policy = workflow.request_defaults.token_preflight
    attempts = 0
    last_error: ApiError | None = None
    for attempt in range(1, policy.max_retries + 1):
        attempts = attempt
        try:
            result = client.count_input_tokens_once(payload)
            input_tokens = result.get("input_tokens")
            if not isinstance(input_tokens, int):
                raise SystemExit("token preflight did not return an integer input_tokens value.")
            diagnostics = {
                "object": "token_preflight",
                "workflow_id": workflow.workflow_id,
                "stage_id": stage.stage_id,
                "input_tokens": input_tokens,
                "max_input_tokens": hard_limit,
                "within_limit": hard_limit is None or input_tokens <= hard_limit,
            }
            diagnostics_path = artifacts.write_token_preflight_success(stage_paths, diagnostics)
            if hard_limit is not None and input_tokens > hard_limit:
                error_payload = {
                    "status": "failed_closed",
                    "reason": "max_input_tokens_exceeded",
                    "input_tokens": input_tokens,
                    "max_input_tokens": hard_limit,
                }
                artifacts.write_stage_checkpoint(
                    stage_paths,
                    {
                        "run_id": payload["metadata"]["run_id"],
                        "stage_id": stage.stage_id,
                        "stage_number": stage.stage_number,
                        "updated_at": runner_now().isoformat(),
                        "status": StageStatus.BLOCKED.value,
                        "terminal": True,
                        "resume_mode": ResumeMode.FRESH_SUBMIT.value,
                        "review_checkpoint_required": stage.gate == GateType.REVIEW_REQUIRED,
                        "request_payload_path": relpath(root, stage_paths["request_payload"]),
                        "input_manifest_json_path": relpath(root, stage_paths["input_manifest_json"]),
                        "input_manifest_markdown_path": relpath(root, stage_paths["input_manifest_md"]),
                        "token_preflight": {
                            "status": "failed_closed",
                            "attempts": attempts,
                            "input_tokens": input_tokens,
                            "error_message": "max_input_tokens exceeded",
                            "diagnostics_path": relpath(root, diagnostics_path),
                        },
                        "artifacts": {
                            "stage_dir": relpath(root, stage_paths["stage_dir"]),
                        },
                        "error": error_payload,
                    },
                )
                raise SystemExit(
                    f"Stage {stage.stage_id} input token count {input_tokens} exceeds configured limit {hard_limit}."
                )
            return {
                "status": "succeeded",
                "attempts": attempts,
                "input_tokens": input_tokens,
                "diagnostics_path": relpath(root, diagnostics_path),
            }
        except ApiError as exc:
            last_error = exc
            if exc.status_code in policy.retryable_http_status_codes and attempt < policy.max_retries:
                continue
            error_payload = {
                "object": "token_preflight_error",
                "workflow_id": workflow.workflow_id,
                "stage_id": stage.stage_id,
                "attempts": attempts,
                "status_code": exc.status_code,
                "error_message": str(exc),
                "fallback_decision": (
                    "continue_without_token_count"
                    if exc.status_code in policy.retryable_http_status_codes
                    and policy.on_retryable_service_failure == "continue_without_token_count"
                    and hard_limit is None
                    else "fail_closed"
                ),
            }
            error_path = artifacts.write_token_preflight_error(stage_paths, error_payload)
            if (
                exc.status_code in policy.retryable_http_status_codes
                and policy.on_retryable_service_failure == "continue_without_token_count"
                and hard_limit is None
            ):
                return {
                    "status": "continued_after_retryable_service_failure",
                    "attempts": attempts,
                    "error_message": str(exc),
                    "diagnostics_path": relpath(root, error_path),
                }
            raise SystemExit(
                f"Token preflight failed closed for stage {stage.stage_id}: {exc}"
            ) from exc
    raise SystemExit(f"Token preflight failed for stage {stage.stage_id}: {last_error}")


def _stage_status_from_response(
    response_json: dict[str, Any],
    stage: StageDefinition,
    has_next_stage: bool,
    *,
    finalized: bool = False,
) -> str:
    status = str(response_json.get("status", "unknown"))
    if status in TERMINAL_RESPONSE_STATUSES and not finalized:
        return StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value
    if status == "completed":
        if stage.gate == GateType.REVIEW_REQUIRED and has_next_stage:
            return StageStatus.WAITING_FOR_REVIEW.value
        return StageStatus.COMPLETED.value
    if status == "failed":
        return (
            StageStatus.FAILED_COMPLETE.value
            if artifacts.extract_output_text(response_json)
            else StageStatus.FAILED_NO_ARTIFACT.value
        )
    if status == "cancelled":
        return StageStatus.CANCELLED.value
    if status == "incomplete":
        return StageStatus.INCOMPLETE.value
    if status in {"queued", "in_progress"}:
        return StageStatus.IN_PROGRESS.value
    return StageStatus.SUBMITTED.value


def _response_supports_sidecar_processing(response_json: dict[str, Any]) -> bool:
    status = str(response_json.get("status", "unknown"))
    if status == "completed":
        return bool(artifacts.extract_output_text(response_json))
    if status != "failed":
        return False
    if not artifacts.extract_output_text(response_json):
        return False
    output = response_json.get("output")
    if not isinstance(output, list):
        return False
    for item in output:
        if not isinstance(item, dict):
            continue
        if item.get("type") != "message":
            continue
        if str(item.get("status", "")) == "completed":
            return True
    return False


def _build_checkpoint(
    *,
    root: Path,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    stage_status: str,
    resume_mode: ResumeMode,
    token_preflight: dict[str, Any],
    response_json: dict[str, Any] | None,
    review_bundle_path: str | None,
    structured_output_written: bool,
    sidecar_written: bool,
    uploads_payload_path: Path | None,
) -> dict[str, Any]:
    stage_summary = artifacts.find_stage_summary(run_manifest, stage.stage_id)
    attempt_id = stage_summary.get("current_attempt_id")
    checkpoint: dict[str, Any] = {
        "run_id": run_manifest["run_id"],
        "stage_id": stage.stage_id,
        "stage_number": stage.stage_number,
        **({"attempt_id": attempt_id} if attempt_id else {}),
        "attempt_dir": relpath(root, stage_paths["attempt_dir"]),
        "updated_at": runner_now().isoformat(),
        "status": stage_status,
        "local_state": stage_status,
        "remote_status": (
            str(response_json.get("status")) if response_json is not None else None
        ),
        "terminal": stage_status in {
            StageStatus.COMPLETED.value,
            StageStatus.WAITING_FOR_REVIEW.value,
            StageStatus.FAILED.value,
            StageStatus.CANCELLED.value,
            StageStatus.INCOMPLETE.value,
            StageStatus.BLOCKED.value,
            StageStatus.BLOCKED_PREFLIGHT.value,
            StageStatus.FAILED_COMPLETE.value,
            StageStatus.FAILED_NO_ARTIFACT.value,
        },
        "resume_mode": resume_mode.value,
        "review_checkpoint_required": stage.gate == GateType.REVIEW_REQUIRED,
        "request_payload_path": relpath(root, stage_paths["request_payload"]),
        "input_manifest_json_path": relpath(root, stage_paths["input_manifest_json"]),
        "input_manifest_markdown_path": relpath(root, stage_paths["input_manifest_md"]),
        "token_preflight": token_preflight,
        "artifacts": {
            "stage_dir": relpath(root, stage_paths["stage_dir"]),
            "response_latest_json_path": relpath(root, stage_paths["response_latest_json"]),
            **(
                {
                    "artifact_markdown_path": relpath(root, stage_paths["artifact_md"]),
                    "artifact_markdown_sha256": sha256_file(stage_paths["artifact_md"]),
                }
                if stage_paths["artifact_md"].exists()
                else {}
            ),
            **(
                {"response_final_json_path": relpath(root, stage_paths["response_final_json"])}
                if stage_paths["response_final_json"].exists()
                else {}
            ),
            **(
                {"response_final_markdown_path": relpath(root, stage_paths["response_final_md"])}
                if stage_paths["response_final_md"].exists()
                else {}
            ),
            **(
                {"structured_output_path": relpath(root, stage_paths["structured_output"])}
                if structured_output_written and stage_paths["structured_output"].exists()
                else {}
            ),
            **(
                {"sidecar_response_json_path": relpath(root, stage_paths["sidecar_response_json"])}
                if sidecar_written and stage_paths["sidecar_response_json"].exists()
                else {}
            ),
            **(
                {"sidecar_response_markdown_path": relpath(root, stage_paths["sidecar_response_md"])}
                if sidecar_written and stage_paths["sidecar_response_md"].exists()
                else {}
            ),
            **(
                {"uploads_json_path": relpath(root, uploads_payload_path)}
                if uploads_payload_path is not None
                else {}
            ),
        },
        "finalization": {
            "status": (
                "completed"
                if stage_status
                in {
                    StageStatus.FINALIZED.value,
                    StageStatus.COMPLETED.value,
                    StageStatus.WAITING_FOR_REVIEW.value,
                    StageStatus.FAILED_COMPLETE.value,
                    StageStatus.FAILED_NO_ARTIFACT.value,
                    StageStatus.CANCELLED.value,
                    StageStatus.INCOMPLETE.value,
                }
                else "pending"
            )
        },
    }
    if review_bundle_path is not None:
        checkpoint["review_bundle_path"] = review_bundle_path
    if response_json is not None:
        checkpoint["response"] = {
            "id": str(response_json.get("id")),
            "status": str(response_json.get("status")),
            "model": str(response_json.get("model")),
            "background": bool(response_json.get("background", False)),
            "store": bool(response_json.get("store", False)),
            **(
                {"created_at": int(response_json["created_at"])}
                if response_json.get("created_at") is not None
                else {}
            ),
            **(
                {"completed_at": int(response_json["completed_at"])}
                if response_json.get("completed_at") is not None
                else {}
            ),
        }
        if response_json.get("error") is not None:
            checkpoint["error"] = response_json.get("error")
        if response_json.get("incomplete_details") is not None:
            checkpoint["incomplete_details"] = response_json.get("incomplete_details")
    return checkpoint


def _sync_stage_summary(
    *,
    root: Path,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    stage_status: str,
    response_json: dict[str, Any] | None,
    review_bundle_path: str | None,
    token_preflight_path: Path | None,
    checkpoint_sha256: str | None = None,
) -> None:
    summary = artifacts.find_stage_summary(run_manifest, stage.stage_id)
    summary["status"] = stage_status
    summary["local_state"] = stage_status
    summary["checkpoint_path"] = relpath(root, stage_paths["stage_checkpoint"])
    if checkpoint_sha256 is not None:
        summary["checkpoint_sha256"] = checkpoint_sha256
    elif stage_paths["stage_checkpoint"].exists():
        summary["checkpoint_sha256"] = sha256_file(stage_paths["stage_checkpoint"])
    summary["input_manifest_json_path"] = relpath(root, stage_paths["input_manifest_json"])
    if stage_paths["artifact_md"].exists():
        summary["artifact_markdown_path"] = relpath(root, stage_paths["artifact_md"])
        summary["artifact_markdown_sha256"] = sha256_file(stage_paths["artifact_md"])
    if stage_paths["response_final_md"].exists():
        summary["response_markdown_path"] = relpath(root, stage_paths["response_final_md"])
        summary["response_markdown_sha256"] = sha256_file(stage_paths["response_final_md"])
    if stage_paths["response_final_json"].exists():
        summary["response_json_path"] = relpath(root, stage_paths["response_final_json"])
        summary["response_json_sha256"] = sha256_file(stage_paths["response_final_json"])
    if stage_paths["structured_output"].exists():
        summary["structured_output_path"] = relpath(root, stage_paths["structured_output"])
        summary["structured_output_sha256"] = sha256_file(stage_paths["structured_output"])
    if stage_paths["sidecar_response_json"].exists():
        summary["sidecar_response_json_path"] = relpath(root, stage_paths["sidecar_response_json"])
        summary["sidecar_response_json_sha256"] = sha256_file(stage_paths["sidecar_response_json"])
    if stage_paths["sidecar_response_md"].exists():
        summary["sidecar_response_markdown_path"] = relpath(root, stage_paths["sidecar_response_md"])
        summary["sidecar_response_markdown_sha256"] = sha256_file(stage_paths["sidecar_response_md"])
    if token_preflight_path is not None and token_preflight_path.exists():
        summary["token_preflight_path"] = relpath(root, token_preflight_path)
    if review_bundle_path is not None:
        summary["review_bundle_path"] = review_bundle_path
    if response_json is not None:
        summary["response_id"] = str(response_json.get("id"))
        summary["response_status"] = str(response_json.get("status"))
        summary["remote_status"] = str(response_json.get("status"))
    current_attempt_id = summary.get("current_attempt_id")
    for attempt in summary.get("attempts", []):
        if attempt.get("attempt_id") == current_attempt_id:
            attempt["local_state"] = stage_status
            attempt["checkpoint_path"] = summary["checkpoint_path"]
            if summary.get("checkpoint_sha256"):
                attempt["checkpoint_sha256"] = summary["checkpoint_sha256"]
            if response_json is not None:
                attempt["response_id"] = summary.get("response_id")
                attempt["remote_status"] = summary.get("remote_status")
            break


def _run_status_after_stage(
    *,
    stage_status: str,
    has_next_stage: bool,
    stage: StageDefinition,
) -> str:
    if stage_status == StageStatus.WAITING_FOR_REVIEW.value:
        return RunStatus.WAITING_FOR_REVIEW.value
    if stage_status == StageStatus.COMPLETED.value and not has_next_stage:
        return RunStatus.COMPLETED.value
    if stage_status in {StageStatus.FAILED.value, StageStatus.FAILED_COMPLETE.value, StageStatus.FAILED_NO_ARTIFACT.value}:
        return RunStatus.FAILED.value
    if stage_status == StageStatus.CANCELLED.value:
        return RunStatus.CANCELLED.value
    if stage_status == StageStatus.INCOMPLETE.value:
        return RunStatus.FAILED.value
    if stage_status == StageStatus.SUBMISSION_OUTCOME_UNKNOWN.value:
        return RunStatus.SUBMISSION_OUTCOME_UNKNOWN.value
    if stage_status == StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value:
        return RunStatus.PENDING_FINALIZATION.value
    if stage_status in {StageStatus.BLOCKED.value, StageStatus.BLOCKED_PREFLIGHT.value}:
        return RunStatus.BLOCKED.value
    return RunStatus.RUNNING.value


def _build_stage_runtime_manifest(
    *,
    root: Path,
    workflow,
    stage: StageDefinition,
    run_manifest: dict[str, Any],
    static_manifest: dict[str, Any],
    runtime: RuntimeOptions,
    review_bundles: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], str | None]:
    reviewed_entries, consumed_review_bundle_path = _review_handoff_entries(
        root=root,
        workflow=workflow,
        run_manifest=run_manifest,
        stage=stage,
        review_bundles=review_bundles,
    )
    carry_forward_reference_entries = _reference_context_from_stage_outputs(
        workflow=workflow,
        run_manifest=run_manifest,
        stage=stage,
    )
    if stage.carry_forward.review_bundle_from_stage_id is not None:
        duplicate_source = stage.carry_forward.review_bundle_from_stage_id
        carry_forward_reference_entries = [
            entry
            for entry in carry_forward_reference_entries
            if entry.notes != f"carry-forward markdown from stage {duplicate_source}"
        ]
    bound_primary = [
        binding.path
        for binding in runtime.input_bindings
        if binding.authority == "primary_job_input"
        and (not binding.stage_ids or stage.stage_id in binding.stage_ids)
    ]
    bound_reference = [
        binding.path
        for binding in runtime.input_bindings
        if binding.authority == "reference_context"
        and (not binding.stage_ids or stage.stage_id in binding.stage_ids)
    ]
    resolved_manifest = attachments.resolve_stage_input_manifest(
        root=root,
        workflow_id=workflow.workflow_id,
        stage_id=stage.stage_id,
        run_id=run_manifest["run_id"],
        manifest_id=f"{workflow.workflow_id}.{stage.stage_id}",
        description=str(static_manifest.get("description") or ""),
        primary_job_inputs=[
            *static_manifest["primary_job_inputs"],
            *_build_operator_entries(runtime.primary_job_inputs, notes="operator-supplied primary job input"),
            *_build_operator_entries(bound_primary, notes="stage-scoped primary job input"),
        ],
        reviewed_handoff_inputs=[
            *static_manifest["reviewed_handoff_inputs"],
            *reviewed_entries,
        ],
        attached_repository_files=static_manifest["attached_repository_files"],
        reference_context=[
            *static_manifest["reference_context"],
            *_build_operator_entries(runtime.reference_context, notes="operator-supplied reference context"),
            *_build_operator_entries(bound_reference, notes="stage-scoped reference context"),
            *carry_forward_reference_entries,
        ],
    )
    return resolved_manifest, consumed_review_bundle_path


def _write_stage_artifacts_for_response(
    *,
    root: Path,
    client: OpenAIClient | None,
    workflow,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    runtime: RuntimeOptions,
    response_json: dict[str, Any],
    uploads_payload: dict[str, Any] | None,
    allow_sidecar_processing: bool = True,
) -> tuple[bool, bool, dict[str, Any] | None]:
    main_requested_text_format = stage.output.primary_format
    structured_output_written = False
    sidecar_written = False
    structured_output = None
    effective_uploads_payload = uploads_payload
    if main_requested_text_format == "json_schema":
        structured_output = artifacts.extract_structured_output(response_json, "json_schema")
        if structured_output is None:
            raise SystemExit(f"Stage {stage.stage_id} did not return structured output.")
        write_json(stage_paths["structured_output"], structured_output)
        structured_output_written = True

    output_text = artifacts.extract_output_text(response_json)
    artifacts.write_response_pair(
        root=root,
        markdown_path=stage_paths["response_final_md"],
        json_path=stage_paths["response_final_json"],
        title="Responses Runner V2 Stage Output",
        workflow_id=workflow.workflow_id,
        run_id=run_manifest["run_id"],
        stage_id=stage.stage_id,
        stage_number=stage.stage_number,
        response_json=response_json,
        requested_text_format=main_requested_text_format,
        structured_output=structured_output,
        uploads_payload=effective_uploads_payload,
        artifact_path=stage_paths["artifact_md"] if output_text else None,
    )
    if str(response_json.get("status")) == "completed" and not output_text:
        raise SystemExit(
            f"Stage {stage.stage_id} completed remotely but returned no assistant artifact text."
        )

    if (
        allow_sidecar_processing
        and stage.output.sidecar is not None
        and _response_supports_sidecar_processing(response_json)
    ):
        if client is None:
            raise SystemExit("A live OpenAI client is required for sidecar processing.")
        structural_model = runtime.structural_model or workflow.model_roles[
            ModelRole.STRUCTURAL_PROCESSING.value
        ].model
        result = sidecar.run_sidecar_processing(
            root=root,
            client=client,
            workflow_id=workflow.workflow_id,
            run_id=run_manifest["run_id"],
            stage_id=stage.stage_id,
            stage_number=stage.stage_number,
            structural_model=structural_model,
            reasoning_effort=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].reasoning_effort,
            prompt_cache_retention=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].prompt_cache_retention,
            reasoning_mode=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].reasoning_mode,
            prompt_cache_mode=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].prompt_cache_mode,
            prompt_cache_ttl=workflow.model_roles[
                ModelRole.STRUCTURAL_PROCESSING.value
            ].prompt_cache_ttl,
            schema_file=stage.output.sidecar.schema_path,
            schema_name=stage.output.sidecar.schema_name,
            response_markdown_path=stage_paths["artifact_md"],
            response_json_path=stage_paths["response_final_json"],
            sidecar_response_json_path=stage_paths["sidecar_response_json"],
            sidecar_response_markdown_path=stage_paths["sidecar_response_md"],
            structured_output_path=stage_paths["structured_output"],
            service_tier=_effective_service_tier(workflow, runtime),
            safety_identifier=_effective_safety_identifier(workflow, runtime),
            file_expiration_policy=_effective_expiration_policy(workflow, runtime),
            delete_uploaded_files_on_complete=_delete_uploads_on_complete(workflow, runtime),
            store=workflow.request_defaults.store,
            file_purpose=workflow.request_defaults.file_uploads.purpose,
        )
        effective_uploads_payload = _merge_uploads_payloads(
            effective_uploads_payload,
            result.get("uploads_payload"),
        )
        if effective_uploads_payload is not None:
            artifacts.write_response_pair(
                root=root,
                markdown_path=stage_paths["response_final_md"],
                json_path=stage_paths["response_final_json"],
                title="Responses Runner V2 Stage Output",
                workflow_id=workflow.workflow_id,
                run_id=run_manifest["run_id"],
                stage_id=stage.stage_id,
                stage_number=stage.stage_number,
                response_json=response_json,
                requested_text_format=main_requested_text_format,
                structured_output=structured_output,
                uploads_payload=effective_uploads_payload,
            )
        structured_output_written = True
        sidecar_written = True
    return structured_output_written, sidecar_written, effective_uploads_payload


def _record_attempt_start(
    *,
    root: Path,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    attempt_number: int,
    stage_paths: dict[str, Path],
    rerun_authorization: dict[str, Any] | None = None,
) -> None:
    summary = artifacts.find_stage_summary(run_manifest, stage.stage_id)
    attempt_id = f"attempt_{attempt_number:03d}"
    summary["current_attempt_id"] = attempt_id
    summary["status"] = StageStatus.STAGING_INPUTS.value
    summary["local_state"] = StageStatus.STAGING_INPUTS.value
    attempt_record = {
        "attempt_id": attempt_id,
        "attempt_number": attempt_number,
        "attempt_dir": relpath(root, stage_paths["attempt_dir"]),
        "local_state": StageStatus.STAGING_INPUTS.value,
        "created_at": runner_now().isoformat(),
    }
    if rerun_authorization is not None:
        attempt_record["rerun_authorization"] = rerun_authorization
    summary.setdefault("attempts", []).append(attempt_record)
    run_manifest["current_stage_id"] = stage.stage_id
    run_manifest["status"] = RunStatus.RUNNING.value
    run_manifest["revision"] = int(run_manifest.get("revision", 0)) + 1


def _reconcile_stage_state_transitions(
    *,
    root: Path,
    run_dir: Path,
    run_manifest: dict[str, Any],
) -> dict[str, Any]:
    """Roll forward durable v2 checkpoint intents whose manifest CAS did not finish.

    The caller holds the run lock. Frozen v1 manifests never enter this path, and
    discovery is restricted to explicit ``attempt_NNN`` directories.
    """

    if run_manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
        return run_manifest
    manifest_path = artifacts.run_manifest_path(run_dir)
    for transition_path in artifacts.list_stage_state_transitions(run_dir):
        transition = artifacts.load_stage_state_transition(transition_path)
        if transition["run_id"] != run_manifest["run_id"]:
            raise SystemExit(
                f"Stage state transition run_id mismatch: {transition_path}"
            )
        base_revision = int(transition["base_manifest_revision"])
        target_revision = int(transition["target_manifest_revision"])
        if target_revision != base_revision + 1:
            raise SystemExit(
                f"Stage state transition must advance exactly one revision: {transition_path}"
            )
        current_revision = int(run_manifest["revision"])
        if target_revision < current_revision:
            continue
        if target_revision == current_revision:
            if sha256_file(manifest_path) != transition["target_run_manifest_sha256"]:
                raise SystemExit(
                    "Committed stage state transition does not match the durable run manifest: "
                    f"{transition_path}"
                )
            continue
        if base_revision != current_revision:
            raise SystemExit(
                "Stage state transition does not continue the durable run manifest revision: "
                f"{transition_path}"
            )
        if sha256_file(manifest_path) != transition["base_manifest_sha256"]:
            raise SystemExit(
                "Stage state transition base manifest hash mismatch: "
                f"{transition_path}"
            )

        stage_id = str(transition["stage_id"])
        attempt_id = str(transition["attempt_id"])
        target_manifest = copy.deepcopy(transition["target_run_manifest"])
        checkpoint = copy.deepcopy(transition["target_checkpoint"])
        if (
            target_manifest.get("schema_version")
            != "responses_runner_v2.run_manifest.v2"
            or int(target_manifest.get("revision", 0)) != target_revision
            or target_manifest.get("run_id") != run_manifest["run_id"]
        ):
            raise SystemExit(f"Invalid target run manifest in transition: {transition_path}")
        current_summary = artifacts.find_stage_summary(run_manifest, stage_id)
        target_summary = artifacts.find_stage_summary(target_manifest, stage_id)
        if (
            current_summary.get("current_attempt_id") != attempt_id
            or target_summary.get("current_attempt_id") != attempt_id
            or checkpoint.get("run_id") != run_manifest["run_id"]
            or checkpoint.get("stage_id") != stage_id
            or checkpoint.get("attempt_id") != attempt_id
        ):
            raise SystemExit(
                f"Stage state transition attempt identity mismatch: {transition_path}"
            )
        try:
            attempt_number = int(attempt_id.removeprefix("attempt_"))
            stage_number = int(checkpoint["stage_number"])
        except (KeyError, ValueError) as exc:
            raise SystemExit(
                f"Invalid stage state transition attempt coordinates: {transition_path}"
            ) from exc
        expected_paths = artifacts.build_stage_paths(
            run_dir,
            stage_number,
            stage_id,
            attempt_number=attempt_number,
            create=False,
        )
        expected_checkpoint_path = relpath(root, expected_paths["stage_checkpoint"])
        expected_transition_path = artifacts.stage_state_transition_path(
            expected_paths,
            target_revision,
        )
        if (
            transition["target_checkpoint_path"] != expected_checkpoint_path
            or transition_path.resolve() != expected_transition_path.resolve()
            or checkpoint.get("attempt_dir")
            != relpath(root, expected_paths["attempt_dir"])
            or int(current_summary.get("stage_number", 0)) != stage_number
            or int(target_summary.get("stage_number", 0)) != stage_number
            or target_summary.get("checkpoint_path") != expected_checkpoint_path
            or target_summary.get("checkpoint_sha256")
            != transition["target_checkpoint_sha256"]
        ):
            raise SystemExit(
                f"Stage state transition path or hash binding mismatch: {transition_path}"
            )

        checkpoint_path = expected_paths["stage_checkpoint"]
        if checkpoint_path.exists():
            actual_checkpoint_sha256 = sha256_file(checkpoint_path)
            allowed_checkpoint_hashes = {
                transition["target_checkpoint_sha256"],
                current_summary.get("checkpoint_sha256"),
            }
            allowed_checkpoint_hashes.discard(None)
            if actual_checkpoint_sha256 not in allowed_checkpoint_hashes:
                raise SystemExit(
                    f"Stage checkpoint cannot be reconciled safely: {checkpoint_path}"
                )
        if (
            not checkpoint_path.exists()
            or sha256_file(checkpoint_path) != transition["target_checkpoint_sha256"]
        ):
            artifacts.write_stage_checkpoint(expected_paths, checkpoint)
        artifacts.write_run_manifest_cas(
            root=root,
            run_dir=run_dir,
            manifest=target_manifest,
            expected_revision=base_revision,
            stage_id=stage_id,
            expected_attempt_id=attempt_id,
            prepared=True,
        )
        run_manifest = target_manifest
    return run_manifest


def _persist_stage_state(
    *,
    root: Path,
    run_dir: Path,
    run_manifest: dict[str, Any],
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    stage_status: str,
    resume_mode: ResumeMode,
    token_preflight: dict[str, Any],
    response_json: dict[str, Any] | None,
    review_bundle_path: str | None,
    structured_output_written: bool = False,
    sidecar_written: bool = False,
    uploads_payload_path: Path | None = None,
    lock_already_held: bool = False,
    allow_prepared_preflight_block: bool = False,
) -> None:
    expected_revision = int(run_manifest.get("revision", 0))
    expected_attempt_id = artifacts.find_stage_summary(
        run_manifest, stage.stage_id
    ).get("current_attempt_id")
    lock_context = nullcontext() if lock_already_held else run_lock(run_dir)
    try:
        with lock_context:
            durable_manifest = artifacts.load_run_manifest(root, run_dir)
            artifacts.require_run_manifest_revision(
                durable_manifest,
                expected_revision=expected_revision,
                stage_id=stage.stage_id,
                expected_attempt_id=expected_attempt_id,
            )
            current_status = str(
                artifacts.find_stage_summary(run_manifest, stage.stage_id).get("status", "")
            )
            if current_status != stage_status:
                if not (
                    allow_prepared_preflight_block
                    and current_status == StageStatus.PREPARED.value
                    and stage_status == StageStatus.BLOCKED_PREFLIGHT.value
                ):
                    assert_stage_transition(current_status, stage_status)
            checkpoint = _build_checkpoint(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                resume_mode=resume_mode,
                token_preflight=token_preflight,
                response_json=response_json,
                review_bundle_path=review_bundle_path,
                structured_output_written=structured_output_written,
                sidecar_written=sidecar_written,
                uploads_payload_path=uploads_payload_path,
            )
            checkpoint_sha256 = artifacts.prepare_stage_checkpoint(checkpoint)
            target_manifest = copy.deepcopy(run_manifest)
            _sync_stage_summary(
                root=root,
                run_manifest=target_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                response_json=response_json,
                review_bundle_path=review_bundle_path,
                token_preflight_path=(
                    stage_paths["token_preflight"]
                    if stage_paths["token_preflight"].exists()
                    else stage_paths["token_preflight_error"]
                    if stage_paths["token_preflight_error"].exists()
                    else None
                ),
                checkpoint_sha256=checkpoint_sha256,
            )
            target_manifest["status"] = _run_status_after_stage(
                stage_status=stage_status,
                has_next_stage=stage.stage_number < len(target_manifest["stage_order"]),
                stage=stage,
            )
            target_manifest["current_stage_id"] = stage.stage_id
            target_manifest["revision"] = expected_revision + 1
            target_manifest["updated_at"] = runner_now().isoformat()
            if not isinstance(expected_attempt_id, str):
                # Dry-run compatibility has no v2 attempt identity and cannot
                # be promoted into the live attempt-transition journal.
                artifacts.write_stage_checkpoint(stage_paths, checkpoint)
                artifacts.write_run_manifest_cas(
                    root=root,
                    run_dir=run_dir,
                    manifest=target_manifest,
                    expected_revision=expected_revision,
                    stage_id=stage.stage_id,
                    expected_attempt_id=None,
                    prepared=True,
                )
                run_manifest.clear()
                run_manifest.update(target_manifest)
                return
            transition = {
                "schema_version": "responses_runner_v2.stage_state_transition.v1",
                "run_id": target_manifest["run_id"],
                "stage_id": stage.stage_id,
                "attempt_id": expected_attempt_id,
                "created_at": runner_now().isoformat(),
                "base_manifest_revision": expected_revision,
                "target_manifest_revision": expected_revision + 1,
                "base_manifest_sha256": sha256_file(
                    artifacts.run_manifest_path(run_dir)
                ),
                "target_checkpoint_path": relpath(
                    root,
                    stage_paths["stage_checkpoint"],
                ),
                "target_checkpoint_sha256": checkpoint_sha256,
                "target_checkpoint": checkpoint,
                "target_run_manifest_sha256": artifacts.json_file_sha256(
                    target_manifest
                ),
                "target_run_manifest": target_manifest,
            }
            artifacts.write_stage_state_transition(stage_paths, transition)
            artifacts.write_stage_checkpoint(stage_paths, checkpoint)
            artifacts.write_run_manifest_cas(
                root=root,
                run_dir=run_dir,
                manifest=target_manifest,
                expected_revision=expected_revision,
                stage_id=stage.stage_id,
                expected_attempt_id=expected_attempt_id,
                prepared=True,
            )
            run_manifest.clear()
            run_manifest.update(target_manifest)
    except RunLockError as exc:
        raise SystemExit(str(exc)) from exc


def _enforce_request_plan_context(
    *,
    root: Path,
    run_dir: Path,
    run_manifest: dict[str, Any],
    workflow,
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    request_plan: dict[str, Any],
    review_bundle_path: str | None,
    dry_run: bool,
) -> None:
    """Apply the same input-plus-output reservation gate to dry and live plans."""

    if request_plan["estimate"]["fits_context"]:
        return
    write_json(
        stage_paths["token_preflight_error"],
        {
            "object": "request_plan_context_error",
            "workflow_id": workflow.workflow_id,
            "stage_id": stage.stage_id,
            "fallback_decision": "failed_closed",
            "attempts": 0,
            "error_message": "request plan exceeds verified model context window",
        },
    )
    _persist_stage_state(
        root=root,
        run_dir=run_dir,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=StageStatus.BLOCKED_PREFLIGHT.value,
        resume_mode=ResumeMode.FRESH_SUBMIT,
        token_preflight={
            "status": "failed_closed",
            "attempts": 0,
            "error_message": "request plan exceeds verified model context window",
            "diagnostics_path": relpath(root, stage_paths["request_plan"]),
        },
        response_json=None,
        review_bundle_path=review_bundle_path,
        allow_prepared_preflight_block=dry_run,
    )
    raise SystemExit(
        f"Stage {stage.stage_id} request plan exceeds the verified model context window."
    )


def _run_stage_validators(
    *,
    root: Path,
    stage: StageDefinition,
    stage_paths: dict[str, Path],
) -> list[dict[str, Any]]:
    reports: list[dict[str, Any]] = []
    for configured in stage.post_output_validators:
        result = run_validator(
            configured.validator_id,
            stage_paths["artifact_md"],
            root=root,
            timeout_seconds=configured.timeout_seconds,
            context={
                "citation_policy": stage.citation_policy,
                "input_manifest_path": relpath(root, stage_paths["input_manifest_json"]),
            },
        )
        result["gate"] = configured.gate
        reports.append(result)
    if reports:
        write_json(
            stage_paths["validator_report"],
            {
                "schema_version": "responses_runner_v2.validator_report.v1",
                "artifact_sha256": sha256_file(stage_paths["artifact_md"]),
                "results": reports,
                "passed": all(
                    item["passed"] or item["gate"] == "advisory" for item in reports
                ),
            },
        )
    blockers = [item for item in reports if item["gate"] == "blocking" and not item["passed"]]
    if blockers:
        raise SystemExit(
            f"Stage {stage.stage_id} failed deterministic validator(s): "
            + ", ".join(item["validator_id"] for item in blockers)
        )
    return reports


def _primary_retry_count(attempt_id: object) -> int | None:
    if not isinstance(attempt_id, str) or not attempt_id.startswith("attempt_"):
        return None
    try:
        return max(0, int(attempt_id.removeprefix("attempt_")) - 1)
    except ValueError:
        return None


def _update_primary_usage_attempt(
    *,
    stage_paths: dict[str, Path],
    attempt_id: object,
    uploads_payload: dict[str, Any] | None,
    response_json: dict[str, Any] | None = None,
    model: object = None,
    status: object = None,
    duration_ms: int | None = None,
    request_wall_ms: int | None = None,
    poll_wall_ms: int | None = None,
    error_type: str | None = None,
    error: str | None = None,
) -> None:
    path = stage_paths["usage_attempt"]
    record = load_json(path, "primary usage attempt") if path.exists() else {}
    record.update(
        {
            "lane": "primary",
            "attempt_id": attempt_id,
            "retry_count": _primary_retry_count(attempt_id),
            "upload_count": len((uploads_payload or {}).get("files", [])),
            "uploaded_bytes": sum(
                int(item.get("bytes", 0))
                for item in (uploads_payload or {}).get("files", [])
                if isinstance(item, dict)
            ),
        }
    )
    if response_json is not None:
        if response_json.get("id") is not None:
            record["response_id"] = response_json.get("id")
        model = response_json.get("model", model)
        status = response_json.get("status", status)
        if "usage" in response_json or "usage" not in record:
            record["usage"] = response_json.get("usage")
    elif "usage" not in record:
        record["usage"] = None
    for key, value in {
        "model": model,
        "status": status,
        "duration_ms": duration_ms,
        "request_wall_ms": request_wall_ms,
        "poll_wall_ms": poll_wall_ms,
    }.items():
        if value is not None or key not in record:
            record[key] = value
    if error_type is not None:
        record["error_type"] = error_type
    if error is not None:
        record["error"] = error
    write_json(path, record)


def _finalize_terminal_response(
    *,
    root: Path,
    run_dir: Path,
    run_manifest: dict[str, Any],
    workflow,
    stage: StageDefinition,
    stage_paths: dict[str, Path],
    runtime: RuntimeOptions,
    client: OpenAIClient,
    response_json: dict[str, Any],
    token_preflight: dict[str, Any],
    review_bundle_path: str | None,
    uploads_payload: dict[str, Any] | None,
    resume_mode: ResumeMode,
) -> tuple[str, dict[str, Any] | None]:
    uploads_path = stage_paths["uploads_json"] if stage_paths["uploads_json"].exists() else None
    _persist_stage_state(
        root=root,
        run_dir=run_dir,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value,
        resume_mode=resume_mode,
        token_preflight=token_preflight,
        response_json=response_json,
        review_bundle_path=review_bundle_path,
        uploads_payload_path=uploads_path,
    )
    try:
        structured_written, sidecar_written, uploads_payload = _write_stage_artifacts_for_response(
            root=root,
            client=client,
            workflow=workflow,
            run_manifest=run_manifest,
            stage=stage,
            stage_paths=stage_paths,
            runtime=runtime,
            response_json=response_json,
            uploads_payload=uploads_payload,
        )
        if stage_paths["artifact_md"].exists():
            _run_stage_validators(root=root, stage=stage, stage_paths=stage_paths)
    except BaseException as exc:
        write_json(
            stage_paths["attempt_dir"] / "finalization.error.json",
            {
                "stage_id": stage.stage_id,
                "attempt_id": artifacts.find_stage_summary(run_manifest, stage.stage_id).get(
                    "current_attempt_id"
                ),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "recorded_at": runner_now().isoformat(),
            },
        )
        _persist_stage_state(
            root=root,
            run_dir=run_dir,
            run_manifest=run_manifest,
            stage=stage,
            stage_paths=stage_paths,
            stage_status=StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value,
            resume_mode=resume_mode,
            token_preflight=token_preflight,
            response_json=response_json,
            review_bundle_path=review_bundle_path,
            structured_output_written=stage_paths["structured_output"].exists(),
            sidecar_written=stage_paths["sidecar_response_json"].exists(),
            uploads_payload_path=uploads_path,
        )
        raise

    if uploads_payload is not None:
        if uploads_payload.get("delete_uploaded_files_on_complete"):
            uploads_payload = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads_payload,
                journal_callback=lambda payload: artifacts.write_uploads_payload(
                    stage_paths,
                    payload,
                ),
            )
        uploads_path = artifacts.write_uploads_payload(stage_paths, uploads_payload)
    _persist_stage_state(
        root=root,
        run_dir=run_dir,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=StageStatus.FINALIZED.value,
        resume_mode=resume_mode,
        token_preflight=token_preflight,
        response_json=response_json,
        review_bundle_path=review_bundle_path,
        structured_output_written=structured_written,
        sidecar_written=sidecar_written,
        uploads_payload_path=uploads_path,
    )
    final_status = _stage_status_from_response(
        response_json,
        stage,
        workflow.next_stage(stage.stage_id) is not None,
        finalized=True,
    )
    _persist_stage_state(
        root=root,
        run_dir=run_dir,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=final_status,
        resume_mode=resume_mode,
        token_preflight=token_preflight,
        response_json=response_json,
        review_bundle_path=review_bundle_path,
        structured_output_written=structured_written,
        sidecar_written=sidecar_written,
        uploads_payload_path=uploads_path,
    )
    _update_primary_usage_attempt(
        stage_paths=stage_paths,
        attempt_id=artifacts.find_stage_summary(run_manifest, stage.stage_id).get(
            "current_attempt_id"
        ),
        uploads_payload=uploads_payload,
        response_json=response_json,
    )
    return final_status, uploads_payload


def run_workflow(
    *,
    workflow_file: str | Path,
    runtime: RuntimeOptions,
    client: OpenAIClient | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Launch the next eligible workflow stage or dry-run request construction."""

    root = root or repo_root()
    workflow = load_workflow_definition(
        workflow_file,
        root=root,
        primary_model_override=runtime.primary_model,
        structural_model_override=runtime.structural_model,
    )
    validate_operator_inputs(
        workflow,
        primary_job_inputs=runtime.primary_job_inputs,
        reference_context=runtime.reference_context,
    )
    run_dir, run_manifest = _load_or_create_run_manifest(root=root, workflow=workflow, runtime=runtime)
    review_bundles = _load_review_bundles(root, runtime.review_bundles)
    current_stage: StageDefinition | None = None

    while True:
        try:
            with run_lock(run_dir):
                run_manifest = artifacts.load_run_manifest(root, run_dir)
                rerun_authorization = None
                if runtime.rerun_archive_manifest is not None:
                    if current_stage is not None:
                        raise SystemExit(
                            "Archive-authorized reruns execute exactly one stage per invocation."
                        )
                    if runtime.stage_id is None:
                        raise SystemExit("Archive-authorized rerun requires an explicit stage_id.")
                    rerun_authorization = _validate_rerun_authorization(
                        root=root,
                        run_dir=run_dir,
                        run_manifest=run_manifest,
                        stage_id=runtime.stage_id,
                        archive_manifest=runtime.rerun_archive_manifest,
                    )
                if current_stage is None:
                    current_stage = _determine_next_stage(
                        workflow=workflow,
                        run_manifest=run_manifest,
                        review_bundles=review_bundles,
                        explicit_stage_id=runtime.stage_id,
                        rerun_authorized_stage_id=(
                            runtime.stage_id if rerun_authorization is not None else None
                        ),
                    )
                stage = current_stage
                if runtime.dry_run:
                    stage_paths = artifacts.build_stage_paths(
                        run_dir / "dry_runs",
                        stage.stage_number,
                        stage.stage_id,
                    )
                else:
                    summary = artifacts.find_stage_summary(run_manifest, stage.stage_id)
                    if summary.get("status") not in RUNNABLE_STAGE_STATES and not (
                        summary.get("status") == StageStatus.FAILED_NO_ARTIFACT.value
                        and rerun_authorization is not None
                    ):
                        raise SystemExit(
                            f"Stage {stage.stage_id} changed state before allocation: "
                            f"{summary.get('status')!r}."
                        )
                    attempt_number, stage_paths = artifacts.allocate_stage_attempt(
                        run_dir,
                        stage.stage_number,
                        stage.stage_id,
                    )
                    _record_attempt_start(
                        root=root,
                        run_manifest=run_manifest,
                        stage=stage,
                        attempt_number=attempt_number,
                        stage_paths=stage_paths,
                        rerun_authorization=rerun_authorization,
                    )
                    artifacts.write_run_manifest(run_dir, run_manifest)
        except RunLockError as exc:
            raise SystemExit(str(exc)) from exc
        static_manifest = load_input_manifest(stage.input_manifest_path, root=root)
        resolved_manifest, consumed_review_bundle_path = _build_stage_runtime_manifest(
            root=root,
            workflow=workflow,
            stage=stage,
            run_manifest=run_manifest,
            static_manifest=static_manifest,
            runtime=runtime,
            review_bundles=review_bundles,
        )
        # A dry run and its later live execution are two renderings of one
        # run-scoped manifest. Anchor the otherwise volatile field to the run.
        resolved_manifest["generated_at"] = run_manifest["started_at"]
        rendered_manifest_md = attachments.render_input_manifest_markdown(resolved_manifest)
        duplicates = attachments.detect_authority_duplicates(resolved_manifest)
        if duplicates:
            raise SystemExit(
                "Unacknowledged attachment duplication across authority roles: "
                + json.dumps(duplicates, sort_keys=True)
            )
        artifacts.write_input_manifests(
            stage_paths=stage_paths,
            resolved_manifest=resolved_manifest,
            rendered_markdown=rendered_manifest_md,
        )
        local_estimate = _local_context_estimate(
            workflow=workflow,
            stage=stage,
            runtime=runtime,
            resolved_manifest=resolved_manifest,
            rendered_manifest_md=rendered_manifest_md,
        )
        write_json(stage_paths["local_context_estimate"], local_estimate)
        if not local_estimate["passed"]:
            write_json(
                stage_paths["token_preflight_error"],
                {
                    "object": "local_context_preflight_error",
                    "workflow_id": workflow.workflow_id,
                    "stage_id": stage.stage_id,
                    "fallback_decision": "fail_closed",
                    "reason": "pre_upload_context_budget_exceeded",
                    "estimate": local_estimate,
                },
            )
            _persist_stage_state(
                root=root,
                run_dir=run_dir,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=StageStatus.BLOCKED_PREFLIGHT.value,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight={
                    "status": "failed_closed",
                    "attempts": 0,
                    "error_message": "pre-upload context budget exceeded",
                    "diagnostics_path": relpath(root, stage_paths["local_context_estimate"]),
                },
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                allow_prepared_preflight_block=runtime.dry_run,
            )
            raise SystemExit(
                f"Stage {stage.stage_id} exceeds its conservative pre-upload context budget."
            )

        uploads_payload: dict[str, Any] | None = None
        request_payload: dict[str, Any]
        staging_dir = stage_paths["attempt_dir"] / "upload_inputs"
        staging_dir.mkdir(parents=True, exist_ok=True)
        prepared_uploads = attachments.prepare_upload_plan(
            root=root,
            resolved_manifest=resolved_manifest,
            input_manifest_markdown_path=stage_paths["input_manifest_md"],
            staging_dir=staging_dir,
        )
        descriptors = [
            {
                "path": item["display_name"],
                "sha256": sha256_file(item["upload_path"]),
                "bytes": item["upload_path"].stat().st_size,
                "authority": item["role_label"],
            }
            for item in prepared_uploads
        ]
        symbolic_by_role: dict[str, list[str]] = {}
        symbolic_manifest_file_id = ""
        for prepared, descriptor in zip(prepared_uploads, descriptors, strict=True):
            symbolic_id = symbolic_file_handle(descriptor["sha256"])
            if prepared["role_label"] == "Stage Input Manifest":
                symbolic_manifest_file_id = symbolic_id
            else:
                symbolic_by_role.setdefault(prepared["role_label"], []).append(symbolic_id)
        symbolic_content, role_blocks = attachments.build_request_input_content(
            task_text=load_text_asset(stage.task_path).strip(),
            input_manifest_file_id=symbolic_manifest_file_id,
            role_to_file_ids=symbolic_by_role,
        )
        symbolic_request_payload = _build_request_payload(
            workflow=workflow,
            stage=stage,
            run_manifest=run_manifest,
            runtime=runtime,
            text_config=_build_text_config(
                root=root,
                workflow=workflow,
                stage=stage,
                runtime=runtime,
            ),
            content=symbolic_content,
            role_blocks=role_blocks,
            tool_settings=_resolve_tool_settings(root, workflow, stage),
        )
        request_plan = build_request_plan(
            text_parts=[
                _build_instructions(workflow, stage),
                load_text_asset(stage.task_path).strip(),
                rendered_manifest_md,
            ],
            files=descriptors,
            context_window=(
                model_context_window(_effective_model(workflow, stage, runtime)) or 1
            ),
            max_output_tokens=_effective_max_output_tokens(workflow, stage, runtime),
            data_handling_policy=ASSURANCE_PROFILES[workflow.assurance_profile]["data_handling"],
            request_store=workflow.request_defaults.store,
            file_purpose=workflow.request_defaults.file_uploads.purpose,
            delete_uploaded_files_on_complete=_delete_uploads_on_complete(workflow, runtime),
            symbolic_request_payload=symbolic_request_payload,
        )
        write_json(stage_paths["request_plan"], request_plan)
        _enforce_request_plan_context(
            root=root,
            run_dir=run_dir,
            run_manifest=run_manifest,
            workflow=workflow,
            stage=stage,
            stage_paths=stage_paths,
            request_plan=request_plan,
            review_bundle_path=consumed_review_bundle_path,
            dry_run=runtime.dry_run,
        )

        if runtime.dry_run:
            request_payload = request_plan["symbolic_request_payload"]
            artifacts.write_request_payload(stage_paths=stage_paths, payload=request_payload)
            stage_status = StageStatus.PREPARED.value
            checkpoint = _build_checkpoint(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight={"status": "pending"},
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                structured_output_written=False,
                sidecar_written=False,
                uploads_payload_path=None,
            )
            artifacts.write_stage_checkpoint(stage_paths, checkpoint)
            _sync_stage_summary(
                root=root,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=stage_status,
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                token_preflight_path=None,
            )
            run_manifest["status"] = RunStatus.CREATED.value
            run_manifest["current_stage_id"] = stage.stage_id
            expected_revision = int(run_manifest.get("revision", 0))
            run_manifest["revision"] = expected_revision + 1
            try:
                with run_lock(run_dir):
                    artifacts.write_run_manifest_cas(
                        root=root,
                        run_dir=run_dir,
                        manifest=run_manifest,
                        expected_revision=expected_revision,
                        stage_id=stage.stage_id,
                        expected_attempt_id=artifacts.find_stage_summary(
                            run_manifest, stage.stage_id
                        ).get("current_attempt_id"),
                    )
            except RunLockError as exc:
                raise SystemExit(str(exc)) from exc
            return {
                "run_dir": relpath(root, run_dir),
                "run_manifest_path": relpath(root, artifacts.run_manifest_path(run_dir)),
                "status": run_manifest["status"],
                "stage_id": stage.stage_id,
            }

        if client is None:
            raise SystemExit("A live OpenAI client is required unless --dry-run is used.")

        with nullcontext(staging_dir):
            _persist_stage_state(
                root=root,
                run_dir=run_dir,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=StageStatus.UPLOADING.value,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight={"status": "pending", "attempts": 0},
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
            )
            try:
                _manifest_file_id, _role_to_file_ids, uploads_payload, resolved_manifest = attachments.upload_prepared_attachments(
                    root=root,
                    client=client,
                    resolved_manifest=resolved_manifest,
                    prepared_uploads=prepared_uploads,
                    purpose=workflow.request_defaults.file_uploads.purpose,
                    file_expiration_policy=_effective_expiration_policy(workflow, runtime),
                    delete_uploaded_files_on_complete=_delete_uploads_on_complete(workflow, runtime),
                    journal_callback=lambda payload: artifacts.write_uploads_payload(
                        stage_paths,
                        payload,
                    ),
                )
            except BaseException as exc:
                if stage_paths["uploads_json"].exists():
                    journal = _load_uploads_payload(stage_paths)
                    if journal is not None:
                        cleaned = attachments.cleanup_uploaded_files(
                            client=client,
                            uploads_payload=journal,
                            journal_callback=lambda payload: artifacts.write_uploads_payload(
                                stage_paths,
                                payload,
                            ),
                        )
                        artifacts.write_uploads_payload(stage_paths, cleaned)
                write_json(
                    stage_paths["attempt_dir"] / "upload.error.json",
                    {
                        "stage_id": stage.stage_id,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "recorded_at": runner_now().isoformat(),
                    },
                )
                _persist_stage_state(
                    root=root,
                    run_dir=run_dir,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=StageStatus.FAILED_NO_ARTIFACT.value,
                    resume_mode=ResumeMode.FRESH_SUBMIT,
                    token_preflight={
                        "status": "pending",
                        "attempts": 0,
                        "error_message": "attachment upload failed before submission",
                    },
                    response_json=None,
                    review_bundle_path=consumed_review_bundle_path,
                    uploads_payload_path=(
                        stage_paths["uploads_json"]
                        if stage_paths["uploads_json"].exists()
                        else None
                    ),
                )
                raise
            artifacts.write_input_manifests(
                stage_paths=stage_paths,
                resolved_manifest=resolved_manifest,
                rendered_markdown=rendered_manifest_md,
            )
            uploads_payload_path = artifacts.write_uploads_payload(stage_paths, uploads_payload)
            uploaded_files = uploads_payload.get("files")
            if not isinstance(uploaded_files, list) or len(uploaded_files) != len(
                request_plan["files"]
            ):
                raise SystemExit("Uploaded attachments do not match the symbolic request plan.")
            provider_file_ids: list[str] = []
            for planned, uploaded in zip(request_plan["files"], uploaded_files, strict=True):
                if (
                    not isinstance(uploaded, dict)
                    or uploaded.get("upload_sha256") != planned["sha256"]
                    or uploaded.get("attachment_role") != planned["authority"]
                    or not isinstance(uploaded.get("file_id"), str)
                    or not uploaded["file_id"]
                ):
                    raise SystemExit("Uploaded attachments do not match the symbolic request plan.")
                provider_file_ids.append(uploaded["file_id"])
            request_payload = materialize_request_payload(
                request_plan["symbolic_request_payload"],
                provider_file_ids,
            )
            try:
                verify_materialized_request(request_plan, request_payload)
            except ValueError as exc:
                raise SystemExit(str(exc)) from exc
            artifacts.write_request_payload(stage_paths=stage_paths, payload=request_payload)
            try:
                token_preflight = _token_preflight_state(
                    root=root,
                    client=client,
                    workflow=workflow,
                    stage=stage,
                    stage_paths=stage_paths,
                    payload=request_payload,
                    runtime=runtime,
                )
            except BaseException:
                _persist_stage_state(
                    root=root,
                    run_dir=run_dir,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=StageStatus.BLOCKED_PREFLIGHT.value,
                    resume_mode=ResumeMode.FRESH_SUBMIT,
                    token_preflight={
                        "status": "failed_closed",
                        "attempts": 0,
                        "error_message": "token preflight failed closed",
                        "diagnostics_path": (
                            relpath(root, stage_paths["token_preflight_error"])
                            if stage_paths["token_preflight_error"].exists()
                            else relpath(root, stage_paths["token_preflight"])
                            if stage_paths["token_preflight"].exists()
                            else None
                        ),
                    },
                    response_json=None,
                    review_bundle_path=consumed_review_bundle_path,
                    uploads_payload_path=uploads_payload_path,
                )
                if uploads_payload is not None:
                    uploads_payload = attachments.cleanup_uploaded_files(
                        client=client,
                        uploads_payload=uploads_payload,
                        journal_callback=lambda payload: artifacts.write_uploads_payload(
                            stage_paths,
                            payload,
                        ),
                    )
                    artifacts.write_uploads_payload(stage_paths, uploads_payload)
                raise
            token_preflight_path = None
            if token_preflight.get("diagnostics_path"):
                token_preflight_path = resolve_under_root(root, token_preflight["diagnostics_path"], must_exist=True)

            _persist_stage_state(
                root=root,
                run_dir=run_dir,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=StageStatus.PREFLIGHT_PASSED.value,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight=token_preflight,
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                uploads_payload_path=uploads_payload_path,
            )

            request_hash = sha256_text(
                json.dumps(request_payload, sort_keys=True, separators=(",", ":"))
            )
            summary = artifacts.find_stage_summary(run_manifest, stage.stage_id)
            attempt_id = str(summary["current_attempt_id"])
            artifacts.write_submission_intent(
                stage_paths,
                {
                    "schema_version": "responses_runner_v2.submission_intent.v1",
                    "run_id": run_manifest["run_id"],
                    "stage_id": stage.stage_id,
                    "attempt_id": attempt_id,
                    "request_sha256": request_hash,
                    "created_at": runner_now().isoformat(),
                },
            )
            summary["request_sha256"] = request_hash
            for attempt in summary.get("attempts", []):
                if attempt.get("attempt_id") == attempt_id:
                    attempt["request_sha256"] = request_hash
                    attempt["submission_intent_path"] = relpath(
                        root,
                        stage_paths["submission_intent"],
                    )
            _persist_stage_state(
                root=root,
                run_dir=run_dir,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=StageStatus.SUBMITTING.value,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight=token_preflight,
                response_json=None,
                review_bundle_path=consumed_review_bundle_path,
                uploads_payload_path=uploads_payload_path,
            )
            _update_primary_usage_attempt(
                stage_paths=stage_paths,
                attempt_id=attempt_id,
                uploads_payload=uploads_payload,
                model=request_payload.get("model"),
                status=StageStatus.SUBMITTING.value,
            )
            request_started = time.monotonic()
            try:
                response_json = client.create_response(request_payload)
            except ApiError as exc:
                request_wall_ms = int((time.monotonic() - request_started) * 1000)
                error_path = stage_paths["attempt_dir"] / "submission.error.json"
                write_json(
                    error_path,
                    {
                        "error": str(exc),
                        "status_code": exc.status_code,
                        "outcome_certainty": exc.outcome_certainty,
                        "request_sha256": request_hash,
                        "recorded_at": runner_now().isoformat(),
                    },
                )
                failed_state = (
                    StageStatus.SUBMISSION_OUTCOME_UNKNOWN.value
                    if exc.outcome_unknown
                    else StageStatus.FAILED_NO_ARTIFACT.value
                )
                _update_primary_usage_attempt(
                    stage_paths=stage_paths,
                    attempt_id=attempt_id,
                    uploads_payload=uploads_payload,
                    model=request_payload.get("model"),
                    status=failed_state,
                    request_wall_ms=request_wall_ms,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                _persist_stage_state(
                    root=root,
                    run_dir=run_dir,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=failed_state,
                    resume_mode=ResumeMode.FRESH_SUBMIT,
                    token_preflight=token_preflight,
                    response_json=None,
                    review_bundle_path=consumed_review_bundle_path,
                    uploads_payload_path=uploads_payload_path,
                )
                if not exc.outcome_unknown and uploads_payload is not None:
                    uploads_payload = attachments.cleanup_uploaded_files(
                        client=client,
                        uploads_payload=uploads_payload,
                        journal_callback=lambda payload: artifacts.write_uploads_payload(
                            stage_paths,
                            payload,
                        ),
                    )
                    artifacts.write_uploads_payload(stage_paths, uploads_payload)
                raise SystemExit(
                    f"Stage {stage.stage_id} submission failed with {failed_state}: {exc}"
                ) from exc
            request_wall_ms = int((time.monotonic() - request_started) * 1000)
            for attempt in summary.get("attempts", []):
                if attempt.get("attempt_id") == attempt_id:
                    attempt["request_wall_ms"] = request_wall_ms
                    attempt["response_id"] = response_json.get("id")
            _update_primary_usage_attempt(
                stage_paths=stage_paths,
                attempt_id=attempt_id,
                uploads_payload=uploads_payload,
                response_json=response_json,
                request_wall_ms=request_wall_ms,
            )
            artifacts.write_response_latest(stage_paths, response_json)
            has_next_stage = workflow.next_stage(stage.stage_id) is not None
            _persist_stage_state(
                root=root,
                run_dir=run_dir,
                run_manifest=run_manifest,
                stage=stage,
                stage_paths=stage_paths,
                stage_status=StageStatus.SUBMITTED.value,
                resume_mode=ResumeMode.FRESH_SUBMIT,
                token_preflight=token_preflight,
                response_json=response_json,
                review_bundle_path=consumed_review_bundle_path,
                uploads_payload_path=uploads_payload_path,
            )
            stage_status = _stage_status_from_response(response_json, stage, has_next_stage)
            if stage_status != StageStatus.SUBMITTED.value:
                _persist_stage_state(
                    root=root,
                    run_dir=run_dir,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=stage_status,
                    resume_mode=ResumeMode.FRESH_SUBMIT,
                    token_preflight=token_preflight,
                    response_json=response_json,
                    review_bundle_path=consumed_review_bundle_path,
                    uploads_payload_path=uploads_payload_path,
                )

            poll_wall_ms = 0
            if runtime.wait and str(response_json.get("status")) not in TERMINAL_RESPONSE_STATUSES:
                poll_started = time.monotonic()
                response_json = client.wait_for_terminal_response(
                    str(response_json["id"]),
                    poll_interval=runtime.poll_interval,
                    max_wait_seconds=runtime.max_wait_seconds,
                    checkpoint_callback=lambda polled: artifacts.write_response_latest(stage_paths, polled),
                )
                poll_wall_ms = int((time.monotonic() - poll_started) * 1000)
                artifacts.write_response_latest(stage_paths, response_json)

            if str(response_json.get("status")) in TERMINAL_RESPONSE_STATUSES:
                final_stage_status, uploads_payload = _finalize_terminal_response(
                    root=root,
                    run_dir=run_dir,
                    run_manifest=run_manifest,
                    workflow=workflow,
                    stage=stage,
                    stage_paths=stage_paths,
                    runtime=runtime,
                    client=client,
                    response_json=response_json,
                    token_preflight=token_preflight,
                    review_bundle_path=consumed_review_bundle_path,
                    uploads_payload=uploads_payload,
                    resume_mode=ResumeMode.FRESH_SUBMIT,
                )
                _update_primary_usage_attempt(
                    stage_paths=stage_paths,
                    attempt_id=attempt_id,
                    uploads_payload=uploads_payload,
                    response_json=response_json,
                    duration_ms=request_wall_ms + poll_wall_ms,
                    request_wall_ms=request_wall_ms,
                    poll_wall_ms=poll_wall_ms,
                )

                if (
                    final_stage_status == StageStatus.COMPLETED.value
                    and has_next_stage
                    and stage.gate == GateType.AUTO
                    and runtime.stage_id is None
                    and runtime.wait
                ):
                    current_stage = workflow.next_stage(stage.stage_id)
                    if current_stage is None:
                        break
                    continue
            return {
                "run_dir": relpath(root, run_dir),
                "run_manifest_path": relpath(root, artifacts.run_manifest_path(run_dir)),
                "status": run_manifest["status"],
                "stage_id": stage.stage_id,
            }


def _load_existing_workflow_for_run(root: Path, run_manifest: dict[str, Any]):
    run_dir = resolve_under_root(root, run_manifest["run_dir"], must_exist=True)
    if run_manifest.get("schema_version") != "responses_runner_v2.run_manifest.v2":
        raise SystemExit(
            "Live v1 runs cannot be resumed under v2 semantics because their original contract "
            "and attempt identity were not frozen. Preserve the evidence and start a new v2 run."
        )
    load_and_verify_run_contract(root=root, run_dir=run_dir)
    return load_workflow_definition(
        run_manifest["workflow_manifest_path"],
        root=root,
    )


def _stage_paths_for_summary(
    run_dir: Path,
    stage: StageDefinition,
    summary: dict[str, Any],
) -> dict[str, Path]:
    attempt_id = summary.get("current_attempt_id")
    if isinstance(attempt_id, str) and attempt_id.startswith("attempt_"):
        try:
            attempt_number = int(attempt_id.removeprefix("attempt_"))
        except ValueError as exc:
            raise SystemExit(f"Invalid current_attempt_id {attempt_id!r}.") from exc
        return artifacts.build_stage_paths(
            run_dir,
            stage.stage_number,
            stage.stage_id,
            attempt_number=attempt_number,
            create=False,
        )
    if summary.get("status") in {
        StageStatus.PREPARED.value,
        StageStatus.COMPLETED.value,
        StageStatus.WAITING_FOR_REVIEW.value,
        StageStatus.FAILED.value,
    }:
        return artifacts.build_stage_paths(
            run_dir,
            stage.stage_number,
            stage.stage_id,
            create=False,
        )
    raise SystemExit(f"Stage {stage.stage_id} has no durable current attempt identity.")


def _load_uploads_payload(stage_paths: dict[str, Path]) -> dict[str, Any] | None:
    if not stage_paths["uploads_json"].exists():
        return None
    return load_json(stage_paths["uploads_json"], "uploads payload")


def _merge_uploads_payloads(
    base_payload: dict[str, Any] | None,
    extra_payload: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if base_payload is None:
        return extra_payload
    if extra_payload is None:
        return base_payload
    merged = dict(base_payload)
    merged["delete_uploaded_files_on_complete"] = bool(
        base_payload.get("delete_uploaded_files_on_complete")
        or extra_payload.get("delete_uploaded_files_on_complete")
    )
    if merged.get("file_expiration_policy") is None and extra_payload.get("file_expiration_policy") is not None:
        merged["file_expiration_policy"] = extra_payload.get("file_expiration_policy")
    merged["files"] = list(base_payload.get("files", [])) + list(extra_payload.get("files", []))
    return merged


def resume_stage(
    *,
    run_dir: str | Path,
    stage_id: str,
    wait: bool,
    poll_interval: float,
    max_wait_seconds: float | None,
    client: OpenAIClient,
    root: Path | None = None,
    refresh_status_only: bool = False,
) -> dict[str, Any]:
    """Resume or finalize a previously submitted stage from its stored response id."""

    root = root or repo_root()
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    try:
        with run_lock(resolved_run_dir):
            run_manifest = artifacts.load_run_manifest(root, resolved_run_dir)
            run_manifest = _reconcile_stage_state_transitions(
                root=root,
                run_dir=resolved_run_dir,
                run_manifest=run_manifest,
            )
    except RunLockError as exc:
        raise SystemExit(str(exc)) from exc
    workflow = _load_existing_workflow_for_run(root, run_manifest)
    effective_runtime = runtime_from_contract(
        load_and_verify_run_contract(root=root, run_dir=resolved_run_dir),
        wait=wait,
        poll_interval=poll_interval,
        max_wait_seconds=max_wait_seconds,
    )
    stage = workflow.stage(stage_id)
    stage_summary = artifacts.find_stage_summary(run_manifest, stage_id)
    stage_paths = _stage_paths_for_summary(resolved_run_dir, stage, stage_summary)
    local_state = str(stage_summary.get("status", ""))
    finalized_local_states = {
        StageStatus.FINALIZED.value,
        StageStatus.COMPLETED.value,
        StageStatus.WAITING_FOR_REVIEW.value,
        StageStatus.FAILED_COMPLETE.value,
        StageStatus.FAILED_NO_ARTIFACT.value,
        StageStatus.CANCELLED.value,
        StageStatus.INCOMPLETE.value,
    }
    if local_state == StageStatus.SUBMISSION_OUTCOME_UNKNOWN.value:
        raise SystemExit(
            f"Stage {stage_id} has an unknown submission outcome and cannot be resumed or resubmitted "
            "without operator reconciliation."
        )
    if local_state not in {
        StageStatus.SUBMITTED.value,
        StageStatus.IN_PROGRESS.value,
        StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value,
        StageStatus.CANCELLING.value,
        # Artifact finalization is durable before the final run/stage transition.
        # A crash in that narrow window must resume the transition without
        # regenerating artifacts or issuing another response request.
        StageStatus.FINALIZED.value,
    } and not (refresh_status_only and local_state in finalized_local_states):
        raise SystemExit(f"Stage {stage_id} is {local_state!r} and is not resumable.")
    response_id = stage_summary.get("response_id")
    if not response_id:
        raise SystemExit(f"Stage {stage_id} has no stored response_id to resume.")
    response_json = client.retrieve_response(str(response_id))
    artifacts.write_response_latest(stage_paths, response_json)
    if wait and str(response_json.get("status")) not in TERMINAL_RESPONSE_STATUSES:
        response_json = client.wait_for_terminal_response(
            str(response_id),
            poll_interval=poll_interval,
            max_wait_seconds=max_wait_seconds,
            checkpoint_callback=lambda polled: artifacts.write_response_latest(stage_paths, polled),
        )
        artifacts.write_response_latest(stage_paths, response_json)
    uploads_payload = _load_uploads_payload(stage_paths)
    _update_primary_usage_attempt(
        stage_paths=stage_paths,
        attempt_id=stage_summary.get("current_attempt_id"),
        uploads_payload=uploads_payload,
        response_json=response_json,
    )
    has_next_stage = workflow.next_stage(stage.stage_id) is not None
    structured_output_written = stage_paths["structured_output"].exists()
    sidecar_written = stage_paths["sidecar_response_json"].exists()
    token_preflight = {"status": "pending"}
    if stage_paths["token_preflight"].exists():
        token_payload = load_json(stage_paths["token_preflight"], "token preflight")
        token_preflight = {
            "status": "succeeded",
            "attempts": 1,
            "input_tokens": token_payload.get("input_tokens"),
            "diagnostics_path": relpath(root, stage_paths["token_preflight"]),
        }
    elif stage_paths["token_preflight_error"].exists():
        token_payload = load_json(stage_paths["token_preflight_error"], "token preflight error")
        token_preflight = {
            "status": token_payload.get("fallback_decision", "failed_closed"),
            "attempts": int(token_payload.get("attempts", 0)),
            "error_message": token_payload.get("error_message"),
            "diagnostics_path": relpath(root, stage_paths["token_preflight_error"]),
        }
    remote_terminal = str(response_json.get("status")) in TERMINAL_RESPONSE_STATUSES
    if remote_terminal and not refresh_status_only and local_state not in finalized_local_states:
        _finalize_terminal_response(
            root=root,
            run_dir=resolved_run_dir,
            run_manifest=run_manifest,
            workflow=workflow,
            stage=stage,
            stage_paths=stage_paths,
            runtime=effective_runtime,
            client=client,
            response_json=response_json,
            token_preflight=token_preflight,
            review_bundle_path=stage_summary.get("review_bundle_path"),
            uploads_payload=uploads_payload,
            resume_mode=ResumeMode.RESUME_RESPONSE_ID,
        )
        return {
            "run_dir": relpath(root, resolved_run_dir),
            "run_manifest_path": relpath(root, artifacts.run_manifest_path(resolved_run_dir)),
            "status": run_manifest["status"],
            "stage_id": stage_id,
        }
    stage_status = _stage_status_from_response(
        response_json,
        stage,
        has_next_stage,
        finalized=(
            local_state in finalized_local_states
            or not refresh_status_only
            and str(response_json.get("status")) in TERMINAL_RESPONSE_STATUSES
        ),
    )
    if local_state == StageStatus.CANCELLING.value and not remote_terminal:
        stage_status = StageStatus.CANCELLING.value
    _persist_stage_state(
        root=root,
        run_dir=resolved_run_dir,
        run_manifest=run_manifest,
        stage=stage,
        stage_paths=stage_paths,
        stage_status=stage_status,
        resume_mode=(
            ResumeMode.REFRESH_STATUS_ONLY if refresh_status_only else ResumeMode.RESUME_RESPONSE_ID
        ),
        token_preflight=token_preflight,
        response_json=response_json,
        review_bundle_path=stage_summary.get("review_bundle_path"),
        structured_output_written=structured_output_written,
        sidecar_written=sidecar_written,
        uploads_payload_path=stage_paths["uploads_json"] if stage_paths["uploads_json"].exists() else None,
    )
    return {
        "run_dir": relpath(root, resolved_run_dir),
        "run_manifest_path": relpath(root, artifacts.run_manifest_path(resolved_run_dir)),
        "status": run_manifest["status"],
        "stage_id": stage_id,
    }


def refresh_stage(
    *,
    run_dir: str | Path,
    stage_id: str,
    client: OpenAIClient,
    root: Path | None = None,
) -> dict[str, Any]:
    """Refresh remote status for a stage without performing local finalization."""

    return resume_stage(
        run_dir=run_dir,
        stage_id=stage_id,
        wait=False,
        poll_interval=0.0,
        max_wait_seconds=None,
        client=client,
        root=root,
        refresh_status_only=True,
    )


def cancel_stage(
    *,
    run_dir: str | Path,
    stage_id: str,
    client: OpenAIClient,
    root: Path | None = None,
) -> dict[str, Any]:
    """Cancel a known live response exactly once, then refresh/finalize it."""

    root = root or repo_root()
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    should_cancel = False
    try:
        with run_lock(resolved_run_dir):
            run_manifest = artifacts.load_run_manifest(root, resolved_run_dir)
            workflow = _load_existing_workflow_for_run(root, run_manifest)
            stage = workflow.stage(stage_id)
            summary = artifacts.find_stage_summary(run_manifest, stage_id)
            stage_paths = _stage_paths_for_summary(resolved_run_dir, stage, summary)
            local_state = str(summary.get("status", ""))
            if local_state == StageStatus.SUBMISSION_OUTCOME_UNKNOWN.value:
                raise SystemExit(
                    "Cancellation is not reconciliation: this stage has an unknown submission outcome."
                )
            if local_state in {
                StageStatus.CANCELLED.value,
                StageStatus.COMPLETED.value,
                StageStatus.WAITING_FOR_REVIEW.value,
                StageStatus.FAILED_COMPLETE.value,
                StageStatus.FAILED_NO_ARTIFACT.value,
                StageStatus.INCOMPLETE.value,
            }:
                return {
                    "run_dir": relpath(root, resolved_run_dir),
                    "run_manifest_path": relpath(
                        root,
                        artifacts.run_manifest_path(resolved_run_dir),
                    ),
                    "status": run_manifest["status"],
                    "stage_id": stage_id,
                }
            response_id = summary.get("response_id")
            if not isinstance(response_id, str) or not response_id:
                raise SystemExit(f"Stage {stage_id} has no known response_id to cancel.")
            if local_state == StageStatus.REMOTE_TERMINAL_PENDING_FINALIZATION.value:
                should_cancel = False
            elif stage_paths["cancellation_intent"].exists():
                should_cancel = False
            elif local_state in {StageStatus.SUBMITTED.value, StageStatus.IN_PROGRESS.value}:
                artifacts.write_cancellation_intent(
                    stage_paths,
                    {
                        "schema_version": "responses_runner_v2.cancellation_intent.v1",
                        "run_id": run_manifest["run_id"],
                        "stage_id": stage_id,
                        "attempt_id": summary.get("current_attempt_id"),
                        "response_id": response_id,
                        "created_at": runner_now().isoformat(),
                    },
                )
                _persist_stage_state(
                    root=root,
                    run_dir=resolved_run_dir,
                    run_manifest=run_manifest,
                    stage=stage,
                    stage_paths=stage_paths,
                    stage_status=StageStatus.CANCELLING.value,
                    resume_mode=ResumeMode.RESUME_RESPONSE_ID,
                    token_preflight={"status": "previously_completed", "attempts": 0},
                    response_json=None,
                    review_bundle_path=summary.get("review_bundle_path"),
                    uploads_payload_path=(
                        stage_paths["uploads_json"]
                        if stage_paths["uploads_json"].exists()
                        else None
                    ),
                    lock_already_held=True,
                )
                should_cancel = True
            else:
                raise SystemExit(f"Stage {stage_id} in state {local_state!r} cannot be cancelled.")
    except RunLockError as exc:
        raise SystemExit(str(exc)) from exc

    if should_cancel:
        try:
            cancellation = client.cancel_response(response_id)
        except ApiError as exc:
            # A terminal race is reconciled by retrieve; an ambiguous cancel is
            # never blindly repeated.
            if exc.outcome_unknown:
                raise SystemExit(
                    f"Cancellation outcome is unknown for response {response_id}; refresh manually."
                ) from exc
            cancellation = {"error": str(exc), "status_code": exc.status_code}
        artifacts.write_cancellation_result(stage_paths, cancellation)
    return resume_stage(
        run_dir=resolved_run_dir,
        stage_id=stage_id,
        wait=False,
        poll_interval=0.0,
        max_wait_seconds=None,
        client=client,
        root=root,
    )


def recover_uploads(
    *,
    run_dir: str | Path,
    stage_id: str,
    client: OpenAIClient,
    attempt_number: int | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    """Idempotently retry deletion of every known upload in one attempt journal."""

    root = root or repo_root()
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    try:
        with run_lock(resolved_run_dir):
            manifest = artifacts.load_run_manifest(root, resolved_run_dir)
            workflow = _load_existing_workflow_for_run(root, manifest)
            stage = workflow.stage(stage_id)
            summary = artifacts.find_stage_summary(manifest, stage_id)
            if attempt_number is None:
                current = str(summary.get("current_attempt_id", ""))
                if not current.startswith("attempt_"):
                    raise SystemExit(f"Stage {stage_id} has no current v2 attempt.")
                attempt_number = int(current.removeprefix("attempt_"))
            stage_paths = artifacts.build_stage_paths(
                resolved_run_dir,
                stage.stage_number,
                stage.stage_id,
                attempt_number=attempt_number,
                create=False,
            )
            uploads = _load_uploads_payload(stage_paths)
            if uploads is None:
                raise SystemExit(f"No upload journal exists for stage {stage_id} attempt {attempt_number}.")
            cleaned = attachments.cleanup_uploaded_files(
                client=client,
                uploads_payload=uploads,
                journal_callback=lambda payload: artifacts.write_uploads_payload(
                    stage_paths,
                    payload,
                ),
            )
            path = artifacts.write_uploads_payload(stage_paths, cleaned)
    except RunLockError as exc:
        raise SystemExit(str(exc)) from exc
    return {"uploads_path": relpath(root, path), "stage_id": stage_id}


def usage_report(*, run_dir: str | Path, root: Path | None = None) -> dict[str, Any]:
    """Aggregate durable primary and sidecar usage without estimating price."""

    root = root or repo_root()
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    attempts: list[dict[str, Any]] = []
    for path in sorted(resolved_run_dir.rglob("usage_attempt.json")):
        payload = load_json(path, "usage attempt")
        attempts.append(payload)
    for path in sorted(resolved_run_dir.rglob("*.attempts.json")):
        payload = load_json(path, "sidecar attempts")
        for index, item in enumerate(payload.get("attempts", []), start=1):
            if not isinstance(item, dict):
                continue
            attempts.append(
                {
                    "attempt_id": item.get("attempt_id") or f"{path.parent.name}-sidecar-{index}",
                    "lane": item.get("lane") or "sidecar",
                    "model": item.get("model"),
                    "status": item.get("status"),
                    "duration_ms": item.get("duration_ms"),
                    "request_wall_ms": item.get("request_wall_ms"),
                    "poll_wall_ms": item.get("poll_wall_ms"),
                    "retry_count": item.get("retry_count"),
                    "upload_count": item.get("upload_count"),
                    "uploaded_bytes": item.get("uploaded_bytes"),
                    "usage": item.get("usage"),
                }
            )
    report = build_usage_report(attempts)
    report_path = resolved_run_dir / "usage_report.json"
    write_json(report_path, report)
    return {
        "usage_report_path": relpath(root, report_path),
        "attempt_count": len(attempts),
    }
