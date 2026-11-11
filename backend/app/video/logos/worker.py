"""영상 로고 확정·정밀 대조의 RunPod Pod HTTP 워커. 모델(OWLv2·SigLIP2·BiRefNet·OCR)은 이 프로세스에만 올린다.

로컬 백엔드(video_logo_flow)는 프레임 추출·저장·화면 데이터를 맡고, 모델이 필요한 두 단계만 이 워커에 맡긴다.
  - candidates : 1초 프레임 + 원본 로고 → 1부 후보 data(video_logo_candidates.build_candidates)
  - compare    : 원본 로고 1장 + 묶음의 초별 crop → 초별 대조 결과(video_logo_compare.reference / compare)
                 포스터 로고 정밀 대조도 같은 작업을 쓴다(crop = 사용자가 확정한 포스터 속 위치)
  - poster_candidates : 포스터 1장 + 원본 로고 → 포스터 로고 1부 후보 data(backend/app/poster/logos/candidates.build_candidates)
  - ocr_frames : 동영상 검수(자막) 프레임 → Paddle 원본 결과 줄(paddle_video_worker와 같은 형식). GPU용 Paddle 환경에서 돈다
작업은 오래 걸리므로 제출(POST /jobs) 후 진행률을 조회(GET /jobs/<id>)한다. GPU를 나눠 쓰지 않게 한 번에 하나씩 돈다.

Pod에서 실행(127.0.0.1에만 열고, 로컬은 SSH 터널로 붙는다):
  uvicorn backend.app.video_logo_worker:app --host 127.0.0.1 --port 8791
VIDEO_LOGO_WORKER_TOKEN을 Pod와 로컬에 같은 값으로 두면 X-Worker-Token 헤더를 확인한다.
자막 OCR은 torch와 CUDA 라이브러리가 겹치지 않게 따로 만든 GPU Paddle 환경의 python으로 돌린다:
  VIDEO_OCR_PYTHON=/root/paddlegpu/bin/python   VIDEO_OCR_DEVICE=gpu (기본)
"""
from __future__ import annotations

import base64
import binascii
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from uuid import uuid4

from fastapi import Body, Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from PIL import Image

MAX_IMAGE_BYTES = 16 * 1024 * 1024
MAX_FRAMES = 3600          # 1초 간격이라 1시간 영상까지
MAX_REFS = 12
MAX_CROPS = 3600
KEEP_SECONDS = 3600        # 끝난 작업 결과는 1시간 뒤 지운다

app = FastAPI(title="video-logo-worker")
_jobs: dict[str, dict] = {}
_lock = threading.Lock()
_queue: "queue.Queue[str]" = queue.Queue()


def _auth(x_worker_token: str | None = Header(None)) -> None:
    token = os.getenv("VIDEO_LOGO_WORKER_TOKEN", "")
    if token and not (x_worker_token and secrets.compare_digest(x_worker_token, token)):
        raise HTTPException(401, "워커 토큰이 맞지 않습니다.")


def _image(encoded, label: str) -> Image.Image:
    if not isinstance(encoded, str) or not encoded or len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
        raise ValueError(f"{label}: base64 이미지가 필요합니다.")
    try:
        im = Image.open(BytesIO(base64.b64decode(encoded, validate=True))); im.load()
    except (binascii.Error, ValueError, OSError):
        raise ValueError(f"{label}: 읽을 수 없는 이미지입니다.") from None
    return im


def _plain(value):
    """numpy 값이 섞인 결과를 JSON으로 보낼 수 있게 바꾼다."""
    return json.loads(json.dumps(value, default=lambda v: v.item() if hasattr(v, "item") else str(v)))


def _set(job_id: str, **changes) -> None:
    with _lock:
        _jobs[job_id].update(changes, updated=time.time())


def _run_candidates(job_id: str, body: dict) -> dict:
    from backend.app.video.logos import candidates as C
    work = Path(tempfile.mkdtemp(prefix="vlogo-")); (work / "full").mkdir()
    try:
        frames = []
        for f in body["frames"]:
            im = _image(f["jpg_base64"], f"{f['sec']}초 프레임").convert("RGB")
            im.save(work / "full" / f"{int(f['sec'])}.jpg", quality=95); frames.append({"sec": int(f["sec"]), "size": list(im.size)})
        refs = [{"name": r["name"], "image": _image(r["png_base64"], f"원본 로고 {r['name']}"), "text": r.get("text", "")} for r in body["refs"]]
        return C.build_candidates(work, frames, refs, lambda p, d: _set(job_id, progress=round(p), detail=d))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _run_poster_candidates(job_id: str, body: dict) -> dict:
    from backend.app.poster.logos import candidates as P
    poster = _image(body["poster"]["jpg_base64"], "포스터").convert("RGB")
    refs = [{"name": r["name"], "image": _image(r["png_base64"], f"원본 로고 {r['name']}"), "text": r.get("text", "")} for r in body["refs"]]
    return P.build_candidates(poster, refs, lambda p, d: _set(job_id, progress=round(p), detail=d))


def _run_compare(job_id: str, body: dict) -> list[dict]:
    from backend.app.video.logos import compare as V
    ref = V.reference(_image(body["ref"]["png_base64"], "원본 로고"), body["ref"].get("text", ""))
    out, crops = [], body["crops"]
    for i, c in enumerate(crops):
        out.append(V.compare(_image(c, f"crop {i + 1}").convert("RGB"), ref))
        _set(job_id, progress=round(100 * (i + 1) / len(crops)), detail=f"crop {i + 1}/{len(crops)}")
    return out


def _run_ocr_frames(job_id: str, body: dict) -> dict:
    from backend.app.ocr.runtime import PROJECT_ROOT
    from backend.app.ocr.paddle import paddle_paths
    from backend.app.ocr.paddle import worker_environment
    _, detection, recognition = paddle_paths()
    python, device = os.getenv("VIDEO_OCR_PYTHON") or sys.executable, os.getenv("VIDEO_OCR_DEVICE", "gpu")
    work = Path(tempfile.mkdtemp(prefix="vocr-"))
    try:
        paths = []
        for i, encoded in enumerate(body["frames"]):
            path = work / f"{i:05d}.jpg"; path.write_bytes(base64.b64decode(encoded, validate=True)); paths.append(str(path))
        listing, output = work / "frames.txt", work / "paddle.raw.jsonl"
        listing.write_text("\n".join(paths), encoding="utf-8")
        deadline = time.monotonic() + 300 + 30 * len(paths)
        with (work / "paddle.log").open("w", encoding="utf-8") as log:
            worker = subprocess.Popen([python, "-m", "backend.app.paddle_video_worker", "--frames", str(listing), "--output", str(output),
                                       "--detection-dir", str(detection), "--recognition-dir", str(recognition), "--device", device],
                                      cwd=PROJECT_ROOT, env=worker_environment(), stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
            try:
                while worker.poll() is None:
                    if time.monotonic() > deadline:
                        raise ValueError("자막 OCR이 제한 시간을 넘겼습니다.")
                    if output.exists():
                        done = max(0, sum(1 for _ in output.open(encoding="utf-8")) - 1)
                        _set(job_id, progress=round(100 * done / len(paths)), detail=f"프레임 {done} / {len(paths)}")
                    time.sleep(1)
            finally:
                if worker.poll() is None:
                    worker.kill(); worker.wait()
        if worker.returncode != 0:
            tail = (work / "paddle.log").read_text(encoding="utf-8", errors="replace").strip().splitlines()[-1:] or ["원인 미상"]
            raise ValueError(f"자막 OCR 워커가 실패했습니다: {tail[0][:300]}")
        return {"rows": [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines() if line.strip()]}
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _free_gpu() -> None:
    """작업이 잡아 둔 GPU 캐시를 돌려줘 실패한 작업의 메모리를 다음 작업이 OOM으로 이어받지 않게 한다."""
    try:
        import gc
        import torch
        gc.collect()  # traceback·프레임의 순환 참조에 남은 텐서까지 풀어 준다
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


RUNNERS = {"candidates": _run_candidates, "poster_candidates": _run_poster_candidates, "compare": _run_compare,
           "ocr_frames": _run_ocr_frames}


def _loop() -> None:
    while True:
        job_id = _queue.get()
        with _lock:
            job = _jobs.get(job_id)
            body = job.pop("body", None) if job else None
        if body is None:
            continue
        _set(job_id, status="running", progress=0)
        try:
            result = RUNNERS[job["kind"]](job_id, body)
            _set(job_id, status="completed", progress=100, result=_plain(result))
        except Exception as exc:  # 실패 원인 종류만 돌려준다(경로·값은 응답에 싣지 않는다). 자세한 내용은 Pod 로그에만 남긴다
            traceback.print_exc()
            _set(job_id, status="failed", error=f"{exc.__class__.__name__}: {exc}" if isinstance(exc, ValueError) else exc.__class__.__name__)
        # except 안에서는 traceback이 실패 지점의 텐서를 잡고 있어 비워지지 않으므로 블록을 벗어난 뒤에 정리한다
        _free_gpu()


threading.Thread(target=_loop, daemon=True, name="video-logo-worker").start()


def _sweep() -> None:
    now = time.time()
    with _lock:
        for key in [k for k, v in _jobs.items() if v["status"] in {"completed", "failed"} and now - v["updated"] > KEEP_SECONDS]:
            del _jobs[key]


@app.get("/health", dependencies=[Depends(_auth)])
def health():
    """모델을 올리지 않고 패키지·GPU·OCR 모델 파일만 확인한다."""
    from backend.app.video.logos.routes import local_readiness
    ready, detail = local_readiness()
    with _lock:
        busy = sum(v["status"] in {"queued", "running"} for v in _jobs.values())
    return {"ready": ready, "detail": detail, "busy": busy}


@app.post("/jobs", status_code=202, dependencies=[Depends(_auth)])
def submit(body: dict = Body(...)):
    """{"kind": "candidates", "frames": [{sec, jpg_base64}], "refs": [{name, png_base64, text}]}
    또는 {"kind": "compare", "ref": {png_base64, text}, "crops": [jpg_base64, ...]}"""
    kind = body.get("kind")
    if kind == "candidates":
        frames, refs = body.get("frames"), body.get("refs")
        if not (isinstance(frames, list) and 1 <= len(frames) <= MAX_FRAMES and isinstance(refs, list) and 1 <= len(refs) <= MAX_REFS):
            raise HTTPException(422, "frames(1개 이상)와 refs(1~12개)가 필요합니다.")
    elif kind == "poster_candidates":
        poster, refs = body.get("poster"), body.get("refs")
        if not (isinstance(poster, dict) and isinstance(poster.get("jpg_base64"), str) and poster["jpg_base64"]
                and isinstance(refs, list) and 1 <= len(refs) <= MAX_REFS):
            raise HTTPException(422, "poster(jpg_base64)와 refs(1~12개)가 필요합니다.")
    elif kind == "ocr_frames":
        frames = body.get("frames")
        if not (isinstance(frames, list) and 1 <= len(frames) <= MAX_FRAMES and all(isinstance(f, str) and f for f in frames)):
            raise HTTPException(422, "frames(base64 이미지 1개 이상)가 필요합니다.")
    elif kind == "compare":
        if not (isinstance(body.get("ref"), dict) and isinstance(body.get("crops"), list) and 1 <= len(body["crops"]) <= MAX_CROPS):
            raise HTTPException(422, "ref와 crops(1개 이상)가 필요합니다.")
    else:
        raise HTTPException(422, "kind는 candidates, poster_candidates, compare, ocr_frames 중 하나입니다.")
    _sweep()
    job_id = uuid4().hex
    with _lock:
        _jobs[job_id] = {"kind": kind, "status": "queued", "progress": 0, "detail": None, "body": body, "updated": time.time()}
    _queue.put(job_id)
    return {"id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}", dependencies=[Depends(_auth)])
def job_status(job_id: str):
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "작업이 없습니다(워커가 재시작됐을 수 있습니다).")
        value = {k: v for k, v in job.items() if k not in {"body", "updated"}}
    return Response(json.dumps(value, ensure_ascii=False), media_type="application/json")
