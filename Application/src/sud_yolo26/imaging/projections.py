"""Shared HU window, image naming and coronal resize geometry."""
import re
import cv2
import numpy as np

from .lps import plane_geometry
from .geometry import scalar


def window_png(hu, valid, low, high):
    if not np.isfinite([low,high]).all() or high<=low:
        raise ValueError('Window bounds must be finite and increasing.')
    image=np.floor(255*np.clip((hu-low)/(high-low),0,1)+.5).astype(np.uint8)
    image[~valid.astype(bool)]=0
    return image


def select_window(header, low=None, high=None, code='WST'):
    if low is not None or high is not None:
        if low is None or high is None or not np.isfinite([low,high]).all() or high<=low:
            raise ValueError('Explicit window requires two finite increasing HU bounds.')
        return dict(window_source='configuration',window_profile=code,window_bounds_hu=[low,high],
                    window_function='linear_clip_bounds_round_half_up')
    missing=[field for field in ('WindowCenter','WindowWidth') if not hasattr(header,field)]
    center=scalar(getattr(header,'WindowCenter',40));width=scalar(getattr(header,'WindowWidth',350))
    function=str(getattr(header,'VOILUTFunction','LINEAR')).strip().upper()
    if not np.isfinite([center,width]).all() or function not in ('LINEAR','LINEAR_EXACT','SIGMOID'):
        raise ValueError('Invalid DICOM window or unsupported VOILUTFunction.')
    if width < (1 if function=='LINEAR' else np.nextafter(0.,1.)):
        raise ValueError('Invalid DICOM WindowWidth.')
    bounds=([center-width/2,center+width/2-1] if function=='LINEAR' else
            [center-width/2,center+width/2] if function=='LINEAR_EXACT' else None)
    return dict(window_source='DICOM' if not missing else 'fallback_missing_dicom_window',
                window_profile='DICOM',window_center=center,window_width=width,
                window_index=0,window_reference_frame='F000000',missing_dicom_window_fields=missing,
                window_bounds_hu=bounds,window_function='DICOM_'+function)


def apply_window(hu, valid, window):
    if window['window_function']=='linear_clip_bounds_round_half_up':
        return window_png(hu,valid,*window['window_bounds_hu'])
    center,width=window['window_center'],window['window_width']
    values=np.asarray(hu,dtype=np.float64)
    function=window['window_function']
    if function=='DICOM_LINEAR':
        mapped=(values>center-.5).astype(float) if width==1 else np.clip((values-(center-.5))/(width-1)+.5,0,1)
    elif function=='DICOM_LINEAR_EXACT':
        mapped=np.clip((values-center)/width+.5,0,1)
    else:
        mapped=1/(1+np.exp(np.clip(-4*(values-center)/width,-700,700)))
    image=np.floor(np.nan_to_num(mapped)*255+.5).astype(np.uint8)
    image[~valid.astype(bool)]=0
    return image


def acquisition_date(header):
    for field in ('AcquisitionDate','FrameAcquisitionDateTime','AcquisitionDateTime','SeriesDate','StudyDate'):
        value = re.sub(r'[^0-9]', '', str(getattr(header,field,'')))
        if len(value)>=8:
            return value[:8]
    return 'UNKNOWN_DATE'


def image_name(patient_id, date, plane, index):
    code={'AX':'axe','COR':'cor'}[plane]
    return f'{patient_id}_{date}_{code}_{index}'


def coronal_indices(shape_zyx, count, step=1):
    count=min(shape_zyx[1],max(1,int(count)))
    start=(shape_zyx[1]-count)//2
    return range(start,start+count,step)


def coronal_projection(volume, canonical, header, index, valid=None, low=None, high=None, window_code='WST', window=None):
    """Shared axial HU window; preserve the former coronal cubic resize and size."""
    section=volume[::-1,index,:]
    validity=valid[::-1,index,:] if valid is not None else np.ones(section.shape,np.uint8)
    window=select_window(header,low,high,window_code) if window is None else window
    mapped=apply_window(section,validity,window)
    sz=canonical['spacing_xyz_mm'][2];sx=canonical['spacing_xyz_mm'][0]
    size=(max(200,section.shape[1]),max(200,int((section.shape[0]-1)*sz/sx)))
    image=np.clip(cv2.resize(mapped.astype(np.int16),size,interpolation=cv2.INTER_CUBIC),0,255).astype(np.uint8)
    sidecar=plane_geometry(canonical,'COR',index)
    old_width,old_height=sidecar['width'],sidecar['height']
    sx,sy=old_width/image.shape[1],old_height/image.shape[0]
    column=np.array(sidecar['column_step_lps_mm'])
    row=np.array(sidecar['row_step_lps_mm'])
    origin=np.array(sidecar['pixel_center_origin_lps_mm'])
    sidecar.update(width=image.shape[1],height=image.shape[0],resize_applied=True,
        source_plane_width=old_width,source_plane_height=old_height,
        pixel_center_origin_lps_mm=(origin+(sx-1)/2*column+(sy-1)/2*row).tolist(),
        column_step_lps_mm=(column*sx).tolist(),row_step_lps_mm=(row*sy).tolist(),
        png_to_plane_pixel_centers=[[sx,0,(sx-1)/2],[0,sy,(sy-1)/2],[0,0,1]],
        resize_interpolation='OpenCV INTER_CUBIC, int16 input after shared uint8 window',
        rendering_profile='shared_window_legacy_coronal_resize',
        invalid_pixel_count=0)
    sidecar.update(window)
    if valid is not None:
        resized=cv2.resize(valid[::-1,index,:],(image.shape[1],image.shape[0]),interpolation=cv2.INTER_NEAREST)
        sidecar['invalid_pixel_count']=int(np.count_nonzero(resized==0))
    return image,sidecar
