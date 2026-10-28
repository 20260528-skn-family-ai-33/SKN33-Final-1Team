"""Read-only exact/perceptual image audit. No model or network access."""
import hashlib
from pathlib import Path
import numpy as np
from PIL import Image, ImageOps

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()

def fingerprint(path):
    # The reviewed source contains a 129 MP poster. Keep a bounded explicit limit.
    Image.MAX_IMAGE_PIXELS=150_000_000
    with Image.open(path) as im:
        rgb=ImageOps.exif_transpose(im).convert('RGB')
        g=np.asarray(rgb.convert('L').resize((32,32)),dtype=float)
        d=np.asarray(rgb.convert('L').resize((9,8)),dtype=float)
        rgb.close()
    c=np.cos(np.pi*(2*np.arange(32)+1)[None,:]*np.arange(8)[:,None]/64)
    q=c@g@c.T
    bits=(q>np.median(q.flatten()[1:])).flatten();bits[0]=False
    def hx(bits): return f'{sum(int(v)<<i for i,v in enumerate(bits)):016x}'
    return dict(sha256=sha(path),phash=hx(bits),dhash=hx((d[:,1:]>d[:,:-1]).flatten()))

def distance(a,b,key): return (int(a[key],16)^int(b[key],16)).bit_count()

def near(a,b):
    return a['sha256']==b['sha256'] or (distance(a,b,'phash')<=8 and distance(a,b,'dhash')<=10)
