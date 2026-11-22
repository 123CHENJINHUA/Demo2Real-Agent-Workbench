"""Task-specific data consumed by the offline trajectory learner and trace evaluator."""
import math


CHECK_TYPES = ("travel","rise","region","stable","clearance","gripper_gap","orientation","unsupported")


def _object(fields):
    return {"type":"object","properties":fields,"required":list(fields),"additionalProperties":False}


def _choice(items):
    return {"type":"string","enum":list(items)}


def _array(items, minimum=0):
    return {"type":"array","items":items,"minItems":minimum}


def _vector():
    return _array({"type":"number"},3) | {"maxItems":3}


def program_schema(objects,arms,cases):
    string = {"type":"string"}
    anchor = {"anyOf":[_choice(objects),{"type":"null"}]} if objects else {"type":"null"}
    history = _array(_object({"case_id":_choice(cases),"decision":_choice(("use","skip")),"reason":string})) if cases else _array(string) | {"maxItems":0}
    return _object({
        "strategy":string,
        "phases":_array(_object({
            "name":string,"instruction":string,"end_fraction":{"type":"number","minimum":0,"maximum":1},
            "anchor":anchor,"arms":_array(_choice(arms)),"adjustment_m":_vector(),"rotation_adjustment_deg":_vector(),
            "grippers":_array(_object({"arm":_choice(arms),"opening_m":{"type":"number","minimum":0,"maximum":.09}})),
            "contact_free":{"type":"boolean"},
            "waypoints":_array(_object({
                "arm":_choice(arms),"fraction":{"type":"number","minimum":0,"maximum":1},
                "relative_to":anchor,"position_m":_vector(),
                "orientation_xyz_deg":{"anyOf":[_vector(),{"type":"null"}]},
                "opening_m":{"anyOf":[{"type":"number","minimum":0,"maximum":.09},{"type":"null"}]},
            })) | {"maxItems":16},
        }),1),
        "variation_groups":_array(_array(_choice(objects),1)) if objects else _array(string) | {"maxItems":0},
        "experience":history,
    })


def acceptance_schema(objects,criterion_count,phase_count):
    string = {"type":"string"}
    reference = {"anyOf":[_choice(objects),{"type":"null"}]} if objects else {"type":"null"}
    position = {"type":"integer","minimum":0}
    return _object({"checks":_array(_object({
        "criterion":position | {"maximum":criterion_count-1},"phase":position | {"maximum":phase_count-1},
        "kind":_choice(CHECK_TYPES),"object_id":reference,"relative_to":reference,
        "lower":_vector(),"upper":_vector(),"value":{"type":"number"},"description":string,
    }),1)})


def _text(value):
    return isinstance(value,str) and 0<len(value.strip())<=1500


def _number(value):
    return type(value) in (float,int) and math.isfinite(value)


def _finite_vector(value):
    return isinstance(value,list) and len(value)==3 and all(_number(item) for item in value)


def validate_program(value,objects,arms,cases,*,require_waypoints=False):
    if not isinstance(value,dict) or not _text(value.get("strategy")):
        raise ValueError("动作计划缺少任务执行思路")
    phases=value.get("phases")
    if not isinstance(phases,list) or not 1<=len(phases)<=100:
        raise ValueError("动作计划缺少有序步骤")
    start=0.
    waypoint_program=any('waypoints' in step for step in phases)
    if require_waypoints and not waypoint_program:
        raise ValueError("新动作计划缺少仿真目标点")
    for phase,step in enumerate(phases):
        end=step["end_fraction"]
        selected=step["arms"]
        grippers=step["grippers"]
        if 'contact_free' in step and type(step['contact_free']) is not bool:
            raise ValueError('接近阶段的无接触标记必须为布尔值')
        if (not _text(step["name"]) or not _text(step["instruction"]) or not _number(end) or not start<end<=1
                or step["anchor"] not in [None,*objects] or not isinstance(selected,list)
                or len(set(selected))!=len(selected) or not set(selected)<=set(arms)
                or not _finite_vector(step["adjustment_m"]) or any(abs(n)>.08 for n in step["adjustment_m"])
                or not _finite_vector(step["rotation_adjustment_deg"]) or any(abs(n)>25 for n in step["rotation_adjustment_deg"])):
            raise ValueError("动作步骤的时间、坐标参考或调整量无效")
        if (not isinstance(grippers,list) or any(row["arm"] not in selected or not _number(row["opening_m"])
                or not 0<=row["opening_m"]<=.09 for row in grippers)
                or len({row["arm"] for row in grippers})!=len(grippers)):
            raise ValueError("夹爪调整必须绑定该步骤中参与动作的机械臂")
        if waypoint_program:
            points=step.get("waypoints")
            if not isinstance(points,list) or len(points)>16:
                raise ValueError("动作阶段的目标点数量无效")
            previous={}
            for point in points:
                arm,fraction=point["arm"],point["fraction"]
                orientation,opening=point["orientation_xyz_deg"],point["opening_m"]
                if (arm not in selected or not _number(fraction) or not 0<=fraction<=1
                        or fraction<=previous.get(arm,0. if phase else -1.)
                        or point["relative_to"] not in [None,*objects]
                        or not _finite_vector(point["position_m"]) or any(abs(n)>5. for n in point["position_m"])
                        or (orientation is not None and (not _finite_vector(orientation) or any(abs(n)>360 for n in orientation)))
                        or (opening is not None and (not _number(opening) or not 0<=opening<=.09))):
                    raise ValueError("目标点的阶段时间、机械臂、位置、朝向或夹爪开度无效")
                previous[arm]=fraction
            if set(previous)!=set(selected):
                raise ValueError("每个参与动作的机械臂必须有自己的仿真目标点")
        start=end
    if start!=1.:
        raise ValueError("动作步骤需要覆盖完整示教时间窗")
    if waypoint_program and not any(step["waypoints"] for step in phases):
        raise ValueError("动作计划需要可在仿真里执行的目标点")
    groups=value["variation_groups"]
    if not isinstance(groups,list) or any(not isinstance(group,list) or not group for group in groups):
        raise ValueError("场景变化分组无效")
    flattened=[item for group in groups for item in group]
    if len(flattened)!=len(set(flattened)) or not set(flattened)<=set(objects):
        raise ValueError("场景变化分组包含重复或未绑定的对象")
    history=value["experience"]
    if not isinstance(history,list) or any(row["case_id"] not in cases or row["decision"] not in ("use","skip")
            or not _text(row["reason"]) for row in history):
        raise ValueError("动作计划引用了未经检索的历史经验")
    if {row["case_id"] for row in history}!=set(cases) or len(history)!=len(cases):
        raise ValueError("动作计划需要记录每条候选经验的采用或跳过理由")
    return value


def validate_acceptance(value,objects,criteria,phase_count):
    rows=value.get("checks") if isinstance(value,dict) else None
    if not isinstance(rows,list) or not 1<=len(rows)<=200:
        raise ValueError("独立验收报告缺少可测判据")
    for row in rows:
        kind=row["kind"]
        if (type(row["criterion"]) is not int or not 0<=row["criterion"]<len(criteria)
                or type(row["phase"]) is not int or not 0<=row["phase"]<phase_count
                or kind not in CHECK_TYPES or not _text(row["description"])
                or row["relative_to"] not in [None,*objects] or not _finite_vector(row["lower"])
                or not _finite_vector(row["upper"]) or not _number(row["value"])
                or row["object_id"] not in [None,*objects]
                or (kind not in ("unsupported","gripper_gap") and row["object_id"] is None)):
            raise ValueError("验收判据引用了无效的阶段、对象或数值")
        if kind=="region" and any(upper<=lower for lower,upper in zip(row["lower"],row["upper"])):
            raise ValueError("目标区域边界无效")
        if kind not in ("region","unsupported") and row["value"]<=0:
            raise ValueError("验收阈值需要正数")
    if {row["criterion"] for row in rows}!=set(range(len(criteria))):
        raise ValueError("验收报告未覆盖任务定义中的全部成功条件")
    if {row["phase"] for row in rows}!=set(range(phase_count)):
        raise ValueError("验收报告未覆盖完整动作流程")
    return {"criteria":criteria,"checks":rows}
