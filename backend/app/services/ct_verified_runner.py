"""Run pinned CT attribute inference in the model environment."""
import hashlib
import json
from pathlib import Path
import sys

import joblib
import numpy as np
import SimpleITK as sitk
import torch
from scipy import ndimage

from app.services.ct_reference_evidence import candidates, original_slices


def load_artifact(folder, name):
    info = json.loads((folder / name / "metrics.json").read_text())
    path = folder / name / {"density-soft-tta": "density-soft-tta.joblib",
                            "cnn-v3": "attributes.pt", "shape-refine": "shape_regions.pt"}[name]
    with path.open("rb") as f:
        if hashlib.file_digest(f, "sha256").hexdigest() != info["weights_sha256"]:
            raise ValueError(f"Model and evaluation fingerprint differ: {name}")
    return path, info


def morphology(ct, component, center, model, thresholds, offsets, crop, regions, anchors):
    coords = np.argwhere(component)
    # Three distinct axial planes of original support are needed for a 3-D anchor.
    if int(np.ptp(coords[:, 0])) < 2:
        return None
    lo = np.maximum(coords.min(0) - 4, 0)
    hi = np.minimum(coords.max(0) + 5, component.shape)
    native = sitk.RegionOfInterest(ct, (hi-lo)[::-1].tolist(), lo[::-1].tolist())
    mask = sitk.GetImageFromArray(component.astype(np.uint8))
    mask.CopyInformation(ct)
    stable = None
    for shift in offsets:
        volume, lesion, spacing = crop(ct, mask, center, shift)
        if not lesion.any() or np.any(lesion[0]) or np.any(lesion[-1]):
            return None
        out = regions(model, volume, lesion, thresholds)[0]
        image = sitk.GetImageFromArray(out.astype(np.uint8))
        image.SetDirection(ct.GetDirection())
        image.SetSpacing((spacing,) * 3)
        rotation = np.asarray(ct.GetDirection()).reshape(3, 3)
        image.SetOrigin(tuple(np.asarray(center) + rotation @ ((np.asarray(shift)-31.5)*spacing)))
        aligned = sitk.GetArrayFromImage(sitk.Resample(image, native, sitk.Transform(),
                                                     sitk.sitkNearestNeighbor, 0)) > 0
        stable = aligned if stable is None else stable & aligned
    counts = stable.sum(axis=(1, 2))
    if np.count_nonzero(counts) < 2:
        return None
    z = int(counts.argmax())
    pixels = np.argwhere(stable[z]) + lo[1:]
    minimum, maximum = pixels.min(0), pixels.max(0) + 1
    ny, nx = component.shape[1:]
    return {"slice": anchors[int(z + lo[0])]["slice"],
            "box_xy": [minimum[1]/nx, minimum[0]/ny, maximum[1]/nx, maximum[0]/ny]}


def main():
    folder = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(folder / "helpers"))
    from density_v2_tta import describe_views
    from infer_0402 import predict_views, voxel_patches
    from train_attribute_model import AttributeNetwork
    from cir_shape_model import OFFSETS, ShapeRegionNetwork, mask_crop, model_regions

    source = json.loads(Path(sys.argv[2]).read_text())
    ct, mask = (sitk.ReadImage(source[name]) for name in ("volume", "mask"))
    if not ct.IsSameImageGeometryAs(mask, 1e-4, 1e-5):
        raise ValueError("CT/mask geometry mismatch")
    anchors, chest = original_slices(ct, source["dicom_dir"], source["series_uid"])
    if not chest or not anchors:
        raise ValueError("Verified original chest CT is required")
    candidates(ct, mask, None, source["lesions"], anchors)
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(2)
    volume = sitk.GetArrayViewFromImage(ct)
    components, _ = ndimage.label(sitk.GetArrayViewFromImage(mask) > 0,
                                 structure=ndimage.generate_binary_structure(3, 3))
    density_path, density_metrics = load_artifact(folder, "density-soft-tta")
    cnn_path, cnn_metrics = load_artifact(folder, "cnn-v3")
    shape_path, shape_metrics = load_artifact(folder, "shape-refine")
    density = joblib.load(density_path)
    cnn = torch.load(cnn_path, map_location="cpu", weights_only=True)
    calc = AttributeNetwork(input_slices=cnn["input_slices"]).cuda().eval()
    calc.load_state_dict(cnn["state_dict"], strict=True)
    calc.inference_tta = cnn["inference_tta"]
    shape = torch.load(shape_path, map_location="cpu", weights_only=True)
    region = ShapeRegionNetwork(shape["use_ct"], shape["exclusive"], shape["shell"]).cuda().eval()
    region.load_state_dict(shape["state"], strict=True)
    if any(shape_metrics[split]["spiculation"]["median_dice_positive"] < .30
           for split in ("test", "external")):
        raise ValueError("Spiculation localization did not pass independent validation")
    result = []
    claimed = set()
    for index, *world in source["lesions"]:
        center = ct.TransformPhysicalPointToContinuousIndex(world)
        x, y, z = np.rint(center).astype(int)
        component_id = int(components[z, y, x])
        if not component_id or component_id in claimed:
            # An unbound or shared component must not produce evidence.
            continue
        claimed.add(component_id)
        component = components == component_id
        views, _ = voxel_patches(volume, component, center, ct.GetSpacing()[:2])
        classified = describe_views(density, density_metrics["test"], views)
        calcium = predict_views(calc, views, cnn["labels"], cnn["temperature"], cnn["gates"],
                                cnn_metrics["test"])["calcification"]
        lesion = f"L{index}"
        if classified["satisfies_offline_gate"] and classified["center_prediction"] in ("solid", "non_solid"):
            result.append({"lesion_id": lesion, "kind": "density", "value": classified["center_prediction"]})
        if calcium["satisfies_offline_gate"]:
            result.append({"lesion_id": lesion, "kind": "calcification", "value": calcium["center_prediction"]})
        located = morphology(ct, component, world, region, shape["thresholds"], OFFSETS, mask_crop,
                             model_regions, anchors)
        if located:
            result.append({"lesion_id": lesion, "kind": "spiculation", "value": "localized", "anchor": located})
    Path(sys.argv[3]).write_text(json.dumps(result, allow_nan=False))


if __name__ == "__main__":
    main()
