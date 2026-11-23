"""Render a draft from the finalized quantitative evidence of one insight."""
import json

import SimpleITK as sitk

from app.services import ct_quantitative_plan as planned
from app.services.ct_verified_insight import LABELS
from app.services.insight_client import ProviderError
from app.services.ct_quantitative import plane_axes
from app.services.ct_structured_report import LUNG_LOBES, VERSION, visible_anchor


def native_axial_size(volume):
    # Read geometry only. The measurement audit already binds indices to this source series.
    info = sitk.ImageFileReader()
    info.SetFileName(volume)
    try:
        info.ReadImageInformation()
    except RuntimeError:
        return None
    if info.GetDimension() != 3 or plane_axes(info).get("axial") != 0:
        return None
    return info.GetSize()


def axial_anchor(lesion, native_size, slice_count):
    if not native_size:
        return None
    axial = lesion["geometry"]["diameters"].get("axial")
    if not axial:
        return None
    x_size, y_size, depth = native_size
    z = axial["slice_index"]
    if type(z) is not int or not 0 <= z < min(depth, slice_count):
        return None
    (x1, y1), (x2, y2) = axial["endpoints_xy"]
    if not (0 <= x1 < x_size and 0 <= x2 < x_size and 0 <= y1 < y_size and 0 <= y2 < y_size):
        return None
    return visible_anchor({"slice": z + 1, "box_xy": [
        max(0, min(x1, x2) - 2) / x_size, max(0, min(y1, y2) - 2) / y_size,
        min(x_size, max(x1, x2) + 3) / x_size, min(y_size, max(y1, y2) + 3) / y_size,
    ]})


def checked_rows(result, audit):
    # Derive units and values from the measurement record, including for older saved insights.
    verified = planned.quantitative.cloud_payload(audit["measurements"])
    rows = result["quantitative_interpretation"]
    plan = {"findings": [{"lesion_id": row["lesion_id"], "evidence_ids": row["evidence_ids"]} for row in rows]}
    for row in rows:
        owner = row["lesion_id"]
        for key in row["evidence_ids"]:
            if key.partition(":")[0] != owner:
                raise ProviderError(f"{owner} 引用了其他病灶的证据 {key}。")
    planned.validate_selection(json.dumps(plan), audit["cloud_input"])
    expected = planned.render(plan, verified)["quantitative_interpretation"]
    actual = {row["lesion_id"]: row["text"] for row in rows}
    for row in expected:
        if actual[row["lesion_id"]] != row["text"]:
            raise ProviderError(f"{row['lesion_id']} 的所见与原始测量不符，请核对数值、单位及表述。")
    return expected


MODEL_TEXT = {"实性": "呈实性密度", "非实性": "呈非实性密度", "有钙化": "检出钙化",
              "未检出钙化": "未检出钙化", "毛刺候选区域": "局部见可疑毛刺影"}


def build_draft(insight_id, model, source, result, audit):
    measured = audit["measurements"]
    slices = measured["protocol"]["dicom_slice_count"]
    lesions = {row["id"]: row for row in measured["lesions"]}
    rows = checked_rows(result, audit)
    size = native_axial_size(source["volume"]) if rows else None
    hints = {}
    seen = {}
    for row in audit.get("specialist", {}).get("evidence", []):
        owner = row["lesion_id"]
        key = row["evidence_id"]
        prefix, _, kind = key.partition(":")
        value = row["value"]
        if prefix != owner or owner not in lesions:
            raise ProviderError(f"{owner} 的模型证据 {key} 归属错误。")
        if (row["label"], value, row["model"]) not in {
            labels for (attribute, _), labels in LABELS.items() if attribute == kind
        }:
            raise ProviderError(f"{key} 的模型或识别值无效。")
        if key in seen:
            if seen[key] != value:
                raise ProviderError(f"{owner} 的{row['label']}结果冲突：{seen[key]}、{value}。")
            continue
        seen[key] = value
        anchor = visible_anchor(row.get("anchor"))
        hints.setdefault(owner, []).append({
            "id": row["evidence_id"], "label": row["label"], "model": row["model"],
            "status": "模型提示", "text": value,
            "anchor": anchor if anchor and anchor["slice"] <= slices else None,
        })
    findings = []
    measured_count = len(rows)
    quantified = {row["lesion_id"] for row in rows}
    for owner in hints:
        if owner in quantified:
            continue
        rows.append({"lesion_id": owner, "text": "", "evidence_ids": []})
    for row in rows:
        owner = row["lesion_id"]
        lesion = lesions[owner]
        region = (lesion.get("location") or {}).get("lobe")
        signals = hints.get(owner, [])
        description = row["text"] or f"{region or '肺部'}候选。"
        if signals:
            description += "，".join(MODEL_TEXT[item["text"]] for item in signals) + "。"
        findings.append({
            "lesion_id": owner, "location": region if region in LUNG_LOBES else None,
            "text": description, "evidence_ids": row["evidence_ids"] + [item["id"] for item in signals],
            "anchor": axial_anchor(lesion, size, slices), "model_hints": hints.get(owner, []),
        })
    count = measured["distribution"]["candidate_count"]
    impression = (f"本次自动分析检出 {count} 处肺部候选灶，{measured_count} 处有定量依据。"
                  if findings else "本次未取得可供解读的候选定量证据。")
    return {"schema_version": VERSION, "basis_insight_id": insight_id, "status": "AI 参考草稿",
            "scope": "胸部 CT 候选病灶阅片草稿", "generated_by": f"{model} 选取证据 · 同次定量结果",
            "findings": findings, "impression": [impression], "review": [],
            "limitations": [line for line in result["limitations"] if line != "候选分割及测量待阅片核对。"], "whole_chest_research": [], "research_status": []}
