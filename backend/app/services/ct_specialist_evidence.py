"""Run the existing, pinned CT attribute models on uniquely bound candidates."""
import hashlib
import json
import os
from pathlib import Path
import sys

import joblib
import numpy as np
import SimpleITK as sitk
import torch

from app.services.ct_reference_evidence import candidate_components, original_slices
from app.services.ct_verified_runner import load_artifact, morphology

VERSIONS = {"density": "density-soft-tta", "calcification": "cnn-v3", "spiculation": "shape-refine"}
DISPLAY = {
    "density": {"solid": "实性", "non_solid": "非实性"},
    "calcification": {"present": "提示钙化", "absent": "模型未提示钙化"},
    "spiculation": {"localized": "毛刺候选区域"},
}


def details(ct, bounds, roi, original_anchors):
    points = np.argwhere(roi)
    center = points.mean(axis=0)
    point = points[np.argmin(np.sum(((points - center) * ct.GetSpacing()[::-1]) ** 2, axis=1))]
    z = int(point[0])
    coords = points[points[:, 0] == z, 1:] + [bounds[1].start, bounds[2].start]
    y, x = coords.min(axis=0)
    sy, sx = coords.max(axis=0) + 1
    nx, ny, _ = ct.GetSize()
    anchor = original_anchors[bounds[0].start + z]
    return {**anchor, "box_xy": [float(x/nx), float(y/ny), float(sx/nx), float(sy/ny)]}


def lifecycle(folder, kind, loaders):
    name = VERSIONS[kind]
    try:
        path, info = load_artifact(folder, name)
        predictor, metrics = loaders[kind](path, info)
        data = {"model": name, "checkpoint_sha256": info["weights_sha256"], "validation": metrics,
                "status": "available", "evidence_use": "candidate_prompt"}
        return predictor, data
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError) as exc:
        return None, {"model": name, "status": "unavailable", "reason": type(exc).__name__}


def collect(source, folder):
    from density_v2_tta import describe_views
    from infer_0402 import predict_views, voxel_patches
    from train_attribute_model import AttributeNetwork
    from cir_shape_model import OFFSETS, ShapeRegionNetwork, mask_crop, model_regions

    ct, mask = (sitk.ReadImage(source[key]) for key in ("volume", "mask"))
    original, chest = original_slices(ct, source["dicom_dir"], source["series_uid"])
    if not chest or len(original) != ct.GetDepth() or source.get("intensity_unit_verified") != "HU":
        raise ValueError("Verified native chest HU and DICOM anchors required")
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(2)
    torch.set_num_threads(2)
    volume = sitk.GetArrayViewFromImage(ct)

    def density_loader(path, info):
        frozen = joblib.load(path)
        test = info["test"]
        return (lambda views, component, world: describe_views(frozen, test, views)), {
            "heldout": test, "display_scope": ["solid", "non_solid"], "thresholds": info["gates"]}

    def calcium_loader(path, info):
        weights = torch.load(path, map_location="cpu", weights_only=True)
        model = AttributeNetwork(input_slices=weights["input_slices"]).cuda().eval()
        model.load_state_dict(weights["state_dict"], strict=True)
        model.inference_tta = weights["inference_tta"]
        def classify(views, component, world):
            return predict_views(model, views, weights["labels"], weights["temperature"], weights["gates"],
                                 info["test"])["calcification"]
        return classify, {"heldout": info["test"]["calcification"], "thresholds": info["gates"]["calcification"]}

    def spiculation_loader(path, info):
        splits = {key: info[key]["spiculation"] for key in ("test", "external")}
        if any(value["median_dice_positive"] < .30 for value in splits.values()):
            raise ValueError("spiculation held-out gate failed")
        weights = torch.load(path, map_location="cpu", weights_only=True)
        model = ShapeRegionNetwork(weights["use_ct"], weights["exclusive"], weights["shell"]).cuda().eval()
        model.load_state_dict(weights["state"], strict=True)
        def localize(views, component, world):
            spatial = morphology(ct, component, world, model, weights["thresholds"], OFFSETS,
                                 mask_crop, model_regions, original)
            return {"anchor": spatial, "satisfies_offline_gate": spatial is not None,
                    "center_prediction": "localized" if spatial else None}
        return localize, {"heldout": splits, "minimum_median_dice": .30,
                          "use": "region candidate only", "thresholds": info["thresholds"]}

    loaders = {"density": density_loader, "calcification": calcium_loader, "spiculation": spiculation_loader}
    models = {kind: lifecycle(folder, kind, loaders) for kind in VERSIONS}
    results = []
    for saved, bounds, roi in candidate_components(ct, mask, source["lesions"]):
        index, *world = saved
        owner = f"L{index}"
        anchor = details(ct, bounds, roi, original)
        component = np.zeros(volume.shape, dtype=bool)
        component[bounds] = roi
        center = ct.TransformPhysicalPointToContinuousIndex(world)
        try:
            views, _ = voxel_patches(volume, component, center, ct.GetSpacing()[:2])
        except (ValueError, IndexError, RuntimeError, TypeError):
            views = None
        for kind, (predict, record) in models.items():
            item = {"id": f"{owner}:{kind}", "lesion_id": owner, "kind": kind,
                    "model": record["model"], "anchor": anchor}
            if predict is None or (kind != "spiculation" and views is None):
                item.update(status="unavailable", reason="model_assets_or_input_unavailable")
            elif kind == "spiculation" and np.count_nonzero(roi.any(axis=(1, 2))) < 3:
                item.update(status="indeterminate", reason="insufficient_native_slices")
            else:
                try:
                    raw = predict(views, component, world)
                    value = raw["center_prediction"]
                    if raw["satisfies_offline_gate"] and value in DISPLAY[kind]:
                        item.update(status="model_hint", value=DISPLAY[kind][value], code=value)
                        if kind == "spiculation":
                            spatial = raw["anchor"]
                            item["anchor"] = {**original[spatial["slice"] - 1], **spatial}
                    else:
                        item.update(status="indeterminate", reason="spatial_or_class_gate_not_met")
                    item["detail"] = raw
                except (RuntimeError, ValueError, KeyError, IndexError, TypeError) as exc:
                    item.update(status="unavailable", reason=type(exc).__name__)
            results.append(item)
    return {"source_slices": len(original), "models": {kind: record for kind, (_, record) in models.items()},
            "candidates": results, "native_spacing_xyz_mm": list(ct.GetSpacing()),
            "runner_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}


if __name__ == "__main__":
    folder = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(folder / "helpers"))
    source = json.loads(Path(sys.argv[2]).read_text())
    result = collect(source, folder)
    Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False, allow_nan=False))
