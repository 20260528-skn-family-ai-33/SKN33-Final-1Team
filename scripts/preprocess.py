"""Preserve a poster, generate comparison images, run EasyOCR on the selected backend."""
import argparse
import hashlib
import json
import os
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageOps

from backend.app.ocr_runtime import recognize_image
from scripts.paths import DEFAULT_ARTIFACTS, PROJECT_ROOT, output_directory, source_reference, artifact_path


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_point(x, y, width, height, orientation):
    return {1: (x,y), 2:(width-x,y), 3:(width-x,height-y), 4:(x,height-y),
            5:(y,x), 6:(y,height-x), 7:(width-y,height-x), 8:(width-y,x)}[orientation]


def to_source(box, size, oriented_size, source_size, orientation, original=False):
    if original:
        return list(box)
    iw,ih=size; nw,nh=oriented_size; ow,oh=source_size
    x1,y1,x2,y2=box
    corners=[source_point(x*nw/iw,y*nh/ih,ow,oh,orientation)
             for x,y in [(x1,y1),(x2,y1),(x2,y2),(x1,y2)]]
    return [max(0.,min(x for x,y in corners)),max(0.,min(y for x,y in corners)),
            min(float(ow),max(x for x,y in corners)),min(float(oh),max(y for x,y in corners))]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,default=DEFAULT_ARTIFACTS)
    parser.add_argument('--project',type=Path,default=PROJECT_ROOT)
    parser.add_argument('--variants',nargs='+',default=['original','rgb_2x','gray_2x','clahe_2x','threshold_2x'],
                        choices=['original','rgb_2x','gray_2x','clahe_2x','threshold_2x'])
    args=parser.parse_args()
    source=args.input.expanduser().resolve(); out=output_directory(args.output, source, args.project)
    source_hash=sha256(source)
    with Image.open(source) as image:
        image.load(); source_size=image.size; orientation=int(image.getexif().get(274,1))
        if orientation not in range(1,9): raise ValueError('Invalid EXIF orientation')
        info={**source_reference(source,args.project),'filename_original':source.name,
              'filename_display_nfc':unicodedata.normalize('NFC',source.name),
              'sha256':source_hash,'actual_format':image.format,'mode':image.mode,
              'width':image.width,'height':image.height,'filename_extension':source.suffix,
              'extension_matches_format':source.suffix.lower() in Image.registered_extensions() and Image.registered_extensions()[source.suffix.lower()]==image.format,
              'exif_orientation':orientation,'has_alpha_channel':'A' in image.getbands(),
              'alpha_extrema':list(image.convert('RGBA').getchannel('A').getextrema())}
        normalized=ImageOps.exif_transpose(image).convert('RGBA')
        rgb=Image.alpha_composite(Image.new('RGBA',normalized.size,'white'),normalized).convert('RGB')
    doubled=rgb.resize((rgb.width*2,rgb.height*2),Image.Resampling.LANCZOS)
    gray=cv2.cvtColor(np.asarray(doubled),cv2.COLOR_RGB2GRAY)
    images={'rgb_normalized':rgb,'rgb_2x':doubled,'gray_2x':Image.fromarray(gray),
            'clahe_2x':Image.fromarray(cv2.createCLAHE(clipLimit=2.,tileGridSize=(8,8)).apply(gray)),
            'threshold_2x':Image.fromarray(cv2.adaptiveThreshold(gray,255,cv2.ADAPTIVE_THRESH_GAUSSIAN_C,cv2.THRESH_BINARY,31,11))}
    manifest={'schema_version':'poster.preprocessing.v2','created_at_utc':datetime.now(timezone.utc).isoformat(),
              'engine':'easyocr','execution_target':os.getenv('RUN_TARGET','local'),
              'source':info,'normalized_dimensions':list(rgb.size),'variants':[],'ocr_runs':[],
              'device_priority':['cuda','mps','cpu'],
              'notes':['Only EasyOCR/PyTorch results are accepted.','Raw OCR is separate from visual reference labels.',
                       'Image variants are diagnostic candidates; resizing cannot recover lost details.',
                       'Original coordinates are raw image pixels; EXIF transform inverted for normalized inputs.']}
    artifact_path(out,'images').mkdir(parents=True,exist_ok=True)
    artifact_path(out,'ocr').mkdir(exist_ok=True)
    for name,image in images.items():
        path=artifact_path(out,f'images/{name}.png')
        if path==source: raise ValueError('Output would overwrite input')
        image.save(path)
        manifest['variants'].append({'id':name,'path':f'images/{name}.png','width':image.width,'height':image.height,'mode':image.mode,'sha256':sha256(path)})
    any_error=False
    for name in args.variants:
        path=source if name=='original' else artifact_path(out,f'images/{name}.png')
        dimensions=source_size if name=='original' else images[name].size
        start=time.monotonic()
        try:
            raw=recognize_image(path)
            if raw.get('engine')!='easyocr' or raw.get('status')!='success':
                raise RuntimeError('Expected a successful EasyOCR result')
            if tuple(raw['input_dimensions'])!=dimensions: raise ValueError('OCR dimension mismatch')
            records=[]
            for i,record in enumerate(raw['records']):
                original_box=to_source(record['bbox_xyxy'],dimensions,rgb.size,source_size,orientation,name=='original')
                records.append({**record,'id':f'{name}_{i:03d}','input_bbox_xyxy':record['bbox_xyxy'],
                                'bbox_xyxy':original_box,'raw_record_id':record['id'],
                                'input_polygon':record.get('polygon'),
                                'polygon':record.get('polygon') if name=='original' else [list(source_point(x*rgb.width/dimensions[0],y*rgb.height/dimensions[1],source_size[0],source_size[1],orientation)) for x,y in record.get('polygon',[])]})
            payload={'schema_version':'poster.ocr.v2','variant':name,'status':'success','engine':'easyocr',
                     'execution_target':raw['execution_target'],'device':raw['device'],'attempts':raw.get('attempts',[]),
                     'source_sha256':source_hash,'input_dimensions':list(dimensions),'original_dimensions':list(source_size),
                     'bbox_coordinate_system':'original raw-image pixels, top-left xyxy, right/bottom exclusive',
                     'human_verified':False,'records':records,'raw_file':f'ocr/{name}.raw.json'}
        except Exception as error:
            any_error=True
            raw={'status':'error','engine':'easyocr','error':str(error)}
            payload={'schema_version':'poster.ocr.v2','variant':name,'status':'error','engine':'easyocr',
                     'source_sha256':source_hash,'records':[],'error':str(error)}
        write_json(artifact_path(out,f'ocr/{name}.raw.json'),raw)
        write_json(artifact_path(out,f'ocr/{name}.json'),payload)
        manifest['ocr_runs'].append({'variant':name,'status':payload['status'],'device':payload.get('device'),
                                    'record_count':len(payload['records']),'elapsed_seconds':round(time.monotonic()-start,3),
                                    'output':f'ocr/{name}.json','error':payload.get('error')})
        manifest['source_unchanged']=sha256(source)==source_hash
        write_json(artifact_path(out,'preprocessing_manifest.json'),manifest)
        print(f"{name}: {payload['status']}, device={payload.get('device')}, records={len(payload['records'])}",flush=True)
    if not manifest['source_unchanged']: raise RuntimeError('Source content changed')
    if any_error: raise SystemExit(1)


if __name__=='__main__': main()
