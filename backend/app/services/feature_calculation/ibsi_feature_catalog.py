"""Explicit IBSI 2019 reference-manual v11 feature catalogue.

The platform historically exposed the 107 PyRadiomics features plus a few
legacy/custom values.  This module deliberately keeps the IBSI vocabulary
separate.  Every feature in the 2019 IBSI guideline table 3.1 has one
permanent four-character identifier and one canonical output key.  The
catalogue is data rather than a count inferred from an implementation so an
unsupported feature cannot accidentally be represented by a made-up alias or
zero value.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class IBSIFeatureSpec:
    """A feature definition from the IBSI 2019 v11 manual, table 3.1.

    ``standard_status`` describes the source document.  ``computed`` means
    the definition has a native implementation in :mod:`ibsi_features`;
    ``not_standardized`` is reserved for features IBSI explicitly says have
    no reference values and should not be used.  Runtime input limitations
    (for example a 2-D request for a 3-D morphology feature) are reported in
    the engine result's ``unavailable`` map and do not mutate this catalogue.
    """

    section: str
    family: str
    name: str
    code: str
    definition: str
    required_mask: str
    requires_discretisation: bool
    standard_status: str = "computed"
    source_note: str | None = None

    @property
    def key(self) -> str:
        return f"ibsi_{self.code}"

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["key"] = self.key
        # ``status``/``reason`` are the compact names consumed by the API
        # layer.  ``standard_status``/``source_note`` remain present so a
        # report can distinguish source-level exclusions from runtime input
        # availability.
        value["status"] = self.standard_status
        value["reason"] = self.source_note
        value["unit"] = _unit_for(self)
        # Semantic inputs are per indicator, not per family. Runtime failures
        # (empty masks, degenerate meshes, etc.) remain engine-result evidence.
        mask = self.required_mask
        value["required_mask"] = mask
        value["required_masks"] = mask.split(" + ")
        value["requires_image_intensity"] = "intensity" in value["required_masks"]
        value["requires_local_context"] = self.family == "local_intensity"
        value["supported_dimensions"] = ["3d"] if self.family in {"morphology", "local_intensity"} else ["2d", "3d"]
        value["discretisation_group"] = (
            self.family if self.family in {"intensity_histogram", "intensity_volume_histogram"}
            else "texture" if self.requires_discretisation else "none"
        )
        value["calculation_module"] = {
            "morphology": "morphology", "local_intensity": "local",
            "intensity_statistics": "statistics", "gldzm": "gldzm",
        }.get(self.family, "texture")
        value["legacy_pyradiomics_class"] = {
            "morphology": "shape", "intensity_statistics": "firstorder",
            "intensity_histogram": "firstorder", "glcm": "glcm", "glrlm": "glrlm",
            "glszm": "glszm", "ngtdm": "ngtdm",
        }.get(self.family)
        value["runtime_constraints"] = ["实际输入仍需运行时校验；目录支持不保证每个病例产生有限数值。"]
        if self.family in {"morphology", "local_intensity"}:
            value["runtime_constraints"].append("当前 IBSI 引擎仅在 3D 模式计算本指标。")
        if self.code in {"L0JK", "N365", "NPT7"}:
            value["runtime_constraints"].append("采用精确凸包/分块距离或完整反距离FFT计算，不抽样、不改变体素间距；计算路径记录在结果溯源中。")
        if self.family == "local_intensity":
            value["runtime_constraints"].append("保留 ROI 外的 1 cm³ 球形邻域图像。")
        return value


def _unit_for(spec: IBSIFeatureSpec) -> str | None:
    """Reporting units derived from the implemented formula.

    grey_level, *_index and *_count denote dimensionless discrete axes,
    not calibrated physical units. Their powers retain useful scale semantics.
    """

    # Keep dimensional units tied to the permanent code where the feature
    # name alone is ambiguous.  In particular, the surface-to-volume ratio
    # is inverse length, and IVH intensity quantiles retain calibrated image
    # units instead of being reported as grey-level indices.
    if spec.code == "2PR5":
        return "mm^-1"
    if spec.code in {"GBPN", "CNV2"}:
        return "intensity"
    if spec.code in {"ECT3", "N8CA"}:
        return "intensity^2"
    if spec.code in {"KE2A", "IPH6", "7TET", "9S40"}:
        return "dimensionless"

    name = spec.name.casefold()
    if spec.family == "morphology":
        if "volume" in name and "density" not in name:
            return "mm3"
        if "surface area" in name or "area density" in name:
            return "mm2" if "density" not in name else "dimensionless"
        if "axis length" in name or "diameter" in name or "mass shift" in name:
            return "mm"
        if "integrated intensity" in name:
            return "intensity*mm3"
        if "ratio" in name or "compactness" in name or "disproportion" in name or "sphericity" in name or "asphericity" in name or "density" in name or "moran" in name or "geary" in name or "elongation" in name or "flatness" in name:
            return "dimensionless"
    if spec.family == "local_intensity" or spec.family == "intensity_statistics":
        return "intensity"
    if spec.family == "intensity_histogram":
        if spec.code == "CH89":
            return "grey_level^2"
        if spec.code in {"88K1", "C3I7", "CWYJ", "SLWD", "BJ5W"}:
            return "dimensionless"
        if spec.code == "TLU2":
            return "bit"
        if spec.code in {"12CE", "VQB3"}:
            return "voxel_count/grey_level"
        return "grey_level"
    if spec.family == "intensity_volume_histogram":
        if "volume at" in name or "area under" in name or "volume fraction difference" in name:
            return "fraction"
        if "intensity fraction difference" in name or "intensity at" in name:
            return "intensity"
        return "dimensionless"
    if spec.family == "glcm":
        powers = {
            "60VM": 1, "TF7R": 1, "ZGXS": 1, "8S9J": 1,
            "UR99": 2, "D3YU": 2, "OEEB": 2, "ACUI": 2,
            "QWB0": 2, "DG8W": 2, "7NFM": 3, "AE86": 4, "E8JP": -2,
        }
        if spec.code in powers:
            power = powers[spec.code]
            return "grey_level" if power == 1 else f"grey_level^{power}"
        if "entropy" in name:
            return "bit"
        return "dimensionless"
    if spec.family in {"glrlm", "glszm", "gldzm", "ngldm"}:
        # All four matrices use the same 16 formula positions in the engine;
        # their column axes are discrete counts/distances, never mm or mm3.
        rows = {"glrlm": _GLRLM, "glszm": _GLSZM, "gldzm": _GLDZM, "ngldm": _NGLDM}[spec.family]
        position = next(index for index, row in enumerate(rows) if row[1] == spec.code)
        axis, count = {
            "glrlm": ("run_length_index", "run_count"),
            "glszm": ("zone_size_index", "zone_count"),
            "gldzm": ("distance_index", "zone_count"),
            "ngldm": ("dependence_size_index", "dependence_count"),
        }[spec.family]
        return [
            f"{axis}^-2", f"{axis}^2", "grey_level^-2", "grey_level^2",
            f"grey_level^-2*{axis}^-2", f"grey_level^2*{axis}^-2",
            f"grey_level^-2*{axis}^2", f"grey_level^2*{axis}^2",
            count, "dimensionless", count, "dimensionless", "fraction",
            "grey_level^2", f"{axis}^2", "bit", "dimensionless",
        ][position]
    if spec.family == "ngtdm":
        return {
            "QCDE": "grey_level^-1/voxel_count", "65HE": "grey_level^3",
            "NQ30": "voxel_count", "HDEZ": "grey_level^2", "1X9X": "grey_level/voxel_count",
        }[spec.code]
    return "dimensionless"


def _specs(
    section: str,
    family: str,
    rows: Iterable[tuple[str, str, str, str]],
    *,
    required_mask: str,
    requires_discretisation: bool,
) -> list[IBSIFeatureSpec]:
    return [
        IBSIFeatureSpec(
            section=section,
            family=family,
            name=name,
            code=code,
            definition=definition,
            required_mask=_indicator_mask(family, code, required_mask),
            requires_discretisation=requires_discretisation,
        )
        for name, code, definition, _ in rows
    ]


def _row(name: str, code: str, definition: str, status: str = "computed") -> tuple[str, str, str, str]:
    return name, code, definition, status


def _indicator_mask(family: str, code: str, default: str) -> str:
    if family != "morphology":
        return default
    if code in {"KLMA", "99N0"}:
        return "morphological + intensity"
    if code in {"N365", "NPT7"}:
        return "intensity"
    return "morphological"


# The names and permanent identifiers below are transcribed from the IBSI
# guideline feature headings.  Formulas are intentionally concise but
# identify the quantity precisely enough for a consumer to distinguish IBSI
# semantics from similarly named PyRadiomics values.
_MORPHOLOGY = [
    _row("Volume (mesh)", "RNU0", "absolute signed volume of the closed ROI marching-cubes mesh"),
    _row("Volume (voxel counting)", "YEKZ", "number of morphological ROI voxels multiplied by voxel volume"),
    _row("Surface area (mesh)", "C0JK", "sum of triangle areas of the closed ROI mesh"),
    _row("Surface to volume ratio", "2PR5", "mesh surface area divided by mesh volume"),
    _row("Compactness 1", "SKGS", "V / (sqrt(pi) * A^(3/2))"),
    _row("Compactness 2", "BQWJ", "36*pi*V^2 / A^3"),
    _row("Spherical disproportion", "KRCK", "A / (36*pi*V^2)^(1/3)"),
    _row("Sphericity", "QCFX", "(36*pi*V^2)^(1/3) / A"),
    _row("Asphericity", "25C7", "(A^3/(36*pi*V^2))^(1/3) - 1"),
    _row("Centre of mass shift", "KLMA", "Euclidean distance between geometric and intensity-weighted ROI centroids"),
    _row("Maximum 3D diameter", "L0JK", "maximum Euclidean distance between mesh vertices"),
    _row("Major axis length", "TDIC", "4*sqrt(largest eigenvalue of physical voxel-centre covariance)"),
    _row("Minor axis length", "P9VJ", "4*sqrt(second physical covariance eigenvalue)"),
    _row("Least axis length", "7J51", "4*sqrt(smallest physical covariance eigenvalue)"),
    _row("Elongation", "Q3CK", "sqrt(second covariance eigenvalue / largest covariance eigenvalue)"),
    _row("Flatness", "N17B", "sqrt(smallest covariance eigenvalue / largest covariance eigenvalue)"),
    _row("Volume density (axis-aligned bounding box)", "PBX1", "mesh volume divided by axis-aligned bounding-box volume"),
    _row("Area density (axis-aligned bounding box)", "R59B", "mesh area divided by axis-aligned bounding-box area"),
    _row("Volume density (oriented minimum bounding box)", "ZH1A", "mesh volume divided by oriented minimum bounding-box volume", "not_standardized"),
    _row("Area density (oriented minimum bounding box)", "IQYR", "mesh area divided by oriented minimum bounding-box area", "not_standardized"),
    _row("Volume density (approximate enclosing ellipsoid)", "6BDE", "mesh volume divided by PCA approximate enclosing-ellipsoid volume"),
    _row("Area density (approximate enclosing ellipsoid)", "RDD2", "mesh area divided by PCA approximate enclosing-ellipsoid area"),
    _row("Volume density (minimum volume enclosing ellipsoid)", "SWZ1", "mesh volume divided by minimum-volume enclosing-ellipsoid volume", "not_standardized"),
    _row("Area density (minimum volume enclosing ellipsoid)", "BRI8", "mesh area divided by minimum-volume enclosing-ellipsoid area", "not_standardized"),
    _row("Volume density (convex hull)", "R3ER", "mesh volume divided by physical convex-hull volume"),
    _row("Area density (convex hull)", "7T7F", "mesh area divided by physical convex-hull area"),
    _row("Integrated intensity", "99N0", "mesh volume multiplied by mean intensity in the intensity mask"),
    _row("Moran's I index", "N365", "inverse-distance spatial autocorrelation over intensity-mask voxels"),
    _row("Geary's C measure", "NPT7", "inverse-distance spatial local autocorrelation over intensity-mask voxels"),
]

_LOCAL_INTENSITY = [
    _row("Local intensity peak", "VJGA", "mean intensity in a 1 cm^3 sphere centred on an ROI maximum"),
    _row("Global intensity peak", "0F91", "maximum 1 cm^3 spherical-neighbourhood mean over ROI centres"),
]

_INTENSITY_STATS = [
    _row("Mean intensity", "Q4LE", "arithmetic mean of continuous ROI intensities"),
    _row("Intensity variance", "ECT3", "population variance of continuous ROI intensities"),
    _row("Intensity skewness", "KE2A", "third central moment divided by variance^(3/2)"),
    _row("(Excess) intensity kurtosis", "IPH6", "fourth standardized central moment minus three"),
    _row("Median intensity", "Y12H", "sample median of continuous ROI intensities"),
    _row("Minimum intensity", "1GSF", "minimum continuous ROI intensity"),
    _row("10th intensity percentile", "QG58", "10th percentile of continuous ROI intensities"),
    _row("90th intensity percentile", "8DWT", "90th percentile of continuous ROI intensities"),
    _row("Maximum intensity", "84IY", "maximum continuous ROI intensity"),
    _row("Intensity interquartile range", "SALO", "75th minus 25th continuous-intensity percentile"),
    _row("Intensity range", "2OJQ", "maximum minus minimum continuous intensity"),
    _row("Intensity-based mean absolute deviation", "4FUA", "mean absolute deviation from continuous mean"),
    _row("Intensity-based robust mean absolute deviation", "1128", "mean absolute deviation after 10th-90th percentile trimming"),
    _row("Intensity-based median absolute deviation", "N72L", "mean absolute deviation from continuous median"),
    _row("Intensity-based coefficient of variation", "7TET", "population standard deviation divided by continuous mean"),
    _row("Intensity-based quartile coefficient of dispersion", "9S40", "IQR divided by the sum of Q25 and Q75"),
    _row("Intensity-based energy", "N8CA", "sum of squared continuous intensities"),
    _row("Root mean square intensity", "5ZWQ", "sqrt(mean squared continuous intensity))"),
]

_INTENSITY_HISTOGRAM = [
    _row("Mean discretised intensity", "X6K6", "mean of fixed-bin-width discretised grey levels"),
    _row("Discretised intensity variance", "CH89", "population variance of discretised grey levels"),
    _row("Discretised intensity skewness", "88K1", "skewness of discretised grey levels"),
    _row("(Excess) discretised intensity kurtosis", "C3I7", "excess kurtosis of discretised grey levels"),
    _row("Median discretised intensity", "WIFQ", "median of discretised grey levels"),
    _row("Minimum discretised intensity", "1PR8", "minimum discretised grey level"),
    _row("10th discretised intensity percentile", "GPMT", "10th percentile of discretised grey levels"),
    _row("90th discretised intensity percentile", "OZ0C", "90th percentile of discretised grey levels"),
    _row("Maximum discretised intensity", "3NCY", "maximum discretised grey level"),
    _row("Intensity histogram mode", "AMMC", "most frequent discretised grey level, ties nearest the mean"),
    _row("Discretised intensity interquartile range", "WR0O", "IQR of discretised grey levels"),
    _row("Discretised intensity range", "5Z3W", "range of discretised grey levels"),
    _row("Intensity histogram mean absolute deviation", "D2ZX", "mean absolute deviation from discretised mean"),
    _row("Intensity histogram robust mean absolute deviation", "WRZB", "trimmed mean absolute deviation of discretised levels"),
    _row("Intensity histogram median absolute deviation", "4RNL", "mean absolute deviation from discretised median"),
    _row("Intensity histogram coefficient of variation", "CWYJ", "discretised standard deviation divided by discretised mean"),
    _row("Intensity histogram quartile coefficient of dispersion", "SLWD", "discretised IQR divided by Q25+Q75"),
    _row("Discretised intensity entropy", "TLU2", "Shannon entropy of discretised grey-level probabilities"),
    _row("Discretised intensity uniformity", "BJ5W", "sum of squared discretised grey-level probabilities"),
    _row("Maximum histogram gradient", "12CE", "maximum first finite difference of the dense histogram"),
    _row("Maximum histogram gradient intensity", "8E6O", "grey level at maximum histogram gradient"),
    _row("Minimum histogram gradient", "VQB3", "minimum first finite difference of the dense histogram"),
    _row("Minimum histogram gradient intensity", "RHQZ", "grey level at minimum histogram gradient"),
]

_INTENSITY_VOLUME_HISTOGRAM = [
    _row("Volume at intensity fraction", "BC2M", "largest cumulative volume fraction at gamma >= 10%"),
    _row("Intensity at volume fraction", "GBPN", "minimum discretised intensity at cumulative volume <= 10%"),
    _row("Volume fraction difference between intensity fractions", "DDTU", "V10 minus V90 from the cumulative IVH"),
    _row("Intensity fraction difference between volume fractions", "CNV2", "I10 minus I90 from the cumulative IVH"),
    _row("Area under the IVH curve", "9CMM", "trapezoidal area under the cumulative IVH curve", "not_standardized"),
]

_GLCM = [
    _row("Joint maximum", "GYBY", "maximum element of the normalized symmetric GLCM"),
    _row("Joint average", "60VM", "sum of grey level times joint probability"),
    _row("Joint variance", "UR99", "second central moment around joint average"),
    _row("Joint entropy", "TU9B", "Shannon entropy of joint GLCM probabilities"),
    _row("Difference average", "TF7R", "mean absolute grey-level difference"),
    _row("Difference variance", "D3YU", "variance of the absolute grey-level difference distribution"),
    _row("Difference entropy", "NTRS", "entropy of the absolute grey-level difference distribution"),
    _row("Sum average", "ZGXS", "mean of the grey-level sum distribution"),
    _row("Sum variance", "OEEB", "variance of the grey-level sum distribution"),
    _row("Sum entropy", "P6QZ", "entropy of the grey-level sum distribution"),
    _row("Angular second moment", "8ZQL", "sum of squared normalized GLCM probabilities"),
    _row("Contrast", "ACUI", "second moment of grey-level differences"),
    _row("Dissimilarity", "8S9J", "mean absolute grey-level difference"),
    _row("Inverse difference", "IB1Z", "sum p/(1+absolute grey-level difference)"),
    _row("Normalised inverse difference", "NDRX", "inverse difference normalized by number of grey levels"),
    _row("Inverse difference moment", "WF0Z", "sum p/(1+grey-level difference squared)"),
    _row("Normalised inverse difference moment", "1QCO", "inverse difference moment normalized by grey-level range"),
    _row("Inverse variance", "E8JP", "sum p/(grey-level difference squared), excluding diagonal"),
    _row("Correlation", "NI2N", "GLCM covariance divided by marginal standard deviations"),
    _row("Autocorrelation", "QWB0", "sum of grey-level products times joint probability"),
    _row("Cluster tendency", "DG8W", "second moment of i+j around twice the marginal mean"),
    _row("Cluster shade", "7NFM", "third signed moment of i+j around twice the marginal mean"),
    _row("Cluster prominence", "AE86", "fourth moment of i+j around twice the marginal mean"),
    _row("Information correlation 1", "R8DG", "one minus joint/conditional entropy normalized by marginal entropy"),
    _row("Information correlation 2", "JN9H", "sqrt(1-exp(-2*(HXY2-HXY)))"),
]

_GLRLM = [
    _row("Short runs emphasis", "22OV", "sum P/(run length squared), normalized by runs"),
    _row("Long runs emphasis", "W4KF", "sum P*run length squared, normalized by runs"),
    _row("Low grey level run emphasis", "V3SW", "sum P/(grey level squared), normalized by runs"),
    _row("High grey level run emphasis", "G3QZ", "sum P*grey level squared, normalized by runs"),
    _row("Short run low grey level emphasis", "HTZT", "sum P/(grey level squared*run length squared)"),
    _row("Short run high grey level emphasis", "GD3A", "sum P*grey level squared/run length squared"),
    _row("Long run low grey level emphasis", "IVPO", "sum P*run length squared/grey level squared"),
    _row("Long run high grey level emphasis", "3KUM", "sum P*grey level squared*run length squared"),
    _row("Grey level non-uniformity", "R5YN", "sum squared grey-level run totals divided by runs"),
    _row("Normalised grey level non-uniformity", "OVBL", "grey-level non-uniformity divided by runs squared"),
    _row("Run length non-uniformity", "W92Y", "sum squared run-length totals divided by runs"),
    _row("Normalised run length non-uniformity", "IC23", "run-length non-uniformity divided by runs squared"),
    _row("Run percentage", "9ZK5", "number of runs divided by voxel-direction opportunities"),
    _row("Grey level variance", "8CE5", "variance of grey-level row totals"),
    _row("Run length variance", "SXLW", "variance of run-length column totals"),
    _row("Run entropy", "HJ9O", "Shannon entropy of normalized run probabilities"),
]

_GLSZM = [
    _row("Small zone emphasis", "5QRC", "sum P/(zone size squared), normalized by zones"),
    _row("Large zone emphasis", "48P8", "sum P*zone size squared, normalized by zones"),
    _row("Low grey level zone emphasis", "XMSY", "sum P/(grey level squared), normalized by zones"),
    _row("High grey level zone emphasis", "5GN9", "sum P*grey level squared, normalized by zones"),
    _row("Small zone low grey level emphasis", "5RAI", "sum P/(grey level squared*zone size squared)"),
    _row("Small zone high grey level emphasis", "HW1V", "sum P*grey level squared/zone size squared"),
    _row("Large zone low grey level emphasis", "YH51", "sum P*zone size squared/grey level squared"),
    _row("Large zone high grey level emphasis", "J17V", "sum P*grey level squared*zone size squared"),
    _row("Grey level non-uniformity", "JNSA", "sum squared grey-level zone totals divided by zones"),
    _row("Normalised grey level non-uniformity", "Y1RO", "grey-level non-uniformity divided by zones squared"),
    _row("Zone size non-uniformity", "4JP3", "sum squared zone-size totals divided by zones"),
    _row("Normalised zone size non-uniformity", "VB3A", "zone-size non-uniformity divided by zones squared"),
    _row("Zone percentage", "P30P", "number of zones divided by ROI voxels"),
    _row("Grey level variance", "BYLV", "variance of grey-level zone totals"),
    _row("Zone size variance", "3NSA", "variance of zone-size totals"),
    _row("Zone size entropy", "GU8N", "Shannon entropy of normalized zone probabilities"),
]

_GLDZM = [
    _row("Small distance emphasis", "0GBI", "sum P/(distance squared), normalized by zones"),
    _row("Large distance emphasis", "MB4I", "sum P*distance squared, normalized by zones"),
    _row("Low grey level zone emphasis", "S1RA", "sum P/(grey level squared), normalized by zones"),
    _row("High grey level zone emphasis", "K26C", "sum P*grey level squared, normalized by zones"),
    _row("Small distance low grey level emphasis", "RUVG", "sum P/(grey level squared*distance squared)"),
    _row("Small distance high grey level emphasis", "DKNJ", "sum P*grey level squared/distance squared"),
    _row("Large distance low grey level emphasis", "A7WM", "sum P*distance squared/grey level squared"),
    _row("Large distance high grey level emphasis", "KLTH", "sum P*grey level squared*distance squared"),
    _row("Grey level non-uniformity", "VFT7", "sum squared grey-level zone totals divided by zones"),
    _row("Normalised grey level non-uniformity", "7HP3", "grey-level non-uniformity divided by zones squared"),
    _row("Zone distance non-uniformity", "V294", "sum squared distance totals divided by zones"),
    _row("Normalised zone distance non-uniformity", "IATH", "distance non-uniformity divided by zones squared"),
    _row("Zone percentage", "VIWW", "number of distance zones divided by ROI voxels"),
    _row("Grey level variance", "QK93", "variance of grey-level zone totals"),
    _row("Zone distance variance", "7WT1", "variance of distance-zone totals"),
    _row("Zone distance entropy", "GBDU", "Shannon entropy of normalized distance-zone probabilities"),
]

_NGTDM = [
    _row("Coarseness", "QCDE", "inverse probability-weighted neighbourhood grey-tone difference"),
    _row("Contrast", "65HE", "grey-level dynamic-range term multiplied by mean neighbourhood difference"),
    _row("Busyness", "NQ30", "mean neighbourhood difference divided by absolute intensity-probability changes"),
    _row("Complexity", "HDEZ", "probability-weighted pairwise grey-level differences and tone differences"),
    _row("Strength", "1X9X", "pairwise grey-level contrast divided by total neighbourhood difference"),
]

_NGLDM = [
    _row("Low dependence emphasis", "SODN", "sum S/(dependence count squared), normalized by dependences"),
    _row("High dependence emphasis", "IMOQ", "sum S*dependence count squared, normalized by dependences"),
    _row("Low grey level count emphasis", "TL9H", "sum S/(grey level squared), normalized by dependences"),
    _row("High grey level count emphasis", "OAE7", "sum S*grey level squared, normalized by dependences"),
    _row("Low dependence low grey level emphasis", "EQ3F", "sum S/(grey level squared*dependence squared)"),
    _row("Low dependence high grey level emphasis", "JA6D", "sum S*grey level squared/dependence squared"),
    _row("High dependence low grey level emphasis", "NBZI", "sum S*dependence squared/grey level squared"),
    _row("High dependence high grey level emphasis", "9QMG", "sum S*grey level squared*dependence squared"),
    _row("Grey level non-uniformity", "FP8K", "sum squared grey-level dependence totals divided by dependences"),
    _row("Normalised grey level non-uniformity", "5SPA", "grey-level non-uniformity divided by dependences squared"),
    _row("Dependence count non-uniformity", "Z87G", "sum squared dependence totals divided by dependences"),
    _row("Normalised dependence count non-uniformity", "OKJI", "dependence non-uniformity divided by dependences squared"),
    _row("Dependence count percentage", "6XV8", "number of dependences divided by ROI voxels"),
    _row("Grey level variance", "1PFV", "variance of grey-level dependence totals"),
    _row("Dependence count variance", "DNX2", "variance of dependence-count totals"),
    _row("Dependence count entropy", "FCBV", "Shannon entropy of normalized dependence probabilities"),
    _row("Dependence count energy", "CAS9", "sum of squared normalized dependence probabilities"),
]


def _build_catalogue() -> tuple[IBSIFeatureSpec, ...]:
    groups = (
        ("3.1", "morphology", _MORPHOLOGY, "morphological + intensity", False),
        ("3.2", "local_intensity", _LOCAL_INTENSITY, "intensity", False),
        ("3.3", "intensity_statistics", _INTENSITY_STATS, "intensity", False),
        ("3.4", "intensity_histogram", _INTENSITY_HISTOGRAM, "intensity", True),
        ("3.5", "intensity_volume_histogram", _INTENSITY_VOLUME_HISTOGRAM, "intensity", True),
        ("3.6", "glcm", _GLCM, "intensity", True),
        ("3.7", "glrlm", _GLRLM, "intensity", True),
        ("3.8", "glszm", _GLSZM, "intensity", True),
        ("3.9", "gldzm", _GLDZM, "morphological + intensity", True),
        ("3.10", "ngtdm", _NGTDM, "intensity", True),
        ("3.11", "ngldm", _NGLDM, "intensity", True),
    )
    result: list[IBSIFeatureSpec] = []
    for section, family, rows, required_mask, requires_discretisation in groups:
        for name, code, definition, source_status in rows:
            result.append(
                IBSIFeatureSpec(
                    section=section,
                    family=family,
                    name=name,
                    code=code,
                    definition=definition,
                    required_mask=_indicator_mask(family, code, required_mask),
                    requires_discretisation=requires_discretisation,
                    standard_status=source_status,
                    source_note=(
                        "IBSI explicitly reports no reference values and says this feature should not be used."
                        if source_status == "not_standardized"
                        else None
                    ),
                )
            )
    return tuple(result)


IBSI_FEATURE_CATALOG: tuple[IBSIFeatureSpec, ...] = _build_catalogue()
IBSI_FEATURES_BY_CODE: dict[str, IBSIFeatureSpec] = {spec.code: spec for spec in IBSI_FEATURE_CATALOG}
IBSI_FEATURES_BY_KEY: dict[str, IBSIFeatureSpec] = {spec.key: spec for spec in IBSI_FEATURE_CATALOG}
IBSI_EXPECTED_FEATURE_COUNT = len(IBSI_FEATURE_CATALOG)
IBSI_FAMILY_COUNTS: dict[str, int] = {
    family: sum(spec.family == family for spec in IBSI_FEATURE_CATALOG)
    for family in {spec.family for spec in IBSI_FEATURE_CATALOG}
}

# Guard the source table at import time.  This catches accidental omissions or
# duplicate permanent identifiers during maintenance and keeps the meeting's
# stated 29+2+18+23+5+25+16+16+16+5+17 contract explicit.
assert IBSI_EXPECTED_FEATURE_COUNT == 172, IBSI_EXPECTED_FEATURE_COUNT
assert IBSI_FAMILY_COUNTS == {
    "morphology": 29,
    "local_intensity": 2,
    "intensity_statistics": 18,
    "intensity_histogram": 23,
    "intensity_volume_histogram": 5,
    "glcm": 25,
    "glrlm": 16,
    "glszm": 16,
    "gldzm": 16,
    "ngtdm": 5,
    "ngldm": 17,
}, IBSI_FAMILY_COUNTS
assert len(IBSI_FEATURES_BY_CODE) == IBSI_EXPECTED_FEATURE_COUNT


def get_ibsi_feature_catalog() -> list[dict[str, Any]]:
    """Return a JSON-serialisable ordered catalogue for API/report use."""

    return [spec.as_dict() for spec in IBSI_FEATURE_CATALOG]


def get_ibsi_feature_spec(code_or_key: str) -> IBSIFeatureSpec:
    """Resolve a permanent code or canonical ``ibsi_<code>`` key."""

    value = str(code_or_key)
    if value.startswith("ibsi_"):
        value = value[5:]
    try:
        return IBSI_FEATURES_BY_CODE[value]
    except KeyError as exc:
        raise KeyError(f"unknown IBSI feature code: {code_or_key}") from exc


__all__ = [
    "IBSIFeatureSpec",
    "IBSI_EXPECTED_FEATURE_COUNT",
    "IBSI_FAMILY_COUNTS",
    "IBSI_FEATURE_CATALOG",
    "IBSI_FEATURES_BY_CODE",
    "IBSI_FEATURES_BY_KEY",
    "get_ibsi_feature_catalog",
    "get_ibsi_feature_spec",
]
