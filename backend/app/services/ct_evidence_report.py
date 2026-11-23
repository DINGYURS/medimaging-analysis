"""Generate source-bound chest CT reports and check every cloud-authored clause."""
import asyncio
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

import pydicom

from app.core.config import settings
from app.core.queue_gate import gpu_gate
from app.db.session import AsyncSessionLocal
from app.services import ct_chest_trial, ct_quantitative_plan as plan
from app.services import ct_report_structure as structure
from app.services.ct_insight_draft import build_draft
from app.services.ct_structured_report import visible_anchor
from app.services.insight_client import ProviderError, request_json
from app.services.insight_payload import signature

VERSION = "ct-evidence-report-v1"
RUNNER = Path(__file__).with_name("ct_specialist_evidence.py")
PROMPT = """你是中文胸部CT报告起草助手。输入 findings 和 impression 各自按主体列出已验证的短语和证据编号。
输出且只输出同名两个数组。每项为 {"subject":"原主体键","text":"中文报告正文","evidence_ids":["对应编号"]}。
每个主体各出现一次，subject 原样复制，包括 L1 等编号。每个短语都要使用且只用一次，保留原词、数值、单位、限定词及证据归属。
按阅读顺序组织短语，可调整先后，使用中文逗号、句号或分号连接。不要新增词句、标题、编号、建议或历史对比。回复必须是唯一一个完整 JSON 对象，首字符为 {，末字符为 }。禁止 Markdown 和任何解释。
格式示例（只示意结构，实际值来自输入）：{"findings":[{"subject":"L1","text":"左肺上叶见候选灶，模型提示呈实性密度。","evidence_ids":["L1:site","L1:density"]}],"impression":[{"subject":"L1","text":"左肺上叶见候选灶，模型提示呈实性密度。","evidence_ids":["L1:site","L1:density"]}]}"""
PROMPT_HASH = hashlib.sha256(PROMPT.encode()).hexdigest()

async def snapshot(task, db):
    source = await plan.build_snapshot(task, db)
    source["schema_version"] = VERSION
    source["pipeline"].extend(signature(p) for p in (RUNNER, Path(__file__), structure.__file__, structure.DICTIONARY, Path(__file__).with_name("ct_report_rules.py"), Path(__file__).with_name("ct_report_bindings.py")))
    trial = await asyncio.to_thread(ct_chest_trial.result_for, task.id, task.study)
    source["effusions"] = {}
    if trial and trial.get("status") == "technical_test":
        for model in trial["models"]:
            if model["id"] == "effusion":
                for row in model["findings"]:
                    if row["label"] in ("胸腔积液", "心包积液") and type(row["voxels"]) is int and row["voxels"] >= 0:
                        source["effusions"][row["label"]] = row["voxels"]
    return source

def infer(source):
    with TemporaryDirectory(prefix="ct-report-") as temp:
        src, dst = [Path(temp) / name for name in ("source.json", "result.json")]
        src.write_text(json.dumps({**source["models"], "intensity_unit_verified":"HU"}))
        subprocess.run([str(plan.specialist.RUNTIME), str(RUNNER), str(plan.specialist.MODEL_ROOT), str(src), str(dst)],
                       env={**os.environ, "CUDA_VISIBLE_DEVICES":str(settings.CT_REFERENCE_LOBE_GPU),
                            "OMP_NUM_THREADS":"2","OPENBLAS_NUM_THREADS":"2"}, check=True, timeout=260,
                       stdout=subprocess.DEVNULL)
        return json.loads(dst.read_text())

def validate(content, payload):
    try:
        report = json.loads(content)
        if set(report) != {"findings","impression"}:
            raise ValueError("报告栏目不完整")
        for section, groups in payload.items():
            rows = report[section]
            if Counter(r["subject"] for r in rows) != Counter(list(groups)):
                raise ValueError(section+" 病灶归属不一致")
            for r in rows:
                facts = groups[r["subject"]]
                if set(r) != {"subject","text","evidence_ids"} or Counter(r["evidence_ids"]) != Counter(list(facts)):
                    raise ValueError(r["subject"]+" 证据编号不一致")
                clauses = [re.sub(r"\s+","",s) for s in re.split("[，,；;。]",r["text"]) if s.strip()]
                if Counter(clauses) != Counter(re.sub(r"\s+","",s) for s in facts.values()):
                    raise ValueError(r["subject"]+" 医学事实与来源不一致")
        return report
    except (TypeError,KeyError,ValueError) as exc:
        raise ProviderError("报告一致性校验失败："+str(exc),True) from None

def technique(source):
    fields = ("StudyDate","SliceThickness","ConvolutionKernel","ContrastBolusAgent")
    values = {k:set() for k in fields}
    for path,*_ in source["dicom_files"]:
        ds = pydicom.dcmread(path, stop_before_pixels=True,specific_tags=list(fields))
        for k in fields:
            values[k].add(str(ds.get(k,"")).strip())
    def unique(k):
        return next(iter(values[k])) if len(values[k])==1 else ""
    date=unique("StudyDate")
    items=[f"{len(source['dicom_files'])} 层"]
    if unique("SliceThickness"): items.append(f"层厚 {float(unique('SliceThickness')):g} mm")
    if unique("ConvolutionKernel"): items.append("重建核 "+unique("ConvolutionKernel"))
    items.append("有造影剂记录" if unique("ContrastBolusAgent") else "增强状态未确认")
    return {"exam":"胸部 CT"+(" · "+date[:4]+"-"+date[4:6]+"-"+date[6:] if re.fullmatch(r"\d{8}",date) else ""),
            "technique":"，".join(items)}

async def generate(source, run_id):
    q=plan.quantitative
    async with AsyncSessionLocal() as db:
        history=await q.rad.load_history(db,source["history"])
    measured=await asyncio.to_thread(q.measure,source,history)
    verified=q.cloud_payload(measured)
    choices=plan.selection_payload(verified)
    selection={"findings":[{"lesion_id":r["lesion_id"],"evidence_ids":[m["id"] for m in r["metrics"][:3]]} for r in choices["candidates"]]}
    result=plan.render(selection,verified)
    raw={"candidates":[]}
    if measured["hu_verified"] and measured["lesions"] and source["models"]:
        await gpu_gate.acquire()
        try:
            raw=await asyncio.to_thread(infer,source)
        finally:
            gpu_gate.release()
    hints=[]
    observations={}
    for r in raw["candidates"]:
        owner,kind=r["lesion_id"],r["kind"]
        if kind in observations.setdefault(owner,{}):
            raise ProviderError("专项输出重复")
        observations[owner][kind]=r
        if r["status"]=="model_hint":
            title,value,model=plan.specialist.LABELS[kind,r["code"]]
            if r["model"]!=model or r["id"]!=owner+":"+kind: raise ProviderError("专项来源不匹配")
            hints.append(dict(evidence_id=r["id"],lesion_id=owner,label=title,value=value,model=model,
                              anchor=r["anchor"] if kind=="spiculation" else None,unit=None))
    evidence=[fact for item in verified["findings"]["lesions"] for group in ("measurements","radiomics") for fact in item[group]]
    audit={"measurements":measured,"cloud_input":choices,"cloud_output":selection,"specialist":{"evidence":hints},
           "evidence":[*evidence,*hints],"attribute_results":raw}
    result["specialist_evidence_ids"]=[h["evidence_id"] for h in hints]
    draft=await asyncio.to_thread(build_draft,run_id,settings.LLM_MODEL,source,result,audit)
    draft.update(await asyncio.to_thread(technique,source))
    groups={}; brief={}
    metrics={f["evidence_id"]:f for f in evidence}
    doc,checks=structure.assess(source,measured,metrics,observations)
    audit.update(structured_findings=doc,clinical_rules=checks)
    allowed=set(checks["report_disposition"]["structured_fact_paths"])
    permitted={}
    for i,f in enumerate(doc["findings"]):
        base=f"$.findings[{i}]"
        permitted[f["candidate_ref"]]={m["evidence_ids"][0] for j,m in enumerate(f["measurements"]) if f"{base}.measurements[{j}]" in allowed}
        if base+".anatomy" in allowed: permitted[f["candidate_ref"]].add(f["candidate_ref"]+":site")
    for row in draft["findings"]:
        owner=row["lesion_id"]
        if owner+":site" not in permitted[owner]: row["location"]=None
        facts={owner+":site":(row["location"] or "肺部")+"见候选灶"}
        brief[owner]=dict(facts)
        for kind in ("axial_diameter","diameter_3d_mm","mean_hu"):
            key=owner+":"+kind
            if key in permitted[owner]: facts[key]=plan.display_value(metrics[key]).replace("平均强度","候选区域平均CT值")
        if row["anchor"] and owner+":axial_diameter" in permitted[owner]: facts[owner+":axial_slice"]=f"轴位测量位于第 {row['anchor']['slice']} 层"
        for kind,r in observations.get(owner,{}).items():
            key=r["id"]
            if r["status"]=="indeterminate":
                facts[key]={"density":"密度类型不确定","calcification":"钙化情况不确定","spiculation":"毛刺征不确定"}[kind]
            elif r["status"]=="model_hint":
                phrase={"non_solid":"模型提示呈非实性密度","solid":"模型提示呈实性密度","localized":"局部见可疑毛刺影",
                        "absent":"模型未检出钙化","present":"模型提示有钙化"}[r["code"]]
                facts[key]=phrase
                if kind!="calcification" or r["code"]=="present": brief[owner][key]=phrase
                if kind=="spiculation" and visible_anchor(r["anchor"]):
                    facts[key+":slice"]=f"可疑毛刺影位于第 {r['anchor']['slice']} 层"
        groups[owner]=facts
    for label,count in source["effusions"].items():
        groups[label]={"trial:"+label:f"{label}专项模型技术试验分割结果为 {count} 个体素"}
    payload={"findings":groups,"impression":brief}
    if len(json.dumps(payload,ensure_ascii=False).encode())>settings.LLM_MAX_INPUT_BYTES:
        raise ProviderError("报告证据超出云端输入上限")
    report,usage={"findings":[],"impression":[]},{}
    if groups:
        prompt=PROMPT
        for attempt in range(2):
            try:
                report,usage=await asyncio.to_thread(request_json,payload,settings,prompt,validate)
                break
            except ProviderError as exc:
                if attempt or not exc.retryable: raise
                prompt=PROMPT+"\n上次未通过校验。只输出单个对象，每条 text 原样连接该主体输入的全部短语；主体键和 evidence_ids 原样保留。"
    indexed={row["subject"]:row for row in report["findings"]}
    for row in draft["findings"]:
        line=indexed[row["lesion_id"]]
        row.update(text=line["text"],evidence_ids=line["evidence_ids"])
    draft.update(schema_version=VERSION,generated_by=settings.LLM_MODEL,impression=[r["text"] for r in report["impression"]],
                 effusions=[indexed[label]["text"] for label in source["effusions"]],fact_checked=True)
    audit.update(report_input=payload,report_output=report,clinical_report=draft)
    return result,usage,audit
