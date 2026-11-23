"""Text-only cloud adapter. No credentials, patient payloads or raw responses in logs."""
import asyncio
import json
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re
import socket
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request
from pydantic import ValidationError
from app.schemas.insight import InsightOutput

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "clinical_insight_v2.txt"
SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")

class ProviderError(Exception):
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable

def measurement_matches(rendered: str, source: float, unit: str) -> bool:
    """Accept ordinary display rounding, not arbitrary relative differences."""
    try:
        number = Decimal(rendered)
        digits = max(0, -number.as_tuple().exponent)
        # Length retains at least tenths of a millimetre; HU and volume may be integers.
        digits = max(digits, 1 if unit == "mm" else 0)
        quantum = Decimal(1).scaleb(-digits)
        original = Decimal(str(source))
        return number == original.quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return False

def validate_output(content: str, payload: dict) -> dict:
    try:
        output = InsightOutput.model_validate_json(content)
    except (ValidationError, ValueError):
        raise ProviderError("模型返回内容不符合洞察输出协议。", True) from None
    lesions = {x["lesion_id"]: x for x in payload["findings"]["lesions"]}
    evidence = {v["evidence_id"]: x["lesion_id"] for x in lesions.values()
                for group in ("measurements", "radiomics") for v in x[group]}
    references = {x["reference_id"]: x for x in payload["references"]}
    is_ct_quantitative = payload.get("schema_version") == "ct-quantitative-insight-v1"
    for item in output.quantitative_interpretation:
        if item.lesion_id not in lesions or not item.evidence_ids or any(
                evidence.get(ref) != item.lesion_id for ref in item.evidence_ids):
            raise ProviderError("模型引用了不存在或不属于该病灶的指标。", True)
    if is_ct_quantitative:
        interpreted = {item.lesion_id for item in output.quantitative_interpretation}
        missing = set(evidence.values()) - interpreted
        if missing:
            raise ProviderError(f"模型未逐个解读具有可用指标的候选病灶（{', '.join(sorted(missing))}）。", True)
    for item in output.review_items:
        if any(ref not in evidence for ref in item.evidence_ids):
            raise ProviderError("待复核事项引用了不存在的指标。", True)
    # Render limitations from verified local quality facts, never model-authored clinical prose.
    output.limitations = list(dict.fromkeys(payload["quality"]["warnings"]))[:20]
    if payload["quality"]["missing_fields"] and len(output.limitations) < 20:
        output.limitations.append("部分定量指标或实际计算设置缺少记录，可解释范围受限。")
    # Check numeric measurements against the actual supplied values and units.
    fields = [output.result_summary] + [x.text for x in output.quantitative_interpretation]
    fields += [text for x in output.review_items for text in [x.action, x.reason]]
    fields += output.limitations
    allowed = {}
    for value in payload.get("exam", {}).get("measurements", []):
        allowed.setdefault(value["unit"], []).append(value["value"])
    if is_ct_quantitative:
        units = r"HU/mm|mm²|mm2|mm³|mm3|mm|HU|mL|cm³|cm3|cm|%|bit"
    else:
        units = r"mm³|mm3|mm|HU|cm³|cm3|cm|%|bit"
    aliases = {"mm³": "mm3", "mm²": "mm2", "hu": "HU", "hu/mm": "HU/mm", "ml": "mL"}
    for lesion in lesions.values():
        for group in ("measurements", "radiomics"):
            for value in lesion[group]:
                allowed.setdefault(value["unit"], []).append(value["value"])
    for text in fields:
        for value, unit in re.findall(rf"([-+]?\d+(?:\.\d+)?)\s*({units})", text, re.I):
            normalized = aliases.get(unit.lower(), unit.lower())
            if not any(measurement_matches(value, x, normalized)
                       for x in allowed.get(normalized, [])):
                raise ProviderError("模型输出包含不一致的测量值或单位。", True)

    for item in output.quantitative_interpretation:
        cited = [v for group in ("measurements", "radiomics")
                 for v in lesions[item.lesion_id][group] if v["evidence_id"] in item.evidence_ids]
        for value, unit in re.findall(rf"([-+]?\d+(?:\.\d+)?)\s*({units})", item.text, re.I):
            normalized = aliases.get(unit.lower(), unit.lower())
            if not any(v["unit"] == normalized and measurement_matches(value, v["value"], normalized) for v in cited):
                raise ProviderError("定量解读的测量值与引用指标不一致。", True)
        for value in re.findall(r"(?<![A-Za-z0-9])[-+]?\d+\.\d+(?![A-Za-z0-9])", item.text):
            if not any(measurement_matches(value, v["value"], v["unit"] or "") for v in cited):
                raise ProviderError("定量解读中的数值与引用指标不一致。", True)
    # Conservative scope gate: a keyword filter supplements, rather than proves, semantic safety.
    scope_text = output.result_summary + " ".join(x.text for x in output.quantitative_interpretation)
    scope_text += " ".join(x.action + x.reason for x in output.review_items)
    scope_text += " ".join(output.limitations)
    if re.search(r"恶性|良性|癌|侵袭性|病理|分期|高风险|低风险|治疗|化疗|放疗|切除|用药|malignan|benign|carcinoma|cancer", scope_text, re.I):
        raise ProviderError("模型输出超出定量解读范围。", True)
    # There are currently no guideline references: reject bypasses in prose as well.
    prose = output.model_dump_json()
    if not references and re.search(r"(?:[0-9一二三四五六七八九十两]+\s*[至到~—-]?\s*)+(?:个?月|周|天|年).{0,10}(?:复查|随访)|(?:复查|随访).{0,10}(?:[0-9一二三四五六七八九十两]+\s*)+(?:个?月|周|天|年)|穿刺|活检|PET[ -]?CT|手术|恶性概率|确诊为", prose, re.I):
        raise ProviderError("模型输出超出当前资料支持的临床参考范围。", True)
    if payload["quality"]["missing_fields"] or payload["quality"]["warnings"]:
        if output.analysis_status == "ready":
            output.analysis_status = "limited"
        output.limitations = list(dict.fromkeys(output.limitations + payload["quality"]["warnings"]))[:20]
    return output.model_dump()

def request_json(payload: dict, config, system_prompt: str, validator):
    base = config.LLM_API_BASE_URL.rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProviderError("云端模型地址配置不正确。")
    key = config.LLM_API_KEY.get_secret_value().strip()
    if not key:
        raise ProviderError("尚未配置云端模型 API Key。")
    body = {"model": config.LLM_MODEL, "stream": False,
            "messages": [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False, allow_nan=False)}],
            "max_tokens": config.LLM_MAX_TOKENS}
    if config.LLM_JSON_MODE:
        body["response_format"] = {"type": "json_object"}
    request = Request(base + "/chat/completions", data=json.dumps(body).encode(),
                      headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                      method="POST")
    # Reject redirects rather than forwarding credentials to a different endpoint.
    from urllib.request import HTTPRedirectHandler, build_opener
    class RejectRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    try:
        with build_opener(RejectRedirect()).open(request, timeout=config.LLM_TIMEOUT_SECONDS) as response:
            raw = response.read(2_000_001)
            if len(raw) > 2_000_000:
                raise ProviderError("云端模型响应过大。")
        data = json.loads(raw)
        choice = data["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ProviderError("云端模型输出未正常结束（" + str(choice.get("finish_reason"))[:30] + "），请重试。", True)
        content = choice["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ProviderError("云端模型返回了空内容。", True)
        result = validator(content, payload)
        usage = data.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        usage = {k: v for k, v in usage.items()
                 if k in ("prompt_tokens", "completion_tokens", "total_tokens")
                 and isinstance(v, int) and 0 <= v < 10000000}
        return result, usage
    except HTTPError as exc:
        code = exc.code
        raise ProviderError(f"云端模型请求失败（HTTP {code}）。", code == 429 or code >= 500) from None
    except (URLError, TimeoutError, socket.timeout, OSError):
        raise ProviderError("云端模型连接失败或请求超时。", True) from None
    except (ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise ProviderError("云端模型响应格式不正确。", True) from None

def _request(payload: dict, config) -> tuple[dict, dict]:
    return request_json(payload, config, SYSTEM_PROMPT, validate_output)

async def generate(payload: dict, config) -> tuple[dict, dict]:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_request, payload, config),
                                      timeout=config.LLM_TIMEOUT_SECONDS + 5)
    except TimeoutError:
        raise ProviderError("云端模型请求超时。", True) from None
