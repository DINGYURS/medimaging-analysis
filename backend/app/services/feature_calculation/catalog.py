import hashlib, json
from .ibsi_feature_catalog import get_ibsi_feature_catalog
from .lung_nodule_custom_features import CUSTOM_FEATURE_KEYS
LEGACY_CUSTOM_FEATURE_KEYS = CUSTOM_FEATURE_KEYS
CUSTOM_FEATURE_VERSION = 'legacy-lung-custom-v1'
_NAMES = [('CTR 最优阈值', 'ROI Otsu 阈值；无法取得时使用 -150 HU', 'HU'), ('3D CTR', '实性体素数 / 结节体素数 × 100%', '%'), ('实性体积', '实性体素数 × 体素体积', 'mm3'), ('实性体素数', 'ROI 内强度不小于 Otsu 阈值的体素数', 'voxel_count'), ('结节体积', '结节体素数 × 体素体积', 'mm3'), ('结节体素数', '结节 ROI 内体素数', 'voxel_count'), ('最大层面积', '最大面积层 ROI 像素数 × 平面像素面积', 'mm2'), ('最大层周长', '沿用原边界掩膜转变计数周长', 'mm'), ('二维等效直径', 'sqrt(4 × 最大层面积 / π)', 'mm'), ('三维等效直径', '(6 × 结节体积 / π)^(1/3)', 'mm'), ('最大层凸包面积', '最大层 ROI 物理坐标凸包面积', 'mm2'), ('最大层凸包周长', '最大层 ROI 物理坐标凸包周长', 'mm'), ('最大层圆形度', '4π × 最大层面积 / 凸包周长²', 'dimensionless'), ('径向距离标准差', '最大层边界到质心物理距离的标准差', 'mm'), ('边界平均梯度', '各层边界法线方向内外三组采样中心差分的均值', 'HU/mm'), ('边界平均绝对梯度', '边界梯度绝对值的均值', 'HU/mm'), ('边界梯度标准差', '边界梯度的标准差', 'HU/mm'), ('边缘锐度 Acutance', '边界梯度 RMS / 最大绝对梯度', 'dimensionless'), ('HSAG', '边界梯度四分位距 / 极差', 'dimensionless'), ('CDF 斜率均值', '最大层 ROI 128 档直方图概率 / 档宽的均值', 'HU^-1'), ('CDF 斜率标准差', 'CDF 斜率样本的标准差', 'HU^-1'), ('CDF 斜率偏度', 'CDF 斜率样本的三阶标准化矩', 'dimensionless'), ('CDF 斜率峰度', 'CDF 斜率样本的四阶标准化矩，不减 3', 'dimensionless'), ('CDF HU 最小值', '最大层 ROI 强度最小值', 'HU'), ('CDF HU 最大值', '最大层 ROI 强度最大值', 'HU'), ('边界凸性', '各层 ROI 面积 / 凸包面积的均值', 'dimensionless'), ('边界/核心熵比', '二维腐蚀两次划分核心与边界；32 档熵均值之比', 'dimensionless'), ('轮廓凸性', '最大层凸包周长 / ROI 周长', 'dimensionless'), ('轮廓实心度', '最大层 ROI 面积 / 凸包面积', 'dimensionless'), ('轮廓圆形度', '最大层边界到质心像素距离的均值 / 标准差', 'dimensionless'), ('轮廓紧致度', '4π × 最大层面积 / ROI 周长²', 'dimensionless')]

def get_custom_feature_catalog():
    rows = []
    for (key, (name, definition, unit)) in zip(LEGACY_CUSTOM_FEATURE_KEYS, _NAMES, strict=True):
        geometry = key in CUSTOM_FEATURE_KEYS[4:14] or key in ('custom_boundary_convexity', *CUSTOM_FEATURE_KEYS[27:])
        masks = ['morphological'] if geometry else ['morphological', 'intensity']
        rows.append(dict(section='自定义增量', family='custom_lung_nodule', name=name, code=key, key=key, definition=definition, required_mask=' + '.join(masks), required_masks=masks, requires_discretisation=False, requires_image_intensity=not geometry, requires_local_context=key.startswith(('custom_margin_', 'custom_edge_')) or key == 'custom_hsag', supported_dimensions=['3d'], discretisation_group='none', calculation_module='custom', legacy_pyradiomics_class=None, standard_status='custom', status='computed', source_note='沿用原肺结节自定义公式；独立于 IBSI，不声明为 IBSI 标准指标。', reason=None, unit=unit, runtime_constraints=['适用于肺 CT；请选择不归一化或按模态自适应，并关闭重分割。', '无法计算时留空并记录原因；沿用原公式，不使用 IBSI 分箱参数。']))
    return rows
def get_research_feature_catalog():
    return [f for f in get_ibsi_feature_catalog() if f['status']=='computed'] + get_custom_feature_catalog()
def radiomics_catalog_version():
    return 'sha256:'+hashlib.sha256(json.dumps(get_research_feature_catalog(),sort_keys=True,separators=(',',':'),ensure_ascii=True).encode()).hexdigest()
