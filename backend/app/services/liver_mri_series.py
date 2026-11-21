"""Conservative selection of native liver-MRI DICOM series.

The ordinary DICOM importer intentionally remains modality agnostic.  This
module is the small, explicit preprocessor used by the batch importer when a
study is recognisably a liver MR examination.  It works from DICOM headers and
returns whole SeriesInstanceUID groups; callers never select individual slices
from a selected series.

The importer stores the resulting role/channel in the existing series metadata
JSON.  No registration or resampling is implied by this module.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
import math
import re
from typing import Any, Iterable, Sequence


LIVER_MRI_CHANNELS: tuple[tuple[int, str], ...] = (
    (0, "t2"),
    (1, "t1_pre"),
    (2, "arterial_early"),
    (3, "portal"),
    (4, "delayed"),
)
ROLE_CHANNEL = dict((role, channel) for channel, role in LIVER_MRI_CHANNELS)


class LiverMriSelectionError(ValueError):
    """Raised when an explicit liver MR study has no usable target series."""


@dataclass(frozen=True)
class LiverMriSeriesSelection:
    """One complete SeriesInstanceUID selected for one model role."""

    study_uid: str
    patient_identity: str
    series_uid: str
    role: str
    channel: int
    entries: tuple[Any, ...]
    series_number: float | None
    acquisition_datetime: str
    spacing_z: float | None

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.study_uid, self.patient_identity, self.series_uid)


_LIVER_RE = re.compile(r"(?:liver|hepatic|hepat(?:ic|obiliary)?|肝脏|肝)", re.IGNORECASE)
_ABDOMEN_RE = re.compile(
    r"(?:"
    r"\babdomen\b|\babdominal\b|\bupper[ _-]?abd\b|\babd\b|"
    r"\b(?:tree|leaf)[ _^:-]*abd\b|腹部|上腹"
    r")",
    re.IGNORECASE,
)
_HEAD_BODY_RE = re.compile(r"(?:\bhead\b|\bbrain\b|\bcran(?:ium|ial)\b|头部|颅脑)", re.IGNORECASE)
_EXCLUDED_RE = re.compile(
    r"(?:"
    r"locali[sz](?:er|ation|ation|e)?|scout|survey|topogram|locator|"
    r"care[ _-]?bolus|bolus[ _-]?track|test[ _-]?bolus|"
    r"resp(?:iratory)?|"
    r"coronal|sagittal|oblique|冠状|矢状|"
    r"dwi|diffusion|diff|adc|trace|"
    r"hepatobiliary|hbp|"
    r"in[ _-]?phase|out[ _-]?phase|inphase|outphase|opposed[ _-]?phase|"
    r"同相|反相|脂像"
    r")",
    re.IGNORECASE,
)
_POSTCONTRAST_RE = re.compile(
    r"(?:post(?:contrast)?|enh(?:anced)?|gad(?:olinium)?|ce[-_ ]?t1|"
    r"arter(?:ial)?|portal|pvp|ven(?:ous)?|delay(?:ed)?|equilibrium)",
    re.IGNORECASE,
)


def _value(ds: Any, name: str, default: Any = None) -> Any:
    value = getattr(ds, name, default)
    return default if value is None else value


def _string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(_string(item) for item in value)
    return str(value)


def _compact(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.casefold())


def dicom_text(ds: Any) -> str:
    """Return the header text relevant to conservative sequence selection."""

    fields = (
        "StudyDescription",
        "SeriesDescription",
        "ProtocolName",
        "SequenceName",
        "BodyPartExamined",
        "ImageType",
        "PerformedProcedureStepDescription",
    )
    return " ".join(_string(_value(ds, field, "")) for field in fields if _value(ds, field, ""))


def sequence_text(ds: Any) -> str:
    """Return per-series labels, excluding study-level anatomy/protocol text."""

    fields = (
        "SeriesDescription",
        "ProtocolName",
        "SequenceName",
        "ImageType",
        "PerformedProcedureStepDescription",
    )
    return " ".join(_string(_value(ds, field, "")) for field in fields if _value(ds, field, ""))


def _is_fat_only_or_dixon_phase(text: str) -> bool:
    """Reject Dixon F/in/opposed images while retaining ordinary fat-sat T2."""

    raw = _string(text)
    lowered = raw.casefold()
    # ``fat sat``/``fat suppression`` and ``*_fs`` are ordinary anatomic
    # sequences and remain eligible.  A standalone FAT image is a separate
    # Dixon output and must be dropped.
    fat_only = bool(re.search(r"(?:^|[^a-z])fat(?:$|[^a-z])", lowered)) and not bool(
        re.search(r"\bfat[ _-]?(?:sat|suppression)\b", lowered)
    )
    dixon_f = bool(re.search(r"(?:^|[_ -])dixon[_ -]?f(?:$|[_ -])", lowered))
    short_phase = bool(re.search(r"(?:^|[_ -])(?:in|out|opp|f)(?:$|[_ -])", lowered))
    return fat_only or dixon_f or short_phase


def _sequence_is_excluded(ds: Any) -> bool:
    text = sequence_text(ds)
    if _EXCLUDED_RE.search(text):
        return True
    if _is_fat_only_or_dixon_phase(text):
        return True
    raw_image_type = _value(ds, "ImageType", ()) or ()
    if isinstance(raw_image_type, str):
        raw_image_type = (raw_image_type,)
    image_type = {_string(item).casefold() for item in raw_image_type}
    return bool(image_type.intersection({"fat", "in_phase", "out_phase", "opp_phase", "opposed_phase"}))


def _is_explicit_head_body_part(ds: Any) -> bool:
    return bool(_HEAD_BODY_RE.search(_string(_value(ds, "BodyPartExamined", ""))))


def _has_prefix_token(text: str, token: str) -> bool:
    """Match scanner prefixes such as ``T2tra`` without matching ``art2``."""

    return bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(token)}", text, re.IGNORECASE))


def is_liver_mri(ds: Any) -> bool:
    """Return true only for an MR header with an explicit liver signal.

    ``ABDOMEN`` and generic ``T1``/``T2`` labels are deliberately insufficient:
    this keeps abdominal MR studies from being silently reduced to five roles.
    """

    modality = _string(_value(ds, "Modality", "")).strip().upper()
    if modality not in {"MR", "MRI"}:
        return False
    body_part = _string(_value(ds, "BodyPartExamined", ""))
    return bool(_LIVER_RE.search(dicom_text(ds)) or _LIVER_RE.search(body_part))


def is_liver_mri_study(ds: Any) -> bool:
    """Descriptive alias used by import callers and tests."""

    return is_liver_mri(ds)


def is_liver_mri_group(entries: Iterable[Any]) -> bool:
    """Infer a liver dynamic-MR study from its complete header collection.

    Some scanners leave BodyPartExamined empty and label the study only as
    ``NBEY Abdomen``.  An isolated abdomen T2 remains generic, while an
    abdomen study containing the characteristic T2/T1 plus multiple contrast
    phases is sufficiently specific to use the liver five-channel importer.
    """

    rows = [_entry_dataset(entry) for entry in entries]
    if not rows:
        return False
    mr_rows = [
        row
        for row in rows
        if _string(_value(row, "Modality", "")).strip().upper() in {"MR", "MRI"}
    ]
    if not mr_rows:
        return False
    explicit_liver_body = any(
        _LIVER_RE.search(_string(_value(row, "BodyPartExamined", "")))
        for row in mr_rows
    )
    # An explicit HEAD body part is a hard stop for anatomy-free fallback;
    # otherwise a head-labelled study with a complete abdominal dynamic set
    # can be recognised from its actual series headers.
    if any(_is_explicit_head_body_part(row) for row in mr_rows) and not explicit_liver_body:
        return False
    if any(is_liver_mri(row) for row in mr_rows):
        return True

    anatomy_text = " ".join(
        _string(_value(row, field, ""))
        for row in mr_rows
        for field in ("StudyDescription", "BodyPartExamined", "PerformedProcedureStepDescription")
    )
    has_abdomen_context = bool(_ABDOMEN_RE.search(anatomy_text))

    roles = {role for row in mr_rows if (role := classify_sequence(row)) is not None}
    if not roles:
        return False
    target_roles = roles.intersection({"t1_pre", "t2", "arterial_early", "portal", "delayed"})
    contrast_roles = roles.intersection({"arterial_early", "portal", "delayed"})
    if has_abdomen_context:
        # BodyPartExamined=ABDOMEN and scanner labels such as Tree^ABD/Leaf
        # ABD are valid anatomy signals. Require a base sequence plus two
        # distinct contrast phases to avoid treating a generic abdomen MR as
        # a native liver study.
        return len(target_roles) >= 3 and len(contrast_roles) >= 2

    # With no trustworthy anatomy label, only a complete five-role dynamic
    # set can establish liver-MR intent. This allows a head-labelled study
    # whose actual series are a complete abdominal dynamic exam, while a
    # title alone never promotes a queue.
    return target_roles == {"t1_pre", "t2", "arterial_early", "portal", "delayed"}


def patient_identity(ds: Any) -> str:
    """Return a stable grouping value without exposing it in logs or errors."""

    return _string(_value(ds, "PatientID", "")).strip()


def study_uid(ds: Any) -> str:
    return _string(_value(ds, "StudyInstanceUID", "")).strip()


def series_uid(ds: Any) -> str:
    return _string(_value(ds, "SeriesInstanceUID", "")).strip()


def _orientation(ds: Any) -> tuple[float, ...] | None:
    raw = _value(ds, "ImageOrientationPatient")
    try:
        values = tuple(float(item) for item in raw)
    except (TypeError, ValueError):
        return None
    return values if len(values) == 6 and all(math.isfinite(item) for item in values) else None


def is_axial(ds: Any) -> bool:
    """Check axial geometry from ImageOrientationPatient.

    Description tokens are checked separately by :func:`sequence_role`; this
    function intentionally refuses missing IOP rather than trusting a filename
    or a renamed series.
    """

    orientation = _orientation(ds)
    if orientation is None:
        return False
    row = orientation[:3]
    column = orientation[3:]
    normal = (
        row[1] * column[2] - row[2] * column[1],
        row[2] * column[0] - row[0] * column[2],
        row[0] * column[1] - row[1] * column[0],
    )
    magnitude = math.sqrt(sum(item * item for item in normal))
    return magnitude > 1e-6 and abs(normal[2] / magnitude) >= 0.80


def _has_token(compact: str, *tokens: str) -> bool:
    return any(token in compact for token in tokens)


def sequence_role(text: str | None) -> str | None:
    """Classify only the five import roles; unsupported sequences return None."""

    raw = _string(text)
    if not raw or _EXCLUDED_RE.search(raw) or _is_fat_only_or_dixon_phase(raw):
        return None
    compact = _compact(raw)

    # Contrast phases take precedence over generic T1 labels.  The explicit
    # forms below cover common names such as ``2AP_W70``, ``A15`` and ``PVP70``.
    if _has_token(
        compact,
        "arterial",
        "artery",
        "2ap",
        "rega",
        "动脉",
    ) or re.search(
        r"(?:^|[^a-z])(?:ap|a(?:1|2|10|15|20|25|30))(?:$|[^a-z0-9])|"
        r"(?<![a-z0-9])art(?:erial|[ _-]?\d+)",
        raw,
        re.IGNORECASE,
    ):
        return "arterial_early"
    if _has_token(compact, "portal", "pvp", "venous", "regv", "mdixonvp", "门脉") or re.search(
        r"(?<![a-z0-9])m?dixon[ _-]?vp", raw, re.IGNORECASE
    ):
        return "portal"
    if _has_token(compact, "delayed", "delay", "equilibrium", "regd", "dly", "mdixondp", "延迟") or re.search(
        r"(?:\d+\s*(?:min|minute|分钟))|(?<![a-z0-9])m?dixon[ _-]?dp",
        raw,
        re.IGNORECASE,
    ):
        return "delayed"
    if _has_prefix_token(raw, "t2") or _has_token(compact, "haste", "ssfse", "tse"):
        return "t2"

    # T1 precontrast/water labels are intentionally explicit.  A bare T1 is
    # accepted only when it is clearly a water/plain/pre series and lacks any
    # post-contrast phase marker.
    has_t1 = _has_prefix_token(raw, "t1") or "vibe" in compact
    has_pre = _has_token(
        compact,
        "t1pre",
        "precontrast",
        "noncontrast",
        "unenhanced",
        "plain",
        "regpre",
        "平扫",
    ) or bool(re.search(r"(?:^|[^a-z])pre(?:$|[^a-z0-9])", raw, re.IGNORECASE))
    has_water = bool(
        re.search(r"(?:^|[^a-z])w(?:ater)?(?=$|[^a-z0-9])", raw, re.IGNORECASE)
        or _has_token(compact, "水像", "water")
    )
    if has_t1 and not _POSTCONTRAST_RE.search(raw) and (has_pre or has_water):
        return "t1_pre"
    return None


def classify_liver_mri_sequence(text: str | None) -> str | None:
    """Compatibility-friendly name for callers that classify description text."""

    return sequence_role(text)


def classify_sequence(ds: Any) -> str | None:
    """Classify one header after applying the axial DICOM geometry check."""

    if _string(_value(ds, "Modality", "")).strip().upper() not in {"MR", "MRI"}:
        return None
    if _is_explicit_head_body_part(ds):
        return None
    if _sequence_is_excluded(ds) or not is_axial(ds):
        return None
    return sequence_role(sequence_text(ds))


def _series_number(ds: Any) -> float | None:
    try:
        value = float(_value(ds, "SeriesNumber"))
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _acquisition_datetime(ds: Any) -> str:
    for name in ("AcquisitionDateTime", "ContentDateTime"):
        value = _string(_value(ds, name, "")).strip()
        if value:
            return value
    date = _string(_value(ds, "SeriesDate", "")).strip()
    time = _string(_value(ds, "SeriesTime", "")).strip()
    return f"{date}{time}"


def _position(ds: Any) -> tuple[float, float, float] | None:
    raw = _value(ds, "ImagePositionPatient")
    try:
        values = tuple(float(item) for item in raw)
    except (TypeError, ValueError):
        return None
    return values if len(values) == 3 and all(math.isfinite(item) for item in values) else None


def _normal(ds: Any) -> tuple[float, float, float] | None:
    orientation = _orientation(ds)
    if orientation is None:
        return None
    row = orientation[:3]
    column = orientation[3:]
    normal = (
        row[1] * column[2] - row[2] * column[1],
        row[2] * column[0] - row[0] * column[2],
        row[0] * column[1] - row[1] * column[0],
    )
    magnitude = math.sqrt(sum(item * item for item in normal))
    return tuple(item / magnitude for item in normal) if magnitude > 1e-6 else None


def _series_geometry_is_3d(entries: Sequence[Any]) -> bool:
    """Reject mixed-direction or repeated-position stacks before 3-D use."""

    if len(entries) <= 1:
        return True
    normals = [_normal(_entry_dataset(item)) for item in entries]
    positions = [_position(_entry_dataset(item)) for item in entries]
    if any(normal is None for normal in normals) or any(position is None for position in positions):
        return False
    reference = normals[0]
    assert reference is not None
    for normal in normals[1:]:
        assert normal is not None
        if abs(sum(left * right for left, right in zip(reference, normal))) < 0.999:
            return False
    # A repeated location in one SeriesUID can be a temporal/triggered stack
    # or mixed acquisition.  It must not masquerade as a regular 3-D volume.
    projected = [round(sum(value * direction for value, direction in zip(position, reference)), 4) for position in positions if position is not None]
    return len(projected) == len(set(projected))


def infer_spacing_z(entries: Sequence[Any]) -> float | None:
    """Infer interslice distance from ImagePositionPatient when available."""

    positions: list[float] = []
    reference_normal = next((normal for item in entries if (normal := _normal(item)) is not None), None)
    if reference_normal is not None:
        for item in entries:
            position = _position(item)
            if position is None:
                continue
            positions.append(sum(a * b for a, b in zip(position, reference_normal)))
    unique_positions = sorted({round(value, 5) for value in positions})
    gaps = [right - left for left, right in zip(unique_positions, unique_positions[1:]) if right - left > 1e-3]
    if gaps:
        gaps.sort()
        distance = gaps[len(gaps) // 2] if len(gaps) % 2 else (
            gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]
        ) / 2
        # DICOM positions often contain harmless ~1e-5 floating noise.  Keep
        # useful physical precision while exposing the scanner value as 3.0
        # or 7.2 rather than a binary-decimal artifact.
        return round(float(distance), 4)

    for item in entries:
        for name in ("SpacingBetweenSlices", "SliceThickness"):
            try:
                value = float(_value(item, name))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                return round(value, 4)
    return None


def _entry_dataset(entry: Any) -> Any:
    return getattr(entry, "dataset", entry)


def _series_role(entries: Sequence[Any]) -> str | None:
    roles: list[str] = []
    for entry in entries:
        ds = _entry_dataset(entry)
        role = classify_sequence(ds)
        if role is not None:
            roles.append(role)
    if not roles:
        return None
    counts = Counter(roles)
    # A series is expected to have a stable description.  If a vendor writes a
    # slightly different description per slice, role/channel priority keeps the
    # result deterministic without choosing by incoming file order.
    return min(counts, key=lambda role: (-counts[role], ROLE_CHANNEL[role]))


def _series_sort_key(selection: LiverMriSeriesSelection) -> tuple[Any, ...]:
    # Prefer a complete stack, then stable DICOM series number/time/UID.  The
    # caller sorts candidates with ``-len(entries)`` before this key.
    return (
        selection.series_number is None,
        selection.series_number if selection.series_number is not None else math.inf,
        selection.acquisition_datetime,
        selection.series_uid,
    )


def select_liver_mri_series(
    entries: Iterable[Any],
    *,
    known_liver_groups: set[tuple[str, str]] | None = None,
    preferred_series_by_role: dict[tuple[str, str], dict[str, set[str]]] | None = None,
) -> tuple[LiverMriSeriesSelection, ...]:
    """Select at most one complete SeriesInstanceUID for each liver-MR role.

    ``entries`` can be any objects with a ``dataset`` attribute (the batch
    importer uses staged temporary files) or raw pydicom datasets.  Non-liver
    MR and non-MR entries are ignored by this selector and must be retained by
    the caller's ordinary import path.
    """

    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    known_liver_groups = known_liver_groups or set()
    preferred_series_by_role = preferred_series_by_role or {}
    for entry in entries:
        ds = _entry_dataset(entry)
        uid = study_uid(ds)
        series = series_uid(ds)
        if not uid or not series:
            continue
        groups[(uid, patient_identity(ds))].append(entry)

    selections: list[LiverMriSeriesSelection] = []
    for (uid, identity), group_entries in groups.items():
        # An already imported native liver-MR study may arrive in separate
        # batches.  Its second upload can contain only one role, so the
        # abdomen-plus-multiple-phase inference used for a fresh study cannot
        # be required again.  The caller supplies only groups whose existing
        # study explicitly carries the per-series contract.
        if (uid, identity) not in known_liver_groups and not is_liver_mri_group(group_entries):
            continue
        by_series: dict[str, list[Any]] = defaultdict(list)
        for entry in group_entries:
            by_series[series_uid(_entry_dataset(entry))].append(entry)

        candidates: list[LiverMriSeriesSelection] = []
        for candidate_uid, series_entries in by_series.items():
            # All entries in a selected UID are carried forward.  We use a
            # representative role from the complete group and never slice a
            # series down to only the headers that happened to match a token.
            if not _series_geometry_is_3d(series_entries):
                continue
            role = _series_role(series_entries)
            if role is None:
                continue
            representative = min(
                (_entry_dataset(item) for item in series_entries),
                key=lambda ds: (_series_number(ds) is None, _series_number(ds) or math.inf, _acquisition_datetime(ds), series_uid(ds)),
            )
            candidates.append(
                LiverMriSeriesSelection(
                    study_uid=uid,
                    patient_identity=identity,
                    series_uid=candidate_uid,
                    role=role,
                    channel=ROLE_CHANNEL[role],
                    entries=tuple(series_entries),
                    series_number=_series_number(representative),
                    acquisition_datetime=_acquisition_datetime(representative),
                    spacing_z=infer_spacing_z([_entry_dataset(item) for item in series_entries]),
                )
            )

        if not candidates:
            # A known native study can receive an incremental upload that
            # contains only a discarded localizer/RESP/DWI object.  It is
            # safe to ignore that batch; a newly inferred liver study still
            # receives the useful 422 explaining that no target role exists.
            if (uid, identity) in known_liver_groups:
                continue
            raise LiverMriSelectionError("检测到肝脏 MRI，但没有可识别的轴位目标序列（需要 T2、T1 平扫、动脉、门脉或延迟期）")

        by_role: dict[str, list[LiverMriSeriesSelection]] = defaultdict(list)
        for candidate in candidates:
            by_role[candidate.role].append(candidate)
        for role in sorted(by_role, key=ROLE_CHANNEL.__getitem__):
            selected = sorted(
                by_role[role],
                key=lambda item: (
                    0
                    if item.series_uid in preferred_series_by_role.get((uid, identity), {}).get(role, set())
                    else 1,
                    -len(item.entries),
                    *_series_sort_key(item),
                ),
            )[0]
            selections.append(selected)

    return tuple(selections)


def selection_map(entries: Iterable[Any]) -> dict[tuple[str, str, str], LiverMriSeriesSelection]:
    """Convenience map used by the importer when deciding which files persist."""

    return {selection.key: selection for selection in select_liver_mri_series(entries)}


def is_liver_mri_candidate(entries: Iterable[Any]) -> bool:
    """Whether a set of staged headers contains an explicit liver MR study."""

    return any(is_liver_mri(_entry_dataset(entry)) for entry in entries)
