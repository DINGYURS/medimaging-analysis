from __future__ import annotations
import numpy as np
import math
from typing import Any
from .protocol import RadiomicsProtocolError
def fail(code, message):
    error = RadiomicsProtocolError(f'{code}: {message}')
    error.source_error_code = code
    raise error

def checked_affine(affine):
    matrix = np.asarray(affine, dtype=float)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all() or (not np.allclose(matrix[3], [0, 0, 0, 1])):
        fail('source_geometry_invalid', '空间矩阵无效')
    spacing = np.linalg.norm(matrix[:3, :3], axis=0)
    if np.any(spacing <= 0):
        fail('source_geometry_invalid', '空间间距必须为有限正数')
    direction = matrix[:3, :3] / spacing
    if not np.allclose(direction.T @ direction, np.eye(3), atol=0.0001, rtol=0):
        fail('source_geometry_sheared', '当前读取版本不支持剪切或退化网格，请提供经确认的规则影像')
    return (matrix, tuple((float(v) for v in spacing)))

def nifti_geometry(image):
    if len(image.shape) != 3 or any((v <= 0 for v in image.shape)):
        fail('source_dimensions_unsupported', '科研抽取仅支持三维影像')
    unit = image.header.get_xyzt_units()[0]
    factor = {'mm': 1.0, 'meter': 1000.0, 'micron': 0.001}.get(unit)
    if factor is None:
        fail('source_spatial_unit_unknown', 'NIfTI 未声明可确认的空间单位，不能推断为 mm')
    if int(image.header['sform_code']) == 0 and int(image.header['qform_code']) == 0:
        fail('source_spatial_transform_missing', 'NIfTI 缺少有效 sform/qform，不能使用回退坐标进行计算')
    affine = np.array(image.affine, dtype=float, copy=True)
    affine[:3, :] *= factor
    (affine, spacing) = checked_affine(affine)
    zooms = np.asarray(image.header.get_zooms()[:3]) * factor
    if not np.allclose(zooms, spacing, atol=1e-05, rtol=1e-05):
        fail('source_spacing_conflict', 'NIfTI affine 轴长与 header 间距不一致')
    return (affine, spacing, {'source_spatial_unit': unit, 'spatial_scale_to_mm': factor, 'affine_source': 'sform' if int(image.header['sform_code']) else 'qform'})

def _macro(ds, frame_index, sequence_name, attribute):
    value = getattr(ds, attribute, None)
    for (group_name, index) in [('SharedFunctionalGroupsSequence', 0), ('PerFrameFunctionalGroupsSequence', frame_index)]:
        groups = getattr(ds, group_name, None)
        if groups is not None:
            if index >= len(groups):
                fail('source_frame_metadata_invalid', '多帧元数据数量与影像帧不一致')
            sequence = getattr(groups[index], sequence_name, None)
            if sequence is not None:
                if len(sequence) != 1:
                    fail('source_frame_metadata_invalid', '功能组必须包含一个有效条目')
                value = getattr(sequence[0], attribute, None)
    return value

def dicom_geometry(refs, datasets):
    if len(refs) < 2:
        fail('source_geometry_incomplete', '至少需要两帧位置才能确认三维源网格')
    reference_uids = {str(getattr(ds, 'FrameOfReferenceUID')) for ds in datasets.values() if getattr(ds, 'FrameOfReferenceUID', None)}
    if len(reference_uids) > 1:
        fail('source_geometry_inconsistent', 'DICOM 各帧声明了不同的 FrameOfReferenceUID')
    (orientations, spacings, positions, shapes) = ([], [], [], [])
    for ref in refs:
        ds = datasets[ref.image_file.id]
        try:
            orientation = np.asarray(_macro(ds, ref.frame_index, 'PlaneOrientationSequence', 'ImageOrientationPatient'), dtype=float)
            pixel_spacing = np.asarray(_macro(ds, ref.frame_index, 'PixelMeasuresSequence', 'PixelSpacing'), dtype=float)
            (position, _) = _frame_position_and_normal(ds, ref.frame_index)
            if orientation.shape != (6,) or pixel_spacing.shape != (2,) or position is None or (not np.isfinite(np.r_[orientation, pixel_spacing, position]).all()) or np.any(pixel_spacing <= 0):
                raise ValueError()
            if not np.allclose([np.linalg.norm(orientation[:3]), np.linalg.norm(orientation[3:]), np.dot(orientation[:3], orientation[3:])], [1, 1, 0], atol=0.0001, rtol=0):
                raise ValueError()
            shapes.append((int(ds.Columns), int(ds.Rows)))
        except (ValueError, TypeError, AttributeError):
            fail('source_geometry_incomplete', 'DICOM 缺少有效的位置、方向、尺寸或 PixelSpacing')
        orientations.append(orientation)
        spacings.append(pixel_spacing)
        positions.append(position)
    if len(set(shapes)) != 1 or not np.allclose(orientations, orientations[0], atol=0.0001, rtol=0) or (not np.allclose(spacings, spacings[0], atol=1e-05, rtol=1e-05)):
        fail('source_geometry_inconsistent', 'DICOM 各帧尺寸、方向或间距不一致')
    steps = np.diff(positions, axis=0)
    step = (positions[-1] - positions[0]) / (len(refs) - 1)
    if np.linalg.norm(step) <= 1e-06 or not np.allclose(steps, step, atol=0.01, rtol=0.01):
        fail('source_geometry_irregular', 'DICOM 层位置重复、不规则或顺序不连续，未重排标注层号')
    expected = positions[0] + np.arange(len(refs))[:, None] * step
    if not np.allclose(positions, expected, atol=0.01, rtol=0):
        fail('source_geometry_irregular', 'DICOM 不能由规则体积表示')
    lps = np.eye(4)
    lps[:3, 0] = orientations[0][:3] * spacings[0][1]
    lps[:3, 1] = orientations[0][3:] * spacings[0][0]
    lps[:3, 2] = step
    lps[:3, 3] = positions[0]
    (affine, spacing) = checked_affine(np.diag([-1.0, -1.0, 1.0, 1.0]) @ lps)
    return ((*shapes[0], len(refs)), affine, spacing)

def frame_transform(ds, frame_index):
    """Select per-frame, shared, then top-level linear transformation.

    No windowing, display inversion, modality guesses or RWVM/LUT evaluation.
    """
    containers = [('top_level', ds)]
    for (name, index) in [('SharedFunctionalGroupsSequence', 0), ('PerFrameFunctionalGroupsSequence', frame_index)]:
        groups = getattr(ds, name, None)
        if groups is not None:
            if index >= len(groups):
                fail('source_frame_metadata_invalid', '多帧强度元数据数量不一致')
            containers.append((name, groups[index]))
    for (_, container) in containers:
        if any((hasattr(container, key) for key in ('ModalityLUTSequence', 'RealWorldValueMappingSequence'))):
            fail('source_intensity_transform_unsupported', '当前读取版本不支持 Modality LUT 或 Real World Value Mapping')
    (selected, origin) = (ds, 'top_level')
    for (name, container) in containers[1:]:
        sequence = getattr(container, 'PixelValueTransformationSequence', None)
        if sequence is not None:
            if len(sequence) != 1:
                fail('source_intensity_transform_invalid', '强度变换必须有一个有效条目')
            (selected, origin) = (sequence[0], name)
    present = [hasattr(selected, field) for field in ('RescaleSlope', 'RescaleIntercept')]
    if not any(present) and origin == 'top_level':
        (slope, intercept, unit) = (1.0, 0.0, None)
        origin = 'stored_values_identity'
    else:
        try:
            (slope, intercept) = (float(selected.RescaleSlope), float(selected.RescaleIntercept))
        except (AttributeError, ValueError, TypeError):
            fail('source_intensity_transform_invalid', '线性强度变换缺少有效 slope/intercept')
        if not np.isfinite([slope, intercept]).all() or slope == 0:
            fail('source_intensity_transform_invalid', '线性强度变换要求有限非零 slope 和有限 intercept')
        unit = str(getattr(selected, 'RescaleType', '')).strip().upper()
        if unit in {'', 'US', 'UNSPECIFIED', 'UNKNOWN'}:
            unit = None
    return {'slope': slope, 'intercept': intercept, 'unit': unit, 'source': origin}

def intensity_evidence(transforms):
    units = {t['unit'] for t in transforms}
    unit = next(iter(units)) if len(units) == 1 else None
    return {'unit': unit, 'evidence': 'dicom_metadata' if unit else 'unknown', 'calibration': 'metadata_declared' if unit else 'unverified', 'description': '各帧实际应用的线性变换声明同一单位；不是独立物理校准证明。' if unit else '实际输入单位缺失或声明不一致；不根据模态推断 HU。'}

def _frame_position_and_normal(ds: Any, frame_index: int) -> tuple[np.ndarray | None, np.ndarray | None]:
    """Read per-frame or top-level IPP/IOP without assuming SliceThickness."""
    position = getattr(ds, 'ImagePositionPatient', None)
    orientation = getattr(ds, 'ImageOrientationPatient', None)
    sequence = getattr(ds, 'PerFrameFunctionalGroupsSequence', None)
    shared_sequence = getattr(ds, 'SharedFunctionalGroupsSequence', None)
    shared_group = None
    if shared_sequence:
        try:
            shared_group = shared_sequence[0]
        except (IndexError, TypeError):
            shared_group = None
    if shared_group is not None:
        shared_orientation = getattr(shared_group, 'PlaneOrientationSequence', None)
        if shared_orientation:
            orientation = getattr(shared_orientation[0], 'ImageOrientationPatient', orientation)
        if position is None:
            shared_position = getattr(shared_group, 'PlanePositionSequence', None)
            if shared_position:
                position = getattr(shared_position[0], 'ImagePositionPatient', position)
    if frame_index > 0 and sequence is None and (int(getattr(ds, 'NumberOfFrames', 1) or 1) > 1):
        position = None
    if sequence is not None and frame_index < len(sequence):
        group = sequence[frame_index]
        plane_position = getattr(group, 'PlanePositionSequence', None)
        if plane_position:
            position = getattr(plane_position[0], 'ImagePositionPatient', position)
        plane_orientation = getattr(group, 'PlaneOrientationSequence', None)
        if plane_orientation:
            orientation = getattr(plane_orientation[0], 'ImageOrientationPatient', orientation)
    try:
        position_array = np.asarray([float(item) for item in position], dtype=np.float64) if position is not None else None
        if position_array is None or position_array.size < 3:
            position_array = None
        else:
            position_array = position_array[:3]
    except (TypeError, ValueError):
        position_array = None
    try:
        orientation_array = np.asarray([float(item) for item in orientation], dtype=np.float64) if orientation is not None else None
        if orientation_array is None or orientation_array.size < 6:
            orientation_array = None
        else:
            orientation_array = orientation_array[:6]
    except (TypeError, ValueError):
        orientation_array = None
    if orientation_array is None:
        return (position_array, None)
    row = orientation_array[:3]
    col = orientation_array[3:6]
    normal = np.cross(row, col)
    norm = float(np.linalg.norm(normal))
    return (position_array, normal / norm if math.isfinite(norm) and norm > 1e-08 else None)
