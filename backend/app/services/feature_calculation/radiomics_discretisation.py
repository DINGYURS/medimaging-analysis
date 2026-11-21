"""Independent IBSI histogram/texture binning on the final intensity ROI."""
from __future__ import annotations

from typing import Any

import numpy as np


def discretise(image: np.ndarray, mask: np.ndarray, rule: dict[str, Any]):
    """Return levels and execution evidence; constant FBN is unavailable."""
    values = image[mask]
    low, high = float(np.min(values)), float(np.max(values))
    evidence = {**rule, "observed_intensity_range": [low, high], "intensity_basis": "calculation_image"}
    levels = np.zeros(image.shape, dtype=np.int64)
    if rule["method"] == "fbn":
        count = rule["bin_count"]
        if count >= np.iinfo(np.int64).max:
            raise ValueError("bin_count exceeds the supported integer range")
        evidence.update(bin_origin=low, bin_origin_source="roi_minimum", effective_bin_width=(high - low) / count)
        if high == low:
            evidence.update(status="unavailable", reason="FBN requires a non-constant intensity ROI (maximum equals minimum)")
            return None, evidence
        # IBSI K15C: maximum maps to Ng, all other values use floor + 1.
        scaled = (values - low) / (high - low)
        if not np.all(np.isfinite(scaled)):
            raise ValueError("intensity range cannot be represented for FBN")
        bins = np.minimum(np.floor(scaled * count), count - 1).astype(np.int64) + 1
    else:
        origin = low if rule.get("bin_origin") is None else rule["bin_origin"]
        if origin > low:
            raise ValueError("bin_origin exceeds the observed finite intensity minimum")
        evidence.update(bin_origin=origin, bin_origin_source="roi_minimum" if rule.get("bin_origin") is None else "declared")
        bins = np.floor((values - origin) / rule["bin_width"]) + 1
        if not np.all(np.isfinite(bins)) or np.max(bins) >= np.iinfo(np.int64).max:
            raise ValueError("discretised grey levels exceed the supported integer range")
        bins = bins.astype(np.int64)
    levels[mask] = bins
    evidence.update(status="computed", occupied_bin_count=int(np.unique(bins).size), maximum_grey_level=int(np.max(bins)))
    return levels, evidence
