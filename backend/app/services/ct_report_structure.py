"""Project live model measurements into the versioned clinical-rule vocabulary."""
import json
from pathlib import Path

from app.services.ct_report_rules import HINT_VALUES, validate
from app.services.insight_client import ProviderError

DICTIONARY = Path(__file__).with_name("ct_report_terms.json")
TERMS = json.loads(DICTIONARY.read_text())["concepts"]
LOBE_IDS = {v["label_zh"]:k for k,v in TERMS.items() if k.startswith("anatomy.lung.") and k.count(".")==3}
MEASURES = {
    "axial_diameter":("measure.diameter.axial_max","same_plane_occupied_voxel_centers","mm"),
    "diameter_3d_mm":("measure.diameter.3d_max","mask_mesh_maximum_distance","mm"),
    "mean_hu":("measure.attenuation.mean","arithmetic_mean_verified_hu_within_candidate_mask","[hnsf'U]"),
}
HINTS = {"solid":"hint.density.solid","non_solid":"hint.density.non_solid","present":"hint.calcification.present",
         "absent":"hint.calcification.not_indicated","localized":"hint.spiculation.region"}

def term(key):
    return {"concept_id":key,"label_zh":TERMS[key]["label_zh"]}

def assess(source, measured, metrics, observations):
    series="SERIES1"
    count=measured["distribution"]["candidate_count"]
    base={"series_ref":series,"anchor_refs":[]}
    evidence=[dict(base,evidence_id="count",kind="candidate_detection",finding_id=None)]
    anchors=[]; findings=[]
    for m in measured["lesions"]:
        owner=m["id"]
        component=owner+":mask"
        def record(key,kind,**details):
            evidence.append(dict(base,evidence_id=key,kind=kind,finding_id=owner,mask_ref="MASK1",component_ref=component)|details)
            return key
        def anchor(key, role, layer, geometry):
            anchors.append(dict(anchor_ref=key,finding_id=owner,series_ref=series,role=role,viewer_slice_number=layer,
                                native_slice_index=layer-1,geometry=geometry))
            return key
        where=m.get("location") or {}
        loc=where.get("lobe")
        anatomy=dict(organ="lung",side=where.get("side"),location=term(LOBE_IDS[loc]) if loc else None,
                     lobe_overlap_fraction=where.get("fractions",{}).get(loc),method="candidate_mask_lobe_overlap",
                     source_state="geometry_estimate",report_use="structured_input",
                     evidence_ids=[record(owner+":site","anatomy_mask_overlap",anatomy_mask_ref="LOBES")] if loc or where.get("side") else [])
        measures=[]; obs=[]
        for name,(concept,method,unit_code) in MEASURES.items():
            key=owner+":"+name
            if key not in metrics: continue
            v=metrics[key]; meta={}
            if name=="axial_diameter":
                line=m["geometry"]["diameters"]["axial"]
                meta["anchor_refs"]=[anchor(owner+":axial_slice","measurement_line",line["slice_index"]+1,
                    dict(coordinate_system="native_image_pixel_xy_zero_based",endpoints_xy=line["endpoints_xy"]))]
            elif name=="diameter_3d_mm": meta["method_details"]=m["geometry"]["diameter_algorithm"]
            else: meta["hu_verified"]=measured["hu_verified"]
            measures.append(dict(term(concept),method=method,value=v["value"],unit=v["unit"],unit_code=unit_code,
                                 source_state="measured",report_use="structured_input",evidence_ids=[record(key,"measurement",**meta)]))
        for kind,r in observations.get(owner,{}).items():
            status=r["status"];key=r["id"];geo=r.get("anchor"); refs=[]
            if geo:
                if geo.get("series_instance_uid")!=source["series_uid"]: raise ProviderError("专项定位序列不符")
                localized=kind=="spiculation" and status=="model_hint"
                refs.append(anchor(key+":slice","model_finding_region" if localized else "candidate_region",geo["slice"],
                                   dict(coordinate_system="normalized_image_xyxy",box_xy=geo["box_xy"])))
            concept=HINTS[r["code"]] if status=="model_hint" else None
            val={"concept_id":concept,"label_zh":HINT_VALUES[kind][concept]} if concept else None
            obs.append(dict(kind=kind,value=val,assessment_state=status,display_zh=val["label_zh"] if val else
                            ("尚不确定" if status=="indeterminate" else "不可用"),report_use="review_only",
                            primary_evidence_id=record(key,"model_output",source_state=status,anchor_refs=refs),related_evidence_ids=[]))
        findings.append(dict(finding_id=owner,candidate_ref=owner,series_ref=series,component_ref=component,
                             anatomy=anatomy,measurements=measures,observations=obs,data_gaps=[],
                             classification=dict(term("obs.pulmonary_nodule_candidate"),source_state="candidate",
                             report_use="structured_input",evidence_ids=[record(owner+":candidate","mask_component")])))
    doc=dict(study=dict(modality="CT",body_region="chest",contrast=dict(state="unknown",value=None,evidence_ids=[]),
                        series=[dict(series_ref=series,slice_count=measured["protocol"]["dicom_slice_count"])]),
             analysis_scope=dict(series_ref=series,candidate_mask_ref="MASK1",region_masks=[dict(mask_ref="LOBES",kind="lung_lobes")] if "lobes" in measured["regions"] else [],
                                 coverage="lung_nodule_candidates",candidate_detection=dict(state="completed",count=count,evidence_ids=["count"]),
                                 other_chest_findings="outside_scope",hu_source="verified" if measured["hu_verified"] else "unverified"),
             findings=findings,evidence=evidence,image_anchors=anchors)
    review=validate(doc)
    if review["summary"]["未通过"]:
        raise ProviderError("生成前医学规则未通过："+",".join(sorted({c["rule_id"] for c in review["checks"] if c["status"]=="未通过"})))
    return doc,review
