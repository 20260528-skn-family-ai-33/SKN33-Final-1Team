"""숏폼 검수 0~2단계 — 영상 검사, 표본 시각 프레임 추출, 검사 범위 기록, 장면 경계.

계획 문서(숏폼_검수_최종판 2026-10-20)의 규칙을 따른다.
- 표본 시각: 1초 간격 전체 + 시작·끝 3초는 0.2초 간격(광고 표시 검사용).
- 시각 k의 프레임은 'pts가 k 이하인 마지막 프레임'(ffmpeg fps=5:round=up 슬롯과 같음).
- 파일명 t012000.jpg는 요청한 표본 시각이다. 긴 변 1920 이하, JPEG 품질 90.
문서는 PyAV(av==19.0.1)를 권하지만 이 시연 환경에는 없으므로 같은 규칙을 ffmpeg로 구현한다.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import re
import shutil
import subprocess

BASE_MS = 1000       # 1초 격자
EDGE_MS = 200        # 시작·끝 창 간격
EDGE_WINDOW_MS = 3000
SLOT_FPS = 5         # 0.2초 슬롯 = fps 5
JPEG_QUALITY = 90
FRAME_SIDE = 1920


def resolve_ffmpeg() -> str | None:
    """FFMPEG_BIN → PATH의 ffmpeg → imageio-ffmpeg 순으로 찾는다."""
    configured = os.getenv("FFMPEG_BIN")
    if configured:
        return configured if Path(configured).is_file() else None
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def sniff_container(head: bytes) -> str | None:
    """파일 머리 바이트로 허용 컨테이너만 고른다. 확장자·클라이언트 MIME은 믿지 않는다.

    ffmpeg에 형식을 자동 판별시키면 업로드한 재생목록(m3u8·concat)이 다른 파일이나 URL을
    읽게 만들 수 있으므로, 여기서 고른 demuxer만 명시해 쓴다."""
    if len(head) >= 12 and head[4:8] in (b"ftyp", b"moov", b"mdat", b"wide", b"free", b"skip"):
        return "mov"  # MP4·MOV·M4V
    if head[:4] == b"\x1a\x45\xdf\xa3":
        return "matroska"  # WEBM·MKV
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return "avi"  # 같은 RIFF라도 WAV·WEBP는 제외
    return None


def _input_args(ffmpeg: str, demuxer: str, path: Path) -> list[str]:
    return [ffmpeg, "-nostdin", "-hide_banner", "-protocol_whitelist", "file", "-f", demuxer, "-i", str(path)]


def _clock_ms(hours: str, minutes: str, seconds: str) -> int:
    return int(round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000))


def probe(ffmpeg: str, demuxer: str, path: Path) -> dict:
    """ffprobe 없이 ffmpeg 입력 정보에서 코덱·해상도·fps·회전·HDR을 읽고, 길이는 영상 스트림 기준으로 잰다.

    WebM은 오디오가 길면 컨테이너 길이가 틀리므로(문서 0단계) 영상 스트림만 복사해 끝 시각을 얻는다."""
    result = subprocess.run(_input_args(ffmpeg, demuxer, path), capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    text = result.stderr.decode("utf-8", errors="replace")
    stream = re.search(r"Stream #\d+:\d+[^\n]*?: Video: ([A-Za-z0-9_]+)[^\n]*?(\d{2,5})x(\d{2,5})[^\n]*", text)
    container = re.search(r"Duration: (\d+):(\d{2}):(\d{2}(?:\.\d+)?)", text)
    if not stream:
        raise ValueError("No video stream")
    line = stream.group(0)
    fps = re.search(r"([\d.]+) fps", line) or re.search(r"([\d.]+) tbr", line)
    rotation = re.search(r"rotation of (-?[\d.]+) degrees", text)
    degrees = int(round(float(rotation.group(1)))) % 360 if rotation else 0
    width, height = int(stream.group(2)), int(stream.group(3))
    if degrees in (90, 270):
        width, height = height, width  # 화면에 보이는 방향 기준
    copied = subprocess.run(_input_args(ffmpeg, demuxer, path) + ["-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
                            capture_output=True, timeout=120, stdin=subprocess.DEVNULL)
    times = re.findall(r"time=(\d+):(\d{2}):(\d{2}(?:\.\d+)?)", copied.stderr.decode("utf-8", errors="replace"))
    container_ms = _clock_ms(*container.groups()) if container else None
    duration_ms = container_ms or 0
    if times:  # 마지막 패킷의 시작 시각이므로 한 프레임 길이를 더하고 컨테이너 길이를 넘지 않게 한다
        frame_ms = 1000 / float(fps.group(1)) if fps else 0
        duration_ms = int(round(_clock_ms(*times[-1]) + frame_ms))
        if container_ms:
            duration_ms = min(duration_ms, container_ms)
    return {
        "codec": stream.group(1), "width": width, "height": height,
        "fps": float(fps.group(1)) if fps else None, "rotation": degrees,
        "hdr": bool(re.search(r"bt2020|smpte2084|arib-std-b67", line)),
        "duration_ms": duration_ms,
        "container_duration_ms": container_ms,
        "container": demuxer,
    }


# ---------------------------------------------------------------------------
# 1단계 — 표본 시각과 검사 범위
# ---------------------------------------------------------------------------
def plan_times(duration_ms: int, *, edges: bool = True) -> list[dict]:
    """1초 격자 전체와 (광고 표시 검사 대상이면) 시작·끝 3초의 0.2초 슬롯. 겹치는 시각은 1초 쪽으로."""
    times = {t: "base" for t in range(0, duration_ms, BASE_MS)}
    if edges:
        for t in range(0, min(EDGE_WINDOW_MS, duration_ms), EDGE_MS):
            times.setdefault(t, "head")
        tail_start = max(0, duration_ms - EDGE_WINDOW_MS)
        for t in range(-(-tail_start // EDGE_MS) * EDGE_MS, duration_ms, EDGE_MS):
            times.setdefault(t, "tail")
    return [{"time_ms": t, "sampling": kind, "frame_id": f"t{t:06d}"} for t, kind in sorted(times.items())]


def coverage(plan: list[dict], duration_ms: int) -> dict:
    """검사한 시각(점)과 그 사이 미검사 구간(열린 구간)을 합치면 [0, 영상 길이)를 빈틈없이 덮는다."""
    inspected = [item["time_ms"] for item in plan]
    gaps = [[a, b] for a, b in zip(inspected, inspected[1:]) if b - a > 0]
    if inspected and inspected[-1] < duration_ms:
        gaps.append([inspected[-1], duration_ms])
    return {"duration_ms": duration_ms, "inspected_ms": inspected, "uninspected": gaps,
            "intervals_ms": {"base": BASE_MS, "edge": EDGE_MS, "edge_window": EDGE_WINDOW_MS},
            "note": "1초 간격 표본 검사 · 시작·끝 3초는 0.2초 간격"}


def extract_frames(ffmpeg: str, demuxer: str, path: Path, out_dir: Path, plan: list[dict]) -> list[dict]:
    """한 번 디코딩해 계획한 슬롯만 골라 JPEG(q90)로 저장한다. 마지막 슬롯이 없으면(잘린 파일) 실패."""
    from PIL import Image

    out_dir.mkdir()
    staging = out_dir / "_slots"
    staging.mkdir()
    slots = [item["time_ms"] // EDGE_MS for item in plan]
    keep = "+".join(f"eq(n,{slot})" for slot in slots)
    scale = f"min(1,{FRAME_SIDE}/max(iw,ih))"
    vf = (f"fps={SLOT_FPS}:round=up,select='{keep}',"
          f"scale=w='trunc({scale}*iw/2)*2':h='trunc({scale}*ih/2)*2'")
    command = _input_args(ffmpeg, demuxer, path) + ["-an", "-sn", "-dn", "-vf", vf, "-fps_mode", "passthrough",
                                                     "-loglevel", "error", str(staging / "%05d.png")]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=600, stdin=subprocess.DEVNULL)
        produced = sorted(staging.glob("*.png"))
        if len(produced) != len(plan):
            raise ValueError(f"Expected {len(plan)} frames, got {len(produced)}")
        frames, seen = [], {}
        for item, source in zip(plan, produced):
            with Image.open(source) as image:
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=JPEG_QUALITY)
                width, height = image.size
            data = buffer.getvalue()
            digest = hashlib.sha256(data).hexdigest()
            (out_dir / f"{item['frame_id']}.jpg").write_bytes(data)
            frames.append({**item, "source_pts_ms": None, "width": width, "height": height,
                           "sha256": digest, "same_as": seen.get(digest)})
            seen.setdefault(digest, item["frame_id"])
        return frames
    finally:
        shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# 2단계 — 장면 경계 (실패해도 검수는 계속)
# ---------------------------------------------------------------------------
def detect_scenes(path: Path, base_times: list[int]) -> dict:
    """PySceneDetect가 있으면 하드컷 후보를 찾고, 없거나 실패하면 상태만 남기고 넘어간다."""
    try:
        from scenedetect import AdaptiveDetector, detect  # scenedetect-headless==0.7.2 (문서 권장)
    except ImportError:
        return {"status": "skipped", "reason": "PySceneDetect 미설치 — 자막 트랙 변화로만 구간을 나눕니다.",
                "cut_ms": [], "grid_ms": []}
    try:
        scenes = detect(str(path), AdaptiveDetector(min_scene_len="0.5s"), backend="pyav")
        cuts = sorted({int(round(start.get_seconds() * 1000)) for start, _ in scenes[1:]})
    except Exception:
        return {"status": "failed", "reason": "장면 검출에 실패해 경고만 남기고 계속합니다.", "cut_ms": [], "grid_ms": []}
    grid = []
    for cut in cuts:  # 컷 직후 첫 1초 프레임을 경계로, 1초 안의 컷 두 개는 하나로
        after = next((t for t in base_times if t >= cut), None)
        if after is not None and (not grid or after - grid[-1] >= BASE_MS):
            grid.append(after)
    return {"status": "completed", "cut_ms": cuts, "grid_ms": grid}
