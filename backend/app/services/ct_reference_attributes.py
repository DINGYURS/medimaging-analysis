"""Versioned DeepSeek organization contract for nodule-attribute evidence."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.ct_nodule_semantics import attribute_text
from app.services.insight_client import ProviderError, request_json

SYSTEM_PROMPT = """你是肺结节候选影像参考的文本组织助手。仅组织输入 JSON 中的证据，后端将绘制事实与数值。
输出 JSON 对象，字段严格为 schema_version、group_id、representative_id、order、finding_plans。
schema_version 必须是 ct-reference-plan-v2。group_id 与 representative_id 复制输入的 group 对应字段。
order 只能是 distribution_first 或 representative_first。
finding_plans 列出全部候选且每个恰好一次，条目恰好包含 finding_id 和 attribute_ids。
attribute_ids 只列同一候选的 measured 或 research_prediction 属性，全部保留，可按重要性排序。
indeterminate 和 not_assessed 均不得引用，若无可用属性则返回空数组。不得输出额外文字或新结论。
示例 JSON {"schema_version":"ct-reference-plan-v2","group_id":"G1","representative_id":"L1","order":"distribution_first","finding_plans":[{"finding_id":"L1","attribute_ids":["L1:mean_hu"]}]}。"""
AVAILABLE = {"measured", "research_prediction"}


class FindingPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    finding_id: str
    attribute_ids: list[str] = Field(max_length=8)


class ReportPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["ct-reference-plan-v2"]
    group_id: Literal["G1"]
    representative_id: str
    order: Literal["distribution_first", "representative_first"]
    finding_plans: list[FindingPlan] = Field(max_length=100)


def cloud_input(findings, representative):
    return {
        "schema_version": "ct-reference-input-v2",
        "scope": "lung_nodule_candidates",
        "group": {"id": "G1", "count": len(findings), "representative_id": representative},
        "findings": [
            {key: item[key] for key in ("id", "lobe", "side", "diameter_mm")} | {
                "attributes": [
                    {key: attr[key] for key in ("id", "code", "status", "value", "unit", "model") if key in attr}
                    for attr in item["attributes"]
                ],
            } for item in findings
        ],
    }


def available(payload):
    return {
        item["id"]: [attr["id"] for attr in item["attributes"] if attr["status"] in AVAILABLE]
        for item in payload["findings"]
    }


def validate_plan(content, payload):
    plan = ReportPlan.model_validate_json(content)
    groups = available(payload)
    if plan.representative_id != payload["group"]["representative_id"]:
        raise ProviderError("代表候选引用不一致。")
    if len(plan.finding_plans) != len(groups) or {x.finding_id for x in plan.finding_plans} != set(groups):
        raise ProviderError("候选引用不完整或重复。")
    for entry in plan.finding_plans:
        allowed = groups[entry.finding_id]
        if len(entry.attribute_ids) != len(allowed) or set(entry.attribute_ids) != set(allowed):
            raise ProviderError("模型引用了不属于此候选或未评估的征象。")
    return plan


def organize(payload, config):
    plan, _ = request_json(payload, config, SYSTEM_PROMPT, validate_plan)
    return plan.model_dump()


def render_details(findings, plan, payload):
    order = (
        {x["finding_id"]: x["attribute_ids"] for x in plan["finding_plans"]}
        if plan else available(payload)
    )
    return {item["id"]: attribute_text(item, order[item["id"]]) for item in findings}
