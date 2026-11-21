from __future__ import annotations
import math
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .parameters import NoResegmentation, RangeResegmentation, PlanDiscretisation
from .catalog import get_research_feature_catalog, radiomics_catalog_version
class PlanFeatureSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    catalog_version: str
    keys: list[str] = Field(min_length=1)
    @model_validator(mode='after')
    def supported(self):
        allowed={f['key'] for f in get_research_feature_catalog()}
        if self.catalog_version!=radiomics_catalog_version(): raise ValueError('特征目录版本不匹配，请重新加载目录')
        if len(self.keys)!=len(set(self.keys)) or not set(self.keys)<=allowed: raise ValueError('仅允许本次198项目录中不重复的特征')
        self.keys=sorted(self.keys)
        return self
FeatureClass=Literal['firstorder','shape','glcm','glrlm','glszm','gldm','ngtdm']
Normalization=Literal['none','z-score','auto']
class RadiomicsConfig(BaseModel):
    model_config = ConfigDict(extra='forbid')
    source_version: Literal['legacy', 'physical-v1'] = 'legacy'
    feature_classes: list[FeatureClass] = Field(default_factory=lambda : ['firstorder', 'shape', 'glcm', 'glrlm', 'glszm', 'gldm', 'ngtdm'])
    features: PlanFeatureSelection | None = None
    discretisation: PlanDiscretisation | None = None

    @model_validator(mode='after')
    def require_output(self):
        from app.services.feature_calculation.lung_nodule_custom_features import CUSTOM_FEATURE_KEYS as legacy_custom_keys
        custom = self.features is not None and any((key in legacy_custom_keys for key in self.features.keys))
        if custom and self.normalization == 'z-score':
            raise ValueError('自定义增量特征沿用原 CT 强度公式，请选择不归一化或按模态自适应')
        if custom and self.resegmentation.method == 'range':
            raise ValueError('自定义增量特征沿用原单一 ROI 公式，请关闭重分割')
        if self.discretisation is not None:
            if self.features is None:
                raise ValueError('分组离散化必须同时指定逐项 IBSI 目标清单')
            self.discretisation.validate_features(self.features.keys)
        if self.resegmentation.method == 'range' and self.include_liver_features:
            raise ValueError('启用重分割时请关闭肝脏补充特征；其双掩膜语义尚未适配')
        if self.features is None and (not self.feature_classes):
            raise ValueError('未指定 IBSI 目标时 feature_classes 不能为空')
        return self
    resampling: list[float] | Literal['none'] = Field(default_factory=lambda : [1.0, 1.0, 1.0])
    image_interpolator: Literal['sitkBSpline', 'sitkLinear', 'sitkNearestNeighbor'] = 'sitkBSpline'
    mask_interpolator: Literal['sitkNearestNeighbor', 'sitkLinear'] = 'sitkNearestNeighbor'
    mask_threshold: float = Field(default=0.5, gt=0, le=1, allow_inf_nan=False, strict=True)
    normalization: Normalization = 'auto'
    processing_order: Literal['legacy', 'interpolate_then_normalize'] = 'legacy'
    normalization_scale: float = Field(default=100.0, gt=0, le=10000, allow_inf_nan=False)
    bin_width: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    include_liver_features: bool = False
    resegmentation: Annotated[NoResegmentation | RangeResegmentation, Field(discriminator='method')] = Field(default_factory=lambda : NoResegmentation(method='none'))

    @field_validator('resampling')
    @classmethod
    def validate_spacing(cls, value):
        if value != 'none' and (len(value) != 3 or any((not math.isfinite(v) or v <= 0 for v in value))):
            raise ValueError('resampling 必须为三个有限正数或 none')
        return value
    @model_validator(mode='after')
    def explicit_targets(self):
        if self.features is None: raise ValueError('本次迁移必须提供逐项目标清单，禁止旧格式扩展输出')
        return self
