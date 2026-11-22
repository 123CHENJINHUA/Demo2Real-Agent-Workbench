"""Allow-listed roles and JSON contracts for demonstration analysis."""

DISPATCH_STAGE = "agent_dispatch"
SYNTHESIS_STAGE = "task_analysis"
SPECIALISTS = {
    "agent_intent": (
        "示教与动作 Agent",
        "Identify the visible task objective, object roles, and observable action phases. "
        "Keep event timing unknown unless directly supported.",
    ),
    "agent_validation": (
        "验收与风险 Agent",
        "Propose observable success criteria and negative checks. Distinguish action completion, "
        "contact, retention, and hidden mechanism state, and flag missing evidence.",
    ),
    "agent_geometry": (
        "几何与接触 Agent",
        "Review object geometry, possible interfaces, grasp and contact transitions, and assets "
        "or measurements needed before simulation. Do not infer engineering dimensions from RGB.",
    ),
    "agent_observation": (
        "观测与标定 Agent",
        "Review views, occlusions, temporal ambiguity, depth evidence and calibration gaps. "
        "State the measurement or observation needed to resolve each important gap.",
    ),
}
REQUIRED_ROLES = frozenset({"agent_intent", "agent_validation"})


def _object(properties):
    return {
        "type": "object", "properties": properties,
        "required": list(properties), "additionalProperties": False,
    }


def validate_specialist_count(value):
    if value is not None and (type(value) is not int or not len(REQUIRED_ROLES) <= value <= len(SPECIALISTS)):
        raise ValueError("专责 Agent 数量请选择自动、2、3 或 4")
    return value


def dispatch_schema(count=None, evidence_refs=None):
    schema = {
        "assignments": {
            "type": "array", "minItems": count or len(REQUIRED_ROLES), "maxItems": count or len(SPECIALISTS),
            "items": _object({
                "role": {"type": "string", "enum": list(SPECIALISTS)},
                "focus": {"type": "string"}, "reason": {"type": "string"},
            }),
        },
    }
    if evidence_refs is not None:
        schema["understanding"] = _object({
            "task_summary":{"type":"string"},
            "phases":{"type":"array","minItems":1,"items":_object({
                "name":{"type":"string"},
                "evidence_refs":{"type":"array","items":{"type":"string","enum":list(evidence_refs)}},
            })},
        })
    return _object(schema)


def validate_understanding(value, evidence_refs):
    if not isinstance(value,dict) or not _text(value.get("task_summary"),1200):
        raise ValueError("总控初步理解缺少示教目标")
    phases=value.get("phases")
    if not isinstance(phases,list) or not 1<=len(phases)<=100:
        raise ValueError("总控初步理解缺少有序步骤")
    for phase in phases:
        if not isinstance(phase,dict) or not _text(phase.get("name"),400):
            raise ValueError("示教步骤需要简洁的动作名称")
        refs=phase.get("evidence_refs")
        if not isinstance(refs,list) or not all(isinstance(ref,str) and ref in evidence_refs for ref in refs):
            raise ValueError("总控初步理解引用了未提供的示教证据")
    return {"task_summary":value["task_summary"],"phases":phases}


def specialist_schema(evidence_refs):
    return _object({
        "summary": {"type": "string"},
        "findings": {
            "type": "array", "maxItems": 8,
            "items": _object({
                "observation": {"type": "string"},
                "evidence_refs": {"type": "array", "items": {"type": "string", "enum": list(evidence_refs)}},
            }),
        },
        "unknowns": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
        "checks": {"type": "array", "maxItems": 8, "items": {"type": "string"}},
    })


def _text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def validate_assignments(value, count=None):
    if not isinstance(value, dict) or set(value) != {"assignments"}:
        raise ValueError("派工结果需要 assignments")
    assignments = value["assignments"]
    if not isinstance(assignments, list) or not len(REQUIRED_ROLES) <= len(assignments) <= len(SPECIALISTS):
        raise ValueError("专责 Agent 数量超出允许范围")
    if count is not None and len(assignments) != count:
        raise ValueError(f"本次要求 {count} 个专责 Agent，派工返回了 {len(assignments)} 个")
    roles = set()
    for item in assignments:
        if not isinstance(item, dict) or set(item) != {"role", "focus", "reason"}:
            raise ValueError("派工项需要 role、focus 和 reason")
        role = item["role"]
        if not isinstance(role, str) or role not in SPECIALISTS or role in roles:
            raise ValueError("派工包含重复或未注册的角色")
        if not _text(item["focus"], 400) or not _text(item["reason"], 400):
            raise ValueError("派工说明需为 1 至 400 字符的文本")
        roles.add(role)
    if not REQUIRED_ROLES <= roles:
        raise ValueError("派工需要示教与动作、验收与风险两个角色")
    return assignments


def validate_specialist(value, evidence_refs):
    if not isinstance(value, dict) or set(value) != {"summary", "findings", "unknowns", "checks"}:
        raise ValueError("专责报告需要 summary、findings、unknowns 和 checks")
    if not _text(value["summary"], 600):
        raise ValueError("专责报告摘要需为 1 至 600 字符的文本")
    for key in ("findings", "unknowns", "checks"):
        if not isinstance(value[key], list) or len(value[key]) > 8:
            raise ValueError(f"{key} 每项最多记录 8 条")
    if not all(_text(item, 400) for item in value["unknowns"] + value["checks"]):
        raise ValueError("未知项和检查项需为 1 至 400 字符的文本")
    allowed = set(evidence_refs)
    for item in value["findings"]:
        if not isinstance(item, dict) or set(item) != {"observation", "evidence_refs"} or not _text(item["observation"], 400):
            raise ValueError("每项观察需含不超过 400 字符的说明和证据引用")
        refs = item["evidence_refs"]
        if not isinstance(refs, list) or not all(isinstance(ref, str) and ref in allowed for ref in refs):
            raise ValueError("专责报告引用了未提供的示教证据")
    return value
