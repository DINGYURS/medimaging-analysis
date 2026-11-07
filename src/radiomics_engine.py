"""
Radiomics Feature Extraction & Clinical Quantification Engine.
Compliant with IBSI (Image Biomarker Standardisation Initiative) Chapter 2 & 3.
Wraps PyRadiomics to extract 3D shape, first-order intensity, and texture features,
supporting dynamic configuration, feature family selection, and multi-sheet scientific export.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import SimpleITK as sitk
from radiomics import featureextractor

# Suppress noisy PyRadiomics debug logs
logging.getLogger("radiomics").setLevel(logging.ERROR)

# ==============================================================================
# IBSI Feature Catalog & Family Definitions (107 Standard PyRadiomics Features)
# ==============================================================================
IBSI_FEATURE_CATALOG: Dict[str, Dict[str, Any]] = {
    # 1. 3D Shape & Morphology
    "original_shape_MeshVolume": {"family": "shape", "name": "MeshVolume", "label": "3D网格体积", "code": "RNU0", "unit": "mm³", "definition": "通过Marching Cubes三角网格计算的连续物理体积"},
    "original_shape_VoxelVolume": {"family": "shape", "name": "VoxelVolume", "label": "体素计数体积", "code": "YEKZ", "unit": "mm³", "definition": "ROI体素数量乘以单体素物理体积"},
    "original_shape_SurfaceArea": {"family": "shape", "name": "SurfaceArea", "label": "表面积", "code": "C670", "unit": "mm²", "definition": "3D三维网格表面积"},
    "original_shape_SurfaceVolumeRatio": {"family": "shape", "name": "SurfaceVolumeRatio", "label": "表面积体积比", "code": "T2C4", "unit": "1/mm", "definition": "表面积与体积比值，反映边缘复杂度"},
    "original_shape_Sphericity": {"family": "shape", "name": "Sphericity", "label": "球形度", "code": "QCFX", "unit": "无量纲", "definition": "衡量病灶接近标准球体的程度(0~1)，恶性结节多较低"},
    "original_shape_Maximum3DDiameter": {"family": "shape", "name": "Maximum3DDiameter", "label": "最大三维直径", "code": "L0JK", "unit": "mm", "definition": "ROI表面两点间最大三维欧氏距离"},
    "original_shape_Maximum2DDiameterSlice": {"family": "shape", "name": "Maximum2DDiameterSlice", "label": "轴状面最大径", "code": "ND2D", "unit": "mm", "definition": "轴位断面上两点间最大二维距离"},
    "original_shape_Maximum2DDiameterRow": {"family": "shape", "name": "Maximum2DDiameterRow", "label": "冠状面最大径", "code": "ND2D_R", "unit": "mm", "definition": "冠状位断面上两点间最大二维距离"},
    "original_shape_Maximum2DDiameterColumn": {"family": "shape", "name": "Maximum2DDiameterColumn", "label": "矢状面最大径", "code": "ND2D_C", "unit": "mm", "definition": "矢状位断面上两点间最大二维距离"},
    "original_shape_MajorAxisLength": {"family": "shape", "name": "MajorAxisLength", "label": "主轴长度", "code": "TDIC", "unit": "mm", "definition": "等效椭球体最长主轴长度"},
    "original_shape_MinorAxisLength": {"family": "shape", "name": "MinorAxisLength", "label": "次轴长度", "code": "P9VJ", "unit": "mm", "definition": "等效椭球体第二长轴长度"},
    "original_shape_LeastAxisLength": {"family": "shape", "name": "LeastAxisLength", "label": "短轴长度", "code": "79VA", "unit": "mm", "definition": "等效椭球体最短轴长度"},
    "original_shape_Elongation": {"family": "shape", "name": "Elongation", "label": "伸长度", "code": "Q3CK", "unit": "无量纲", "definition": "两主轴长度比例关系(0~1)，1表示圆形对称"},
    "original_shape_Flatness": {"family": "shape", "name": "Flatness", "label": "扁平度", "code": "N17B", "unit": "无量纲", "definition": "短轴与长轴比例关系(0~1)，值越小病灶越扁平"},

    # 2. First-Order Intensity Statistics
    "original_firstorder_Mean": {"family": "firstorder", "name": "Mean", "label": "平均CT值", "code": "Q4LE", "unit": "HU", "definition": "ROI内所有体素强度的算术平均值"},
    "original_firstorder_Median": {"family": "firstorder", "name": "Median", "label": "中位数CT值", "code": "Y12H", "unit": "HU", "definition": "ROI内强度的中位数，抗极端噪点干扰"},
    "original_firstorder_Variance": {"family": "firstorder", "name": "Variance", "label": "方差", "code": "EF_VAR", "unit": "HU²", "definition": "体素强度分布方差"},
    "original_firstorder_Minimum": {"family": "firstorder", "name": "Minimum", "label": "最低CT值", "code": "1D9F", "unit": "HU", "definition": "ROI内部体素强度的最小值"},
    "original_firstorder_Maximum": {"family": "firstorder", "name": "Maximum", "label": "最高CT值", "code": "84IY", "unit": "HU", "definition": "ROI内部体素强度的最大值"},
    "original_firstorder_10Percentile": {"family": "firstorder", "name": "10Percentile", "label": "第10百分位数", "code": "QG58", "unit": "HU", "definition": "强度第10百分位切点值"},
    "original_firstorder_90Percentile": {"family": "firstorder", "name": "90Percentile", "label": "第90百分位数", "code": "8DWT", "unit": "HU", "definition": "强度第90百分位切点值"},
    "original_firstorder_Skewness": {"family": "firstorder", "name": "Skewness", "label": "偏度", "code": "KE22", "unit": "无量纲", "definition": "强度分布不对称性，正值代表高密度偏斜"},
    "original_firstorder_Kurtosis": {"family": "firstorder", "name": "Kurtosis", "label": "峰度", "code": "IPDV", "unit": "无量纲", "definition": "强度分布陡峭程度与重尾特性"},
    "original_firstorder_Entropy": {"family": "firstorder", "name": "Entropy", "label": "一阶信息熵", "code": "TLU2", "unit": "无量纲", "definition": "ROI灰度强度分布的无序度与复杂性"},
    "original_firstorder_Uniformity": {"family": "firstorder", "name": "Uniformity", "label": "均匀度", "code": "RZJR", "unit": "无量纲", "definition": "体素灰度一致性度量"},
    "original_firstorder_Energy": {"family": "firstorder", "name": "Energy", "label": "能量", "code": "Z8PR", "unit": "HU²", "definition": "体素强度平方和"},
    "original_firstorder_TotalEnergy": {"family": "firstorder", "name": "TotalEnergy", "label": "总能量", "code": "M8NT", "unit": "HU²·mm³", "definition": "能量乘以体素体积"},
    "original_firstorder_InterquartileRange": {"family": "firstorder", "name": "InterquartileRange", "label": "四分位距(IQR)", "code": "SALD", "unit": "HU", "definition": "第75百分位数与第25百分位数之差"},
    "original_firstorder_Range": {"family": "firstorder", "name": "Range", "label": "全距", "code": "20GS", "unit": "HU", "definition": "最大强度与最小强度差值"},
    "original_firstorder_MeanAbsoluteDeviation": {"family": "firstorder", "name": "MeanAbsoluteDeviation", "label": "平均绝对偏差", "code": "4FUA", "unit": "HU", "definition": "体素强度距均值的平均绝对距离"},
    "original_firstorder_RobustMeanAbsoluteDeviation": {"family": "firstorder", "name": "RobustMeanAbsoluteDeviation", "label": "稳健平均绝对偏差", "code": "00KK", "unit": "HU", "definition": "排除极端值后的稳健离散度度量"},
    "original_firstorder_RootMeanSquared": {"family": "firstorder", "name": "RootMeanSquared", "label": "均方根(RMS)", "code": "F5QP", "unit": "HU", "definition": "强度的平方根均值"},

    # 3. GLCM (Grey Level Co-occurrence Matrix)
    "original_glcm_Contrast": {"family": "glcm", "name": "Contrast", "label": "GLCM对比度", "code": "ACUI", "unit": "无量纲", "definition": "相邻体素局部灰度变化剧烈程度"},
    "original_glcm_Correlation": {"family": "glcm", "name": "Correlation", "label": "GLCM相关性", "code": "NB5Q", "unit": "无量纲", "definition": "体素与其邻域在线性空间的相关程度"},
    "original_glcm_JointEntropy": {"family": "glcm", "name": "JointEntropy", "label": "GLCM联合熵", "code": "TF9S", "unit": "无量纲", "definition": "共生矩阵纹理随机性与异质性"},
    "original_glcm_JointEnergy": {"family": "glcm", "name": "JointEnergy", "label": "GLCM联合能量", "code": "J13V", "unit": "无量纲", "definition": "纹理均匀性度量(角二阶矩)"},
    "original_glcm_Idm": {"family": "glcm", "name": "Idm", "label": "反差分矩(同质性)", "code": "IBE6", "unit": "无量纲", "definition": "体素差分反比，表征纹理局部均匀程度"},
    "original_glcm_Id": {"family": "glcm", "name": "Id", "label": "反差分", "code": "ND69", "unit": "无量纲", "definition": "反差分线性衰减"},
    "original_glcm_Idmn": {"family": "glcm", "name": "Idmn", "label": "归一化反差分矩", "code": "EPN2", "unit": "无量纲", "definition": "归一化反差分矩"},
    "original_glcm_Idn": {"family": "glcm", "name": "Idn", "label": "归一化反差分", "code": "EN99", "unit": "无量纲", "definition": "归一化反差分"},
    "original_glcm_InverseVariance": {"family": "glcm", "name": "InverseVariance", "label": "逆方差", "code": "E822", "unit": "无量纲", "definition": "共生矩阵对角线偏离惩罚度量"},
    "original_glcm_MaximumProbability": {"family": "glcm", "name": "MaximumProbability", "label": "最大概率", "code": "GY5W", "unit": "无量纲", "definition": "最高出现频率的共生对比例"},
    "original_glcm_Autocorrelation": {"family": "glcm", "name": "Autocorrelation", "label": "自相关性", "code": "QKNX", "unit": "无量纲", "definition": "灰度微结构粗糙程度"},
    "original_glcm_ClusterProminence": {"family": "glcm", "name": "ClusterProminence", "label": "聚类突出度", "code": "AE86", "unit": "无量纲", "definition": "矩阵不对称程度，反映边缘对比"},
    "original_glcm_ClusterShade": {"family": "glcm", "name": "ClusterShade", "label": "聚类阴影", "code": "7N31", "unit": "无量纲", "definition": "矩阵偏斜度量"},
    "original_glcm_ClusterTendency": {"family": "glcm", "name": "ClusterTendency", "label": "聚类趋势", "code": "DG8W", "unit": "无量纲", "definition": "具有相近灰度水平体素聚集倾向"},
    "original_glcm_DifferenceAverage": {"family": "glcm", "name": "DifferenceAverage", "label": "差分平均值", "code": "TF7R", "unit": "无量纲", "definition": "灰度差分布均值"},
    "original_glcm_DifferenceEntropy": {"family": "glcm", "name": "DifferenceEntropy", "label": "差分熵", "code": "NTRS", "unit": "无量纲", "definition": "灰度差分布随机性"},
    "original_glcm_DifferenceVariance": {"family": "glcm", "name": "DifferenceVariance", "label": "差分方差", "code": "D308", "unit": "无量纲", "definition": "灰度差分布离散度"},
    "original_glcm_JointAverage": {"family": "glcm", "name": "JointAverage", "label": "联合平均值", "code": "60VM", "unit": "无量纲", "definition": "联合概率灰度均值"},
    "original_glcm_SumAverage": {"family": "glcm", "name": "SumAverage", "label": "和平均值", "code": "ZGGE", "unit": "无量纲", "definition": "和分布灰度均值"},
    "original_glcm_SumEntropy": {"family": "glcm", "name": "SumEntropy", "label": "和熵", "code": "P6QZ", "unit": "无量纲", "definition": "和分布随机性"},
    "original_glcm_SumSquares": {"family": "glcm", "name": "SumSquares", "label": "和方差", "code": "OAE2", "unit": "无量纲", "definition": "和分布方差"},
    "original_glcm_Imc1": {"family": "glcm", "name": "Imc1", "label": "信息测度1", "code": "R5DG", "unit": "无量纲", "definition": "相关性信息测度第一项"},
    "original_glcm_Imc2": {"family": "glcm", "name": "Imc2", "label": "信息测度2", "code": "JN9H", "unit": "无量纲", "definition": "相关性信息测度第二项"},
    "original_glcm_MCC": {"family": "glcm", "name": "MCC", "label": "??????", "code": "ND89", "unit": "???", "definition": "????Q???????????????????????????"},

    # 4. GLRLM (Grey Level Run Length Matrix)
    "original_glrlm_ShortRunEmphasis": {"family": "glrlm", "name": "ShortRunEmphasis", "label": "短游程强调", "code": "22OV", "unit": "无量纲", "definition": "细微精细纹理占比"},
    "original_glrlm_LongRunEmphasis": {"family": "glrlm", "name": "LongRunEmphasis", "label": "长游程强调", "code": "W4KF", "unit": "无量纲", "definition": "粗大连续均一纹理占比"},
    "original_glrlm_GrayLevelNonUniformity": {"family": "glrlm", "name": "GrayLevelNonUniformity", "label": "灰度不均匀性", "code": "R5YN", "unit": "无量纲", "definition": "游程灰度差异性"},
    "original_glrlm_GrayLevelNonUniformityNormalized": {"family": "glrlm", "name": "GrayLevelNonUniformityNormalized", "label": "归一化灰度不均匀性", "code": "OVBL", "unit": "无量纲", "definition": "归一化游程灰度差异"},
    "original_glrlm_RunLengthNonUniformity": {"family": "glrlm", "name": "RunLengthNonUniformity", "label": "游程长度不均匀性", "code": "W92Y", "unit": "无量纲", "definition": "游程跨度异质性"},
    "original_glrlm_RunLengthNonUniformityNormalized": {"family": "glrlm", "name": "RunLengthNonUniformityNormalized", "label": "归一化游程长不均匀性", "code": "IC23", "unit": "无量纲", "definition": "归一化游程长度差异"},
    "original_glrlm_RunPercentage": {"family": "glrlm", "name": "RunPercentage", "label": "游程百分比", "code": "9QKx", "unit": "无量纲", "definition": "可能游程与实际体素比值"},
    "original_glrlm_GrayLevelVariance": {"family": "glrlm", "name": "GrayLevelVariance", "label": "灰度方差", "code": "8CE5", "unit": "无量纲", "definition": "游程中灰度方差"},
    "original_glrlm_RunVariance": {"family": "glrlm", "name": "RunVariance", "label": "游程方差", "code": "SXLW", "unit": "无量纲", "definition": "游程长度方差"},
    "original_glrlm_RunEntropy": {"family": "glrlm", "name": "RunEntropy", "label": "游程熵", "code": "HJ92", "unit": "无量纲", "definition": "游程分布信息熵"},
    "original_glrlm_LowGrayLevelRunEmphasis": {"family": "glrlm", "name": "LowGrayLevelRunEmphasis", "label": "低灰度游程强调", "code": "V3SW", "unit": "无量纲", "definition": "低密度(如含气磨玻璃)连续游程权重"},
    "original_glrlm_HighGrayLevelRunEmphasis": {"family": "glrlm", "name": "HighGrayLevelRunEmphasis", "label": "高灰度游程强调", "code": "G3QZ", "unit": "无量纲", "definition": "高密度(如实性/钙化)连续游程权重"},
    "original_glrlm_ShortRunLowGrayLevelEmphasis": {"family": "glrlm", "name": "ShortRunLowGrayLevelEmphasis", "label": "短游程低灰度强调", "code": "HTZT", "unit": "无量纲", "definition": "低灰度短片段分布"},
    "original_glrlm_ShortRunHighGrayLevelEmphasis": {"family": "glrlm", "name": "ShortRunHighGrayLevelEmphasis", "label": "短游程高灰度强调", "code": "GD3P", "unit": "无量纲", "definition": "高灰度短片段分布"},
    "original_glrlm_LongRunLowGrayLevelEmphasis": {"family": "glrlm", "name": "LongRunLowGrayLevelEmphasis", "label": "长游程低灰度强调", "code": "IVMD", "unit": "无量纲", "definition": "大片低密度均匀区"},
    "original_glrlm_LongRunHighGrayLevelEmphasis": {"family": "glrlm", "name": "LongRunHighGrayLevelEmphasis", "label": "长游程高灰度强调", "code": "3KZW", "unit": "无量纲", "definition": "大片高密度均匀区"},

    # 5. GLSZM (Grey Level Size Zone Matrix)
    "original_glszm_SmallAreaEmphasis": {"family": "glszm", "name": "SmallAreaEmphasis", "label": "小区域强调", "code": "5Z3W", "unit": "无量纲", "definition": "微小连通斑块权重"},
    "original_glszm_LargeAreaEmphasis": {"family": "glszm", "name": "LargeAreaEmphasis", "label": "大区域强调", "code": "48P8", "unit": "无量纲", "definition": "巨大均匀连通斑块权重"},
    "original_glszm_GrayLevelNonUniformity": {"family": "glszm", "name": "GrayLevelNonUniformity", "label": "区域灰度不均匀性", "code": "JNSA", "unit": "无量纲", "definition": "连通区域间灰度差异"},
    "original_glszm_GrayLevelNonUniformityNormalized": {"family": "glszm", "name": "GrayLevelNonUniformityNormalized", "label": "归一化区域灰度不均", "code": "YHOT", "unit": "无量纲", "definition": "归一化区域灰度差异"},
    "original_glszm_SizeZoneNonUniformity": {"family": "glszm", "name": "SizeZoneNonUniformity", "label": "区域尺寸不均匀性", "code": "4MA3", "unit": "无量纲", "definition": "斑块体积多分散性"},
    "original_glszm_SizeZoneNonUniformityNormalized": {"family": "glszm", "name": "SizeZoneNonUniformityNormalized", "label": "归一化尺寸不均匀性", "code": "NPUI", "unit": "无量纲", "definition": "归一化尺寸差异"},
    "original_glszm_ZonePercentage": {"family": "glszm", "name": "ZonePercentage", "label": "区域百分比", "code": "VIWW", "unit": "无量纲", "definition": "斑块数量与体素比值"},
    "original_glszm_GrayLevelVariance": {"family": "glszm", "name": "GrayLevelVariance", "label": "区域灰度方差", "code": "BYLV", "unit": "无量纲", "definition": "斑块灰度方差"},
    "original_glszm_ZoneVariance": {"family": "glszm", "name": "ZoneVariance", "label": "尺寸方差", "code": "3NSA", "unit": "无量纲", "definition": "斑块体积方差"},
    "original_glszm_ZoneEntropy": {"family": "glszm", "name": "ZoneEntropy", "label": "区域大小熵", "code": "GU8N", "unit": "无量纲", "definition": "区域异质性熵值"},
    "original_glszm_LowGrayLevelZoneEmphasis": {"family": "glszm", "name": "LowGrayLevelZoneEmphasis", "label": "低灰度区域强调", "code": "XMSY", "unit": "无量纲", "definition": "低密度连通区权重"},
    "original_glszm_HighGrayLevelZoneEmphasis": {"family": "glszm", "name": "HighGrayLevelZoneEmphasis", "label": "高灰度区域强调", "code": "5GN9", "unit": "无量纲", "definition": "高密度连通区权重"},
    "original_glszm_SmallAreaLowGrayLevelEmphasis": {"family": "glszm", "name": "SmallAreaLowGrayLevelEmphasis", "label": "微小低灰度区强调", "code": "5RAI", "unit": "无量纲", "definition": "微细低密度小结节"},
    "original_glszm_SmallAreaHighGrayLevelEmphasis": {"family": "glszm", "name": "SmallAreaHighGrayLevelEmphasis", "label": "微小高灰度区强调", "code": "HW1V", "unit": "无量纲", "definition": "微细实性钙化灶"},
    "original_glszm_LargeAreaLowGrayLevelEmphasis": {"family": "glszm", "name": "LargeAreaLowGrayLevelEmphasis", "label": "大片低灰度区强调", "code": "YH51", "unit": "无量纲", "definition": "成片均质磨玻璃"},
    "original_glszm_LargeAreaHighGrayLevelEmphasis": {"family": "glszm", "name": "LargeAreaHighGrayLevelEmphasis", "label": "大片高灰度区强调", "code": "J2HR", "unit": "无量纲", "definition": "成片均质实性浸润"},

    # 6. GLDM (Grey Level Dependence Matrix)
    "original_gldm_SmallDependenceEmphasis": {"family": "gldm", "name": "SmallDependenceEmphasis", "label": "小相关度强调", "code": "UBMD", "unit": "无量纲", "definition": "独立无依赖孤集体素"},
    "original_gldm_LargeDependenceEmphasis": {"family": "gldm", "name": "LargeDependenceEmphasis", "label": "大相关度强调", "code": "OAE7", "unit": "无量纲", "definition": "强相依密集纹理体素"},
    "original_gldm_GrayLevelNonUniformity": {"family": "gldm", "name": "GrayLevelNonUniformity", "label": "依赖灰度不均匀性", "code": "1X9X", "unit": "无量纲", "definition": "相关体素灰度不均"},
    "original_gldm_DependenceNonUniformity": {"family": "gldm", "name": "DependenceNonUniformity", "label": "依赖性不均匀度", "code": "WR0O", "unit": "无量纲", "definition": "相依度分布不均"},
    "original_gldm_DependenceNonUniformityNormalized": {"family": "gldm", "name": "DependenceNonUniformityNormalized", "label": "归一化依赖不均", "code": "9PBW", "unit": "无量纲", "definition": "归一化相依度差异"},
    "original_gldm_GrayLevelVariance": {"family": "gldm", "name": "GrayLevelVariance", "label": "依赖灰度方差", "code": "NKQ2", "unit": "无量纲", "definition": "相依灰度方差"},
    "original_gldm_DependenceVariance": {"family": "gldm", "name": "DependenceVariance", "label": "相依度方差", "code": "SZOB", "unit": "无量纲", "definition": "相依度分散性"},
    "original_gldm_DependenceEntropy": {"family": "gldm", "name": "DependenceEntropy", "label": "相依熵", "code": "P67Y", "unit": "无量纲", "definition": "相依分布混乱度"},
    "original_gldm_LowGrayLevelEmphasis": {"family": "gldm", "name": "LowGrayLevelEmphasis", "label": "低灰度相关强调", "code": "TL9H", "unit": "无量纲", "definition": "低密度相关性"},
    "original_gldm_HighGrayLevelEmphasis": {"family": "gldm", "name": "HighGrayLevelEmphasis", "label": "高灰度相关强调", "code": "K26C", "unit": "无量纲", "definition": "高密度相关性"},
    "original_gldm_SmallDependenceLowGrayLevelEmphasis": {"family": "gldm", "name": "SmallDependenceLowGrayLevelEmphasis", "label": "小依赖低灰度强调", "code": "ND10", "unit": "无量纲", "definition": "微弱低密度相关"},
    "original_gldm_SmallDependenceHighGrayLevelEmphasis": {"family": "gldm", "name": "SmallDependenceHighGrayLevelEmphasis", "label": "小依赖高灰度强调", "code": "ND11", "unit": "无量纲", "definition": "微弱高密度相关"},
    "original_gldm_LargeDependenceLowGrayLevelEmphasis": {"family": "gldm", "name": "LargeDependenceLowGrayLevelEmphasis", "label": "大依赖低灰度强调", "code": "ND12", "unit": "无量纲", "definition": "致密低密度相关"},
    "original_gldm_LargeDependenceHighGrayLevelEmphasis": {"family": "gldm", "name": "LargeDependenceHighGrayLevelEmphasis", "label": "大依赖高灰度强调", "code": "ND13", "unit": "无量纲", "definition": "致密高密度相关"},

    # 7. NGTDM (Neighbourhood Grey Tone Difference Matrix)
    "original_ngtdm_Coarseness": {"family": "ngtdm", "name": "Coarseness", "label": "粗糙度", "code": "QCDE", "unit": "无量纲", "definition": "纹理颗粒粗大或细腻程度，数值大代表粗糙均一"},
    "original_ngtdm_Contrast": {"family": "ngtdm", "name": "Contrast", "label": "邻域对比度", "code": "65HE", "unit": "无量纲", "definition": "相邻体素强度剧变程度，恶性病灶多较高"},
    "original_ngtdm_Busyness": {"family": "ngtdm", "name": "Busyness", "label": "繁忙度", "code": "NQ30", "unit": "无量纲", "definition": "局部空间变化高频交替程度"},
    "original_ngtdm_Complexity": {"family": "ngtdm", "name": "Complexity", "label": "复杂度", "code": "HDEZ", "unit": "无量纲", "definition": "信息复杂多变性，混合磨玻璃恶变结节显著升高"},
    "original_ngtdm_Strength": {"family": "ngtdm", "name": "Strength", "label": "强度/显著性", "code": "1X9V", "unit": "无量纲", "definition": "局部特征斑块对周围背景的显著突出程度"}
}

# ==============================================================================
# Feature Families Grouping Definition
# ==============================================================================
FEATURE_FAMILIES = [
    {
        "id": "shape",
        "name": "三维形态与几何学",
        "short_name": "Morphology",
        "description": "病灶体积、表面积、球形度、长短径及紧凑度，不依赖灰度离散化。",
        "pyradiomics_class": "shape"
    },
    {
        "id": "firstorder",
        "name": "一阶灰度统计学",
        "short_name": "Intensity Stats",
        "description": "平均CT值、中位数、偏度、峰度、百分位数及均匀度，反映病灶密度基底。",
        "pyradiomics_class": "firstorder"
    },
    {
        "id": "glcm",
        "name": "灰度共生矩阵 (GLCM)",
        "short_name": "GLCM",
        "description": "反映体素对在空间距离和方向上的联合灰度分布，表征局部反差与均一性。",
        "pyradiomics_class": "glcm"
    },
    {
        "id": "glrlm",
        "name": "灰度游程长矩阵 (GLRLM)",
        "short_name": "GLRLM",
        "description": "反映相同灰度连续延伸的长度与频次，度量条索、纹理方向性与均一性。",
        "pyradiomics_class": "glrlm"
    },
    {
        "id": "glszm",
        "name": "灰度区域大小矩阵 (GLSZM)",
        "short_name": "GLSZM",
        "description": "度量相同灰度连通微小区域与大片区域的聚集分布，衡量异质性斑块尺寸。",
        "pyradiomics_class": "glszm"
    },
    {
        "id": "gldm",
        "name": "灰度依赖矩阵 (GLDM)",
        "short_name": "GLDM",
        "description": "度量中心体素与其周围邻域相近灰度的依赖强度，量化微观致密度。",
        "pyradiomics_class": "gldm"
    },
    {
        "id": "ngtdm",
        "name": "邻域灰度差矩阵 (NGTDM)",
        "short_name": "NGTDM",
        "description": "度量体素与其相邻邻域平均灰度的绝对差异，评估粗糙度、繁忙度与复杂度。",
        "pyradiomics_class": "ngtdm"
    }
]

# ==============================================================================
# Clinical Presets Definition
# ==============================================================================
RADIOMICS_PRESETS = [
    {
        "id": "clinical_default",
        "name": "临床常规诊断推荐 (42维核心指标)",
        "description": "以 42 项核心解剖形态与灰度纹理为主，采用 CT 标准 25 HU 分箱与 1.0mm 各向同性重采样，计算极快，直观辅助结节良恶性评估。",
        "config": {
            "resampling_mode": "isotropic_1mm",
            "custom_spacing": [1.0, 1.0, 1.0],
            "bin_width": 25.0,
            "interpolator": "sitkBSpline",
            "resegmentation_enabled": False,
            "selected_classes": ["shape", "firstorder", "glcm", "glrlm", "glszm", "ngtdm"]
        }
    },
    {
        "id": "ibsi_full",
        "name": "IBSI 国际标准全量组学 (107维全家族)",
        "description": "完整提取 7 大特征家族共 107 维标准影像组学指标，支持导出结构化科研宽表，满足机器学习建模与发表高水平科研论文需求。",
        "config": {
            "resampling_mode": "isotropic_1mm",
            "custom_spacing": [1.0, 1.0, 1.0],
            "bin_width": 25.0,
            "interpolator": "sitkBSpline",
            "resegmentation_enabled": False,
            "selected_classes": ["shape", "firstorder", "glcm", "glrlm", "glszm", "gldm", "ngtdm"]
        }
    },
    {
        "id": "morphology_only",
        "name": "纯几何与三维形态学 (14维指标)",
        "description": "仅量化病灶的三维物理体积、表面积、长短径及球形度，保留原生网格，无需灰度离散化与重采样，毫秒级快速完成形态量化。",
        "config": {
            "resampling_mode": "native",
            "custom_spacing": [1.0, 1.0, 1.0],
            "bin_width": 25.0,
            "interpolator": "sitkBSpline",
            "resegmentation_enabled": False,
            "selected_classes": ["shape"]
        }
    }
]


def get_radiomics_catalog_metadata() -> Dict[str, Any]:
    """返回用于前端展示的特征目录与预设元数据字典."""
    families_out = []
    for fam in FEATURE_FAMILIES:
        fam_id = fam["id"]
        feats = [
            {
                "key": k,
                "name": v["name"],
                "label": v["label"],
                "ibsi_code": v["code"],
                "unit": v["unit"],
                "definition": v["definition"]
            }
            for k, v in IBSI_FEATURE_CATALOG.items()
            if v["family"] == fam_id
        ]
        families_out.append({
            "id": fam_id,
            "name": fam["name"],
            "short_name": fam["short_name"],
            "description": fam["description"],
            "count": len(feats),
            "features": feats
        })

    return {
        "presets": RADIOMICS_PRESETS,
        "families": families_out,
        "total_features_count": len(IBSI_FEATURE_CATALOG)
    }


def get_configurable_radiomics_extractor(
    bin_width: float = 25.0,
    resampled_spacing: Optional[Tuple[float, float, float]] = (1.0, 1.0, 1.0),
    interpolator: str = "sitkBSpline",
    feature_classes: Optional[List[str]] = None,
    resegment_range: Optional[Tuple[float, float]] = None
) -> featureextractor.RadiomicsFeatureExtractor:
    """
    根据 IBSI 第二章及医生选择动态构建 PyRadiomics 提取器.
    
    参数说明:
        - bin_width: 灰度离散化分箱宽度 FBS (HU)
        - resampled_spacing: 空间重采样体素间距，若为 None 则保留原生网格
        - interpolator: 图像插值算法 ('sitkBSpline', 'sitkLinear', 'sitkNearestNeighbor')
        - feature_classes: 启用的特征类列表，默认启用全部 7 类
        - resegment_range: (min_hu, max_hu) 重分割区间，用于实性成分筛选等
    """
    settings: Dict[str, Any] = {
        "binWidth": float(bin_width),
        "correctMask": True,
        "label": 1,
    }

    if resampled_spacing is not None:
        settings["resampledPixelSpacing"] = [float(x) for x in resampled_spacing]
        settings["interpolator"] = interpolator
        settings["padDistance"] = 5

    if resegment_range is not None:
        settings["resegmentRange"] = [float(resegment_range[0]), float(resegment_range[1])]

    enabled_classes = feature_classes if feature_classes else [
        "shape", "firstorder", "glcm", "glrlm", "glszm", "gldm", "ngtdm"
    ]

    feature_class_dict = {cls_name: None for cls_name in enabled_classes}

    params: Dict[str, Any] = {
        "setting": settings,
        "imageType": {
            "Original": {}
        },
        "featureClass": feature_class_dict
    }

    return featureextractor.RadiomicsFeatureExtractor(params)


def get_default_radiomics_extractor(
    bin_width: float = 25.0,
    resampled_spacing: Optional[Tuple[float, float, float]] = (1.0, 1.0, 1.0)
) -> featureextractor.RadiomicsFeatureExtractor:
    """保持向后兼容的标准提取器构造函数."""
    return get_configurable_radiomics_extractor(
        bin_width=bin_width,
        resampled_spacing=resampled_spacing
    )


def extract_lesion_radiomics(
    image: sitk.Image,
    mask: sitk.Image,
    lesion_id: int = 1,
    extractor: Optional[featureextractor.RadiomicsFeatureExtractor] = None,
    config_summary: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    从 3D 图像与病灶 Mask 中提取符合 IBSI 标准的影像组学定量指标.
    返回按特征家族分类整理的字典、临床摘要、全量原始特征以及 Provenance 计算上下文.
    """
    if extractor is None:
        extractor = get_default_radiomics_extractor()

    # PyRadiomics calculation
    raw_result = extractor.execute(image, mask)

    # Filter out diagnostics metadata to keep only feature values
    features: Dict[str, float] = {}
    for k, v in raw_result.items():
        if k.startswith("original_"):
            try:
                features[k] = float(np.asarray(v).item())
            except Exception:
                pass

    # Group into feature families
    features_by_class: Dict[str, Dict[str, float]] = {
        "shape": {},
        "firstorder": {},
        "glcm": {},
        "glrlm": {},
        "glszm": {},
        "gldm": {},
        "ngtdm": {}
    }

    for feat_key, feat_val in features.items():
        meta = IBSI_FEATURE_CATALOG.get(feat_key)
        if meta:
            fam = meta["family"]
            if fam in features_by_class:
                features_by_class[fam][feat_key] = feat_val
        else:
            # Fallback parsing by prefix
            for fam in features_by_class.keys():
                if f"original_{fam}_" in feat_key:
                    features_by_class[fam][feat_key] = feat_val
                    break

    # Also merge all texture families into a top-level texture dictionary
    features_by_class["texture"] = {
        **features_by_class["glcm"],
        **features_by_class["glrlm"],
        **features_by_class["glszm"],
        **features_by_class["gldm"],
        **features_by_class["ngtdm"],
    }

    # Extract clinical subsets
    morphology = {
        "volume_mm3": round(features.get("original_shape_MeshVolume", features.get("original_shape_VoxelVolume", 0.0)), 2),
        "voxel_volume_mm3": round(features.get("original_shape_VoxelVolume", 0.0), 2),
        "surface_area_mm2": round(features.get("original_shape_SurfaceArea", 0.0), 2),
        "surface_to_volume_ratio": round(features.get("original_shape_SurfaceVolumeRatio", 0.0), 4),
        "sphericity": round(features.get("original_shape_Sphericity", 0.0), 4),
        "compactness_1": round(features.get("original_shape_Compactness1", 0.0), 4),
        "compactness_2": round(features.get("original_shape_Compactness2", 0.0), 4),
        "max_3d_diameter_mm": round(features.get("original_shape_Maximum3DDiameter", 0.0), 2),
        "major_axis_length_mm": round(features.get("original_shape_MajorAxisLength", 0.0), 2),
        "minor_axis_length_mm": round(features.get("original_shape_MinorAxisLength", 0.0), 2),
        "least_axis_length_mm": round(features.get("original_shape_LeastAxisLength", 0.0), 2),
        "elongation": round(features.get("original_shape_Elongation", 0.0), 4),
        "flatness": round(features.get("original_shape_Flatness", 0.0), 4),
    }

    intensity = {
        "mean_hu": round(features.get("original_firstorder_Mean", 0.0), 1),
        "median_hu": round(features.get("original_firstorder_Median", 0.0), 1),
        "std_hu": round(features.get("original_firstorder_StandardDeviation", np.sqrt(features.get("original_firstorder_Variance", 0.0))), 1),
        "min_hu": round(features.get("original_firstorder_Minimum", 0.0), 1),
        "max_hu": round(features.get("original_firstorder_Maximum", 0.0), 1),
        "percentile_10_hu": round(features.get("original_firstorder_10Percentile", 0.0), 1),
        "percentile_90_hu": round(features.get("original_firstorder_90Percentile", 0.0), 1),
        "skewness": round(features.get("original_firstorder_Skewness", 0.0), 4),
        "kurtosis": round(features.get("original_firstorder_Kurtosis", 0.0), 4),
        "entropy": round(features.get("original_firstorder_Entropy", 0.0), 4),
        "uniformity": round(features.get("original_firstorder_Uniformity", 0.0), 4),
    }

    texture = {
        "glcm_contrast": round(features.get("original_glcm_Contrast", 0.0), 4),
        "glcm_correlation": round(features.get("original_glcm_Correlation", 0.0), 4),
        "glcm_joint_entropy": round(features.get("original_glcm_JointEntropy", 0.0), 4),
        "glcm_homogeneity": round(features.get("original_glcm_Idm", 0.0), 4),
        "glrlm_gray_level_non_uniformity": round(features.get("original_glrlm_GrayLevelNonUniformity", 0.0), 2),
        "glszm_zone_entropy": round(features.get("original_glszm_ZoneEntropy", 0.0), 4),
        "ngtdm_coarseness": round(features.get("original_ngtdm_Coarseness", 0.0), 6),
        "ngtdm_busyness": round(features.get("original_ngtdm_Busyness", 0.0), 4),
        "ngtdm_complexity": round(features.get("original_ngtdm_Complexity", 0.0), 2),
    }

    # Clinical Interpretation Logic
    mean_hu = intensity["mean_hu"]
    if mean_hu < -350:
        attenuation_type = "纯磨玻璃结节 (pGGO / Non-solid)"
    elif mean_hu <= -100:
        attenuation_type = "部分实性/混合磨玻璃结节 (Part-Solid / Subsolid)"
    elif mean_hu <= 150:
        attenuation_type = "实性结节 (Solid nodule)"
    else:
        attenuation_type = "高密度/钙化结节 (Calcified / Hyperdense)"

    sphericity = morphology["sphericity"]
    if sphericity >= 0.80:
        margin_shape_eval = "形态较规则，类圆形，边缘较光整"
    elif sphericity >= 0.65:
        margin_shape_eval = "形态轻度欠规则，可见分叶或轻度切迹"
    else:
        margin_shape_eval = "形态不规则，球形度偏低，需关注毛刺征或棘状突起"

    clinical_summary = {
        "lesion_id": lesion_id,
        "max_diameter_mm": morphology["max_3d_diameter_mm"],
        "major_axis_mm": morphology["major_axis_length_mm"],
        "volume_mm3": morphology["volume_mm3"],
        "mean_attenuation_hu": mean_hu,
        "attenuation_category": attenuation_type,
        "sphericity": sphericity,
        "margin_morphology_description": margin_shape_eval,
        "internal_heterogeneity_entropy": intensity["entropy"],
    }

    provenance = {
        "ibsi_standard": "IBSI Chapter 2 (Image Processing) & Chapter 3 (Feature Calculation)",
        "engine": "PyRadiomics 3.0.1",
        "extracted_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
        **(config_summary or {})
    }

    return {
        "lesion_id": lesion_id,
        "clinical_summary": clinical_summary,
        "morphology": morphology,
        "intensity": intensity,
        "texture": texture,
        "features_by_class": features_by_class,
        "total_extracted_features_count": len(features),
        "all_raw_features": features,
        "provenance": provenance
    }


def export_radiomics_to_dataframe(
    radiomics_results: List[Dict[str, Any]],
    selected_keys: Optional[List[str]] = None
) -> pd.DataFrame:
    """生成 Excel 与 CSV 共用的特征数据矩阵。"""
    records = []
    for item in radiomics_results:
        row: Dict[str, Any] = {
            "病灶序号 (Lesion ID)": item.get("lesion_id", 1),
            "病灶分类 (Density Type)": item.get("clinical_summary", {}).get("attenuation_category", "")
        }
        all_raw = item.get("all_raw_features", {})
        target_keys = selected_keys if selected_keys else list(all_raw.keys())
        for key in target_keys:
            if key in all_raw:
                meta = IBSI_FEATURE_CATALOG.get(key, {})
                label = meta.get("label", key)
                code = f" [{meta.get('code')}]" if meta.get("code") else ""
                unit = f" ({meta.get('unit')})" if meta.get("unit") and meta.get("unit") != "无量纲" else ""
                row[f"{label}{code}{unit}"] = all_raw[key]
        records.append(row)
    return pd.DataFrame(records)


def export_radiomics_to_excel(
    radiomics_results: List[Dict[str, Any]],
    config_summary: Optional[Dict[str, Any]] = None,
    selected_keys: Optional[List[str]] = None
) -> bytes:
    """导出仅包含特征数据矩阵的 Excel 文件。"""
    output = io.BytesIO()
    matrix = export_radiomics_to_dataframe(radiomics_results, selected_keys=selected_keys)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        matrix.to_excel(writer, sheet_name="特征数据矩阵", index=False)
    return output.getvalue()


def export_radiomics_to_csv(
    radiomics_results: List[Dict[str, Any]],
    selected_keys: Optional[List[str]] = None
) -> str:
    """导出为标准 CSV 字符串."""
    df = export_radiomics_to_dataframe(radiomics_results, selected_keys=selected_keys)
    return df.to_csv(index=False, encoding="utf-8-sig")
