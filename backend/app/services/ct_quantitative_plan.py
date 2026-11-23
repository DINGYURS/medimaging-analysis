"""Generate concise CT interpretations from a strictly validated evidence selection."""
import asyncio
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import math
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from app.core.config import settings
from app.core.queue_gate import gpu_gate
from app.db.session import AsyncSessionLocal
from app.services import ct_quantitative_insight as quantitative, ct_verified_insight as specialist
from app.services.ct_reference_evidence import EvidenceUnavailable
from app.services.insight_client import ProviderError, request_json, validate_output
from app.services.insight_payload import signature

VERSION = "ct-quantitative-insight-v2"
SYSTEM_PROMPT = """你为医生组织胸部 CT 定量洞察。只返回 JSON 对象，唯一字段是 findings。
findings 中每个输入候选恰好出现一次，每项只含 lesion_id 和 evidence_ids。
为每个候选选择本候选最值得复核的 1 至 3 个实际证据编号，按径线、强度、体积或其他测量的顺序排列。
存在轴位或三维最大径时，至少选一个径线指标。编号从相应候选的指标原样复制。
禁止自由文本、新增数值、改写单位、推断性质或添加字段。服务端按已验证的原始测量渲染短句。"""
PROMPT_HASH = hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest()
DISPLAY_LABELS = {
    "axial_diameter": "轴位最大径", "diameter_3d_mm": "三维最大径",
    "mean_hu": "平均强度", "median_hu": "中位强度", "voxel_volume_mm3": "体素体积",
    "surface_mm2": "表面积", "outer_lung_distance_mm": "至肺分割外缘最短距离",
    "coronal_diameter": "冠状位最大径", "sagittal_diameter": "矢状位最大径",
    "solid_volume_fraction_percent": "阈上体积分数", "sphericity": "球形度",
    "min_hu": "最低强度", "max_hu": "最高强度", "core_minus_shell_hu": "核心与边缘强度差",
}
DIAMETERS = {"axial_diameter", "diameter_3d_mm"}
UNITS = {"mm": "mm", "mm2": "mm²", "mm3": "mm³", "HU": "HU", "%": "%",
         "bit": "bit", "HU/mm": "HU/mm", "dimensionless": ""}
LOBES = {"左肺上叶", "左肺下叶", "右肺上叶", "右肺中叶", "右肺下叶"}


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lesion_id: StrictStr
    evidence_ids: list[StrictStr] = Field(min_length=1, max_length=3)


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[Finding] = Field(max_length=100)


async def build_snapshot(task, db):
    source = await quantitative.build_snapshot(task, db)
    source["schema_version"] = VERSION
    source["pipeline"].append(signature(Path(__file__)))
    return source


def label(fact):
    return DISPLAY_LABELS.get(fact["name"], "定量指标")


def selection_payload(payload):
    candidates = []
    for item in payload["findings"]["lesions"]:
        available = [fact for group in ("measurements", "radiomics") for fact in item[group]
                     if fact["unit"] in UNITS and type(fact["value"]) in (float, int)
                     and math.isfinite(fact["value"])]
        indexed = {fact["name"]: fact for fact in available}
        preferred = [indexed[key] for key in DISPLAY_LABELS if key in indexed]
        choices = preferred[:6] or available[:1]
        if choices:
            lobe = item["anatomical_location"]
            candidates.append({"lesion_id": item["lesion_id"], "lobe": lobe if lobe in LOBES else None,
                "metrics": [{"id": fact["evidence_id"], "kind": fact["name"], "name": label(fact),
                             "value": fact["value"], "unit": fact["unit"]} for fact in choices]})
    result = {"schema_version": VERSION, "coverage": "algorithm_detected_chest_ct_candidates",
              "candidates": candidates}
    for limit in (6, 3, 1):
        for item in candidates:
            item["metrics"] = item["metrics"][:limit]
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False).encode()) <= settings.LLM_MAX_INPUT_BYTES:
            return result
    raise ProviderError("候选数量和关键测量超过云端输入大小上限。")


def validate_selection(content, payload):
    try:
        selection = Selection.model_validate_json(content)
    except ValidationError:
        raise ProviderError("模型返回的证据选择结构不正确。", True) from None
    available = {row["lesion_id"]: {fact["id"]: fact for fact in row["metrics"]}
                 for row in payload["candidates"]}
    if sorted(item.lesion_id for item in selection.findings) != sorted(available):
        raise ProviderError("模型证据选择未逐个覆盖可量化候选。", True)
    for item in selection.findings:
        facts = available[item.lesion_id]
        selected = set(item.evidence_ids)
        diameters = {key for key, fact in facts.items() if fact["kind"] in DIAMETERS}
        if (len(selected) != len(item.evidence_ids) or not selected <= facts.keys()
                or (diameters and not selected & diameters)):
            raise ProviderError("模型选择的证据归属、重复项或径线不符合要求。", True)
    return selection.model_dump()


def display_value(fact):
    value = Decimal(str(fact["value"])).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    unit = UNITS[fact["unit"]]
    return f"{label(fact)} {value:.2f}{' ' + unit if unit else ''}"


def render(plan, payload):
    candidates = {row["lesion_id"]: row for row in payload["findings"]["lesions"]}
    chosen = {row["lesion_id"]: row["evidence_ids"] for row in plan["findings"]}
    rows = []
    for owner, item in candidates.items():
        if owner not in chosen:
            continue
        ids = chosen[owner]
        facts = {fact["evidence_id"]: fact for group in ("measurements", "radiomics") for fact in item[group]}
        lobe = item["anatomical_location"]
        prefix = f"{lobe}候选" if lobe in LOBES else "肺部候选"
        rows.append({"lesion_id": owner, "text": prefix + "，" +
                     "，".join(display_value(facts[key]) for key in ids) + "。", "evidence_ids": ids})
    count = payload["findings"]["distribution"]["candidate_count"]
    summary = f"自动分析检出 {count} 处肺部候选，{len(rows)} 处有可用定量指标。"
    result = {"schema_version": "clinical-insight-v2", "analysis_status": "limited" if rows else "insufficient",
              "result_summary": summary, "quantitative_interpretation": rows,
              "review_items": [], "limitations": []}
    # Reuse the same unit, sign, rounding, ownership and coverage checks on the actual displayed text.
    return validate_output(json.dumps(result, ensure_ascii=False, allow_nan=False), payload)


async def specialist_facts(source, audit):
    if not source["models"] or not audit["hu_verified"] or not audit["lesions"]:
        return []
    await gpu_gate.acquire()
    try:
        raw = await asyncio.to_thread(specialist.infer, source["models"])
    finally:
        gpu_gate.release()
    findings = []
    for row in raw:
        title, value, version = specialist.LABELS[row["kind"], row["value"]]
        candidate = row["lesion_id"]
        findings.append({"evidence_id": candidate + ":" + row["kind"], "lesion_id": candidate,
                         "label": title, "value": value, "model": version, "unit": None,
                         **({"anchor": row["anchor"]} if "anchor" in row else {})})
    return findings


async def generate(source):
    async with AsyncSessionLocal() as db:
        history = await quantitative.rad.load_history(db, source["history"])
    try:
        audit = await asyncio.to_thread(quantitative.measure, source, history)
    except EvidenceUnavailable as exc:
        raise ProviderError(str(exc)) from None
    verified = quantitative.cloud_payload(audit)
    payload = selection_payload(verified)
    selection, usage = {"findings": []}, {}
    if payload["candidates"]:
        selection, usage = await asyncio.to_thread(request_json, payload, settings, SYSTEM_PROMPT, validate_selection)
    result = render(selection, verified)
    try:
        hints = await specialist_facts(source, audit)
    except ProviderError:
        hints = []
        if len(result["limitations"]) < 20:
            result["limitations"].append("专项模型结果暂不可用，本次保留候选定量测量。")
    result["specialist_evidence_ids"] = [fact["evidence_id"] for fact in hints]
    evidence = [*verified["exam"]["measurements"], *[
        fact for item in verified["findings"]["lesions"] for group in ("measurements", "radiomics")
        for fact in item[group]
    ], *hints]
    return result, usage, {"evidence": evidence, "cloud_input": payload, "cloud_output": selection,
                           "measurements": audit, "specialist": {"evidence": hints}}
