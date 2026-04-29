from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from .contracts import (
    DEFAULT_PRIMARY_MODEL,
    DEFAULT_STRUCTURAL_MODEL,
    FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION,
    REVIEW_DECISION_SCHEMA_VERSION,
    SUPERVISOR_SESSION_SCHEMA_VERSION,
    RuntimeOptions,
    load_json,
    normalize_slug,
    relpath,
    resolve_under_root,
    runner_now,
    sha256_file,
    sha256_text,
    write_json,
    write_text,
)
from .review_bundle import create_review_bundle
from .workflow import run_workflow
from . import artifacts as runner_artifacts
from . import supervisor_agents
from . import supervisor_artifacts
from . import supervisor_policies

OPERATOR_BOUNDARY = "headless_discrete_codex_exec_with_deterministic_supervisor_cli"
REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL = {"scaffold", "stage_output", "final_packet", "recovery"}


def _load_session_and_path(root: Path, session_ref: str | Path) -> tuple[dict[str, Any], Path]:
    path = supervisor_artifacts.session_dir(root, session_ref)
    return supervisor_artifacts.load_session(root, path), path


def _write_session(root: Path, session_path: Path, session: dict[str, Any]) -> dict[str, Any]:
    return supervisor_artifacts.write_session(root, session_path, session)


def _default_policy() -> dict[str, Any]:
    return {
        "review_agents": {
            "operator_codex": {"command": "codex exec", "required": True},
            "codex_review_agent": {"command": "codex exec", "required": True, "read_only": True},
            "claude_review_agent": {"command": "claude --bare -p", "required": True, "read_only": True},
        },
        "retry_budgets": {
            "failed_no_artifact": 1,
            "incomplete_output_limit": 0,
            "blocked_token_preflight": 0,
            "long_running_monitoring_anomaly_duplicate_submit": 0,
        },
        "monitoring": {
            "poll_interval_seconds": 300,
            "stale_after_seconds": 21600,
            "max_refresh_attempts": 288,
            "max_resume_attempts": 3,
            "no_duplicate_submit": True,
        },
        "human_pause_rules": {
            "post_clarification_pauses_are_exception_only": True,
            "require_trigger_artifact_decision_safe_continuation": True,
        },
        "read_only_enforcement_method": "workspace_snapshot_excluding_local_artifacts",
    }


def _default_model_defaults() -> dict[str, Any]:
    return {
        "primary": DEFAULT_PRIMARY_MODEL,
        "structural": DEFAULT_STRUCTURAL_MODEL,
        "prompt_cache_retention": "24h",
        "max_output_tokens": 128000,
        "primary_reasoning_effort": "xhigh",
        "structural_reasoning_effort": "high",
        "primary_verbosity": "high",
        "structural_verbosity": "medium",
    }


def create_session(*, root: Path, clarified_task_brief: str | Path, summary: str, session_id: str | None = None) -> dict[str, Any]:
    brief_path = resolve_under_root(root, clarified_task_brief, must_exist=True)
    created_id, session_path = supervisor_artifacts.create_session_dir(root, session_id)
    now = runner_now().isoformat()
    session = {
        "schema_version": SUPERVISOR_SESSION_SCHEMA_VERSION,
        "supervisor_session_id": created_id,
        "created_at": now,
        "updated_at": now,
        "workspace_root": str(root.resolve()),
        "operator_boundary": OPERATOR_BOUNDARY,
        "status": "clarified",
        "current_phase": "scaffold",
        "clarified_task_brief": {
            "path": relpath(root, brief_path),
            "sha256": sha256_file(brief_path),
            "accepted_at": now,
            "summary": summary,
        },
        "policy": _default_policy(),
        "model_defaults": _default_model_defaults(),
        "retry_budget": {"failed_no_artifact": 1, "incomplete_output_limit_auto_progress": 0},
        "monitoring_policy": _default_policy()["monitoring"],
        "scaffold_versions": [],
        "dry_run_validations": [],
        "runs": [],
        "stage_outcomes": [],
        "monitoring_events": [],
        "review_cycles": [],
        "review_agent_invocations": [],
        "consolidations": [],
        "operator_acceptance_records": [],
        "human_pauses": [],
        "archives": [],
        "approved_review_bundles": [],
        "final_bundle": None,
        "validation_results": [],
        "command_log": [],
        "errors": [],
    }
    return _write_session(root, session_path, session)


def stage_scaffold(*, root: Path, session_ref: str | Path, scaffold_path: str | Path, created_by: str = "operator_codex") -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    version_id = normalize_slug(f"scaffold_{len(session['scaffold_versions']) + 1:03d}")
    destination = session_path / "scaffolds" / version_id / "source"
    staged_path = supervisor_artifacts.copy_into_scaffold_version(root, scaffold_path, destination)
    hash_manifest_path = supervisor_artifacts.hash_manifest(root, destination, session_path / "scaffolds" / version_id / "hash_manifest.json")
    record = {
        "version_id": version_id,
        "path": staged_path,
        "hash_manifest_path": hash_manifest_path,
        "created_at": runner_now().isoformat(),
        "created_by": created_by,
        "dry_run_artifacts": [],
        "approval_status": "staged",
    }
    session["scaffold_versions"].append(record)
    session["status"] = "scaffold_staged"
    session["current_phase"] = "scaffold_review"
    _write_session(root, session_path, session)
    return record


def dry_run_scaffold(*, root: Path, session_ref: str | Path, workflow_file: str | Path, run_name: str = "supervisor-scaffold-dry-run") -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    output_root = Path(relpath(root, session_path / "dry_runs"))
    command = ["python3", "automation/run_responses_v2.py", "run", "--root", str(root), "--workflow-file", str(workflow_file), "--dry-run"]
    started_at = runner_now().isoformat()
    status = "passed"
    exit_code = 0
    result: dict[str, Any] = {}
    error_message = None
    try:
        result = run_workflow(
            workflow_file=workflow_file,
            runtime=RuntimeOptions(run_name=run_name, output_root=output_root, dry_run=True),
            root=root,
        )
    except SystemExit as exc:
        status = "failed"
        exit_code = 1
        error_message = str(exc)
    record = {
        "validation_id": normalize_slug(f"dry_run_{len(session['dry_run_validations']) + 1:03d}"),
        "command": command,
        "started_at": started_at,
        "completed_at": runner_now().isoformat(),
        "exit_code": exit_code,
        "status": status,
        "result": result,
        "error_message": error_message,
    }
    session["dry_run_validations"].append(record)
    if session["scaffold_versions"]:
        session["scaffold_versions"][-1]["dry_run_artifacts"].append(record)
        if status == "passed":
            session["scaffold_versions"][-1]["approval_status"] = "dry_run_passed"
    session["status"] = "scaffold_reviewing" if status == "passed" else "blocked"
    session["current_phase"] = "scaffold_review"
    if status != "passed":
        session["errors"].append(
            {
                "error_id": record["validation_id"],
                "severity": "blocking",
                "message": error_message or "Dry-run validation failed.",
                "related_artifact": str(workflow_file),
                "recovery_action": "repair_scaffold",
            }
        )
    _write_session(root, session_path, session)
    return record


def create_review_cycle(*, root: Path, session_ref: str | Path, review_cycle_id: str, review_kind: str, artifacts_reviewed: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    if any(cycle["review_cycle_id"] == review_cycle_id for cycle in session["review_cycles"]):
        raise SystemExit(f"Review cycle already exists: {review_cycle_id}")
    cycle = {
        "review_cycle_id": review_cycle_id,
        "review_kind": review_kind,
        "artifacts_reviewed": artifacts_reviewed or [],
        "operator_provisional_record": None,
        "review_agent_outputs": {},
        "consolidation": None,
        "acceptance_status": "pending",
        "created_at": runner_now().isoformat(),
    }
    session["review_cycles"].append(cycle)
    _write_session(root, session_path, session)
    return cycle


def _find_cycle(session: dict[str, Any], review_cycle_id: str) -> dict[str, Any]:
    for cycle in session["review_cycles"]:
        if cycle["review_cycle_id"] == review_cycle_id:
            return cycle
    raise SystemExit(f"Unknown review cycle: {review_cycle_id}")


def _require_operator_provisional(cycle: dict[str, Any]) -> str:
    value = cycle.get("operator_provisional_record")
    if not isinstance(value, str) or not value:
        raise SystemExit("Review cycle cannot progress without required operator_provisional_record.")
    return value


def invoke_operator(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    review_kind: str,
    job_json: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    if not any(cycle["review_cycle_id"] == review_cycle_id for cycle in session["review_cycles"]):
        create_review_cycle(root=root, session_ref=session_ref, review_cycle_id=review_cycle_id, review_kind=review_kind)
        session, session_path = _load_session_and_path(root, session_ref)
    output_dir = output_dir or (session_path / "review_cycles" / review_cycle_id / "operator")
    result = supervisor_agents.invoke_operator_codex(
        root=root,
        review_kind=review_kind,
        review_cycle_id=review_cycle_id,
        supervisor_session_id=session["supervisor_session_id"],
        job=job_json,
        output_dir=output_dir,
    )
    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    cycle["operator_provisional_record"] = result.decision_path
    session["review_agent_invocations"].append(
        {
            "command_id": result.command_id,
            "actor_role": result.actor_role,
            "argv": result.command["argv"],
            "cwd": result.command["cwd"],
            "started_at": result.command["started_at"],
            "completed_at": result.command["completed_at"],
            "exit_code": result.command["exit_code"],
            "stdout_path": result.stdout_path,
            "stderr_path": result.stderr_path,
            "read_only_result": result.read_only_check,
        }
    )
    _write_session(root, session_path, session)
    return {"operator_review": result.decision_path}


def invoke_reviewers(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    review_kind: str,
    job_json: str | Path,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    if review_kind in REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL:
        _require_operator_provisional(cycle)
    output_dir = output_dir or (session_path / "review_cycles" / review_cycle_id / "agents")
    codex = supervisor_agents.invoke_codex_review_agent(
        root=root,
        review_kind=review_kind,
        review_cycle_id=review_cycle_id,
        supervisor_session_id=session["supervisor_session_id"],
        job=job_json,
        output_dir=output_dir,
    )
    claude = supervisor_agents.invoke_claude_review_agent(
        root=root,
        review_kind=review_kind,
        review_cycle_id=review_cycle_id,
        supervisor_session_id=session["supervisor_session_id"],
        job=job_json,
        output_dir=output_dir,
    )
    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    cycle["review_agent_outputs"]["codex_review_agent"] = codex.decision_path
    cycle["review_agent_outputs"]["claude_review_agent"] = claude.decision_path
    for result in (codex, claude):
        session["review_agent_invocations"].append(
            {
                "command_id": result.command_id,
                "actor_role": result.actor_role,
                "argv": result.command["argv"],
                "cwd": result.command["cwd"],
                "started_at": result.command["started_at"],
                "completed_at": result.command["completed_at"],
                "exit_code": result.command["exit_code"],
                "stdout_path": result.stdout_path,
                "stderr_path": result.stderr_path,
                "read_only_result": result.read_only_check,
            }
        )
    _write_session(root, session_path, session)
    return {"codex_review": codex.decision_path, "claude_review": claude.decision_path}


def _load_decision(root: Path, path: str | Path, label: str) -> dict[str, Any]:
    payload = load_json(resolve_under_root(root, path, must_exist=True), label)
    supervisor_agents.validate_review_decision(payload)
    return payload


def _recommendation_key(recommendation: dict[str, Any]) -> str:
    return json.dumps(
        {
            "recommendation": recommendation.get("recommendation"),
            "affected_artifacts": recommendation.get("affected_artifacts") if isinstance(recommendation.get("affected_artifacts"), list) else [],
        },
        sort_keys=True,
    )


def consolidate_reviews(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    codex_review: str | Path,
    claude_review: str | Path,
    output: str | Path,
    operator_review: str | Path | None = None,
) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    cycle = _find_cycle(session, review_cycle_id)
    if cycle.get("review_kind") in REVIEW_KINDS_REQUIRING_OPERATOR_PROVISIONAL:
        operator_review = operator_review or _require_operator_provisional(cycle)
    operator = _load_decision(root, operator_review, "operator provisional review") if operator_review else None
    codex = _load_decision(root, codex_review, "codex review")
    claude = _load_decision(root, claude_review, "claude review")

    output_json_path = resolve_under_root(root, output, must_exist=False)
    output_md_path = output_json_path.with_suffix(".md")
    seen: set[str] = set()
    consolidated_recommendations: list[dict[str, Any]] = []
    for source in [item for item in (operator, codex, claude) if item is not None]:
        source_agent = str(source.get("actor_role"))
        for original in source.get("recommendations", []):
            if not isinstance(original, dict):
                continue
            rec = dict(original)
            rec.setdefault("source_agent", source_agent)
            rec.setdefault("recommendation_id", f"{source_agent}_{len(consolidated_recommendations) + 1:03d}")
            key = _recommendation_key(rec)
            if key in seen:
                rec["consolidation_recommendation"] = "duplicate"
            elif not rec.get("evidence"):
                rec["consolidation_recommendation"] = "needs_operator_judgment"
            else:
                rec["consolidation_recommendation"] = "accepted_for_operator_review"
            seen.add(key)
            rec.pop("operator_decision", None)
            consolidated_recommendations.append(rec)

    sources = [item for item in (operator, codex, claude) if item is not None]
    blocking_issues: list[Any] = []
    non_blocking: list[Any] = []
    unsupported_claims: list[Any] = []
    evidence: list[Any] = []
    reviewed_artifacts: list[Any] = []
    missing_artifacts: list[Any] = []
    for source in sources:
        blocking_issues.extend(source.get("blocking_issues", []))
        non_blocking.extend(source.get("non_blocking_improvements", []))
        unsupported_claims.extend(source.get("unsupported_claims", []))
        evidence.extend(source.get("evidence", []))
        reviewed_artifacts.extend(source.get("reviewed_artifacts", []))
        missing_artifacts.extend(source.get("missing_artifacts", []))

    decision = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "decision_id": f"consolidation_{review_cycle_id}",
        "created_at": runner_now().isoformat(),
        "supervisor_session_id": session["supervisor_session_id"],
        "workflow_id": codex.get("workflow_id") or claude.get("workflow_id"),
        "run_id": codex.get("run_id") or claude.get("run_id"),
        "stage_id": codex.get("stage_id") or claude.get("stage_id"),
        "review_cycle_id": review_cycle_id,
        "review_kind": "consolidation",
        "actor_role": "consolidation_pass",
        "agent_command_id": None,
        "status": "succeeded",
        "approval_decision": "do_not_approve" if blocking_issues else "approve_with_conditions",
        "summary": "Deterministic consolidation of independent review findings. This is not final operator acceptance.",
        "markdown_report_path": relpath(root, output_md_path),
        "json_report_path": relpath(root, output_json_path),
        "reviewed_artifacts": reviewed_artifacts,
        "missing_artifacts": missing_artifacts,
        "blocking_issues": blocking_issues,
        "non_blocking_improvements": non_blocking,
        "recommendations": consolidated_recommendations,
        "unsupported_claims": unsupported_claims,
        "evidence": evidence,
        "command": None,
        "read_only_check": None,
        "validation_errors": [],
        "next_action": "proceed_to_operator_acceptance",
    }
    supervisor_agents.validate_review_decision(decision)
    write_json(output_json_path, decision)
    lines = [
        "# Consolidated Review",
        "",
        "This report preserves reviewer provenance and does not create final operator acceptance.",
        "",
        f"- review_cycle_id: {review_cycle_id}",
        f"- recommendation_count: {len(consolidated_recommendations)}",
        f"- blocking_issue_count: {len(blocking_issues)}",
        "",
        "## Recommendations",
        "",
    ]
    if not consolidated_recommendations:
        lines.append("None.")
    for rec in consolidated_recommendations:
        lines.append(f"- {rec.get('recommendation_id')}: {rec.get('consolidation_recommendation')} — {rec.get('recommendation')}")
    write_text(output_md_path, "\n".join(lines).rstrip() + "\n")

    cycle["consolidation"] = relpath(root, output_json_path)
    session["consolidations"].append(
        {
            "review_cycle_id": review_cycle_id,
            "consolidated_review_json": relpath(root, output_json_path),
            "consolidated_review_md": relpath(root, output_md_path),
            "recommendation_counts": {
                "total": len(consolidated_recommendations),
                "accepted_for_operator_review": sum(1 for rec in consolidated_recommendations if rec.get("consolidation_recommendation") == "accepted_for_operator_review"),
                "needs_operator_judgment": sum(1 for rec in consolidated_recommendations if rec.get("consolidation_recommendation") == "needs_operator_judgment"),
                "duplicate": sum(1 for rec in consolidated_recommendations if rec.get("consolidation_recommendation") == "duplicate"),
            },
        }
    )
    _write_session(root, session_path, session)
    return decision


def _load_applied_change_evidence(root: Path, evidence_path: str | Path | None) -> dict[str, Any]:
    if evidence_path is None:
        return {"recommendations": {}}
    payload = load_json(resolve_under_root(root, evidence_path, must_exist=True), "applied change evidence")
    recs = payload.get("recommendations")
    if not isinstance(recs, dict):
        raise SystemExit("applied change evidence must contain recommendations object keyed by recommendation id.")
    return payload


def _accepted_change_payload(evidence_payload: dict[str, Any], rec_id: str) -> dict[str, Any] | None:
    recs = evidence_payload.get("recommendations")
    if not isinstance(recs, dict):
        return None
    item = recs.get(rec_id)
    if not isinstance(item, dict):
        return None
    changes = item.get("changes_applied")
    validation = item.get("validation_evidence")
    rationale = item.get("operator_rationale")
    if not isinstance(changes, list) or not changes:
        return None
    if not isinstance(validation, list) or not validation:
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        return None
    return item


def accept_consolidated_review(
    *,
    root: Path,
    session_ref: str | Path,
    review_cycle_id: str,
    consolidated_review: str | Path,
    accepted_recommendation_ids: Sequence[str],
    output: str | Path,
    applied_change_evidence: str | Path | None = None,
) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    consolidated = _load_decision(root, consolidated_review, "consolidated review")
    accepted = set(accepted_recommendation_ids)
    evidence_payload = _load_applied_change_evidence(root, applied_change_evidence)
    output_json_path = resolve_under_root(root, output, must_exist=False)
    output_md_path = output_json_path.with_suffix(".md")

    recommendations = []
    for original in consolidated.get("recommendations", []):
        rec = dict(original)
        rec_id = str(rec.get("recommendation_id"))
        reviewer_evidence = rec.get("evidence")
        has_reviewer_evidence = isinstance(reviewer_evidence, list) and len(reviewer_evidence) > 0
        applied_payload = _accepted_change_payload(evidence_payload, rec_id)
        if rec_id in accepted and has_reviewer_evidence and applied_payload is not None:
            rec["operator_decision"] = "accepted"
            rec["decision_rationale"] = str(applied_payload["operator_rationale"])
            rec["changes_applied"] = applied_payload["changes_applied"]
            rec["validation_evidence"] = applied_payload["validation_evidence"]
            rec["rejected_reason"] = ""
        elif rec_id in accepted and not has_reviewer_evidence:
            rec["operator_decision"] = "rejected"
            rec["decision_rationale"] = "Rejected because the recommendation lacks reviewer evidence."
            rec["changes_applied"] = []
            rec["validation_evidence"] = []
            rec["rejected_reason"] = "Unsupported recommendation; operator cannot accept without evidence."
        elif rec_id in accepted and applied_payload is None:
            rec["operator_decision"] = "rejected"
            rec["decision_rationale"] = "Rejected because no concrete applied-change evidence and validation evidence were supplied."
            rec["changes_applied"] = []
            rec["validation_evidence"] = []
            rec["rejected_reason"] = "Missing applied-change evidence; supervisor does not synthesize changes_applied."
        else:
            rec["operator_decision"] = "rejected"
            rec["decision_rationale"] = "Rejected or deferred because it was not selected for evidence-supported acceptance."
            rec["changes_applied"] = []
            rec["validation_evidence"] = []
            rec["rejected_reason"] = "Not accepted by operator selective-acceptance pass."
        recommendations.append(rec)

    blocking_after_acceptance = [
        rec
        for rec in recommendations
        if rec.get("operator_decision") == "rejected"
        and rec.get("severity") in {"critical", "blocking"}
        and rec.get("consolidation_recommendation") not in {"duplicate", "already_satisfied", "out_of_scope"}
    ]
    approval = "approve" if not blocking_after_acceptance else "do_not_approve"
    decision = {
        "schema_version": REVIEW_DECISION_SCHEMA_VERSION,
        "decision_id": f"operator_acceptance_{review_cycle_id}",
        "created_at": runner_now().isoformat(),
        "supervisor_session_id": session["supervisor_session_id"],
        "workflow_id": consolidated.get("workflow_id"),
        "run_id": consolidated.get("run_id"),
        "stage_id": consolidated.get("stage_id"),
        "review_cycle_id": review_cycle_id,
        "review_kind": "operator_acceptance",
        "actor_role": "operator_codex",
        "agent_command_id": None,
        "status": "succeeded",
        "approval_decision": approval,
        "summary": "Operator selective acceptance record. Accepted recommendations require applied-change evidence.",
        "markdown_report_path": relpath(root, output_md_path),
        "json_report_path": relpath(root, output_json_path),
        "reviewed_artifacts": consolidated.get("reviewed_artifacts", []),
        "missing_artifacts": consolidated.get("missing_artifacts", []),
        "blocking_issues": blocking_after_acceptance,
        "non_blocking_improvements": consolidated.get("non_blocking_improvements", []),
        "recommendations": recommendations,
        "unsupported_claims": consolidated.get("unsupported_claims", []),
        "evidence": consolidated.get("evidence", []),
        "command": None,
        "read_only_check": None,
        "validation_errors": [],
        "next_action": "create_review_bundle" if approval == "approve" else "blocked",
    }
    supervisor_agents.validate_review_decision(decision)
    write_json(output_json_path, decision)
    lines = ["# Operator Acceptance", "", "Accepted recommendations include concrete applied-change evidence; no changes are synthesized.", ""]
    for rec in recommendations:
        lines.append(f"- {rec.get('recommendation_id')}: {rec.get('operator_decision')} — {rec.get('decision_rationale')}")
    write_text(output_md_path, "\n".join(lines).rstrip() + "\n")

    cycle = _find_cycle(session, review_cycle_id)
    cycle["acceptance_status"] = "accepted" if approval == "approve" else "blocked"
    if cycle.get("review_kind") == "scaffold" and approval == "approve" and session["scaffold_versions"]:
        session["scaffold_versions"][-1]["approval_status"] = "accepted"
    session["operator_acceptance_records"].append(relpath(root, output_json_path))
    session["status"] = "ready_to_launch" if approval == "approve" else "blocked"
    session["current_phase"] = "stage_execution" if approval == "approve" else "acceptance"
    _write_session(root, session_path, session)
    return decision


def assert_scaffold_launch_allowed(*, root: Path, session_ref: str | Path) -> None:
    session, _session_path = _load_session_and_path(root, session_ref)
    if not session["scaffold_versions"]:
        raise SystemExit("No scaffold has been staged.")
    latest = session["scaffold_versions"][-1]
    if latest.get("approval_status") != "accepted":
        raise SystemExit("Scaffold launch is blocked until operator acceptance is recorded.")
    accepted_cycles = [cycle for cycle in session["review_cycles"] if cycle.get("review_kind") == "scaffold" and cycle.get("acceptance_status") == "accepted"]
    if not accepted_cycles:
        raise SystemExit("Scaffold launch requires operator review, Codex review, Claude review, consolidation, and operator acceptance.")


def classify_stage(*, root: Path, session_ref: str | Path, run_dir: str | Path, stage_id: str, output: str | Path | None = None) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    run_manifest = runner_artifacts.load_run_manifest(root, resolved_run_dir)
    summary = runner_artifacts.find_stage_summary(run_manifest, stage_id)
    checkpoint_value = summary.get("checkpoint_path")
    if not isinstance(checkpoint_value, str) or not checkpoint_value:
        stage_dirs = sorted((resolved_run_dir / "stages").glob(f"*_{stage_id}"))
        if not stage_dirs:
            raise SystemExit(f"Could not find stage directory for {stage_id}")
        checkpoint_value = relpath(root, stage_dirs[0] / "stage_checkpoint.json")
    output_path = output or (session_path / "review_cycles" / f"{stage_id}_classification" / "stage_outcome.json")
    human_pause_path = Path(output_path).with_suffix(".human_pause.json")
    outcome = supervisor_policies.classify_stage_outcome(root=root, checkpoint_path=checkpoint_value, human_pause_output=human_pause_path)
    outcome_rel = supervisor_policies.write_stage_outcome(root, output_path, outcome)
    session["stage_outcomes"].append(
        {
            "run_id": outcome.get("run_id"),
            "stage_id": outcome.get("stage_id"),
            "classification": outcome.get("classification"),
            "artifact_path": outcome_rel,
            "reviewability": bool(outcome.get("reviewable")),
        }
    )
    if outcome.get("human_pause"):
        session["human_pauses"].append(outcome["human_pause"])
    _write_session(root, session_path, session)
    return outcome


def monitor_stage(*, root: Path, session_ref: str | Path, run_dir: str | Path, stage_id: str, stale_after_seconds: float) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    resolved_run_dir = resolve_under_root(root, run_dir, must_exist=True)
    run_manifest = runner_artifacts.load_run_manifest(root, resolved_run_dir)
    summary = runner_artifacts.find_stage_summary(run_manifest, stage_id)
    checkpoint_value = summary.get("checkpoint_path")
    if not isinstance(checkpoint_value, str) or not checkpoint_value:
        stage_dirs = sorted((resolved_run_dir / "stages").glob(f"*_{stage_id}"))
        if not stage_dirs:
            raise SystemExit(f"Could not find stage directory for {stage_id}")
        checkpoint_value = relpath(root, stage_dirs[0] / "stage_checkpoint.json")
    output_dir = session_path / "monitoring"
    output_dir.mkdir(parents=True, exist_ok=True)
    anomaly = supervisor_policies.detect_monitoring_anomaly(
        root=root,
        checkpoint_path=checkpoint_value,
        stale_after_seconds=stale_after_seconds,
        human_pause_output=output_dir / f"{stage_id}.monitoring.human_pause.json",
    )
    event = {
        "timestamp": runner_now().isoformat(),
        "run_id": run_manifest["run_id"],
        "stage_id": stage_id,
        "response_id": summary.get("response_id"),
        "status": summary.get("response_status") or summary.get("status"),
        "action": "monitor_without_duplicate_submit" if anomaly else "no_anomaly",
        "artifact_path": checkpoint_value,
    }
    session["monitoring_events"].append(event)
    if anomaly:
        outcome_path = output_dir / f"{stage_id}.monitoring_anomaly.json"
        anomaly_rel = supervisor_policies.write_stage_outcome(root, outcome_path, anomaly)
        session["stage_outcomes"].append(
            {
                "run_id": anomaly.get("run_id"),
                "stage_id": anomaly.get("stage_id"),
                "classification": anomaly.get("classification"),
                "artifact_path": anomaly_rel,
                "reviewability": False,
            }
        )
        if anomaly.get("human_pause"):
            session["human_pauses"].append(anomaly["human_pause"])
    _write_session(root, session_path, session)
    return anomaly or event


def archive_attempt(*, root: Path, session_ref: str | Path, run_dir: str | Path, stage_id: str, reason: str) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    before = dict(session.get("retry_budget", {}))
    budget = int(before.get("failed_no_artifact", 0))
    if budget <= 0:
        raise SystemExit("No failed_no_artifact retry budget remains; archive/rerun is blocked.")
    after = dict(before)
    after["failed_no_artifact"] = budget - 1
    manifest = supervisor_artifacts.archive_attempt(
        root=root,
        session_path=session_path,
        session=session,
        run_dir=run_dir,
        stage_id=stage_id,
        reason=reason,
        retry_budget_before=before,
        retry_budget_after=after,
    )
    session["retry_budget"] = after
    session["archives"].append({"archive_manifest_path": manifest["archive_manifest_path"]})
    _write_session(root, session_path, session)
    return manifest


def create_approved_review_bundle(
    *,
    root: Path,
    session_ref: str | Path,
    output_path: str | Path,
    workflow_id: str,
    source_stage_id: str,
    source_run_id: str,
    primary_artifact_markdown: str | Path,
    response_artifact_json: str | Path,
    reviewer_notes: str | Path,
    acceptance_record: str | Path,
    approved_handoff_markdown: str | Path | None = None,
    structured_artifact_json: str | Path | None = None,
) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    acceptance = _load_decision(root, acceptance_record, "operator acceptance")
    if acceptance.get("review_kind") != "operator_acceptance" or acceptance.get("approval_decision") != "approve":
        raise SystemExit("Approved review bundle requires an approving operator acceptance record.")
    payload = create_review_bundle(
        root=root,
        output_path=output_path,
        workflow_id=workflow_id,
        source_stage_id=source_stage_id,
        source_run_id=source_run_id,
        primary_artifact_markdown=primary_artifact_markdown,
        response_artifact_json=response_artifact_json,
        reviewer_notes=reviewer_notes,
        approved_handoff_markdown=approved_handoff_markdown,
        structured_artifact_json=structured_artifact_json,
        locked_decisions=["Operator accepted only supported recommendations before bundle creation."],
        open_dependencies=[],
        notes=[f"operator_acceptance_record={relpath(root, resolve_under_root(root, acceptance_record, must_exist=True))}"],
    )
    session["approved_review_bundles"].append(
        {
            "bundle_path": payload["bundle_path"],
            "source_run_id": source_run_id,
            "source_stage_id": source_stage_id,
            "artifact_hashes": payload["artifact_hashes"],
            "validation_status": "created",
        }
    )
    _write_session(root, session_path, session)
    return payload


def _require_final_bundle_payload(payload: dict[str, Any]) -> None:
    required = [
        "packet_version",
        "summary",
        "file_inventory",
        "emitted_files",
        "validation_evidence",
        "agent_reviews",
        "consolidation",
        "operator_acceptance",
        "model_migration_summary",
        "failure_policy_summary",
        "human_pause_summary",
        "rollout_instructions",
        "residual_risks",
    ]
    missing = [key for key in required if key not in payload]
    if missing:
        raise SystemExit(f"Final implementation bundle missing required keys: {', '.join(missing)}")
    inventory_paths = [item.get("path") for item in payload.get("file_inventory", []) if isinstance(item, dict)]
    emitted_paths = [item.get("path") for item in payload.get("emitted_files", []) if isinstance(item, dict)]
    if sorted(inventory_paths) != sorted(emitted_paths):
        raise SystemExit("Final implementation bundle inventory and emitted file paths must match.")
    agent_reviews = payload.get("agent_reviews")
    if not isinstance(agent_reviews, dict):
        raise SystemExit("Final implementation bundle agent_reviews must be an object.")
    for key in ("operator_codex", "codex_review_agent", "claude_review_agent"):
        if key not in agent_reviews:
            raise SystemExit(f"Final implementation bundle missing agent review: {key}")


def create_final_implementation_bundle(*, root: Path, session_ref: str | Path, payload: dict[str, Any], output: str | Path) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    _require_final_bundle_payload(payload)
    bundle = dict(payload)
    bundle["schema_version"] = FINAL_IMPLEMENTATION_BUNDLE_SCHEMA_VERSION
    bundle.setdefault("created_at", runner_now().isoformat())
    output_path = resolve_under_root(root, output, must_exist=False)
    supervisor_artifacts.write_json_validated(output_path, bundle, "final_implementation_bundle.schema.json", "final implementation bundle")
    session["final_bundle"] = {
        "bundle_path": relpath(root, output_path),
        "schema_validation_status": "validated",
        "file_inventory_hash": sha256_text(json.dumps(bundle["file_inventory"], sort_keys=True)),
        "acceptance_record_id": str(bundle.get("operator_acceptance", {}).get("decision_id", "")),
    }
    session["status"] = "completed"
    session["current_phase"] = "finalization"
    _write_session(root, session_path, session)
    return bundle


def finalize_bundle(*, root: Path, session_ref: str | Path, packet_json: str | Path, output: str | Path) -> dict[str, Any]:
    payload = load_json(resolve_under_root(root, packet_json, must_exist=True), "final bundle packet")
    return create_final_implementation_bundle(root=root, session_ref=session_ref, payload=payload, output=output)


def validate_session(*, root: Path, session_ref: str | Path) -> dict[str, Any]:
    session, session_path = _load_session_and_path(root, session_ref)
    supervisor_artifacts.validate_against_schema(
        {key: value for key, value in session.items() if not key.startswith("_")},
        "supervisor_session.schema.json",
        "supervisor session",
    )
    return {"session": session["supervisor_session_id"], "manifest": relpath(root, supervisor_artifacts.session_manifest_path(session_path)), "status": "valid"}
