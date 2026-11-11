"""Evidence-backed case retrieval and append-only incident storage."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import uuid
import yaml

VERSION = "experience.v1.1"


def _root_path(root, relative):
    root = Path(root).resolve()
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Knowledge/evidence path escapes project or uses a symlink")
    return path


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def list_cases(root) -> list[dict]:
    root = Path(root).resolve()
    directory = _root_path(root, "knowledge/cases")
    if not directory.exists():
        return []
    cases = []
    for path in sorted(directory.rglob("*.yaml")):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        case = _load(path)
        if not case.get("case_id"):
            continue
        cases.append(case | {"record_path": path.relative_to(root).as_posix(), "hardware_verified": case.get("hardware_verified") is True})
    return cases


def verify_evidence(root, case: dict) -> list[dict]:
    root = Path(root).resolve()
    provenance = case.get("source_provenance")
    expected = {}
    if provenance:
        path = _root_path(root, provenance)
        if path.is_file():
            expected = {item["project_relative_path"]: item.get("sha256") for item in json.loads(path.read_text()).get("artifacts", [])}
    reports = []
    for evidence in case.get("evidence", []):
        reference = evidence if isinstance(evidence, str) else evidence.get("path", "")
        expected_hash = evidence.get("sha256") if isinstance(evidence, dict) else None
        expected_hash = expected_hash or expected.get(reference)
        path = _root_path(root, reference)
        exists = path.is_file()
        digest = _digest(path) if exists else None
        reports.append({"path": reference, "exists": exists, "sha256": digest, "expected_sha256": expected_hash, "verified": bool(exists and expected_hash and digest == expected_hash)})
    return reports


def _incident_search_record(incident: dict) -> dict:
    """Expose a report for review without promoting it into a curated case."""
    observed = incident.get("observed_behavior", "")
    summary = incident.get("summary") or observed
    checks = incident.get("reusable_checklist") or []
    checks = [item if isinstance(item, str) else item.get("text", "") for item in checks if isinstance(item, (str, dict))]
    checks = [text for text in checks if text.strip()]
    if not checks:
        checks = ["审阅本条未审核事件的现象和证据，记录是否适用于当前任务；不得直接采用其参数或假设修正。"]
    diagnosis = incident.get("diagnosis") or {"state": "unknown"}
    applicability = incident.get("applicability") or {}
    source_status = incident.get("status")
    status = "reported_resolved_unreviewed" if incident.get("resolved") is True else "open"
    return dict(incident) | {
        "record_type": "incident_unreviewed",
        "source_record_type": "incident",
        "case_id": incident["incident_id"],
        "title": incident.get("title") or summary[:140] or incident["incident_id"],
        "summary": summary,
        "stages": incident.get("stages") or ([incident["stage"]] if incident.get("stage") else []),
        "diagnosis": diagnosis,
        "status": status,
        "review_status": "unreviewed",
        "source_status": source_status,
        "hardware_verified": False,
        "preventive_checks": checks,
        "source_verification": incident.get("verification"),
        "verification": {"scope": "unreviewed incident report", "passed_subchecks": [], "failed_or_unverified": ["尚未策展或独立复核；日志完整性核验不等于根因、修正或任务性能已验证。"]},
        "applicability": dict(applicability) | {"parameters_not_to_copy": list(applicability.get("parameters_not_to_copy", [])) + ["未审核事件中的尺寸、姿态、控制命令及模型参数均不可自动应用。"]},
        "automatic_parameter_application": False,
    }


def search(root, query: str = "", category=None, stage=None, limit: int = 20) -> list[dict]:
    root = Path(root).resolve()
    taxonomy = _load(_root_path(root, "knowledge/taxonomy.yaml"))
    normalized = query.casefold().strip()
    terms = [x for x in re.split(r"[\s,，/、;；]+", normalized) if x]
    # Match compact mixed queries such as "USD纹理" or "迁移依赖". Fragments
    # remain explicit lexical matches, not semantic similarity or applicability.
    fragments = set(re.findall(r"[a-z0-9_.-]{2,}", normalized))
    for phrase in re.findall(r"[\u4e00-\u9fff]+", normalized):
        fragments.update(phrase[index:index + 2] for index in range(len(phrase) - 1))
    fragments -= set(terms)
    matched_features = set()
    feature_matches = {}
    for feature, aliases in taxonomy.get("feature_aliases", {}).items():
        matched = [alias for alias in [feature, *aliases] if alias.casefold() in normalized]
        if matched:
            matched_features.add(feature)
            feature_matches[feature] = matched
    found = []
    records = list_cases(root) + [_incident_search_record(item) for item in list_incidents(root)]
    for case in records:
        if category and category not in [case.get("primary_category"), *case.get("secondary_categories", [])]:
            continue
        if stage and stage not in case.get("stages", []):
            continue
        tags = set(case.get("feature_tags", []))
        text = json.dumps(case, ensure_ascii=False).casefold()
        reasons = []
        score = 0
        for tag in sorted(tags & matched_features):
            score += 5
            reasons.append({"type": "feature_alias", "feature": tag, "matched": feature_matches[tag]})
        for term in terms:
            if term in text:
                score += 2
                reasons.append({"type": "keyword", "matched": term})
        headline = str(case.get("title", "")).casefold()
        for fragment in sorted(fragments):
            if fragment in text:
                score += 2 if fragment in headline else 1
                reasons.append({"type": "keyword_fragment", "matched": fragment})
        if normalized and not score:
            continue
        found.append(case | {"match_score": score, "match_reasons": reasons, "reuse_boundary": "Similarity is a review suggestion; revalidate functional frames, dimensions, physics and success criteria in this task. No parameters are applied automatically.", "evidence_verification": verify_evidence(root, case)})
    return sorted(found, key=lambda c: (-c["match_score"], c["case_id"]))[:max(0, min(int(limit), 100))]


def record_incident(root, payload: dict) -> dict:
    """Persist an immutable revision; never overwrite original failures or evidence."""
    root = Path(root).resolve()
    taxonomy = _load(_root_path(root, "knowledge/taxonomy.yaml"))
    category = payload.get("primary_category", "workflow_integrity")
    stage = payload.get("stage", "runtime")
    if category not in taxonomy.get("categories", {}):
        raise ValueError("Unknown primary_category")
    if stage not in taxonomy.get("stages", []):
        raise ValueError("Unknown stage")
    if not str(payload.get("observed_behavior", "")).strip():
        raise ValueError("observed_behavior is required")
    if payload.get("diagnosis", {}).get("state", "unknown") not in taxonomy.get("diagnosis_states", []):
        raise ValueError("Unknown diagnosis state")
    if payload.get("mode", "offline") not in {"offline", "simulation", "mock", "shadow", "read_only", "hardware"}:
        raise ValueError("Unknown recording mode")
    incident_id = payload.get("incident_id") or "INC-" + uuid.uuid4().hex[:16]
    if not re.fullmatch(r"INC-[A-Za-z0-9_-]{1,64}", incident_id):
        raise ValueError("Invalid incident_id")
    directory = _root_path(root, "knowledge/incidents")
    directory.mkdir(parents=True, exist_ok=True)
    previous = sorted(directory.glob(incident_id + ".r*.json"))
    revision = max([int(path.stem.rsplit(".r", 1)[1]) for path in previous] + [0]) + 1
    if revision > 1 and not payload.get("revision_reason"):
        raise ValueError("A revision_reason is required for an existing incident")
    evidence = []
    for item in payload.get("evidence", []):
        reference = item if isinstance(item, str) else item.get("path", "")
        if not reference:
            raise ValueError("Evidence must reference a project file")
        path = _root_path(root, reference)
        if not path.is_file():
            raise ValueError(f"Missing evidence: {reference}")
        size = path.stat().st_size
        if size > 1024**3:
            raise ValueError("Incident evidence exceeds 1 GiB snapshot budget")
        digest = _digest(path)
        suffix = path.suffix if re.fullmatch(r"\.[A-Za-z0-9]{1,10}", path.suffix) else ".bin"
        snapshot = _root_path(root, f"knowledge/evidence/incidents/{digest[:2]}/{digest}{suffix}")
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        if not snapshot.exists():
            temporary = snapshot.with_name(snapshot.name + "." + uuid.uuid4().hex + ".tmp")
            with path.open("rb") as source, temporary.open("xb") as destination:
                while chunk := source.read(1024 * 1024):
                    destination.write(chunk)
                destination.flush()
                os.fsync(destination.fileno())
            if _digest(temporary) != digest:
                temporary.unlink()
                raise ValueError("Evidence changed during snapshot")
            # Hard-link publishes atomically without overwriting existing content.
            try:
                os.link(temporary, snapshot)
            except FileExistsError:
                pass
            temporary.unlink()
        if _digest(snapshot) != digest:
            raise ValueError("Existing evidence snapshot integrity failed")
        evidence.append({"path": snapshot.relative_to(root).as_posix(), "source_reference": path.relative_to(root).as_posix(), "sha256": digest, "bytes": size})
    value = dict(payload) | {"schema_version": 1, "record_type": "incident", "incident_id": incident_id, "revision": revision, "timestamp_utc": datetime.now(timezone.utc).isoformat(), "primary_category": category, "stage": stage, "evidence": evidence, "hardware_verified": False}
    # Recording an incident never confers a hardware validation or enables motion.
    path = directory / f"{incident_id}.r{revision:04d}.json"
    temporary = directory / ("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # Readers see a complete immutable revision; concurrent writers never overwrite.
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValueError("Concurrent revision collision; retry append") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return value | {"record_path": path.relative_to(root).as_posix()}


def list_incidents(root, category=None) -> list[dict]:
    root = Path(root).resolve()
    directory = _root_path(root, "knowledge/incidents")
    if not directory.exists():
        return []
    latest = {}
    for path in directory.glob("INC-*.r*.json"):
        if path.is_symlink() or not path.resolve().is_relative_to(root):
            continue
        value = json.loads(path.read_text())
        identifier = value["incident_id"]
        current = latest.get(identifier)
        if current is None or int(value["revision"]) > int(current["revision"]):
            latest[identifier] = value | {"record_path": path.relative_to(root).as_posix()}
    # Filter AFTER choosing the latest revision: reclassification must not resurrect
    # a superseded category. Timestamps sort display only; they never select revision.
    selected = [value for value in latest.values() if not category or value.get("primary_category") == category]
    return sorted(selected, key=lambda value: (value.get("timestamp_utc", ""), value["incident_id"]), reverse=True)


def run(ctx: dict) -> dict:
    root = Path(ctx["root"]).resolve()
    output = Path(ctx["output_dir"]).resolve()
    if not output.is_relative_to(root):
        raise ValueError("Output outside project")
    output.mkdir(parents=True, exist_ok=True)
    value = ctx.get("input", {})
    query = value.get("query") or value.get("goal", "")
    features = value.get("features", [])
    if isinstance(features, list):
        query += " " + " ".join(str(x) for x in features)
    cases = search(root, query, value.get("category"), value.get("stage"))
    checklist = []
    for case in cases:
        for index, check in enumerate(case.get("preventive_checks", [])):
            checklist.append({"check_id": f"{case['case_id']}-{index + 1}", "text": check, "source_case": case["case_id"], "source_revision": case.get("revision"), "source_record_type": case.get("record_type"), "source_record_path": case.get("record_path"), "source_review_status": case.get("review_status", "curated_historical"), "source_diagnosis_state": case.get("diagnosis", {}).get("state", "unknown"), "source_resolution_status": case.get("status"), "status": "pending_task_validation", "adoption": "review_required", "adoption_reason": None, "evidence_scopes": case.get("evidence_scopes", []), "hardware_verified": False, "parameters_not_to_copy": case.get("applicability", {}).get("parameters_not_to_copy", [])})
    report = {"schema_version": 1, "module_version": VERSION, "query": query.strip(), "status": "completed", "cases": cases, "checklist": checklist, "automatic_parameter_application": False}
    path = output / "experience_checklist.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ctx.get("emit", lambda *args: None)("artifact", {"path": path.relative_to(root).as_posix(), "kind": "experience_checklist"})
    return report | {"checklist_path": path.relative_to(root).as_posix()}
