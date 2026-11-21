from __future__ import annotations
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator
from .catalog import CUSTOM_FEATURE_KEYS
from .ibsi_feature_catalog import get_ibsi_feature_spec
class PlanModel(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False, str_strip_whitespace=True)

class NoResegmentation(PlanModel):
    method: Literal['none']

class IntensityBasis(PlanModel):
    kind: Literal['calibrated', 'arbitrary', 'unknown']
    unit: str = Field(min_length=1)
    source: str | None = None

    @model_serializer(mode='wrap')
    def serialize_compatible_basis(self, handler):
        value = handler(self)
        if self.source is None:
            value.pop('source', None)
        return value

class RangeResegmentation(PlanModel):
    method: Literal['range']
    lower: float
    upper: float
    intensity_basis: IntensityBasis
    ibsi_deviation_acknowledged: bool = False

    @model_validator(mode='after')
    def valid_range_and_declaration(self):
        if self.lower >= self.upper:
            raise ValueError('重分割下限必须小于上限；保留范围包含上下边界')
        return self

class NoDiscretisation(PlanModel):
    method: Literal['none']

class FixedBinSize(PlanModel):
    method: Literal['fbs']
    bin_width: float = Field(gt=0)
    bin_origin: float | None = None

class FixedBinNumber(PlanModel):
    method: Literal['fbn']
    bin_count: int = Field(gt=0)
DiscretisationRule = Annotated[NoDiscretisation | FixedBinSize | FixedBinNumber, Field(discriminator='method')]

class AutoIVH(PlanModel):
    method: Literal['auto']

class PlanDiscretisation(PlanModel):
    intensity_histogram: DiscretisationRule
    texture: DiscretisationRule
    intensity_volume_histogram: Annotated[AutoIVH | FixedBinSize, Field(discriminator='method')]

    def validate_features(self, keys: list[str]) -> None:
        families = {get_ibsi_feature_spec(key).family for key in keys if key not in CUSTOM_FEATURE_KEYS}
        if 'intensity_histogram' in families and self.intensity_histogram.method == 'none':
            raise ValueError('IH 目标特征需要离散化配置')
        if families.intersection({'glcm', 'glrlm', 'glszm', 'gldzm', 'ngtdm', 'ngldm'}) and self.texture.method == 'none':
            raise ValueError('纹理目标特征需要离散化配置')
