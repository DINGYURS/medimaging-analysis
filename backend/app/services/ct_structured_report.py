"""Source-bound CT report previews; the cloud selects evidence and the server renders facts."""
import hashlib
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import settings
from app.services.insight_client import ProviderError
from app.services.insight_payload import signature, source_record

VERSION = "ct-structured-report-preview-v1"
SYSTEM_PROMPT = """你为放射科医生整理胸部CT初步阅片草稿。你只能使用匿名结构化候选测量与专项模型提示，无法查看原片。
返回一个JSON对象，只包含 findings、impression_order、review_priority。
findings 是数组，每个有证据的 lesion_id 必须恰好出现一次。每项含 lesion_id 和 evidence_ids，挑选该候选的1至4条最能描述位置、大小、密度或形态的证据。优先选轴位径或三维径，单项CT层面径线保持原测量定义。
impression_order 是候选ID数组，每个候选恰好出现一次，按复核优先次序排列。
review_priority 是0至4条互不重复的实际证据ID，用于医生复核。引用模型提示时保持其待复核性质。
由服务器把选择渲染为检查范围、影像所见、初步印象和待复核事项。无需输出自由文本、解释、诊断、风险、阴性结论或随访治疗建议。"""
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
MEASURES = ("axial_diameter", "diameter_3d_mm", "coronal_diameter", "sagittal_diameter",
            "voxel_volume_mm3", "mean_hu", "median_hu", "solid_volume_fraction_percent",
            "surface_mm2", "outer_lung_distance_mm")
LUNG_LOBES = ("左肺上叶", "左肺下叶", "右肺上叶", "右肺中叶", "右肺下叶")
MODELS = {"density": ("密度分类", ("实性", "非实性"), "density-soft-tta"),
          "calcification": ("钙化识别", ("提示钙化", "模型未提示钙化"), "cnn-v3"),
          "spiculation": ("毛刺候选", ("毛刺候选区域",), "shape-refine")}
REGION_LABELS = {"effusion": "TotalSegmentator 积液", "medpseg": "MEDPSeg 肺实质"}
UNITS = {"mm": "mm", "mm2": "mm²", "mm3": "mm³", "HU": "HU", "%": "%"}
STATUS_LABELS = {"model_hint": "模型提示", "indeterminate": "结果不确定", "unavailable": "不可用"}


class PlannedFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesion_id: str
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[PlannedFinding] = Field(max_length=100)
    impression_order: list[str] = Field(max_length=100)
    review_priority: list[str] = Field(max_length=4)


def evidence_input(quantitative, specialists):
    predictions = {row["id"]: row for row in specialists["candidates"]}
    findings = []
    for item in quantitative["cloud_input"]["findings"]["lesions"]:
        owner = item["lesion_id"]
        measurements = [{"id": f"{owner}:{kind}", "kind": kind, "label": fact["label"],
                         "value": fact["value"], "unit": fact["unit"]}
                        for kind in MEASURES
                        for fact in item["measurements"] if fact["name"] == kind
                        and type(fact["value"]) in (int, float) and math.isfinite(fact["value"])
                        and fact["unit"] in UNITS]
        hints = []
        for kind, (label, values, model) in MODELS.items():
            signal = predictions.get(f"{owner}:{kind}", {})
            if (signal.get("status") == "model_hint" and signal.get("value") in values
                    and signal.get("model") == model):
                hints.append({"id": f"{owner}:{kind}", "kind": kind, "label": label,
                              "value": signal["value"], "state": "待医生复核"})
        if measurements or hints:
            position = item["anatomical_location"]
            findings.append({"lesion_id": owner, "lobe": position if position in LUNG_LOBES else None,
                             "measurements": measurements, "model_hints": hints})
    return {"schema_version": VERSION, "exam": {"modality": "CT", "region": "chest",
             "scope": "algorithm_detected_pulmonary_candidates", "comparison": "not_provided"},
            "findings": findings}


def validate_plan(content, payload):
    try:
        plan = Plan.model_validate_json(content)
    except ValidationError:
        raise ProviderError("模型返回的检查草稿结构不正确。", True) from None
    facts = {item["lesion_id"]: {row["id"]: row for row in item["measurements"] + item["model_hints"]}
             for item in payload["findings"]}
    if sorted(item.lesion_id for item in plan.findings) != sorted(facts):
        raise ProviderError("检查草稿未逐个覆盖有证据的候选。", True)
    if sorted(plan.impression_order) != sorted(facts):
        raise ProviderError("初步印象未覆盖各候选。", True)
    for item in plan.findings:
        known = facts[item.lesion_id]
        selected = set(item.evidence_ids)
        preferred = {key for key, fact in known.items() if fact["kind"] in ("axial_diameter", "diameter_3d_mm")}
        if (len(selected) != len(item.evidence_ids) or not selected <= known.keys()
                or (preferred and not selected & preferred)):
            raise ProviderError("所见中的证据归属、重复项或径线证据不符合要求。", True)
    owned = set().union(*(set(facts[lesion]) for lesion in facts))
    priority = set(plan.review_priority)
    if len(priority) != len(plan.review_priority) or not priority <= owned:
        raise ProviderError("待复核事项引用了无效的证据。", True)
    return plan.model_dump()


def local_plan(payload):
    return {"findings": [{"lesion_id": row["lesion_id"],
                          "evidence_ids": [fact["id"] for fact in (row["measurements"] + row["model_hints"])[:4]]}
                         for row in payload["findings"]],
            "impression_order": [row["lesion_id"] for row in payload["findings"]],
            "review_priority": [signal["id"] for row in payload["findings"]
                                for signal in row["model_hints"]][:4]}


def visible_anchor(anchor):
    if not anchor or type(anchor.get("slice")) is not int or anchor["slice"] < 1:
        return None
    box = anchor.get("box_xy")
    if not isinstance(box, list) or len(box) != 4 or not all(type(value) in (int, float) and 0 <= value <= 1 for value in box):
        return None
    if not box[0] < box[2] or not box[1] < box[3]:
        return None
    return {"slice": anchor["slice"], "box_xy": box}


def compact_fact(fact):
    unit = fact["unit"]
    return f"{fact['label']} {fact['value']:.2f} {UNITS[unit]}"


def render(payload, plan, specialists, whole_chest, research, *, generated_by):
    by_owner = {row["lesion_id"]: row for row in payload["findings"]}
    findings, owners = [], {}
    for selection in plan["findings"]:
        owner = selection["lesion_id"]
        item = by_owner[owner]
        measurements = {fact["id"]: fact for fact in item["measurements"]}
        values = [compact_fact(measurements[key]) for key in selection["evidence_ids"] if key in measurements]
        if not values and measurements:
            values = [compact_fact(next(iter(measurements.values())))]
        hints = []
        candidate_anchor = None
        for signal in specialists["candidates"]:
            if signal["lesion_id"] != owner or signal["kind"] not in MODELS:
                continue
            anchor = visible_anchor(signal["anchor"])
            candidate_anchor = candidate_anchor or anchor
            model_label = MODELS[signal["kind"]][0]
            hints.append({"id": signal["id"], "label": model_label, "model": signal["model"],
                          "status": STATUS_LABELS[signal["status"]],
                          "text": signal.get("value") if signal["status"] == "model_hint" else None,
                          "anchor": anchor if signal["status"] == "model_hint" else None})
        lobe = item["lobe"]
        text = f"{lobe or '肺叶待定位'}候选灶" + ("，" + "，".join(values) if values else "") + "。"
        row = {"lesion_id": owner, "location": lobe, "text": text, "anchor": candidate_anchor, "model_hints": hints}
        findings.append(row)
        owners[owner] = row
    anchors = {hint["id"]: (f"复核 {row['lesion_id']} {hint['label']}提示", row["lesion_id"], hint["anchor"])
               for row in findings for hint in row["model_hints"] if hint["status"] == "模型提示"}
    review = [{"text": "复核各候选在原片上的位置、边界及测量。"}] if findings else []
    for key in plan["review_priority"]:
        if key in anchors:
            text, owner, anchor = anchors[key]
            review.append({"text": text + "。", "lesion_id": owner, "anchor": anchor})
        else:
            owner = key.split(":", 1)[0]
            fact = next((m for m in by_owner[owner]["measurements"] if m["id"] == key), None)
            if fact:
                review.append({"text": f"复核 {owner} 的{fact['label']}。", "lesion_id": owner,
                               "anchor": owners[owner]["anchor"]})
    limitations = ["范围为算法检出的胸部 CT 候选及页面明确列出的专项输出。", "未提供既往检查对照。",
                   "模型提示待医生阅片复核。"]
    clinical = []
    for key in plan["impression_order"]:
        row = owners[key]
        measures = by_owner[key]["measurements"]
        primary = next((fact for fact in measures if fact["kind"] in {"axial_diameter", "diameter_3d_mm"}), None)
        follow = f"，{compact_fact(primary)}" if primary else ""
        clinical.append(f"{row['location'] or '肺叶待定位'}候选灶（{key}）{follow}，待阅片复核。")
    return {"schema_version": VERSION, "generated_by": generated_by, "status": "待医生复核",
            "scope": "胸部 CT 候选病灶阅片草稿", "findings": findings, "impression": clinical,
            "review": review, "limitations": limitations, "whole_chest_research": whole_chest,
            "research_status": research}


def preview_for(task, study):
    if task.status != "COMPLETED" or study is None or study.modality != "CT":
        return None
    directory = Path(settings.STORAGE_DIR) / "ct_report_previews"
    volume = study.nifti_path
    mask = Path(settings.MASK_DIR) / f"task_{task.id}_mask.nii.gz"
    try:
        record = json.loads((directory / f"task_{task.id}.json").read_text(encoding="utf-8"))
        source = source_record(volume, mask)
        confirmed = json.loads(Path(str(mask) + ".source.json").read_text(encoding="utf-8"))
        if (record["task_id"] == task.id and source == confirmed == record["source"]
                and record["series_uid"] == (study.metadata_json or {}).get("series_instance_uid")
                and record["report"]["schema_version"] == VERSION
                and all(signature(row[0]) == row for row in record["anchor_files"])):
            return record["report"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return None
