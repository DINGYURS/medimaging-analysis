"""White-listed input and constrained text organization for CT references."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from app.services.insight_client import ProviderError, request_json


SYSTEM_PROMPT = """你是肺结节候选影像参考的文本组织助手。只能使用输入 JSON 中的候选信息。
返回 JSON 对象，恰好包含 group_id、representative_id、order 三个字段。
group_id 必须引用输入的 group.id，representative_id 必须与输入的 group.representative_id 一致。
order 只能是 distribution_first 或 representative_first。决定先概括分布还是先突出代表候选。
不得输出诊断、阴性判断、建议、额外文字或新数值。
示例 JSON {"group_id":"G1","representative_id":"L1","order":"distribution_first"}。"""


class ReportPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: Literal["G1"]
    representative_id: str
    order: Literal["distribution_first", "representative_first"]


def cloud_input(findings, representative):
    return {
        "schema_version": "ct-reference-v1",
        "scope": "lung_nodule_candidates",
        "group": {"id": "G1", "count": len(findings), "representative_id": representative},
        "findings": [
            {key: item[key] for key in ("id", "lobe", "side", "diameter_mm")}
            for item in findings
        ],
    }


def validate_plan(content, payload):
    try:
        plan = ReportPlan.model_validate_json(content)
    except ValidationError:
        raise ProviderError("模型返回的组织结构无效。") from None
    if plan.representative_id != payload["group"]["representative_id"]:
        raise ProviderError("模型引用了不匹配的代表候选。")
    return plan


def organize(payload, config):
    plan, _ = request_json(payload, config, SYSTEM_PROMPT, validate_plan)
    return plan.order


def render(findings, lobe_available, order):
    overview = f"本次自动分析标记 {len(findings)} 个肺结节候选。"
    if not findings:
        return "本次自动分析未标记肺结节候选。", "本次结果覆盖自动分析的肺结节候选。"
    sides = [item["side"] for item in findings if item["side"]]
    if set(sides) == {"左肺", "右肺"}:
        distribution = "有定位依据的候选分布于双肺。"
    elif sides:
        distribution = f"有定位依据的候选位于{sides[0]}。"
    else:
        distribution = "本次候选的肺叶分布未能确定。" if lobe_available else "本次未评估候选的肺叶分布。"
    measured = [item for item in findings if item["diameter_raw_mm"] is not None]
    detail = "本次未获得可用的候选轴位长径。"
    if measured:
        largest = max(measured, key=lambda item: item["diameter_raw_mm"])
        location = largest["lobe"] or largest["side"]
        position = f"位于{location}，" if location else ""
        detail = f"已测量候选中，病灶 #{largest['lesion_index']}{position}轴位长径最大，约 {largest['diameter_mm']:.1f} mm。"
    summary = detail + distribution if order == "representative_first" else distribution + detail
    return overview, summary
