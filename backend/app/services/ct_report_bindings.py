"""Structural and reference rules for the lean chest CT report input."""

import math


PASS, REVIEW, FAIL = "通过", "待复核", "未通过"
ROOT = {"study", "analysis_scope", "findings", "evidence", "image_anchors"}


def text_ref(value):
    return isinstance(value, str) and bool(value.strip())


def finite_number(value):
    if type(value) is int:
        return True
    return type(value) is float and math.isfinite(value)


def object_list(value):
    return isinstance(value, list) and all(isinstance(item, dict) for item in value)


def id_list(value):
    return isinstance(value, list) and all(text_ref(item) for item in value)


def unique_refs(items, key):
    refs = [item.get(key) for item in items]
    return all(text_ref(ref) for ref in refs) and len(set(refs)) == len(refs)


def structure_errors(doc):
    if not isinstance(doc, dict):
        return ["$"]
    errors = [f"$.{key}" for key in sorted(ROOT ^ doc.keys())]
    if errors:
        return errors
    for key in ("study", "analysis_scope"):
        if not isinstance(doc[key], dict):
            errors.append(f"$.{key}")
    for key in ("findings", "evidence", "image_anchors"):
        if not object_list(doc[key]):
            errors.append(f"$.{key}")
    if errors:
        return errors

    study, scope = doc["study"], doc["analysis_scope"]
    required_objects = {
        "$.study.contrast": study.get("contrast"),
        "$.analysis_scope.candidate_detection": scope.get("candidate_detection"),
    }
    for path, value in required_objects.items():
        if not isinstance(value, dict):
            errors.append(path)
    for path, value in (
        ("$.study.series", study.get("series")),
        ("$.analysis_scope.region_masks", scope.get("region_masks")),
    ):
        if not object_list(value):
            errors.append(path)
    for i, f in enumerate(doc["findings"]):
        path = f"$.findings[{i}]"
        for key in ("classification", "anatomy"):
            if not isinstance(f.get(key), dict):
                errors.append(f"{path}.{key}")
        for key in ("measurements", "observations", "data_gaps"):
            if not object_list(f.get(key)):
                errors.append(f"{path}.{key}")
    return errors


def all_fact_refs(source, key):
    refs = source.get(key, [])
    return refs if id_list(refs) else []


def found_fields(f, path):
    """Enumerate the candidate statements that must link to evidence."""
    yield (path+".classification", f["classification"].get("evidence_ids"), "mask_component", [])
    # Missing localization remains an explicit review item in R04.
    if f["anatomy"].get("location") is not None or f["anatomy"].get("side"):
        yield (path+".anatomy", f["anatomy"].get("evidence_ids"), "anatomy_mask_overlap", [])
    for i, row in enumerate(f["measurements"]):
        yield (f"{path}.measurements[{i}]", row.get("evidence_ids"), "measurement", [])
    for i, row in enumerate(f["observations"]):
        yield (
            f"{path}.observations[{i}]", [row.get("primary_evidence_id")],
            "observation", row.get("related_evidence_ids", []),
        )


def check_references(run):
    doc, scope, evidence, anchors = run.doc, run.scope, run.evidence, run.anchors
    if not unique_refs(doc["evidence"],"evidence_id") or not unique_refs(doc["image_anchors"],"anchor_ref"):
        run.emit("R10","证据集合",FAIL,"证据或原片锚点 ID 缺失或重复。",["$.evidence","$.image_anchors"],gate="batch")
        return False

    used = set()
    exam_errors, exam_refs = [], []
    for node,path,required,kind in (
        (scope["candidate_detection"],"$.analysis_scope.candidate_detection",
         scope["candidate_detection"]["state"]=="completed","candidate_detection"),
        (run.study["contrast"],"$.study.contrast",False,"contrast_assessment"),
    ):
        refs=node.get("evidence_ids",[])
        if not id_list(refs) or (required and not refs):
            exam_errors.append(path+".evidence_ids")
            continue
        exam_refs.extend(refs)
        for ref in refs:
            item=evidence.get(ref)
            if item is None or item.get("finding_id") is not None or item.get("kind")!=kind:
                exam_errors.append(path+".evidence_ids")
    used.update(exam_refs)
    run.invalid_paths.update(p.removesuffix(".evidence_ids") for p in exam_errors)
    run.emit("R10","检查级证据",FAIL if exam_errors else PASS,
             "检查级证据缺失、类型错误或引用到候选。" if exam_errors else "检查级证据归属及类型一致。",
             exam_errors or ["$.analysis_scope.candidate_detection.evidence_ids", "$.study.contrast.evidence_ids"],
             evidence=exam_refs, observed={"reference_count":len(set(exam_refs))})

    for i,f in enumerate(doc["findings"]):
        path=f"$.findings[{i}]"
        failed, refs = [], []
        for field,ids,kind,related in found_fields(f,path):
            if not id_list(ids) or not ids or not id_list(related):
                failed.append(field)
                continue
            refs+=ids+list(related)
            valid_kind=({"model_output","source_omission"} if kind=="observation" else {kind})
            for ref in ids+list(related):
                e=evidence.get(ref)
                if not e or e.get("kind") not in valid_kind or (
                    e.get("finding_id"), e.get("series_ref"), e.get("component_ref"), e.get("mask_ref")
                ) != (f["finding_id"],f["series_ref"],f["component_ref"],scope.get("candidate_mask_ref")):
                    failed.append(field)
                    break
                loc=e.get("anchor_refs",[])
                if not id_list(loc) or any(a not in anchors for a in loc):
                    failed.append(field)
                    break
        used.update(refs)
        run.invalid_paths.update(failed)
        run.emit(
            "R10",run.subject(f),FAIL if failed else PASS,
            "候选字段的证据缺失、无法解析或归属错误。" if failed else "候选证据与序列、掩膜和组件归属一致。",
            sorted(set(failed)) or [path],
            evidence=refs, observed={"reference_count":len(set(refs)),"component_ref":f["component_ref"],
                                     "mask_ref":scope.get("candidate_mask_ref"),"series_ref":f["series_ref"]},
        )

    # A record that is only left over by a failed reference belongs to the same failure.
    if not run.invalid_paths and used!=evidence.keys():
        run.emit("R10","证据集合",FAIL,"存在未关联的证据条目。",["$.evidence"],evidence=sorted(evidence.keys()-used))
    return True


def valid_line(geometry):
    points=geometry.get("endpoints_xy")
    return (
        geometry.get("coordinate_system")=="native_image_pixel_xy_zero_based"
        and isinstance(points,list) and len(points)==2
        and all(isinstance(p,list) and len(p)==2 and all(type(x) is int and x>=0 for x in p) for p in points)
        and points[0]!=points[1]
    )


def valid_box(geometry):
    box=geometry.get("box_xy")
    return (
        geometry.get("coordinate_system")=="normalized_image_xyxy"
        and isinstance(box,list) and len(box)==4
        and all(finite_number(v) and 0<=v<=1 for v in box)
        and box[0]<box[2] and box[1]<box[3]
    )


def check_anchors(run):
    slice_count=run.study["series"][0].get("slice_count")
    for i,anchor in enumerate(run.doc["image_anchors"]):
        ref=anchor["anchor_ref"]
        linked=[e for e in run.evidence.values() if ref in all_fact_refs(e,"anchor_refs")]
        role, geometry=anchor.get("role"),anchor.get("geometry")
        layer,native=anchor.get("viewer_slice_number"),anchor.get("native_slice_index")
        valid_geometry=isinstance(geometry,dict) and (
            valid_line(geometry) if role=="measurement_line"
            else role in ("candidate_region","model_finding_region") and valid_box(geometry)
        )
        valid=(
            valid_geometry and bool(linked) and type(layer) is int and type(native) is int
            and type(slice_count) is int and 1<=layer<=slice_count and native+1==layer
            and all((e.get("finding_id"),e.get("series_ref"))==(anchor.get("finding_id"),anchor.get("series_ref")) for e in linked)
        )
        if not valid:
            run.invalid_anchors.add(ref)
        run.emit("R11",ref,PASS if valid else FAIL,
                 "原片层号、坐标、角色和关联归属一致。" if valid else "层号越界、几何非法、角色无效或锚点归属冲突。",
                 [f"$.image_anchors[{i}]"],evidence=[e["evidence_id"] for e in linked],anchors=[ref],
                 observed={"role":role,"viewer_slice_number":layer,"native_slice_index":native,"geometry":geometry})
