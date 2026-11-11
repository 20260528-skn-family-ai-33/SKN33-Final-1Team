"""포스터 로고 위치 확정·정밀 대조 API (/api/poster-logos).

흐름: 포스터 + 원본 로고 업로드 → 1부 후보(poster/logos/candidates) → 사용자가 확정 화면에서 원본 로고마다 위치 확정
      (후보 선택 · 직접 지정 · 포스터에 없음) → 2부 정밀 대조(영상 2부 규칙) → 위치별 근거(판정 없음, 최종 판단은 사람).

영상 로고 API(/api/video-logos)와 접속 경계·저장 방식·RunPod 워커를 함께 쓴다:
  - localhost 접속만 받고, 로그인 기능이 켜져 있으면 세션을 확인한다. isolate_users면 사용자별 폴더로 나눈다
  - 작업 폴더 <root>/<id>/: result.json(상태), poster.png(원본 해상도, 화면 방향·흰 배경), poster.jpg(화면용),
    refs/<i>.png(원본 로고), data.json(후보), compare.json(근거), crops/<로고>_<위치>.jpg(대조한 영역)
  - run_id는 같은 포스터로 접수한 프레임워크 포스터 검수 실행 번호를 적어 두는 연결 표시다. 실행의 권한·상태는 여기서 확인하지 않는다
포스터 검수(A04·A05) 판정과 섞지 않는다. 로고 후보 찾기·대조가 실패해도 포스터 검수는 그대로 진행된다.
"""
from __future__ import annotations

from io import BytesIO
from ipaddress import ip_address
import json
import math
from pathlib import Path
import shutil
import threading
from typing import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from starlette.concurrency import run_in_threadpool

from backend.app.video.logos.client import WorkerPool
from backend.app.video.logos.routes import failure_text
from backend.app.video.logos.routes import readiness
from backend.app.video.review import _now
from backend.app.video.review import _read_json
from backend.app.video.review import _write_json

# 포스터 검수 업로드 한도와 같게 둔다(20MiB, 1,000만 화소, 한 변 8192px).
MAX_POSTER_BYTES = 20 * 1024 * 1024
MAX_POSTER_PIXELS = 10_000_000
MAX_POSTER_SIDE = 8192
POSTER_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_LOGO_BYTES = 8 * 1024 * 1024
MAX_LOGO_PIXELS = 25_000_000
MAX_LOGOS = 12
MAX_LOCATIONS = 40       # 한 번의 정밀 대조에 보낼 위치(후보 + 직접 지정) 총수
DISPLAY_LONG = 2000      # 확정 화면에 보내는 포스터 긴 변(좌표는 원본 기준이라 화면 크기와 무관하다)
LIST_FIELDS = ("id", "title", "status", "stage", "progress", "stage_detail", "error", "warnings", "compare_status",
               "compare_progress", "compare_error", "run_id", "candidate_count", "created_at")


def _flatten(image: Image.Image) -> Image.Image:
    """투명한 부분은 흰 배경에 올린다(포스터 검수 A04·A05의 이미지 준비와 같다)."""
    if "A" in image.getbands() or "transparency" in image.info:
        rgba = image.convert("RGBA"); flat = Image.new("RGB", rgba.size, "white"); flat.paste(rgba, mask=rgba.getchannel("A"))
        return flat
    return image.convert("RGB")


def normalize_poster(raw: bytes) -> Image.Image:
    """포스터 이미지를 화면 방향(EXIF 회전 반영)·RGB로 맞춘다. 디코딩 전에 해상도 한도를 확인한다."""
    try:
        image = Image.open(BytesIO(raw)); width, height = image.size; kind = image.format
    except Image.DecompressionBombError:
        raise HTTPException(422, "포스터 해상도가 너무 큽니다.") from None
    except Exception:
        raise HTTPException(422, "포스터가 이미지가 아닙니다.") from None
    if kind not in POSTER_FORMATS:
        raise HTTPException(422, "포스터는 JPG·PNG·WEBP 이미지여야 합니다.")
    if width * height > MAX_POSTER_PIXELS or max(width, height) > MAX_POSTER_SIDE:
        raise HTTPException(422, f"포스터는 {MAX_POSTER_PIXELS // 10_000:,}만 화소·한 변 {MAX_POSTER_SIDE:,}px 이하여야 합니다.")
    try:
        return _flatten(ImageOps.exif_transpose(image))
    except Exception:
        raise HTTPException(422, "포스터 이미지를 읽지 못했습니다.") from None


def display_copy(image: Image.Image) -> Image.Image:
    k = min(1.0, DISPLAY_LONG / max(image.size))
    return image if k >= 1 else image.resize((max(1, round(image.width * k)), max(1, round(image.height * k))), Image.LANCZOS)


def save_poster(image: Image.Image, target: Path) -> None:
    """대조용 원본(무손실, 빠른 압축)과 화면용 사본을 저장한다."""
    image.save(target / "poster.png", compress_level=1)
    display_copy(image).save(target / "poster.jpg", quality=85)


def _uuid_or_422(value: str, label: str) -> str:
    try:
        if str(UUID(value)) != value:
            raise ValueError
    except ValueError:
        raise HTTPException(422, f"{label}가 올바르지 않습니다.") from None
    return value


def _valid_box(box, width: int, height: int) -> bool:
    return (isinstance(box, list) and len(box) == 4 and all(type(v) in (int, float) and math.isfinite(v) for v in box)
            and 0 <= box[0] < box[2] <= width and 0 <= box[1] < box[3] <= height)


def poster_logo_router(*, root: Path, inference_lock: threading.Lock, readonly: bool = False,
                       authenticate: Callable[[Request], object] | None = None,
                       allowed_hosts: tuple[str, ...] | None = None,
                       candidates: Callable | None = None, comparer: Callable | None = None,
                       isolate_users: bool = False, worker_pool: WorkerPool | None = None) -> APIRouter:
    """candidates·comparer는 테스트에서 실제 모델 대신 주입한다. 모델 실행 경계는 영상 로고 API와 같다."""
    if not readonly:
        root.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    for path in (() if readonly else root.glob("*/*/result.json" if isolate_users else "*/result.json")):
        try:  # 서버가 재시작되면 돌던 작업은 이어서 돌지 않으므로 실패로 알린다
            value = _read_json(path); changed = False
            if value.get("status") in {"queued", "running"}:
                value.update(status="failed", error="서버가 재시작되어 작업이 중단됐습니다. 다시 접수해 주세요."); changed = True
            if value.get("compare_status") in {"queued", "running"}:
                value.update(compare_status="failed", compare_error="서버가 재시작되어 정밀 대조가 중단됐습니다. 다시 실행해 주세요."); changed = True
            if changed:
                value["updated_at"] = _now(); _write_json(path, value)
        except (OSError, ValueError):
            continue

    def guard(request: Request) -> None:
        try:
            local = bool(request.client and ip_address(request.client.host).is_loopback)
        except ValueError:
            local = False
        hosts = allowed_hosts if authenticate is not None and allowed_hosts is not None else {"localhost", "127.0.0.1", "::1"}
        if not local or request.url.hostname not in hosts:
            raise HTTPException(404, "찾을 수 없습니다.")
        if authenticate is not None and authenticate(request) is None:
            raise HTTPException(401, {"code": "authentication_required", "message": "로그인이 필요합니다."})

    router = APIRouter(prefix="/api/poster-logos", dependencies=[Depends(guard)])

    def owner_root(request: Request) -> Path:
        return root / request.state.video_owner if isolate_users else root

    def directory(request: Request, job_id: str) -> Path:
        try:
            if str(UUID(job_id)) != job_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "포스터 로고 작업을 찾을 수 없습니다.") from None
        target = owner_root(request) / job_id
        if not (target / "result.json").is_file():
            raise HTTPException(404, "포스터 로고 작업을 찾을 수 없습니다.")
        return target

    def update(target: Path, **changes) -> None:
        with lock:
            value = _read_json(target / "result.json"); value.update(changes, updated_at=_now()); _write_json(target / "result.json", value)

    def slot_for(target: Path, meta: dict):
        return worker_pool.acquire(meta.get("worker_slot"), target.name) if worker_pool else inference_lock

    def run_candidates(target: Path) -> None:
        from backend.app.poster.logos import candidates as C
        from backend.app.poster.logos import client as R
        meta = _read_json(target / "result.json")
        try:
            with slot_for(target, meta) as slot:
                if worker_pool:
                    update(target, worker_slot=slot)
                update(target, status="running", stage="candidates", progress=1)
                poster = Image.open(target / "poster.png").convert("RGB")
                refs = [{"name": r["name"], "image": Image.open(target / "refs" / r["file"]), "text": r["text"]} for r in meta["logos"]]
                build = candidates or (R.build_candidates if R.worker_url() else C.build_candidates)
                data = build(poster, refs, lambda p, d: update(target, progress=min(99, round(p)), stage_detail=d))
            for i, r in enumerate(data["refs"]):
                r["img"] = f"refs/{i}.png"
            data.update(title=meta["title"], image="poster.jpg")
            _write_json(target / "data.json", data)
            update(target, status="candidates_ready", stage="candidates_ready", progress=100, stage_detail=None,
                   candidate_count=len(data["candidates"]))
        except Exception as exc:
            update(target, status="failed", error=f"후보 찾기에 실패했습니다: {failure_text(exc)}")

    def run_compare(target: Path, answers: list[dict], absent: list[int]) -> None:
        from backend.app.poster.logos import client as R
        from backend.app.poster.logos import compare as P
        meta, data = _read_json(target / "result.json"), _read_json(target / "data.json")
        out, skipped = [], []
        try:
            with slot_for(target, meta) as slot:
                if worker_pool:
                    update(target, worker_slot=slot)
                update(target, compare_status="running", compare_progress=0)
                poster = Image.open(target / "poster.png").convert("RGB")
                crops_dir = target / "crops"; shutil.rmtree(crops_dir, ignore_errors=True); crops_dir.mkdir()
                total = sum(len(a["crops"]) for a in answers) or 1; done = 0
                for a in answers:
                    li = a["logo"]; logo = meta["logos"][li]; items = []
                    for ci, c in enumerate(a["crops"]):
                        candidate = data["candidates"][c["candidate"]] if c["source"] == "candidate" else None
                        # 후보는 서버에 저장한 그 원본 로고의 대표 상자로 자른다(화면이 보낸 좌표를 쓰지 않는다)
                        box = candidate["rep"][li] if candidate else c["bbox"]
                        crop = P.crop_box(poster, box, c["source"])
                        if min(crop.size) < P.MIN_CROP_PX:
                            skipped.append({"logo": li, "crop": ci, "reason": "crop_too_small"}); continue
                        items.append((ci, c, candidate, box, crop))
                    crops = [item[-1] for item in items]
                    tick = lambda n, base=done: update(target, compare_progress=min(99, round(100 * (base + n) / total)))
                    if not crops:
                        results = []
                    elif comparer is None and R.worker_url():
                        results = R.compare_crops(Image.open(target / "refs" / logo["file"]), logo["text"], crops, tick)
                    else:
                        results = P.compare_local(Image.open(target / "refs" / logo["file"]), logo["text"], crops, tick, comparer)
                    done += len(a["crops"])
                    for (ci, c, candidate, box, crop), result in zip(items, results):
                        crop.save(crops_dir / f"{li}_{ci}.jpg", quality=92)
                        out.append({"reference": logo["name"], "logo": li, "crop": ci, "source": c["source"],
                                    "candidate": c["candidate"] if candidate else None,
                                    "rank": candidate["rank"][li] if candidate else None,
                                    "box": [round(float(v), 1) for v in box], **P.location_evidence(result, crop)})
            small = sum(1 for item in out if item["small"])
            warnings = ([f"영역이 너무 작아({P.MIN_CROP_PX}px 미만) 대조하지 못한 위치 {len(skipped)}개"] if skipped else []) + \
                ([f"짧은 변 {P.SMALL_CROP_PX}px 미만 위치 {small}개는 정상 로고도 차이 근거가 나오기 쉬워 원본과 직접 비교해 주세요."] if small else [])
            _write_json(target / "compare.json", {
                "locations": out, "skipped": skipped, "warnings": warnings, "created_at": _now(),
                "absent": [{"logo": li, "reference": meta["logos"][li]["name"]} for li in absent]})
            update(target, compare_status="completed", compare_progress=100)
        except Exception as exc:
            update(target, compare_status="failed", compare_error=f"정밀 대조에 실패했습니다: {failure_text(exc)}")

    @router.get("/status")
    def get_status():
        ready, detail = worker_pool.readiness() if worker_pool else readiness()
        return {"ready": ready and not readonly, "detail": detail if not readonly else "읽기 전용 모드입니다."}

    @router.get("")
    def listing(request: Request, run_id: str | None = None):
        """run_id를 주면 그 포스터 검수 실행과 함께 올린 로고 작업만 돌려준다(최근 20개)."""
        jobs = []
        for path in owner_root(request).glob("*/result.json"):
            try:
                value = _read_json(path)
            except (OSError, ValueError):
                continue
            if run_id and value.get("run_id") != run_id:
                continue
            jobs.append({**{k: value.get(k) for k in LIST_FIELDS}, "logo_count": len(value.get("logos") or [])})
        return {"items": sorted(jobs, key=lambda j: j.get("created_at") or "", reverse=True)[:20]}

    if not readonly:
        @router.post("", status_code=202)
        async def submit(request: Request, background: BackgroundTasks, poster: UploadFile = File(...),
                         logos: list[UploadFile] = File(...), logo_texts: str = Form("[]"),
                         title: str = Form("포스터 로고 검수", min_length=1, max_length=120),
                         run_id: str | None = Form(None)):
            """logo_texts: 원본 로고마다 기준 문구(줄은 |로 구분). 비우면 문자 비교는 원본 OCR로 대신한다.
            run_id: 같은 포스터로 접수한 포스터 검수(프레임워크) 실행 번호."""
            if run_id is not None:
                _uuid_or_422(run_id, "run_id")
            if not 1 <= len(logos) <= MAX_LOGOS:
                raise HTTPException(422, f"원본 로고는 1~{MAX_LOGOS}장입니다.")
            try:
                texts = json.loads(logo_texts)
            except ValueError:
                raise HTTPException(422, "logo_texts는 JSON 배열이어야 합니다.") from None
            if not isinstance(texts, list) or len(texts) not in (0, len(logos)) or not all(isinstance(t, str) and len(t) <= 300 for t in texts):
                raise HTTPException(422, "logo_texts는 원본 로고 수와 같은 길이의 문자열 배열이어야 합니다.")
            raw = await poster.read(MAX_POSTER_BYTES + 1)
            if len(raw) > MAX_POSTER_BYTES:
                raise HTTPException(413, "포스터 파일이 너무 큽니다.")
            image = await run_in_threadpool(normalize_poster, raw)  # 1,000만 화소 디코딩이 요청 처리 루프를 막지 않게
            job_id = str(uuid4()); target = owner_root(request) / job_id; (target / "refs").mkdir(parents=True)
            try:  # 검증에 걸리면 반쯤 만든 작업 폴더를 남기지 않는다
                items = []
                for i, logo in enumerate(logos):
                    data = await logo.read(MAX_LOGO_BYTES + 1)
                    if len(data) > MAX_LOGO_BYTES:
                        raise HTTPException(413, "원본 로고 이미지가 너무 큽니다.")
                    try:
                        im = Image.open(BytesIO(data))
                        if im.width * im.height > MAX_LOGO_PIXELS:
                            raise ValueError
                        im = ImageOps.exif_transpose(im)
                    except Exception:
                        raise HTTPException(422, "원본 로고가 읽을 수 있는 이미지가 아닙니다(SVG는 PNG로 바꿔 올려 주세요).") from None
                    im.convert("RGBA").save(target / "refs" / f"{i}.png")
                    items.append({"name": Path(logo.filename or f"logo_{i}").stem[:120] or f"logo_{i}", "file": f"{i}.png",
                                  "text": texts[i].strip() if texts else ""})
                await run_in_threadpool(save_poster, image, target)
            except BaseException:
                shutil.rmtree(target, ignore_errors=True)
                raise
            _write_json(target / "result.json", {
                "id": job_id, "title": title, "status": "queued", "stage": "queued", "progress": 0, "logos": items,
                "run_id": run_id, "poster": {"name": Path(poster.filename or "poster").name[:200], "size": list(image.size)},
                "created_at": _now(), "updated_at": _now()})
            background.add_task(run_candidates, target)
            return {"id": job_id, "status": "queued"}

        @router.post("/{job_id}/compare", status_code=202)
        def compare(request: Request, job_id: str, background: BackgroundTasks, answers: list[dict] = Body(...)):
            """확정 화면이 원본 로고마다 저장한 선택의 목록을 받는다.
            [{"logo": 원본 번호(업로드 순서), "decision": "done"|"none",
              "crops": [{"source": "candidate", "candidate": 후보 번호} | {"source": "manual", "bbox": [x1, y1, x2, y2]}]}]
            bbox는 data.json의 size(포스터 원본 픽셀) 기준이다. none은 '포스터에 없음'으로 사람이 확인한 기록으로 남는다."""
            target = directory(request, job_id); meta = _read_json(target / "result.json")
            if meta.get("status") != "candidates_ready":
                raise HTTPException(409, "후보 찾기가 끝난 뒤에 정밀 대조를 실행할 수 있습니다.")
            data = _read_json(target / "data.json"); width, height = data["size"]; count = len(data["candidates"])
            done, absent, total = [], [], 0
            for a in answers:
                li = a.get("logo")
                if (type(li) is not int or not 0 <= li < len(meta["logos"]) or a.get("decision") not in ("done", "none")
                        or not isinstance(a.get("crops"), list) or (a["decision"] == "done") != bool(a["crops"])):
                    raise HTTPException(422, "원본 로고 번호(logo)·decision(done|none)·crops 형식이 맞지 않습니다.")
                crops = []
                for c in a["crops"]:
                    source = c.get("source") if isinstance(c, dict) else None
                    if source == "candidate" and type(c.get("candidate")) is int and 0 <= c["candidate"] < count:
                        crops.append({"source": "candidate", "candidate": c["candidate"]})
                    elif source == "manual" and _valid_box(c.get("bbox"), width, height):
                        crops.append({"source": "manual", "bbox": [float(v) for v in c["bbox"]]})
                    else:
                        raise HTTPException(422, "위치는 후보 번호(candidate) 또는 포스터 안의 직접 지정 bbox(x1<x2, y1<y2)여야 합니다.")
                total += len(crops)
                (done.append({"logo": li, "crops": crops}) if a["decision"] == "done" else absent.append(li))
            if not answers or len({a["logo"] for a in answers}) != len(answers):
                raise HTTPException(422, "원본 로고마다 한 번씩 확정한 선택이 필요합니다.")
            if total > MAX_LOCATIONS:
                raise HTTPException(422, f"한 번에 대조할 위치는 {MAX_LOCATIONS}개 이하입니다.")
            with lock:
                if _read_json(target / "result.json").get("compare_status") in {"queued", "running"}:
                    raise HTTPException(409, "정밀 대조가 이미 실행 중입니다.")
                (target / "compare.json").unlink(missing_ok=True)  # 이전 결과를 새 결과처럼 보여 주지 않는다
                update(target, compare_status="queued", compare_progress=0, compare_error=None)
            background.add_task(run_compare, target, done, absent)
            return {"id": job_id, "compare_status": "queued"}

    @router.get("/{job_id}")
    def get_job(request: Request, job_id: str):
        return _read_json(directory(request, job_id) / "result.json")

    @router.get("/{job_id}/data.json")
    def data(request: Request, job_id: str):
        path = directory(request, job_id) / "data.json"
        if not path.is_file():
            raise HTTPException(404, "후보가 아직 없습니다.")
        return _read_json(path)

    @router.get("/{job_id}/poster.jpg")
    def poster_image(request: Request, job_id: str):
        return FileResponse(directory(request, job_id) / "poster.jpg", media_type="image/jpeg")

    @router.get("/{job_id}/refs/{index_no}.png")
    def ref_image(request: Request, job_id: str, index_no: int):
        path = directory(request, job_id) / "refs" / f"{index_no}.png"
        if not path.is_file():
            raise HTTPException(404, "원본 로고가 없습니다.")
        return FileResponse(path, media_type="image/png")

    @router.get("/{job_id}/compare")
    def compare_result(request: Request, job_id: str):
        target = directory(request, job_id); meta = _read_json(target / "result.json")
        body = _read_json(target / "compare.json") if (target / "compare.json").is_file() else {"locations": [], "absent": []}
        return {"compare_status": meta.get("compare_status"), "compare_progress": meta.get("compare_progress"),
                "compare_error": meta.get("compare_error"), **body}

    @router.get("/{job_id}/compare/crop/{logo}/{crop}.jpg")
    def crop_image(request: Request, job_id: str, logo: int, crop: int):
        path = directory(request, job_id) / "crops" / f"{logo}_{crop}.jpg"
        if not path.is_file():
            raise HTTPException(404, "대조한 영역 이미지가 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    return router
