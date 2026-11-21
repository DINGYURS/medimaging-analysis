"""Formula-preserving bounded-memory spatial feature algorithms."""
from __future__ import annotations
import math
import numpy as np
from scipy.spatial import ConvexHull,QhullError
from scipy.spatial.distance import cdist
SPATIAL_VERSION='spatial-exact-block-fft-v2'

def diameter(vertices,block_size=512):
    points=np.asarray(vertices,dtype=np.float64)
    if len(points)<2:raise ValueError('diameter requires at least two vertices')
    original=len(points);method='all_vertices_blocked'
    try:
        hull=ConvexHull(points);points=points[hull.vertices];method='convex_hull_blocked'
    except QhullError:pass
    maximum=0.0
    for i in range(0,len(points),block_size):
        for j in range(i,len(points),block_size):maximum=max(maximum,float(cdist(points[i:i+block_size],points[j:j+block_size],metric='sqeuclidean').max()))
    return math.sqrt(maximum),{'method':method,'input_vertices':original,'evaluated_vertices':len(points),'block_size':block_size,'sampling':False}

def blocked_autocorrelation(coords,values,block_size=512):
    coords=np.asarray(coords,dtype=np.float64);values=np.asarray(values,dtype=np.float64);n=len(values)
    if n<2:raise ValueError('spatial autocorrelation requires at least two intensity voxels')
    z=values-float(np.mean(values));variance=float(np.sum(z*z))
    if variance<=0:raise ValueError('intensity variance is zero')
    sums_w=[];sums_m=[];sums_g=[]
    for i in range(0,n,block_size):
        a=slice(i,min(i+block_size,n))
        for j in range(i,n,block_size):
            b=slice(j,min(j+block_size,n));dist=cdist(coords[a],coords[b]);weights=np.zeros_like(dist);np.divide(1.,dist,out=weights,where=dist>0)
            factor=1. if i==j else 2.
            sums_w.append(factor*float(np.sum(weights)))
            sums_m.append(factor*float(np.einsum('i,ij,j->',z[a],weights,z[b],optimize=False)))
            differences=z[a,None]-z[None,b]
            sums_g.append(factor*float(np.einsum('ij,ij,ij->',weights,differences,differences,optimize=False)))
    total=math.fsum(sums_w)
    if total<=0:raise ValueError('inverse-distance weight sum is zero')
    moran=n/total*math.fsum(sums_m)/variance;geary=(n-1)/(2*total)*math.fsum(sums_g)/variance
    return moran,geary,{'method':'blocked_all_pairs','voxels':n,'block_size':block_size,'weight_sum':total,'sampling':False,'version':SPATIAL_VERSION}


def fft_autocorrelation(image,mask,spacing,max_fft_cells=8_000_000):
    from scipy.fft import rfftn,irfftn,next_fast_len
    mask=np.asarray(mask,dtype=bool);image=np.asarray(image,dtype=np.float64)
    indexes=np.argwhere(mask)
    if len(indexes)<2:raise ValueError('spatial autocorrelation requires at least two intensity voxels')
    box=tuple(slice(int(indexes[:,a].min()),int(indexes[:,a].max())+1) for a in range(3));m=mask[box].astype(np.float64);values=image[mask];z=values-values.mean();variance=float(np.sum(z*z))
    if variance<=0:raise ValueError('intensity variance is zero')
    shape=tuple(next_fast_len(2*n-1) for n in m.shape)
    if math.prod(shape)>max_fft_cells:raise MemoryError('FFT workspace exceeds bounded grid budget')
    centered=np.zeros(m.shape,dtype=np.float64);centered[mask[box]]=z
    fm=rfftn(m,s=shape,axes=(0,1,2),workers=1);fz=rfftn(centered,s=shape,axes=(0,1,2),workers=1);fq=rfftn(centered*centered,s=shape,axes=(0,1,2),workers=1)
    lags=[];valid=[]
    for size,length,step in zip(m.shape,shape,spacing):
        index=np.arange(length);lag=np.where(index<size,index,index-length);lags.append(lag*float(step));valid.append(np.abs(lag)<size)
    distance=np.sqrt(lags[0][:,None,None]**2+lags[1][None,:,None]**2+lags[2][None,None,:]**2)
    kernel=np.zeros(shape,dtype=np.float64);active=(distance>0)&valid[0][:,None,None]&valid[1][None,:,None]&valid[2][None,None,:];np.divide(1.,distance,out=kernel,where=active);del distance,active
    def weighted_corr(a,b):
        correlation=irfftn(a*np.conj(b),s=shape,axes=(0,1,2),workers=1)
        return float(np.einsum('ijk,ijk->',correlation,kernel,optimize=False))
    total=weighted_corr(fm,fm);cross=weighted_corr(fz,fz);geary_sum=2*(weighted_corr(fq,fm)-cross)
    if total<=0 or geary_sum<0:raise ArithmeticError('FFT reduction is numerically unstable')
    n=len(values);moran=n/total*cross/variance;geary=(n-1)/(2*total)*geary_sum/variance
    return moran,geary,{'method':'fft_full_inverse_distance','voxels':n,'bbox_shape':list(m.shape),'fft_shape':list(shape),'weight_sum':total,'sampling':False,'version':SPATIAL_VERSION}

def autocorrelation(image,mask,spacing,method='auto',direct_threshold=4096,block_size=512):
    n=int(np.count_nonzero(mask));selected='fft' if method=='auto' and n>direct_threshold else 'blocked' if method=='auto' else method
    if selected=='fft':
        try:return fft_autocorrelation(image,mask,spacing)
        except (MemoryError,ArithmeticError):
            coords=(np.argwhere(mask).astype(np.float64)+.5)*spacing
            result=blocked_autocorrelation(coords,np.asarray(image,dtype=np.float64)[mask],block_size);result[2]['fft_fallback']=True;return result
    if selected!='blocked':raise ValueError('unsupported spatial method')
    coords=(np.argwhere(mask).astype(np.float64)+.5)*spacing
    return blocked_autocorrelation(coords,np.asarray(image,dtype=np.float64)[mask],block_size)
