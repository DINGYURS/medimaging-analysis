"""Modality/organ profiles shared by the workspace radiomics paths.

The profile decision is deliberately conservative for MR: MR signal alone is
not evidence that an image is a liver study.  Older CT rows without anatomy
metadata retain the historical lung profile for backwards compatibility.
"""
from __future__ import annotations

from typing import Any


_LIVER_TOKENS = (
    "liver",
    "hepatic",
    "hepat",
    "肝",
    "肝脏",
)
_LUNG_TOKENS = ("lung", "pulmonary", "chest", "thorax", "肺", "胸")
_MR_MODALITIES = {"MR", "MRI"}
_CT_MODALITIES = {"CT"}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(_text(item) for item in value)
    return str(value)


def _metadata(series: Any) -> dict[str, Any]:
    value = getattr(series, "series_metadata", None)
    if isinstance(value, dict):
        return value
    value = getattr(series, "metadata", None)
    return value if isinstance(value, dict) else {}


def _explicit_liver(series: Any) -> bool:
    metadata = _metadata(series)
    body_part = _text(getattr(series, "body_part", None))
    # These fields are the stable organ/sequence hints produced by the
    # importer and liver-MRI workspace.  Do not treat a generic T1/T2 role as
    # liver by itself: many non-liver MR studies use the same roles.
    explicit_values: list[Any] = [body_part, getattr(series, "description", None)]
    for key in (
        "organ",
        "anatomy",
        "body_part",
        "bodyPart",
        "dataset",
        "dataset_name",
        "datasetName",
        "task_name",
        "taskName",
        "sequence_role",
        "sequenceRole",
        "nnunet_channel",
        "nnunetChannel",
        "channel",
        "description",
        "series_description",
    ):
        explicit_values.append(metadata.get(key))
    # A nested dataset/sequence metadata object is common in imported NIfTI
    # manifests, so include it without making arbitrary MR text liver-like.
    for key in ("dataset", "sequence", "series", "study"):
        nested = metadata.get(key)
        if isinstance(nested, dict):
            explicit_values.extend(nested.values())
    haystack = _text(explicit_values).casefold()
    return any(token.casefold() in haystack for token in _LIVER_TOKENS)


def _explicit_lung(series: Any) -> bool:
    metadata = _metadata(series)
    values: list[Any] = [getattr(series, "body_part", None), getattr(series, "description", None)]
    for key in (
        "organ",
        "anatomy",
        "body_part",
        "bodyPart",
        "dataset",
        "dataset_name",
        "task_name",
        "description",
        "series_description",
    ):
        values.append(metadata.get(key))
    haystack = _text(values).casefold()
    return any(token.casefold() in haystack for token in _LUNG_TOKENS)


def resolve_radiomics_profile(series: Any) -> str:
    """Return the safe radiomics profile for an ``ImageSeries``-like object.

    Liver anatomy wins over modality.  CT without anatomy metadata maps to
    ``lung_ct`` so existing lung fixtures and historical CT rows keep their
    feature contract.  MR without an explicit liver signal is ``generic``;
    this prevents arbitrary MR signal from being treated as liver or HU.
    """

    modality = _text(getattr(series, "modality", None)).strip().upper()
    if _explicit_liver(series):
        if modality in _MR_MODALITIES:
            return "liver_mr"
        if modality in _CT_MODALITIES:
            return "liver_ct"

    if modality in _CT_MODALITIES:
        # Historical CT rows often have no anatomy metadata and are retained
        # as lung_ct.  Once a non-lung anatomy is explicit, avoid applying
        # lung-specific custom features to it.
        if not _text(getattr(series, "body_part", None)).strip() or _explicit_lung(series):
            return "lung_ct"
        return "generic"
    if modality in _MR_MODALITIES:
        return "generic"
    return "generic"


def sequence_role(series: Any) -> str | None:
    """Return a persisted sequence role, when available, for provenance."""

    metadata = _metadata(series)
    for key in ("sequence_role", "sequenceRole", "role"):
        value = metadata.get(key)
        if value not in (None, ""):
            return str(value)
    return None


def intensity_unit(profile: str, *, normalize: bool, modality: str | None = None) -> str:
    """Return the displayed unit without conflating MR signal and HU.

    Normalization changes the reporting unit for every modality.  For an
    otherwise generic profile, the actual series modality is more reliable
    than anatomy assumptions (e.g. a non-liver abdominal CT remains HU).
    """

    if normalize:
        # Keep this string aligned with the research export manifest.  It
        # makes the scale convention visible wherever a result is displayed
        # and prevents a normalized MR/CT value from being mistaken for HU.
        return "normalized (z-score x scale)"
    modality_name = str(modality or "").strip().upper()
    if modality_name == "CT" or profile in {"lung_ct", "liver_ct"}:
        return "HU"
    if modality_name in {"MR", "MRI"} or profile == "liver_mr":
        return "a.u."
    return "native"
