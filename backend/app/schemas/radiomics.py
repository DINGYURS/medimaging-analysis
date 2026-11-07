from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

class RadiomicsPresetItem(BaseModel):
    id: str
    name: str
    description: str
    config: Dict[str, Any]

class RadiomicsFeatureItem(BaseModel):
    key: str
    name: str
    label: str
    ibsi_code: Optional[str] = None
    unit: Optional[str] = None
    definition: Optional[str] = None

class RadiomicsFamilyItem(BaseModel):
    id: str
    name: str
    short_name: str
    description: str
    count: int
    features: List[RadiomicsFeatureItem]

class RadiomicsCatalogResponse(BaseModel):
    presets: List[RadiomicsPresetItem]
    families: List[RadiomicsFamilyItem]
    total_features_count: int

class RadiomicsCalculationConfig(BaseModel):
    preset_id: Optional[str] = None
    bin_width: float = Field(default=25.0, description="固定分箱宽度 FBS (HU)")
    resampling_mode: str = Field(default="isotropic_1mm", description="native | isotropic_1mm | custom")
    custom_spacing: Optional[List[float]] = Field(default=[1.0, 1.0, 1.0], description="[X, Y, Z] 间距 mm")
    interpolator: str = Field(default="sitkBSpline", description="sitkBSpline | sitkLinear | sitkNearestNeighbor")
    resegmentation_enabled: bool = Field(default=False, description="是否启用强度范围重分割")
    resegment_min_hu: Optional[float] = Field(default=-100.0, description="重分割最低强度 (HU)")
    resegment_max_hu: Optional[float] = Field(default=150.0, description="重分割最高强度 (HU)")
    selected_classes: Optional[List[str]] = Field(default=None, description="启用的特征类列表")
    selected_keys: Optional[List[str]] = Field(default=None, description="具体特征指标 key 列表 (可选细化)")

class RadiomicsExportRequest(RadiomicsCalculationConfig):
    format: str = Field(default="excel", description="excel | csv")