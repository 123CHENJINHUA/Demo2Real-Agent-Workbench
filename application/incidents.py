"""Project knowledge revisions are authoritative; SQLite preserves unarchived failures."""
from datetime import datetime


def merge_incidents(database_records, knowledge_records):
    authoritative={record['incident_id']:record for record in knowledge_records}
    items=[]
    for record in authoritative.values():
        resolution=record.get('resolution',{})
        if not isinstance(resolution,dict):resolution={}
        category=record.get('primary_category','workflow_integrity')
        items.append({
            'id':record['incident_id'], 'incident_id':record['incident_id'],
            'run_id':record.get('run_id'), 'task_id':record.get('task_id'),
            'category':category, 'primary_category':category,
            'status':record.get('status') or record.get('resolution_state') or resolution.get('state') or 'open',
            'summary':record.get('summary') or record.get('observed_behavior') or '未命名问题记录',
            'created_at':record.get('timestamp_utc'), 'record':record,
            'details':record, 'source':'knowledge', 'revision':record.get('revision'),
        })
    for row in database_records:
        details=row.get('details') or {}
        if not isinstance(details,dict):details={}
        nested=details.get('experience_record') or {}
        if not isinstance(nested,dict):nested={}
        links={row.get('id'),details.get('incident_id'),nested.get('incident_id')}
        if any(link in authoritative for link in links if link):continue
        items.append(dict(row)|{'record':details,'source':'sqlite'})
    def timestamp(item):
        value=item.get('created_at')
        if isinstance(value,(float,int)):return float(value)
        try:return datetime.fromisoformat(str(value).replace('Z','+00:00')).timestamp()
        except (ValueError,TypeError,OverflowError):return 0
    return sorted(items,key=lambda item:(timestamp(item),str(item.get('id',''))),reverse=True)


def browse_incidents(root, items, *, query="", category="", stage="", status="", page=1, limit=15):
    from modules.experience import finding_key, incident_display

    groups = {}
    terms = query.casefold().split()
    for item in items:
        record = incident_display(root, item["record"])
        actual_category = record.get("primary_category") or item.get("category")
        actual_stage = record.get("stage")
        if (category and category != actual_category) or (stage and stage != actual_stage) or (status and status != item["status"]):
            continue
        summary = record.get("summary") or item["summary"]
        text = " ".join(str(value or "") for value in (
            item["id"], item.get("run_id"), summary, record.get("observed_behavior"),
            record.get("finding_code"), record.get("finding_title"), record.get("diagnosis"), record.get("revision_note"),
            record.get("feature_tags"), record.get("expected_behavior"),
            record.get("trigger_conditions"), record.get("reusable_checklist"),
        )).casefold()
        if not all(term in text for term in terms):
            continue
        key = finding_key(record) or item["id"]
        if key not in groups:
            groups[key] = {
                "id": item["id"], "summary": summary, "title": record.get("finding_title"),
                "category": actual_category, "stage": actual_stage, "status": item["status"],
                "created_at": item["created_at"], "revision": item.get("revision"),
                "source": item["source"], "record_origin": record.get("record_origin"),
                "finding_code": record.get("finding_code"), "severity": record.get("severity"),
                "occurrences": [],
            }
        groups[key]["occurrences"].append({
            "id": item["id"], "run_id": item.get("run_id"),
            "created_at": item["created_at"], "asset_id": record.get("asset_id"),
        })
    rows = list(groups.values())
    start = (page - 1) * limit
    return {"incidents": rows[start:start+limit], "total": len(rows), "report_count": sum(len(row["occurrences"]) for row in rows), "page": page, "page_size": limit}


def manual_text(payload, field, *, required=False, limit=4000):
    value = payload.get(field, "")
    if not isinstance(value, str) or (required and not value.strip()) or len(value) > limit:
        raise ValueError(f"{field} 需要{'非空' if required else '有效'}文本（最多 {limit} 字符）")
    return value.strip()


def evidence_paths(root, payload):
    from pathlib import Path

    references = payload.get("evidence", [])
    if not isinstance(references, list) or len(references)>8:
        raise ValueError("每次最多附加 8 个运行产物")
    paths = []
    for value in references:
        if not isinstance(value, str) or not value.startswith("runs/"):
            raise ValueError("证据使用 runs/ 下的项目相对路径")
        path = Path(value)
        if ".." in path.parts or (root/path).resolve() != root/path:
            raise ValueError("证据路径不能跳转目录或经过软链接")
        paths.append(path.as_posix())
    return list(dict.fromkeys(paths))
