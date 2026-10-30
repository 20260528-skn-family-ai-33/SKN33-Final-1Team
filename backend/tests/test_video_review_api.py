"""숏폼 동영상 검수(계획 문서 0~7단계). OCR은 주입하고 ffmpeg로 만든 작은 합성 영상만 쓴다."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
from fastapi.testclient import TestClient

from backend.app import api as backend
from backend.app import video_frames, video_review

FFMPEG = video_frames.resolve_ffmpeg()
pytestmark = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg(imageio-ffmpeg) is required to build test videos")
SUBTITLE = "2월 한정 50% 할인"


def make_video(path: Path, seconds=6, size="640x360") -> bytes:
    subprocess.run([FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=10",
                    "-t", str(seconds), "-pix_fmt", "yuv420p", str(path)], check=True)
    return path.read_bytes()


def make_webm_video_shorter_than_audio(path: Path, *, video_s=6, audio_s=7, size="640x360") -> bytes:
    """영상이 오디오보다 짧은 WebM(영상 video_s초·오디오 audio_s초). 인코더가 없으면 테스트를 건너뛴다."""
    result = subprocess.run(
        [FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=rate=10:size={size}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={audio_s}",
         "-filter_complex", f"[0:v]trim=0:{video_s},setpts=PTS-STARTPTS[v]",
         "-map", "[v]", "-map", "1:a", "-c:v", "libvpx-vp9", "-b:v", "200k", "-c:a", "libopus", str(path)],
        capture_output=True)
    if result.returncode != 0:
        pytest.skip("WebM(VP9/Opus) 인코더가 없어 건너뜁니다")
    return path.read_bytes()


def fake_ocr(calls, *, fail=(), ad_text="#광고"):
    """하단 중앙 고정 자막(한 프레임은 '할언'으로 오인식) + 움직이는 저신뢰 간판 + 상단 광고 표시."""
    def recognize(frames, directory, progress):
        calls.append([p.stem for p in frames])
        out = []
        for index, frame in enumerate(frames):
            if frame.stem in fail:
                out.append({"status": "failed", "records": []})
                continue
            text = SUBTITLE.replace("할인", "할언") if frame.stem == "t002000" else SUBTITLE
            records = [{"id": "paddle_000", "text": text, "confidence": .97, "bbox_xyxy": [128, 288, 512, 324]},
                       {"id": "paddle_001", "text": "식당", "confidence": .3, "bbox_xyxy": [10 + index * 9, 10, 60 + index * 9, 30]}]
            if ad_text:
                records.append({"id": "paddle_002", "text": ad_text, "confidence": .95, "bbox_xyxy": [20, 40, 90, 60]})
            out.append({"status": "ok", "records": records})
            progress(index + 1)
        return {"frames": out, "load_seconds": 1.0, "inference_seconds": [2.0] * len(frames)}
    return recognize


def make_app(tmp_path, *, ready=True, ocr=None, logo_review=None, logo_readiness=None):
    return backend.create_app(
        review_root=tmp_path / "reviews", video_root=tmp_path / "videos",
        recognizer=lambda path: {}, runtime_status=lambda: {"ready": True},
        video_ocr=ocr or fake_ocr([]), video_readiness=lambda: (ready, "준비됨" if ready else "Paddle 모델 없음"),
        video_logo_review=logo_review, video_logo_readiness=logo_readiness,
    )


class LoggedIn(TestClient):
    """localhost 접속으로 admin 로그인까지 마친 브라우저(영상 API는 로그인 세션을 요구한다)."""
    def __enter__(self):
        super().__enter__()
        token = self.get("/api/v1/auth/csrf").json()["csrf_token"]
        assert self.post("/api/v1/auth/login", json={"username": "admin", "password": "admin"},
                         headers={"X-CSRFToken": token, "Origin": "http://127.0.0.1"}).status_code == 200
        return self


def make_client(tmp_path, *, ready=True, ocr=None, logo_review=None, logo_readiness=None):
    return LoggedIn(make_app(tmp_path, ready=ready, ocr=ocr, logo_review=logo_review, logo_readiness=logo_readiness),
                    base_url="http://127.0.0.1", client=("127.0.0.1", 12345))


def upload(client, data, *, name="clip.mp4", criteria=None, disclosure=None, logo=None):
    form = {"criteria": json.dumps(criteria or []), "criteria_version": "1", "project_name": "숏폼 시험",
            "disclosure": json.dumps(disclosure or {})}
    files = {"video": (name, data, "video/mp4")}
    if logo is not None:
        files["logo"] = ("logo.png", logo, "image/png")
    return client.post("/api/video-reviews", data=form, files=files)


def png_bytes(color=(200, 30, 30)) -> bytes:
    import io

    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), color).save(buffer, format="PNG")
    return buffer.getvalue()


def fake_logo(verdict="detected"):
    """주입용 가짜 로고 검수. 파이프라인이 넘긴 base 프레임·저장한 쿼리 로고가 실재하는지 확인한다."""
    labels = {"detected": "확실", "review": "확인 필요", "absent": "미검출"}

    def review(frame_items, query_path):
        assert query_path.is_file()
        assert frame_items and all(item["path"].is_file() for item in frame_items)
        per_frame = [{"frame_id": item["frame_id"], "time_ms": item["time_ms"], "verdict": verdict,
                      "top_cosine": 0.95, "boxes": []} for item in frame_items]
        return {"status": "completed", "verdict": verdict, "verdict_label": labels[verdict],
                "summary": {"verdict": verdict, "verdict_label": labels[verdict], "frames": len(per_frame),
                            "detected_frames": len(per_frame) if verdict == "detected" else 0,
                            "review_frames": len(per_frame) if verdict == "review" else 0,
                            "detected_times": [], "review_times": [], "best_cosine": 0.95},
                "per_frame": per_frame}
    return review


def criterion(text, item_id="c1", kind="information"):
    return {"id": item_id, "text": text, "type": kind, "source": "user"}


def test_full_pipeline_matches_plan_stages(tmp_path):
    calls = []
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path, ocr=fake_ocr(calls)) as client:
        assert client.get("/api/video-reviews/status").json()["limits"]["duration_seconds"] == 180
        response = upload(client, data, name="../../etc/passwd.mp4",
                          criteria=[criterion(f'"{SUBTITLE}"'), criterion("'최고' 표현은 쓰지 마세요", "c2")],
                          disclosure={"kind": "endorsement", "title": "#광고 신상 소개"})
        assert response.status_code == 202
        job = client.get(f"/api/video-reviews/{response.json()['id']}").json()

    assert job["status"] == "completed" and job["progress"] == 100, job.get("error")
    stored = tmp_path / "videos" / job["id"]
    # 0단계: 원본 보존 · sha256 일치 · 사용자 파일명 미사용
    assert (stored / "original.bin").read_bytes() == data
    assert json.loads((stored / "media.json").read_text(encoding="utf-8"))["sha256"] == job["media"]["sha256"]
    assert not any("passwd" in str(path) for path in (tmp_path / "videos").rglob("*"))
    # 1단계: 1초 격자 + 시작·끝 0.2초 창, 검사 범위가 [0, 길이)를 덮음
    kinds = [f["sampling"] for f in job["frames"]]
    assert kinds.count("base") == 6 and kinds.count("head") == 12 and kinds.count("tail") == 12
    assert len(calls[0]) == 30 and all((stored / "frames" / f"{fid}.jpg").is_file() for fid in calls[0])
    cover = job["coverage"]
    points, gaps = cover["inspected_ms"], cover["uninspected"]
    assert points[0] == 0 and gaps[-1][1] == cover["duration_ms"]
    assert all(a == p for (a, _), p in zip(gaps, points))
    for name in ("frames.json", "coverage.json", "scenes.json", "ocr/frames.jsonl", "ocr/roles.jsonl",
                 "tracks.json", "segments.json", "review.json", "snapshot.json"):
        assert (stored / name).is_file(), name
    # 4단계: 오인식 한 프레임은 같은 트랙으로 묶고 variants로 남긴다(투표로 사라지는 문구 0)
    subtitle = [t for t in job["tracks"] if t["text"] == SUBTITLE]
    assert len(subtitle) == 1 and subtitle[0]["variants"][0]["text"].endswith("할언")
    assert all(t["text"] != "식당" for t in job["tracks"])  # 신뢰도 0.5 미만은 제외(첫 판 규칙)
    seg = job["segments"]
    assert seg[0]["start_ms"] == 0 and seg[-1]["end_ms"] == cover["duration_ms"]
    assert all(a["end_ms"] == b["start_ms"] for a, b in zip(seg, seg[1:]))
    assert all(s["rep_frame_id"] in s["frame_ids"] for s in seg if s["frame_ids"])
    # 6단계: 영상당 1회 판정, variants가 있으면 충족 대신 확인 필요, 금지 기준은 충족으로 뒤집히지 않음
    review = job["review"]
    assert review["calls"] == 1
    first, second = review["results"]
    assert first["status"] == "review" and first["mismatch"][0]["kind"] == "variants"
    assert first["evidence_segments"] and all(s["start_ms"] <= e["time_ms"] < s["end_ms"]
                                              for e in first["evidence_segments"] for s in seg if s["id"] == e["segment_id"])
    assert second["status"] == "review" and second["reviewer"] == "manager"
    # 6-A: 시작·중간·끝 모두 허용 문구 → 표시 문구·시간 통과, 시인성·가상인물 남아 전체는 확인 필요
    disclosure = job["disclosure"]
    assert disclosure["text_time_status"] == "pass" and disclosure["status"] == "review"
    assert job["double_check"]["status"] == "skipped"


def test_webm_audio_longer_than_video_extracts(tmp_path):
    """영상 6초·오디오 7초 WebM: 길이를 영상 끝(약 6초)으로 보고 프레임 추출까지 정상 완료한다.

    과다계산(이전 버그)이면 길이를 약 7초로 보고 plan_times가 영상 끝 이후 프레임을 요청해
    extracting 단계에서 실패했다.
    """
    data = make_webm_video_shorter_than_audio(tmp_path / "clip.webm")
    with make_client(tmp_path) as client:
        job = client.get(f"/api/video-reviews/{upload(client, data, name='clip.webm').json()['id']}").json()
    assert job["status"] == "completed", job.get("failed_stage") or job.get("error")
    assert job["media"]["container"] == "matroska"
    assert 5500 <= job["media"]["duration_ms"] <= 6200   # 영상 끝 기준(과다계산이면 약 7000)
    assert job["media"]["container_duration_ms"] >= 6800  # 컨테이너는 오디오 기준이라 더 길다


def test_self_ad_skips_edge_frames(tmp_path):
    calls = []
    with make_client(tmp_path, ocr=fake_ocr(calls)) as client:
        job_id = upload(client, make_video(tmp_path / "clip.mp4"), disclosure={"kind": "self"}).json()["id"]
        job = client.get(f"/api/video-reviews/{job_id}").json()
    assert len(calls[0]) == 6 and job["disclosure"]["status"] == "not_applicable"


def test_ocr_frame_failures(tmp_path):
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path, ocr=fake_ocr([], fail={"t001000"})) as client:
        job = client.get(f"/api/video-reviews/{upload(client, data).json()['id']}").json()
    assert job["status"] == "completed" and any("실패한 프레임" in w for w in job["warnings"])
    many = {f"t{t:06d}" for t in range(0, 3000, 200)} | {"t003000", "t004000"}
    with make_client(tmp_path, ocr=fake_ocr([], fail=many)) as client:
        job = client.get(f"/api/video-reviews/{upload(client, data).json()['id']}").json()
    assert job["status"] == "failed" and job["failed_stage"] == "ocr"


def test_avi_gets_playback_mp4(tmp_path):
    """AVI는 원본을 보존하고 재생용 MP4를 따로 만들어 /video로 내보낸다(브라우저 재생 호환)."""
    data = make_video(tmp_path / "clip.avi")
    assert data[:4] == b"RIFF" and data[8:12] == b"AVI "
    with make_client(tmp_path) as client:
        job_id = upload(client, data, name="clip.avi").json()["id"]
        job = client.get(f"/api/video-reviews/{job_id}").json()
        assert job["status"] == "completed" and job["media"]["container"] == "avi"
        assert job["playback"]["available"] is True
        stored = tmp_path / "videos" / job_id
        assert (stored / "original.bin").read_bytes() == data   # 원본은 그대로 보존
        assert (stored / "playback.mp4").is_file()
        video = client.get(f"/api/video-reviews/{job_id}/video")
        assert video.status_code == 200 and video.headers["content-type"] == "video/mp4"


def test_mp4_keeps_original_without_transcode(tmp_path):
    """브라우저 재생이 되는 MP4는 변환하지 않고 원본을 그대로 제공한다."""
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path) as client:
        job_id = upload(client, data).json()["id"]
        job = client.get(f"/api/video-reviews/{job_id}").json()
    assert job["playback"]["available"] is False
    assert not (tmp_path / "videos" / job_id / "playback.mp4").exists()


def test_video_range_and_frame_routes(tmp_path):
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path) as client:
        job_id = upload(client, data).json()["id"]
        part = client.get(f"/api/video-reviews/{job_id}/video", headers={"Range": "bytes=0-99"})
        assert part.status_code == 206 and part.content == data[:100]
        assert client.get(f"/api/video-reviews/{job_id}/frames/t001000").headers["content-type"] == "image/jpeg"
        assert client.get(f"/api/video-reviews/{job_id}/frames/..%2Fmedia").status_code == 404
        assert client.get(f"/api/video-reviews/{job_id}/frames/t099000").status_code == 404
        assert [item["id"] for item in client.get("/api/video-reviews").json()["items"]] == [job_id]
        assert client.get("/api/video-reviews/not-a-uuid").status_code == 404


@pytest.mark.parametrize("payload,status", [
    (b"", 400),
    (b"\x89PNG\r\n\x1a\n" + b"0" * 64, 415),
    (b"#EXTM3U\n#EXTINF:1,\nfile:///C:/Windows/win.ini\n", 415),  # 재생목록 위장은 ffmpeg에 넘기기 전에 거절
    (b"\x00\x00\x00\x18ftypmp42" + b"0" * 64, 415),
    (b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 64, 415),
    (b"RIFF\x24\x00\x00\x00AVI LIST" + b"0" * 64, 415),
])
def test_rejects_without_leftovers(tmp_path, payload, status):
    with make_client(tmp_path) as client:
        assert upload(client, payload).status_code == status
    assert list((tmp_path / "videos").iterdir()) == []


def test_limits_and_status_codes(tmp_path, monkeypatch):
    data = make_video(tmp_path / "clip.mp4", seconds=4)
    with make_client(tmp_path, ready=False) as client:
        assert upload(client, data).status_code == 503
    with make_client(tmp_path) as client:
        assert upload(client, data, criteria=[{"id": "x"}]).status_code == 422
        assert upload(client, data, disclosure={"kind": "maybe"}).status_code == 422
        assert upload(client, make_video(tmp_path / "small.mp4", size="320x240")).status_code == 422
    monkeypatch.setenv("VIDEO_MAX_SECONDS", "2")
    with make_client(tmp_path) as client:
        assert upload(client, data).status_code == 422
    monkeypatch.setenv("VIDEO_MAX_SECONDS", "180")
    monkeypatch.setenv("VIDEO_MAX_MB", "1")
    with make_client(tmp_path) as client:
        assert upload(client, b"\x00\x00\x00\x18ftypmp42" + b"0" * (2 * 1024 * 1024)).status_code == 413
    monkeypatch.setenv("VIDEO_MAX_MB", "500")
    with make_client(tmp_path) as client:  # 시작 시 복구가 끝난 뒤 실행 중 3건을 둔다 → 429
        for index in range(3):
            busy = tmp_path / "videos" / f"00000000-0000-4000-8000-00000000000{index}"
            busy.mkdir(parents=True)
            (busy / "result.json").write_text(json.dumps({"status": "running"}), encoding="utf-8")
        assert upload(client, data).status_code == 429


def test_avi_and_restart_recovery(tmp_path):
    data = make_video(tmp_path / "clip.avi")
    assert data[:4] == b"RIFF" and data[8:12] == b"AVI "

    def broken(frames, directory, progress):
        raise RuntimeError("secret path C:/models")
    with make_client(tmp_path, ocr=broken) as client:
        job = client.get(f"/api/video-reviews/{upload(client, data, name='clip.avi').json()['id']}").json()
    assert job["media"]["container"] == "avi"
    assert job["status"] == "failed" and job["failed_stage"] == "ocr" and "secret" not in json.dumps(job)
    stuck = tmp_path / "videos" / job["id"] / "result.json"
    stuck.write_text(json.dumps({**job, "status": "running", "stage": "ocr"}), encoding="utf-8")
    with make_client(tmp_path) as client:
        assert client.get(f"/api/video-reviews/{job['id']}").json()["status"] == "failed"


def test_plan_sizes_follow_document():
    assert [len(video_frames.plan_times(d)) for d in (30000, 60000, 90000)] == [54, 84, 114]
    base = [p["frame_id"] for p in video_frames.plan_times(60000) if p["sampling"] == "base"]
    assert base[0] == "t000000" and base[-1] == "t059000" and len(base) == 60


def test_video_api_requires_local_login(tmp_path):
    data = make_video(tmp_path / "clip.mp4")
    app = make_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 12345)) as anonymous:
        assert anonymous.get("/api/video-reviews/status").status_code == 401
        assert upload(anonymous, data).status_code == 401
    # 서버를 네트워크 주소에 바인딩해도 외부 접속에는 영상·결과 경로 자체를 열지 않는다
    with TestClient(app, base_url="http://192.168.0.10", client=("192.168.0.20", 12345)) as remote:
        assert remote.get("/api/video-reviews").status_code == 404
        assert upload(remote, data).status_code == 404
    assert list((tmp_path / "videos").iterdir()) == []


def test_screen_criteria_payload_is_accepted(tmp_path):
    """화면은 parseRequirements 결과에서 id·text·type·source만 보낸다(selected를 보내면 422)."""
    data = make_video(tmp_path / "clip.mp4")
    screen = [{"id": "criterion-1", "text": "행사명: 김천김밥축제", "type": "information", "source": "행사명: 김천김밥축제"}]
    with make_client(tmp_path) as client:
        assert upload(client, data, criteria=[{**screen[0], "selected": True}]).status_code == 422
        assert upload(client, data, criteria=screen).status_code == 202


def test_concurrent_submissions_cannot_exceed_limit(tmp_path, monkeypatch):
    import threading
    import time as clock
    monkeypatch.setenv("VIDEO_MAX_ACTIVE", "1")
    slow_probe = video_review.probe

    def probe(*args):  # 두 요청이 한도 검사를 지난 뒤 같은 구간에 머물게 한다
        clock.sleep(1)
        return slow_probe(*args)
    monkeypatch.setattr(video_review, "probe", probe)
    release = threading.Event()

    def blocking_ocr(frames, directory, progress):
        release.wait(10)
        return fake_ocr([])(frames, directory, progress)
    data = make_video(tmp_path / "clip.mp4")
    codes = []
    with make_client(tmp_path, ocr=blocking_ocr) as client:
        workers = [threading.Thread(target=lambda: codes.append(upload(client, data).status_code)) for _ in range(2)]
        for worker in workers:
            worker.start()
        clock.sleep(3)
        release.set()
        for worker in workers:
            worker.join(30)
    assert sorted(codes) == [202, 429]


def test_logo_match_skipped_without_logo(tmp_path):
    """지정 로고를 올리지 않으면 로고 블록은 skipped이고 OCR 검수는 그대로 완료된다."""
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path) as client:
        status = client.get("/api/video-reviews/status").json()
        assert "logo" in status and "ready" in status["logo"]  # 로고 준비상태는 정보용(전체 ready 비게이팅)
        job = client.get(f"/api/video-reviews/{upload(client, data).json()['id']}").json()
    assert job["status"] == "completed" and job["logo_query"] is False
    assert job["logo_match"]["status"] == "skipped"


def test_logo_match_runs_when_logo_uploaded(tmp_path):
    """지정 로고를 올리면 base 프레임을 대조해 logo_match가 채워지고, OCR 결과는 그대로 유지된다."""
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path, logo_review=fake_logo("detected")) as client:
        job_id = upload(client, data, logo=png_bytes()).json()["id"]
        job = client.get(f"/api/video-reviews/{job_id}").json()
    assert job["status"] == "completed" and job["logo_query"] is True
    block = job["logo_match"]
    assert block["status"] == "completed" and block["verdict"] == "detected"
    assert block["per_frame"] and all(frame["verdict"] == "detected" for frame in block["per_frame"])
    assert job["counts"]["frames"] > 0 and "results" in job["review"]  # OCR 검수는 로고와 무관하게 수행됨
    stored = tmp_path / "videos" / job_id
    assert (stored / "logo_query.bin").is_file() and (stored / "logo.json").is_file()


def test_logo_failure_keeps_ocr_review(tmp_path):
    """로고 검수가 예외로 실패해도 OCR 검수는 완료되고 경고만 남는다."""
    def broken_logo(frame_items, query_path):
        raise RuntimeError("endpoint down")
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path, logo_review=broken_logo) as client:
        job = client.get(f"/api/video-reviews/{upload(client, data, logo=png_bytes()).json()['id']}").json()
    assert job["status"] == "completed" and job["logo_match"]["status"] == "failed"
    assert any("로고 검수" in w for w in job["warnings"])


def test_logo_rejects_non_image(tmp_path):
    """이미지가 아닌 로고 업로드는 415로 거절하고 저장 폴더를 남기지 않는다."""
    data = make_video(tmp_path / "clip.mp4")
    with make_client(tmp_path) as client:
        assert upload(client, data, logo=b"not an image at all").status_code == 415
    assert list((tmp_path / "videos").iterdir()) == []


def test_read_only_mode_reports_not_ready(tmp_path, monkeypatch):
    (tmp_path / "reviews").mkdir()
    monkeypatch.setenv("LEGACY_READ_ONLY", "1")
    monkeypatch.setenv("APP_ENV", "development")
    with make_client(tmp_path) as client:
        status = client.get("/api/video-reviews/status").json()
        assert status["ready"] is False and status["readonly"] is True and "읽기 전용" in status["detail"]
        assert upload(client, make_video(tmp_path / "clip.mp4")).status_code == 405
