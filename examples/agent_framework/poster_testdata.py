"""Code-native SVG/PNG development posters; never edit the preserved originals.

Sidecar fields emulate observations for wiring tests, NOT OCR or model accuracy.
"""
from hashlib import sha256
from html import escape
import json
from pathlib import Path

VARIANTS=('normal','wrong_date','missing_notice','revised')


def fields(case,variant):
    if variant not in VARIANTS:raise ValueError('unknown test variant')
    return {'title':case+' TEST POSTER','date':'2099-01-16' if variant=='wrong_date' else '2099-01-15',
        'notice':'' if variant=='missing_notice' else 'ENTRY NOTICE REQUIRED'}


def svg(case,variant):
    value=fields(case,variant)
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="600" height="800" viewBox="0 0 600 800">
<rect width="600" height="800" fill="#f4f1e7"/><rect x="28" y="28" width="544" height="744" rx="14" fill="none" stroke="#214c40" stroke-width="2"/>
<text x="52" y="85" font-family="sans-serif" font-size="16" fill="#214c40">SYNTHETIC / DEVELOPMENT TEST</text>
<text x="52" y="170" font-family="sans-serif" font-size="27" fill="#183e34">{escape(value['title'])}</text>
<path d="M52 205H548" stroke="#b6773d" stroke-width="3"/>
<text x="52" y="310" font-family="sans-serif" font-size="18" fill="#627268">EVENT DATE</text>
<text x="52" y="360" font-family="sans-serif" font-size="34" fill="#183e34">{escape(value['date'])}</text>
<text x="52" y="480" font-family="sans-serif" font-size="20" fill="#183e34">{escape(value['notice'])}</text>
<text x="52" y="700" font-family="sans-serif" font-size="15" fill="#627268">{escape(variant.upper())} / NOT A REAL CLIENT DELIVERABLE</text>
</svg>'''


def generate(output,cases=('EXH-NEW004','CON-NEW043','FES-NEW072')):
    import pymupdf
    from backend.app.storage.images import normalize
    output=Path(output);output.mkdir(parents=True,exist_ok=True);records=[]
    for case in cases:
        for variant in VARIANTS:
            markup=svg(case,variant)
            with pymupdf.open(stream=markup.encode(),filetype='svg') as doc:
                raw=doc[0].get_pixmap(alpha=False).tobytes('png')
            display,_=normalize(raw)
            name=case+'.'+variant
            (output/(name+'.svg')).write_bytes(markup.encode('utf-8'))
            (output/(name+'.png')).write_bytes(raw)
            records.append({'case_id':case,'variant':variant,'svg':name+'.svg','png':name+'.png',
                'svg_sha256':sha256(markup.encode()).hexdigest(),'png_sha256':sha256(raw).hexdigest(),
                'display_sha256':sha256(display).hexdigest(),'fields':fields(case,variant),
                'synthetic':True,'observation_source':'scripted sidecar, not OCR or model output'})
    manifest={'artifact_role':'synthetic_poster_wiring_fixtures','real_model_calls':0,'records':records}
    (output/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8',newline='\n')
    return manifest


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args();result=generate(args.output)
    print(json.dumps({'synthetic':True,'posters':len(result['records']),'real_model_calls':0}))
