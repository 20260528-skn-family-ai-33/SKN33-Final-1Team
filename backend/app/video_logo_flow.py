"""영상 로고 확정·정밀 대조 API (/api/video-logos).

흐름: 영상 + 원본 로고 업로드 → 1부 후보(video_logo_candidates) → 사용자가 확정 화면에서 위치 확정
      → 2부 정밀 대조(video_logo_compare) → 묶음별 근거(판정 없음, 최종 판단은 사람).

확정 화면(LogoConfirmPage.jsx)이 정적 시연 데이터와 같은 경로로 읽을 수 있게, 작업 하나를 세트 하나처럼 낸다:
  {base}/index.json, {base}/video/data.json, {base}/video/f/<sec>.jpg, {base}/refs/<i>.png   (base = /api/video-logos/<id>)
모델은 이 서버 프로세스(RunPod Pod)에서 돈다. 접속 경계는 동영상 검수(video_review)와 같다.
"""
from __future__ import annotations

from io import BytesIO
from ipaddress import ip_address
import json
from pathlib import Path
import shutil
import threading
from typing import Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from PIL import Image

from .video_review import _now, _read_json, _write_json

VIDEO_KEY = "video"
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
MAX_LOGO_BYTES = 8 * 1024 * 1024
MAX_LOGOS = 12
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


def readiness() -> tuple[bool, str]:
    """VIDEO_LOGO_WORKER_URL이 있으면 RunPod 워커의 준비 상태, 없으면 이 프로세스의 준비 상태."""
    from . import video_logo_remote as R
    try:
        remote = R.worker_url()
    except R.WorkerError as exc:
        return False, str(exc)
    return R.health() if remote else local_readiness()


def failure_text(exc: Exception) -> str:
    """워커 연결 문제는 안내 문구 그대로, 나머지는 예외 종류만 보여 준다."""
    from .video_logo_remote import WorkerError
    return str(exc) if isinstance(exc, WorkerError) else exc.__class__.__name__


def local_readiness() -> tuple[bool, str]:
    """모델을 올리지 않고 필요한 패키지와 GPU만 확인한다(Pod 안에서 직접 추론할 때)."""
    try:
        import cv2  # noqa: F401
        import paddleocr  # noqa: F401
        import torch
        import transformers  # noqa: F401
    except Exception as exc:  # pragma: no cover - 환경 확인
        return False, f"영상 로고 검수 패키지가 없습니다: {exc.__class__.__name__}"
    if not torch.cuda.is_available():
        return False, "GPU가 없습니다. RunPod Pod 워커(VIDEO_LOGO_WORKER_URL)를 연결하세요."
    from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model
    from .paddle_ocr import paddle_paths
    _, detection, recognition = paddle_paths()
    try:  # OCR은 자막 검사와 같은 모델 파일을 쓴다
        verify_model(detection, DETECTION_MODEL); verify_model(recognition, MODEL)
    except (OSError, ValueError, TypeError, KeyError):
        return False, "자막 검사 OCR 모델(.runtime/paddle-models)이 없거나 무결성 확인에 실패했습니다."
    return True, "준비됨"


def seconds_text(secs: list[int], limit: int = 20) -> str:
    shown = ", ".join(f"{s}초" for s in secs[:limit])
    return shown + (f" 외 {len(secs) - limit}개" if len(secs) > limit else "")


def missing_text(missing: list[int], total: int) -> str:
    return f"영상 {total}초 중 {len(missing)}초의 프레임을 읽지 못해 후보 찾기·정밀 대조에서 빠졌습니다: {seconds_text(missing)}"


def video_logo_router(*, root: Path, inference_lock: threading.Lock, readonly: bool = False,
                      authenticate: Callable[[Request], object] | None = None,
                      candidates: Callable | None = None, comparer: Callable | None = None) -> APIRouter:
    """candidates·comparer는 테스트에서 실제 모델 대신 주입한다."""
    if not readonly:
        root.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()
    for path in (() if readonly else root.glob("*/result.json")):  # 재시작 전에 돌던 작업은 실패로 남긴다
        try:
            value = _read_json(path)
            changed = False
            if value.get("status") in {"queued", "running"}:
                value.update(status="failed", error="서버가 재시작되어 작업이 중단됐습니다. 다시 실행해 주세요."); changed = True
            if value.get("compare_status") in {"queued", "running"}:  # 정밀 대조도 같은 이유로 멈춘다
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
        if not local or request.url.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(404, "찾을 수 없습니다.")
        if authenticate is not None and authenticate(request) is None:
            raise HTTPException(401, {"code": "authentication_required", "message": "로그인이 필요합니다."})

    router = APIRouter(prefix="/api/video-logos", dependencies=[Depends(guard)])

    def directory(job_id: str) -> Path:
        try:
            if str(UUID(job_id)) != job_id:
                raise ValueError
        except ValueError:
            raise HTTPException(404, "영상 로고 작업을 찾을 수 없습니다.") from None
        target = root / job_id
        if not (target / "result.json").is_file():
            raise HTTPException(404, "영상 로고 작업을 찾을 수 없습니다.")
        return target

    def update(target: Path, **changes) -> None:
        with lock:
            value = _read_json(target / "result.json"); value.update(changes, updated_at=_now()); _write_json(target / "result.json", value)

    def run_candidates(target: Path) -> None:
        from . import video_logo_candidates as C
        from . import video_logo_remote as R
        meta = _read_json(target / "result.json")
        try:
            with inference_lock:
                update(target, status="running", stage="frames", progress=1)
                frames, missing, total = C.sample_frames(target / meta["video_file"], target / "frames")
                if not frames:
                    raise RuntimeError("no frames")
                if missing:  # 못 읽은 초는 후보·대조에서 빠지므로 바로 알린다
                    update(target, missing_seconds=missing, warnings=[missing_text(missing, total)])
                refs = [{"name": r["name"], "image": Image.open(target / "refs" / r["file"]), "text": r["text"]} for r in meta["logos"]]
                build = candidates or (R.build_candidates if R.worker_url() else C.build_candidates)
                data = build(target / "frames", frames, refs, lambda p, d: update(target, stage="candidates", progress=round(p), stage_detail=d))
            data.update(key=VIDEO_KEY, title=meta["title"], seconds=total, missing=missing)
            for i, r in enumerate(data["refs"]):
                r["img"] = f"refs/{i}.png"
            _write_json(target / "data.json", data)
            update(target, status="candidates_ready", stage="candidates_ready", progress=100, stage_detail=None, seconds=total)
        except Exception as exc:
            update(target, status="failed", error=f"후보 찾기에 실패했습니다: {failure_text(exc)}")

    def run_compare(target: Path, answers: list[dict]) -> None:
        from . import video_logo_compare as V
        from . import video_logo_remote as R
        meta, data = _read_json(target / "result.json"), _read_json(target / "data.json")
        out, local_refs = [], {}

        def compare_crops(li: int, logo: dict, crops: list, tick) -> list[dict]:
            """원본 로고 1장 대 crop 여러 장. RunPod 워커가 설정돼 있으면 워커에서, 아니면 이 프로세스에서 대조한다."""
            ref_image = Image.open(target / "refs" / logo["file"])
            if comparer is None and R.worker_url():
                return R.compare_crops(ref_image, logo["text"], crops, tick)
            if li not in local_refs:
                local_refs[li] = V.reference(ref_image, logo["text"])
            results = []
            for i, q in enumerate(crops):
                results.append((comparer or V.compare)(q, local_refs[li])); tick(i + 1)
            return results

        try:
            with inference_lock:
                update(target, compare_status="running", compare_progress=0)
                total = sum(len(V.bundle_seconds(c, data["groups"])) for a in answers for c in a.get("crops", [])) or 1; done = 0
                for a in answers:
                    li = a["logo"]; logo = meta["logos"][li]
                    for ci, crop in enumerate(a.get("crops", [])):
                        skipped, items = [], []
                        for sec in V.bundle_seconds(crop, data["groups"]):
                            path = target / "frames" / "full" / f"{sec}.jpg"
                            if not path.is_file():
                                skipped.append({"sec": sec, "reason": "frame_missing"}); continue
                            box = crop["bbox"]
                            if crop.get("source") == "candidate" and crop.get("group") is not None:
                                # 후보는 화면에서 본 그 초의 위치로 자른다. 직접 지정은 고정 좌표를 그대로 쓴다.
                                box = data["groups"][crop["group"]]["box"][li].get(str(sec), box)
                            q = V.crop_at(Image.open(path).convert("RGB"), box, data["size"], crop.get("source", "manual"))
                            if min(q.size) < 4:
                                skipped.append({"sec": sec, "reason": "crop_too_small"}); continue
                            items.append((sec, q))
                        base = done + len(skipped)
                        results = compare_crops(li, logo, [q for _, q in items],
                                                lambda n, base=base: update(target, compare_progress=min(99, round(100 * (base + n) / total))))
                        done = base + len(items)
                        per = [{"sec": sec, "size": list(q.size), "outcome": r["outcome"], "findings": r["findings"], "kinds": r["kinds"],
                                "color": r["color"], "elements": r["elements"], "ocr": r["ocr"], "sharp": round(V.sharpness(q), 1)}
                               for (sec, q), r in zip(items, results)]
                        ev = V.bundle_evidence(per)
                        if ev["representative"]:
                            rep = ev["representative"]["sec"]
                            next(q for sec, q in items if sec == rep).save(target / "frames" / f"rep_{li}_{ci}.jpg", quality=90)
                        out.append({"reference": logo["name"], "logo": li, "crop": ci, "source": crop.get("source"), "box": crop["bbox"],
                                    "evidence": ev, "per_sec": per, "skipped": skipped})
            lost = sorted({x["sec"] for b in out for x in b["skipped"] if x["reason"] == "frame_missing"})
            small = sum(x["reason"] == "crop_too_small" for b in out for x in b["skipped"])
            warnings = ([f"프레임이 없어 대조하지 못한 초 {len(lost)}개: {seconds_text(lost)}"] if lost else []) + \
                ([f"자른 영역이 너무 작아(4px 미만) 대조하지 못한 초 {small}개"] if small else [])
            _write_json(target / "compare.json", {"bundles": out, "warnings": warnings, "created_at": _now()})
            update(target, compare_status="completed", compare_progress=100)
        except Exception as exc:
            update(target, compare_status="failed", compare_error=f"정밀 대조에 실패했습니다: {failure_text(exc)}")

    @router.get("/status")
    def get_status():
        ready, detail = readiness()
        return {"ready": ready and not readonly, "detail": detail if not readonly else "읽기 전용 모드입니다."}

    @router.get("")
    def listing(review_id: str | None = None):
        """review_id를 주면 그 동영상 검수와 함께 올린 로고 작업만 돌려준다."""
        jobs = []
        for path in root.glob("*/result.json"):
            try:
                v = _read_json(path)
            except (OSError, ValueError):
                continue
            if review_id and v.get("review_id") != review_id:
                continue
            jobs.append({k: v.get(k) for k in ("id", "title", "status", "stage", "progress", "stage_detail", "error", "warnings",
                                               "compare_status", "compare_progress", "review_id", "created_at")})
        return {"items": sorted(jobs, key=lambda j: j.get("created_at") or "", reverse=True)[:20]}

    if not readonly:
        @router.post("", status_code=202)
        async def submit(background: BackgroundTasks, video: UploadFile = File(...), logos: list[UploadFile] = File(...),
                         logo_texts: str = Form("[]"), title: str = Form("영상 로고 검수", min_length=1, max_length=120),
                         review_id: str | None = Form(None)):
            """logo_texts: 원본 로고마다 기준 문구(줄은 |로 구분, 예: "울산광역시남구|ULSAN METROPOLITAN NAMGU"). 비우면 문자 비교를 하지 않는다.
            review_id: 같은 영상으로 함께 접수한 동영상 검수 작업(화면에서 묶어 보여 주는 데만 쓴다)."""
            if review_id is not None:
                try:
                    if str(UUID(review_id)) != review_id:
                        raise ValueError
                except ValueError:
                    raise HTTPException(422, "review_id가 올바르지 않습니다.") from None
            if not 1 <= len(logos) <= MAX_LOGOS:
                raise HTTPException(422, f"원본 로고는 1~{MAX_LOGOS}장입니다.")
            try:
                texts = json.loads(logo_texts)
            except ValueError:
                raise HTTPException(422, "logo_texts는 JSON 배열이어야 합니다.") from None
            if not isinstance(texts, list) or len(texts) not in (0, len(logos)) or not all(isinstance(t, str) and len(t) <= 300 for t in texts):
                raise HTTPException(422, "logo_texts는 원본 로고 수와 같은 길이의 문자열 배열이어야 합니다.")
            suffix = Path(video.filename or "").suffix.lower()
            if suffix not in VIDEO_SUFFIXES:
                raise HTTPException(422, "지원하는 영상 형식이 아닙니다.")
            job_id = str(uuid4()); target = root / job_id; (target / "refs").mkdir(parents=True)
            try:  # 검증에 걸리면 반쯤 만든 작업 폴더를 남기지 않는다
                size = 0
                with open(target / f"video{suffix}", "wb") as fh:
                    while chunk := await video.read(1024 * 1024):
                        size += len(chunk)
                        if size > MAX_VIDEO_BYTES:
                            raise HTTPException(413, "영상이 너무 큽니다.")
                        fh.write(chunk)
                items = []
                for i, logo in enumerate(logos):
                    raw = await logo.read(MAX_LOGO_BYTES + 1)
                    if len(raw) > MAX_LOGO_BYTES:
                        raise HTTPException(413, "원본 로고 이미지가 너무 큽니다.")
                    try:
                        im = Image.open(BytesIO(raw)); im.load()
                    except Exception:
                        raise HTTPException(422, "원본 로고가 이미지가 아닙니다.") from None
                    im.convert("RGBA").save(target / "refs" / f"{i}.png")
                    items.append({"name": Path(logo.filename or f"logo_{i}").stem, "file": f"{i}.png", "text": texts[i] if texts else ""})
            except BaseException:
                shutil.rmtree(target, ignore_errors=True)
                raise
            _write_json(target / "result.json", {"id": job_id, "title": title, "status": "queued", "stage": "queued", "progress": 0,
                                                 "video_file": f"video{suffix}", "logos": items, "review_id": review_id, "created_at": _now(), "updated_at": _now()})
            background.add_task(run_candidates, target)
            return {"id": job_id, "status": "queued"}

        @router.post("/{job_id}/compare", status_code=202)
        def compare(job_id: str, background: BackgroundTasks, answers: list[dict] = Body(...)):
            """확정 화면이 내려받는 정답 값(원본 로고마다 {logo, reference, decision, crops})의 목록을 받는다.
            logo는 원본 로고 번호(업로드 순서)다. 파일 이름이 같은 로고가 있어도 번호로 구분한다."""
            target = directory(job_id); meta = _read_json(target / "result.json")
            if meta.get("status") != "candidates_ready":
                raise HTTPException(409, "후보 찾기가 끝난 뒤에 정밀 대조를 실행할 수 있습니다.")
            if meta.get("compare_status") == "running":
                raise HTTPException(409, "정밀 대조가 이미 실행 중입니다.")
            for a in answers:
                li = a.get("logo")
                if type(li) is not int or not 0 <= li < len(meta["logos"]) or not isinstance(a.get("crops"), list):
                    raise HTTPException(422, "원본 로고 번호(logo)나 crops 형식이 맞지 않습니다.")
            if len({a["logo"] for a in answers}) != len(answers):
                raise HTTPException(422, "같은 원본 로고 번호가 두 번 들어 있습니다.")
                for c in a["crops"]:
                    if not (isinstance(c.get("bbox"), list) and len(c["bbox"]) == 4 and isinstance(c.get("sec"), int)):
                        raise HTTPException(422, "crop마다 sec(정수)와 bbox(4개)가 필요합니다.")
            (target / "compare.json").unlink(missing_ok=True)  # 다시 실행하면 이전 결과를 새 결과처럼 보여 주지 않는다
            update(target, compare_status="queued", compare_progress=0, compare_error=None)
            background.add_task(run_compare, target, [a for a in answers if a.get("decision") == "done"])
            return {"id": job_id, "compare_status": "queued"}

    @router.get("/{job_id}")
    def get_job(job_id: str):
        meta = _read_json(directory(job_id) / "result.json")
        return {k: v for k, v in meta.items() if k != "video_file"}

    @router.get("/{job_id}/index.json")
    def index(job_id: str):
        target = directory(job_id); meta = _read_json(target / "result.json")
        if not (target / "data.json").is_file():
            raise HTTPException(404, "후보가 아직 없습니다.")
        return [{"key": VIDEO_KEY, "title": meta["title"], "refs": [r["name"] for r in meta["logos"]], "seconds": meta.get("seconds", 0),
                 "missing": len(meta.get("missing_seconds", []))}]

    @router.get(f"/{{job_id}}/{VIDEO_KEY}/data.json")
    def data(job_id: str):
        path = directory(job_id) / "data.json"
        if not path.is_file():
            raise HTTPException(404, "후보가 아직 없습니다.")
        return _read_json(path)

    @router.get(f"/{{job_id}}/{VIDEO_KEY}/f/{{sec}}.jpg")
    def frame(job_id: str, sec: int):
        path = directory(job_id) / "frames" / "f" / f"{sec}.jpg"
        if not path.is_file():
            raise HTTPException(404, "프레임이 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    @router.get("/{job_id}/refs/{index_no}.png")
    def ref_image(job_id: str, index_no: int):
        path = directory(job_id) / "refs" / f"{index_no}.png"
        if not path.is_file():
            raise HTTPException(404, "원본 로고가 없습니다.")
        return FileResponse(path, media_type="image/png")

    @router.get("/{job_id}/compare")
    def compare_result(job_id: str):
        target = directory(job_id); meta = _read_json(target / "result.json")
        body = _read_json(target / "compare.json") if (target / "compare.json").is_file() else {"bundles": []}
        return {"compare_status": meta.get("compare_status"), "compare_progress": meta.get("compare_progress"),
                "compare_error": meta.get("compare_error"), **body}

    @router.get("/{job_id}/compare/rep/{logo}/{crop}.jpg")
    def representative(job_id: str, logo: int, crop: int):
        path = directory(job_id) / "frames" / f"rep_{logo}_{crop}.jpg"
        if not path.is_file():
            raise HTTPException(404, "대표 프레임이 없습니다.")
        return FileResponse(path, media_type="image/jpeg")

    return router
