"""Deterministic clinical logic checks for the approved chest CT candidate scope."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

from app.services.ct_report_bindings import (
    FAIL, PASS, REVIEW, all_fact_refs, check_anchors, check_references, finite_number,
    structure_errors, text_ref, unique_refs,
)


AXIAL = "measure.diameter.axial_max"
THREE_D = "measure.diameter.3d_max"
HU_METHODS = {
    "measure.attenuation.mean": "arithmetic_mean_verified_hu_within_candidate_mask",
    "measure.attenuation.median": "median_verified_hu_within_candidate_mask",
}
SIZE_REVIEW_MM = 30
LOBE_OVERLAP = 0.8
LOBE_SIDE = {
    "anatomy.lung.right.upper":"right", "anatomy.lung.right.middle":"right",
    "anatomy.lung.right.lower":"right", "anatomy.lung.left.upper":"left",
    "anatomy.lung.left.lower":"left",
}
NORMALIZED_LENGTH = {
    AXIAL, THREE_D, "measure.diameter.sagittal_max", "measure.diameter.coronal_max",
    "measure.diameter.short_axis",
}
OBSERVATIONS = {"density":"密度", "calcification":"钙化", "spiculation":"毛刺"}
HINT_VALUES = {
    "density":{"hint.density.solid":"实性","hint.density.non_solid":"非实性"},
    "calcification":{"hint.calcification.not_indicated":"无钙化","hint.calcification.present":"有钙化"},
    "spiculation":{"hint.spiculation.region":"毛刺候选区域"},
}
GAPS = {"short_axis":"短径","volume":"体积","boundary":"边界","mean_hu":"ROI 平均 CT 值"}
UNASSESSED = ("not_emitted","not_assessed","unavailable")
EXCESS_CLAIMS = {
    "clinical_assertion","diagnosis","impression","recommendation","longitudinal_comparison",
    "malignancy","malignancy_probability","clinical_diagnosis",
}


def safe(value):
    """Allow invalid nonfinite measurement input to appear in a standard JSON report."""
    if isinstance(value,dict):
        return {key:safe(item) for key,item in value.items()}
    if isinstance(value,list):
        return [safe(item) for item in value]
    if type(value) is float and not finite_number(value):
        return repr(value)
    return value


def forbidden_paths(node,path="$"):
    if isinstance(node,dict):
        for key,item in node.items():
            field=f"{path}.{key}"
            if key in EXCESS_CLAIMS:
                yield field
            yield from forbidden_paths(item,field)
    elif isinstance(node,list):
        for i,item in enumerate(node):
            yield from forbidden_paths(item,f"{path}[{i}]")


def valid_measurement(row):
    value,concept=row.get("value"),row.get("concept_id")
    unit=(row.get("unit"),row.get("unit_code"))
    if not finite_number(value) or not text_ref(concept) or row.get("source_state")!="measured":
        return False
    if concept in NORMALIZED_LENGTH:
        return unit==("mm","mm") and value>0
    if concept in HU_METHODS:
        return unit==("HU","[hnsf'U]")
    if concept in ("measure.volume.voxel","measure.volume.total"):
        return unit in (("mm3","mm3"),("mL","mL")) and value>0
    if concept=="measure.volume.above_threshold_fraction":
        return unit==("%","%") and 0<=value<=100
    return False


class Review:
    def __init__(self,doc):
        self.doc=doc
        self.checks=[]
        self.invalid_paths=set()
        self.invalid_anchors=set()

    @staticmethod
    def subject(f,key=None):
        name=f'{f["finding_id"]} / {f["candidate_ref"]}'
        return f"{name} · {key}" if key is not None else name

    def emit(self,rule,subject,status,detail,paths,*,observed=None,evidence=(),anchors=(),action=None,gate="field"):
        default={
            PASS:"保留已核对的值和证据，按原使用范围组织。",
            REVIEW:"保留现值及状态，取得对应证据后复核。",
            FAIL:"暂停整份输入，修正绑定后重跑。" if gate=="batch" else "暂停使用受影响的字段或定位，修正证据后重跑。",
        }
        self.checks.append({
            "rule_id":rule,"subject":subject,"status":status,"field_paths":paths,
            "detail":detail,"observed":safe(observed if observed is not None else {}),
            "evidence_ids":list(dict.fromkeys(evidence)),
            "anchor_refs":list(dict.fromkeys(anchors)),
            "blocking_scope":gate if status==FAIL else "none",
            "action":action or default[status],
        })

    def refs(self,row,key="evidence_ids"):
        return all_fact_refs(row,key)

    def locations(self,refs):
        return list(dict.fromkeys(a for ref in refs for a in all_fact_refs(self.evidence[ref],"anchor_refs")))

    def run(self):
        errors=structure_errors(self.doc)
        self.emit("R01","整份 CT 对象",FAIL if errors else PASS,
                  "必要对象或数组缺失，或包含当前输入契约之外的顶层字段。" if errors else "五个顶层字段及对象、数组类型符合精简契约。",
                  errors or ["$"],observed={"top_level_fields":list(self.doc) if isinstance(self.doc,dict) else []},gate="batch")
        if errors:
            return
        self.study,self.scope=self.doc["study"],self.doc["analysis_scope"]
        if not self.check_study():
            return
        if not self.check_candidate_count():
            return
        self.evidence={item.get("evidence_id"):item for item in self.doc["evidence"] if text_ref(item.get("evidence_id"))}
        self.anchors={item.get("anchor_ref"):item for item in self.doc["image_anchors"] if text_ref(item.get("anchor_ref"))}
        if not check_references(self):
            return
        check_anchors(self)
        for i,f in enumerate(self.doc["findings"]):
            path=f"$.findings[{i}]"
            self.check_anatomy(f,path+".anatomy")
            self.check_measurements(f,path)
            self.check_observations(f,path)
            self.check_gaps(f,path)
        self.check_report_scope()
        self.check_contrast()

    def check_study(self):
        series=self.study["series"]
        ref=self.scope.get("series_ref")
        accepted=(
            self.study.get("modality")=="CT" and self.study.get("body_region")=="chest"
            and text_ref(ref) and len(series)==1 and series[0].get("series_ref")==ref
            and all(item.get("series_ref")==ref for key in ("findings","evidence","image_anchors") for item in self.doc[key])
        )
        self.emit("R02","检查与序列",PASS if accepted else FAIL,
                  "胸部 CT 的检查、候选、证据及锚点指向同一输入序列。" if accepted else "检查不在适用范围内，或序列绑定不一致。",
                  ["$.study","$.analysis_scope.series_ref"],observed={"series_ref":ref,"modality":self.study.get("modality"),"body_region":self.study.get("body_region")},gate="batch")
        return accepted

    def check_candidate_count(self):
        detect=self.scope["candidate_detection"]
        count,state=detect.get("count"),detect.get("state")
        findings=self.doc["findings"]
        ids=all(unique_refs(findings,key) for key in ("finding_id","candidate_ref","component_ref"))
        ready=state=="completed" and type(count) is int and count>=0 and count==len(findings) and ids
        pending=state in ("unknown","not_assessed","unavailable") and count is None and not findings
        self.emit("R03","候选清单",PASS if ready else REVIEW if pending else FAIL,
                  "检测完成，数量与候选清单一致，候选引用唯一。" if ready else "候选检测未取得完整结果。" if pending else "候选状态、数量或候选身份不一致。",
                  ["$.analysis_scope.candidate_detection","$.findings"],
                  evidence=self.refs(detect),observed={"state":state,"declared_count":count,"actual_count":len(findings)},gate="batch")
        return ready

    def check_anatomy(self,f,path):
        if path in self.invalid_paths:
            return
        body=f["anatomy"]
        side,loc=body.get("side"),body.get("location")
        concept=loc.get("concept_id") if isinstance(loc,dict) else None
        target=LOBE_SIDE.get(concept) if text_ref(concept) else None
        fraction=body.get("lobe_overlap_fraction")
        refs=self.refs(body)
        lobe_masks={r.get("mask_ref") for r in self.scope["region_masks"] if r.get("kind")=="lung_lobes" and text_ref(r.get("mask_ref"))}
        wrong=(body.get("organ") not in (None,"lung") or side not in (None,"left","right")
               or (target and side and target!=side)
               or (fraction is not None and (not finite_number(fraction) or not 0<=fraction<=1)))
        ready=(not wrong and target is not None and side==target and body.get("organ")=="lung"
               and body.get("method")=="candidate_mask_lobe_overlap" and body.get("source_state")=="geometry_estimate"
               and finite_number(fraction) and fraction>=LOBE_OVERLAP
               and bool(refs) and all(text_ref(self.evidence[ref].get("anatomy_mask_ref")) and self.evidence[ref]["anatomy_mask_ref"] in lobe_masks for ref in refs))
        self.emit("R04",self.subject(f),FAIL if wrong else PASS if ready else REVIEW,
                  "器官、侧别、肺叶和候选体素重叠核对通过。" if ready else "器官、侧别或重叠比例冲突。" if wrong else "肺叶归属依据不完整，保留可靠的定位粒度。",
                  [path],observed={"organ":body.get("organ"),"side":side,"location":loc,"overlap_fraction":fraction,"threshold":LOBE_OVERLAP},evidence=refs)

    def check_measurements(self,f,path):
        admitted=[]
        for i,row in enumerate(f["measurements"]):
            field=f"{path}.measurements[{i}]"
            if field in self.invalid_paths:
                continue
            subject=self.subject(f,row.get("label_zh") or row.get("concept_id") or f"测量 {i+1}")
            ref=self.refs(row)
            observed={key:row.get(key) for key in ("concept_id","value","unit","unit_code","method")}
            ok=valid_measurement(row)
            self.emit("R05",subject,PASS if ok else FAIL,
                      "数值、单位与该测量类型一致。" if ok else "测量类型、数值、单位或来源状态不符合要求。",
                      [field],observed=observed,evidence=ref)
            if not ok:
                continue
            concept=row["concept_id"]
            if concept==AXIAL:
                points=self.locations(ref)
                cites_wrong=any(self.anchors[a].get("role")!="measurement_line" for a in points)
                wrong=row.get("method") not in (None,"same_plane_occupied_voxel_centers") or cites_wrong
                ready=not wrong and row.get("method")=="same_plane_occupied_voxel_centers" and bool(points) and not any(a in self.invalid_anchors for a in points)
                self.emit("R06",self.subject(f),FAIL if wrong else PASS if ready else REVIEW,
                         "轴位口径及原片测量线一致。" if ready else "轴位径线的测量方法或锚点角色错误。" if wrong else "轴位测量线或方法需复核。",
                         [field],observed=observed,evidence=ref,anchors=points)
                if row.get("method")=="same_plane_occupied_voxel_centers" and not wrong:
                    admitted.append((field,observed,ref))
            elif concept==THREE_D:
                algorithms=[self.evidence[e].get("method_details") for e in ref]
                wrong=row.get("method") not in (None,"mask_mesh_maximum_distance")
                ready=(not wrong and row.get("method")=="mask_mesh_maximum_distance"
                       and all(isinstance(a,dict) and text_ref(a.get("method")) and type(a.get("sampling")) is bool for a in algorithms))
                self.emit("R07",self.subject(f),FAIL if wrong else PASS if ready else REVIEW,
                          "三维网格测量、算法、采样状态和掩膜组件齐全。" if ready else "三维测量口径不匹配。" if wrong else "三维测量算法或采样依据不全。",
                          [field],observed=observed|{"methods":algorithms,"component_ref":f["component_ref"]},evidence=ref)
                if row.get("method")=="mask_mesh_maximum_distance" and not wrong:
                    admitted.append((field,observed,ref))
            elif concept in HU_METHODS:
                ready=self.scope.get("hu_source")=="verified" and row.get("method")==HU_METHODS[concept] and all(self.evidence[e].get("hu_verified") is True for e in ref)
                self.emit("R08",self.subject(f),PASS if ready else REVIEW,
                          "检查范围和该测量的 HU 来源均已核对。" if ready else "HU 来源或强度测量口径待复核。",
                          [field,"$.analysis_scope.hu_source"],observed=observed|{"hu_source":self.scope.get("hu_source")},evidence=ref,
                          action=None if ready else "暂停使用该强度结论，核对原始 HU 来源及测量口径。")
        if admitted:
            sizes=[item[1]["value"] for item in admitted]
            flagged=max(sizes)>SIZE_REVIEW_MM
            self.emit("R09",self.subject(f),REVIEW if flagged else PASS,
                      "至少一项最大径超过 30 mm，保留各自测量口径并复核命名。" if flagged else "现有最大径未触发 30 mm 术语复核提醒。",
                      [item[0] for item in admitted],observed={
                          "measurements":[item[1] for item in admitted],
                          "review_threshold":{"value":SIZE_REVIEW_MM,"unit":"mm"},
                          "crosses_threshold":min(sizes)<=SIZE_REVIEW_MM<max(sizes),
                      },evidence=[ref for _,_,refs in admitted for ref in refs],
                      action="保留候选标签及各项原始测量，请医生确认用于命名的口径。" if flagged else None)

    def check_observations(self,f,path):
        # Equality-based counting also rejects malformed non-string kinds below.
        kinds=[item.get("kind") for item in f["observations"]]
        for i,row in enumerate(f["observations"]):
            field=f"{path}.observations[{i}]"
            if field in self.invalid_paths:
                continue
            kind,state,value=row.get("kind"),row.get("assessment_state"),row.get("value")
            name=OBSERVATIONS.get(kind) if text_ref(kind) else None
            subject=self.subject(f,name or f"征象 {i+1}")
            primary=row["primary_evidence_id"]
            source=self.evidence[primary]
            concept=value.get("concept_id") if isinstance(value,dict) else None
            label=value.get("label_zh") if isinstance(value,dict) else None
            eligible=row.get("report_use")
            display=row.get("display_zh")
            ok=False
            if name and kinds.count(kind)==1 and source.get("source_state")==state:
                if state=="model_hint":
                    mapping=HINT_VALUES[kind]
                    ok=(source.get("kind")=="model_output" and text_ref(concept) and concept in mapping
                        and label==mapping[concept] and display==label and eligible=="review_only")
                elif state=="indeterminate":
                    ok=source.get("kind")=="model_output" and value is None and display=="尚不确定" and eligible=="review_only"
                elif state in UNASSESSED:
                    expected_kind="source_omission" if state=="not_emitted" else "model_output"
                    ok=source.get("kind")==expected_kind and value is None and eligible in ("review_only","audit_only") and display in (
                        "此来源未输出","未评估","不可用","尚不确定")
            observed={"kind":kind,"assessment_state":state,"value":value,"display_zh":display,
                      "report_use":eligible,"evidence_state":source.get("source_state")}
            self.emit("R12",subject,PASS if ok else FAIL,
                      "主征象唯一，术语、值、模型状态和主证据一致。" if ok else "征象重复，或术语、值、状态、使用范围与证据冲突。",
                      [field],observed=observed,evidence=[primary])
            if not ok:
                continue
            for key in row.get("related_evidence_ids",[]):
                self.check_related(subject,field,state,primary,key)
            if kind=="spiculation":
                self.check_spiculation(f,field,state,primary)
            if eligible=="review_only":
                self.emit("R18",subject,REVIEW,"专项模型结果仅供阅片复核。",
                          [field],observed=observed,evidence=[primary],anchors=self.locations([primary]),
                          action="保持在「专项模型结果 · 待复核」区，正式所见需核对同一候选及对应原片。")

    def check_related(self,subject,path,state,primary,ref):
        related=self.evidence[ref]
        kind, secondary=related.get("kind"),related.get("source_state")
        if kind=="source_omission":
            consistent=secondary=="not_emitted" and not related.get("anchor_refs")
            status=PASS if consistent else FAIL
            detail="关联证据记录此来源未输出。" if consistent else "缺项证据与其状态或影像定位矛盾。"
        elif kind=="model_output" and secondary not in ("model_hint","indeterminate","not_assessed","unavailable"):
            status,detail=FAIL,"关联模型证据状态无效。"
        elif secondary!=state:
            status,detail=REVIEW,"关联模型状态与主证据不同，需核对上游原始记录。"
        else:
            status,detail=PASS,"本精简对象中的主证据与关联证据状态一致。"
        self.emit("R13",subject,status,detail,[path+".related_evidence_ids"],
                  observed={"related_kind":kind,"related_state":secondary,"primary_state":state},evidence=[primary,ref])

    def check_spiculation(self,f,path,state,ref):
        points=self.locations([ref])
        roles=[self.anchors[key].get("role") for key in points]
        expected="model_finding_region" if state=="model_hint" else "candidate_region"
        wrong=any(role!=expected for role in roles)
        pending=(state=="model_hint" and not points) or any(key in self.invalid_anchors for key in points)
        self.emit("R14",self.subject(f),FAIL if wrong else REVIEW if pending else PASS,
                  "毛刺证据与锚点角色一致。" if not (wrong or pending) else "毛刺锚点用途或证据需校验。",
                  [path],observed={"assessment_state":state,"anchor_roles":roles},evidence=[ref],anchors=points)

    def check_gaps(self,f,path):
        for i,gap in enumerate(f["data_gaps"]):
            key=gap.get("field")
            valid=text_ref(key) and text_ref(gap.get("state"))
            label=GAPS.get(key,key) if text_ref(key) else f"缺项 {i+1}"
            self.emit("R17",self.subject(f,label),REVIEW if valid else FAIL,
                      "保留缺项及原因。" if valid else "缺项名称或原因无效。",
                      [f"{path}.data_gaps[{i}]"],observed=gap,
                      action="需要该字段时以同一候选、同一序列的已核对来源补入，并重新校验。" if valid else None)

    def check_report_scope(self):
        paths=list(forbidden_paths(self.doc))
        unsupported_coverage=self.scope.get("coverage")!="lung_nodule_candidates" or self.scope.get("other_chest_findings")!="outside_scope"
        if unsupported_coverage:
            paths.append("$.analysis_scope")
        for i,f in enumerate(self.doc["findings"]):
            c=f["classification"]
            if c.get("source_state")!="candidate" or c.get("concept_id")!="obs.pulmonary_nodule_candidate" or c.get("label_zh")!="肺结节候选":
                paths.append(f"$.findings[{i}].classification")
            for j,o in enumerate(f["observations"]):
                if o.get("report_use") not in ("review_only","audit_only"):
                    paths.append(f"$.findings[{i}].observations[{j}].report_use")
        self.emit("R15","报告可用范围",FAIL if paths else PASS,
                  "结论、范围或使用权限超出已绑定的候选证据。" if paths else "只提供本次肺结节候选所见，专项结果限于复核范围。",
                  paths or ["$.analysis_scope","$.findings"],
                  observed={"coverage":self.scope.get("coverage"),"other_chest_findings":self.scope.get("other_chest_findings")},
                  gate="batch" if unsupported_coverage else "field")

    def check_contrast(self):
        path="$.study.contrast"
        if path in self.invalid_paths:
            return
        contrast=self.study["contrast"]
        value,state=contrast.get("value"),contrast.get("state")
        refs=self.refs(contrast)
        facts=[self.evidence[ref].get("value") for ref in refs]
        valid_values=[item for item in facts if type(item) is bool]
        conflicting=len(set(valid_values))>1 or (type(value) is bool and any(v!=value for v in valid_values))
        wrong=conflicting or (state=="unknown" and value is not None) or (value is not None and type(value) is not bool)
        ready=type(value) is bool and bool(valid_values) and not wrong
        self.emit("R16","检查增强状态",FAIL if wrong else PASS if ready else REVIEW,
                  "增强状态有同次检查技术证据。" if ready else "增强状态及证据相冲突。" if wrong else "增强状态缺少可确认的依据。",
                  [path],observed=contrast,evidence=refs,
                  action=None if ready or wrong else "保留增强未知，需要明确技术描述时核对同一次检查的原始记录。")


def report_disposition(run,batch):
    blocked=list(dict.fromkeys(
        path for row in run.checks if row["status"]==FAIL for path in row["field_paths"]
    ))
    if batch or not hasattr(run,"evidence"):
        return {"structured_fact_paths":[],"blocked_field_paths":blocked}
    failed=set(blocked)
    passed={(row["rule_id"],path) for row in run.checks if row["status"]==PASS for path in row["field_paths"]}
    allowed=[]
    for i,f in enumerate(run.doc["findings"]):
        root=f"$.findings[{i}]"
        if root+".classification" in failed or root+".classification" in run.invalid_paths:
            continue
        candidates=[(root+".classification",f["classification"],()),
                    (root+".anatomy",f["anatomy"],("R04",))]
        for j,m in enumerate(f["measurements"]):
            concept=m.get("concept_id")
            form=("R06",) if concept==AXIAL else ("R07",) if concept==THREE_D else ("R08",) if text_ref(concept) and concept in HU_METHODS else ()
            candidates.append((f"{root}.measurements[{j}]",m,("R05",)+form))
        for path,obj,rules in candidates:
            if (obj.get("report_use")=="structured_input" and path not in failed and path not in run.invalid_paths
                    and all((rule,path) in passed for rule in rules)):
                allowed.append(path)
    return {"structured_fact_paths":allowed,"blocked_field_paths":blocked}


def validate(doc):
    run=Review(doc)
    run.run()
    counts=Counter(item["status"] for item in run.checks)
    batch=any(item["status"]==FAIL and item["blocking_scope"]=="batch" for item in run.checks)
    status="blocked" if batch else "limited" if counts[REVIEW] or counts[FAIL] else "passed"
    return {
        "rule_set":"chest-ct-clinical-rules-v1",
        "report_input_status":status,
        "summary":{key:counts[key] for key in (PASS,REVIEW,FAIL)},
        "check_count":len(run.checks),"report_disposition":report_disposition(run,batch),"checks":run.checks,
    }
