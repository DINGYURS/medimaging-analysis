"""Nodule-CADx research texture model using the original linear SVR coefficients.

Model and intensity normalization from wenyalintw/Nodule-CADx
(dd0b3d1d672141f8dfabde1a05ef33f87681f8e4, MIT).
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import SimpleITK as sitk
from radiomics import featureextractor
from scipy import ndimage

MODEL = "Nodule-CADx:dd0b3d1"
COEFFICIENT_SHA256 = "3358291ceecc65fb0086ffce28cd8f3aeb19e4c0fe0a16555fc672fcd16d25f9"
TEXTURES = ("non_solid", "non_to_part_solid", "part_solid", "part_to_solid", "solid")


def feature_vector(extractor, volume, roi):
    # The checkpoint scaler centers Original Mean at -209.8, which cannot
    # arise from uint8. This research adaptation preserves signed HU values.
    image = sitk.GetImageFromArray(volume.astype(np.float32, copy=False))
    mask = sitk.GetImageFromArray(roi.astype(np.uint8))
    raw = extractor.execute(image, mask)
    features = [(key, value) for key, value in raw.items() if not key.startswith("diagnostics_")]
    vector = np.asarray([value for _, value in features], dtype=np.float64)
    if vector.shape != (1688,) or not np.isfinite(vector).all():
        raise ValueError(f"Expected 1688 finite texture features, got {vector.size}")
    key_digest = hashlib.sha256("\n".join(key for key, _ in features).encode()).hexdigest()
    return vector, key_digest


def predict(vector, weights):
    standardized = (vector - weights["mean"]) / weights["scale"]
    if np.max(np.abs(standardized)) > 25:
        return None, None
    components = (standardized - weights["pca_mean"]) @ weights["components"].T
    score = float(((components - weights["post_mean"]) / weights["post_scale"]) @ weights["coef"]
                  + weights["intercept"][0])
    if not np.isfinite(score) or not 0.5 <= score <= 5.5:
        return score, None
    if score <= 1.5:
        return score, TEXTURES[0]
    if score <= 2.5:
        return score, TEXTURES[1]
    if score < 3.5:
        return score, TEXTURES[2]
    if score < 4.5:
        return score, TEXTURES[3]
    return score, TEXTURES[4]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("volume", "mask", "weights", "coordinates"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    sitk.ProcessObject.SetGlobalDefaultNumberOfThreads(2)
    with args.weights.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != COEFFICIENT_SHA256:
            raise ValueError("Unexpected texture coefficients digest")
    source = sitk.ReadImage(str(args.volume))
    mask = sitk.ReadImage(str(args.mask))
    if not source.IsSameImageGeometryAs(mask, 1e-4, 1e-5):
        raise ValueError("Candidate mask does not match the source CT")
    targets = json.loads(args.coordinates.read_text(encoding="utf-8"))
    labels, count = ndimage.label(sitk.GetArrayViewFromImage(mask) > 0)
    counts = np.bincount(labels.ravel(), minlength=count + 1)
    voxel_volume = float(np.prod(source.GetSpacing()))
    keys = [code for code in range(1, count + 1) if counts[code] * voxel_volume >= 25.0]
    keys.sort(key=lambda code: round(float(counts[code]) * voxel_volume, 2), reverse=True)
    if len(keys) != len(targets):
        raise ValueError("Candidate mask count changed")
    bounds = ndimage.find_objects(labels)
    volume = sitk.GetArrayViewFromImage(source)
    extractor = featureextractor.RadiomicsFeatureExtractor()
    extractor.enableAllImageTypes()
    outputs = {}
    with np.load(args.weights, allow_pickle=False) as weights:
        for code, target in zip(keys, targets):
            bbox = bounds[code - 1]
            crop = tuple(slice(region.start // 4 * 4, min(((region.stop + 3) // 4) * 4, size))
                         for region, size in zip(bbox, volume.shape))
            voxels = volume[bbox][labels[bbox] == code]
            attenuation = {"mean_hu": round(float(np.mean(voxels, dtype=np.float64)), 1),
                           "median_hu": round(float(np.median(voxels)), 1)}
            try:
                vector, key_digest = feature_vector(extractor, volume[crop], labels[crop] == code)
                score, label = predict(vector, weights)
                tight_vector, _ = feature_vector(extractor, volume[bbox], labels[bbox] == code)
                tight_score, tight_label = predict(tight_vector, weights)
                outputs[target["id"]] = {
                    "class": label if label == tight_label else None,
                    "score": score, "tight_crop_score": tight_score, "feature_count": len(vector),
                    "feature_order_sha256": key_digest, "protocol": "verified_hu_research_adaptation", **attenuation,
                }
            except (ValueError, RuntimeError, ImportError) as exc:
                outputs[target["id"]] = {"class": None, "reason": type(exc).__name__, "detail": str(exc)[:150], **attenuation}
    print(json.dumps({"model": MODEL, "predictions": outputs}, allow_nan=False))


if __name__ == "__main__":
    main()
