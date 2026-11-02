"""Inspect actual media and prepare bounded RGB JPEG observations, never originals."""

from __future__ import annotations

import io
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import warnings as python_warnings
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 10_000_000
MAX_VIDEO_BYTES = 100 * 1024 * 1024
MAX_VIDEO_SECONDS = 60
MAX_VIDEO_FRAMES = 12
DERIVED_LONG_EDGE = 1280
FFPROBE_TIMEOUT = 20
FFMPEG_FRAME_TIMEOUT = 20
PREPARE_TIMEOUT = 120
IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}
EXTENSIONS = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


class MediaError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _binary(name: str) -> str:
    configured = os.environ.get(f"{name.upper()}_BINARY")
    if name == "ffmpeg" and not configured:
        configured = os.environ.get("IMAGEIO_FFMPEG_EXE")
    if configured:
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        candidate = Path(configured)
        if candidate.is_file():
            return str(candidate.resolve())
        raise MediaError("media_binary_missing", f"설정한 {name} 실행 파일이 없습니다.")
    installed = shutil.which(name)
    if installed:
        return installed
    if name == "ffmpeg":
        try:
            import imageio_ffmpeg
            candidate = imageio_ffmpeg.get_ffmpeg_exe()
            if Path(candidate).is_file():
                return candidate
        except (ImportError, RuntimeError):
            pass
    raise MediaError("media_binary_missing", f"{name} 실행 파일이 필요합니다. 설치 경로나 환경변수로 지정해야 합니다.")


def _run(command: list[str], *, timeout: float) -> subprocess.CompletedProcess:
    try:
        completed = subprocess.run(
            command, capture_output=True, timeout=timeout, check=False,
            stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaError("media_timeout", "미디어 처리 시간이 제한을 넘었습니다.") from exc
    except OSError as exc:
        raise MediaError("media_binary_error", "미디어 처리 실행 파일을 시작하지 못했습니다.") from exc
    # Decoder messages may contain input names or metadata: never expose them to logs/errors.
    if len(completed.stdout) > 1024 * 1024 or len(completed.stderr) > 1024 * 1024:
        raise MediaError("media_output_limit", "미디어 디코더 출력이 한도를 넘었습니다.")
    if completed.returncode:
        raise MediaError("invalid_media", "미디어를 디코딩할 수 없습니다.")
    return completed


def _image(data: bytes, filename: str) -> tuple[Image.Image, dict]:
    if len(data) > MAX_IMAGE_BYTES:
        raise MediaError("image_size_limit", "이미지는 20MiB 이하여야 합니다.")
    try:
        with python_warnings.catch_warnings():
            python_warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                mime = IMAGE_FORMATS.get(source.format)
                suffix = Path(filename).suffix.lower()
                if mime is None or EXTENSIONS.get(suffix) != mime:
                    raise MediaError("invalid_image_format", "실제 이미지 형식과 확장자가 일치하는 PNG/JPEG/WEBP가 필요합니다.")
                if source.width < 1 or source.height < 1 or source.width * source.height > MAX_IMAGE_PIXELS:
                    raise MediaError("image_pixel_limit", "이미지는 1000만 픽셀 이하여야 합니다.")
                if getattr(source, "n_frames", 1) != 1:
                    raise MediaError("animated_image_unsupported", "움직이는 이미지는 지원하지 않습니다.")
                source.verify()
            with Image.open(io.BytesIO(data)) as source:
                source.load()
                oriented = ImageOps.exif_transpose(source)
                image = oriented.copy()
                meta = {
                    "kind": "poster", "actual_mime": mime, "mime_type": mime,
                    "width": image.width, "height": image.height,
                    "duration": None, "duration_ms": None, "size_bytes": len(data),
                    "orientation_applied": oriented.size != source.size or bool(source.getexif().get(274, 1) != 1),
                }
                return image, meta
    except MediaError:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning, OSError, SyntaxError, ValueError) as exc:
        raise MediaError("invalid_image", "이미지 파일이 손상되었거나 올바른 형식이 아닙니다.") from exc


def _number(value) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _video(data: bytes, filename: str) -> dict:
    if len(data) > MAX_VIDEO_BYTES:
        raise MediaError("video_size_limit", "영상은 100MiB 이하여야 합니다.")
    suffix = Path(filename).suffix.lower()
    if suffix == ".mp4":
        if len(data) < 16 or data[4:8] != b"ftyp":
            raise MediaError("invalid_video_format", "올바른 MP4 파일 시그니처가 필요합니다.")
        mime = "video/mp4"
    elif suffix == ".webm":
        if not data.startswith(b"\x1a\x45\xdf\xa3") or b"\x42\x82\x84webm" not in data[:4096]:
            raise MediaError("invalid_video_format", "올바른 WebM 파일 시그니처가 필요합니다.")
        mime = "video/webm"
    else:
        raise MediaError("unsupported_video", "MP4와 WebM 영상만 지원합니다.")
    with tempfile.TemporaryDirectory(prefix="creative-probe-") as folder:
        source = Path(folder) / f"input{suffix}"
        source.write_bytes(data)
        output = _run([
            _binary("ffprobe"), "-v", "error", "-protocol_whitelist", "file,pipe",
            "-show_entries", "format=format_name,duration,start_time:stream=index,codec_type,codec_name,width,height,duration,start_time:stream_tags=rotate:stream_side_data=rotation:stream_disposition=attached_pic",
            "-of", "json", str(source),
        ], timeout=FFPROBE_TIMEOUT)
    try:
        probe = json.loads(output.stdout)
        videos = [item for item in probe["streams"] if item.get("codec_type") == "video" and not item.get("disposition", {}).get("attached_pic")]
        if len(videos) != 1:
            raise MediaError("unsupported_video_streams", "하나의 영상 스트림이 있는 파일이 필요합니다.")
        stream = videos[0]
        format_info = probe["format"]
        formats = format_info.get("format_name", "").split(",")
        if (mime == "video/mp4" and "mp4" not in formats) or (mime == "video/webm" and "webm" not in formats):
            raise MediaError("invalid_video_format", "FFprobe 검사 결과가 파일 형식과 다릅니다.")
        durations = [_number(value) for value in (format_info.get("duration"), stream.get("duration"))]
        duration = max((value for value in durations if value is not None), default=0)
        if not 0 < duration <= MAX_VIDEO_SECONDS:
            raise MediaError("video_duration_limit", "영상 길이를 확인할 수 없거나 60초를 넘습니다.")
        width, height = int(stream["width"]), int(stream["height"])
        rotation = _number(stream.get("tags", {}).get("rotate", 0)) or 0
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotation = _number(side["rotation"])
                if rotation is None:
                    raise MediaError("invalid_video_rotation", "영상 회전 정보를 해석할 수 없습니다.")
        if rotation % 90:
            raise MediaError("unsupported_video_rotation", "90도 단위의 영상 회전만 지원합니다.")
        if int(rotation) % 180:
            width, height = height, width
        if min(width, height) < 1 or max(width, height) > 1920 or min(width, height) > 1080:
            raise MediaError("video_resolution_limit", "영상은 긴 변 1920, 짧은 변 1080 이하여야 합니다.")
        return {
            "kind": "video", "actual_mime": mime, "mime_type": mime,
            "width": width, "height": height, "duration": duration,
            "duration_ms": math.ceil(duration * 1000), "size_bytes": len(data),
            "codec": stream.get("codec_name"), "stream_index": stream["index"],
            "start_time": _number(stream.get("start_time")) or _number(format_info.get("start_time")) or 0,
            "rotation": rotation,
        }
    except MediaError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MediaError("invalid_video_metadata", "영상 메타데이터를 해석할 수 없습니다.") from exc


def inspect_asset(data: bytes, filename: str, kind: str) -> dict:
    if not isinstance(data, bytes) or not data:
        raise MediaError("empty_media", "미디어 내용이 비어 있습니다.")
    if not isinstance(filename, str) or not filename:
        raise MediaError("invalid_filename", "미디어 파일명이 필요합니다.")
    if kind == "poster":
        image, metadata = _image(data, filename)
        image.close()
        return metadata
    if kind == "video":
        return _video(data, filename)
    raise MediaError("unsupported_asset_kind", "미디어 종류는 poster 또는 video여야 합니다.")


def _jpeg(image: Image.Image, destination: Path) -> tuple[int, int]:
    image.thumbnail((DERIVED_LONG_EDGE, DERIVED_LONG_EDGE), Image.Resampling.LANCZOS)
    rgba = image.convert("RGBA")
    rgb = Image.new("RGB", rgba.size, "white")
    rgb.paste(rgba, mask=rgba.getchannel("A"))
    rgb.save(destination, "JPEG", quality=92, optimize=True)
    size = rgb.size
    rgba.close()
    rgb.close()
    return size


def prepare_asset(data: bytes, filename: str, kind: str, work_dir: Path) -> dict:
    """At most 12 video frames. Coverage is point sampling, never continuous exposure."""
    import time

    started = time.monotonic()
    metadata = inspect_asset(data, filename, kind)
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []
    frames: list[dict] = []
    try:
        if kind == "poster":
            image, _ = _image(data, filename)
            try:
                source_width, source_height = image.size
                regions = [("full", (0, 0, source_width, source_height))]
                # Three overlapping half-width/half-height crops improve small-print
                # resolution while the full view retains layout context in one batch.
                horizontal = source_width >= source_height
                length = source_width if horizontal else source_height
                span = max(1, math.ceil(length / 2))
                for fraction in (0, 0.5, 1):
                    start = round((length - span) * fraction)
                    box = (start, 0, start + span, source_height) if horizontal else (0, start, source_width, start + span)
                    regions.append(("crop", box))
                for role, box in regions:
                    destination = work_dir / f"poster-{uuid4().hex}.jpg"
                    generated.append(destination)
                    region = image.crop(box)
                    try:
                        width, height = _jpeg(region, destination)
                    finally:
                        region.close()
                    left, top, right, bottom = box
                    frames.append({
                        "path": str(destination), "frame_ms": None, "width": width, "height": height,
                        "media_type": "image/jpeg", "role": role,
                        "source_bbox": [left / source_width, top / source_height, (right - left) / source_width, (bottom - top) / source_height],
                        "source_width": source_width, "source_height": source_height,
                    })
            finally:
                image.close()
            return {"frames": frames, "coverage": {"kind": "poster", "complete": True, "sampled_frame_ms": [], "uninspected_intervals": []}, "warnings": [], "metadata": metadata}

        duration_ms = metadata["duration_ms"]
        count = min(MAX_VIDEO_FRAMES, max(2, math.ceil(metadata["duration"] / 5) + 1))
        # The final target is slightly before the end so it can produce a real frame.
        last_ms = max(0, duration_ms - min(250, duration_ms // 2))
        requested = sorted({round(index * last_ms / (count - 1)) for index in range(count)})
        ffmpeg = _binary("ffmpeg")
        with tempfile.TemporaryDirectory(prefix="creative-frames-") as folder:
            source = Path(folder) / f"input{Path(filename).suffix.lower()}"
            source.write_bytes(data)
            for target in requested:
                remaining = PREPARE_TIMEOUT - (time.monotonic() - started)
                if remaining <= 0:
                    raise MediaError("media_timeout", "영상 전처리 전체 시간이 제한을 넘었습니다.")
                destination = work_dir / f"frame-{uuid4().hex}.jpg"
                generated.append(destination)
                output = _run([
                    ffmpeg, "-hide_banner", "-nostdin", "-v", "info", "-protocol_whitelist", "file,pipe",
                    "-ss", f"{target / 1000:.6f}", "-copyts", "-i", str(source),
                    "-map", f"0:{metadata['stream_index']}", "-an", "-sn", "-dn", "-frames:v", "1",
                    "-vf", f"scale=w='min({DERIVED_LONG_EDGE},iw)':h='min({DERIVED_LONG_EDGE},ih)':force_original_aspect_ratio=decrease,showinfo",
                    "-fps_mode", "passthrough", "-q:v", "2", "-update", "1", str(destination),
                ], timeout=min(FFMPEG_FRAME_TIMEOUT, remaining))
                stamps = re.findall(rb"\bn:\s*0\s+pts:\s*-?\d+\s+pts_time:\s*(-?[0-9.eE+]+)", output.stderr)
                if not stamps or not destination.is_file():
                    raise MediaError("missing_frame_timestamp", "디코딩한 프레임의 실제 시각을 확인할 수 없습니다.")
                stamp = _number(stamps[0])
                if stamp is None:
                    raise MediaError("missing_frame_timestamp", "프레임 시각이 올바르지 않습니다.")
                actual_ms = round((stamp - metadata["start_time"]) * 1000)
                if actual_ms < 0 or actual_ms >= duration_ms or abs(actual_ms - target) > 1500:
                    raise MediaError("invalid_frame_timestamp", "실제 프레임 시각이 요청 구간과 일치하지 않습니다.")
                with Image.open(destination) as image:
                    image.load()
                    width, height = _jpeg(image, destination)
                if any(frame["frame_ms"] == actual_ms for frame in frames):
                    destination.unlink()
                    generated.remove(destination)
                    continue
                frames.append({"path": str(destination), "frame_ms": actual_ms, "requested_ms": target, "width": width, "height": height, "media_type": "image/jpeg", "role": "frame", "source_bbox": [0, 0, 1, 1], "source_width": metadata["width"], "source_height": metadata["height"]})
        samples = sorted(frame["frame_ms"] for frame in frames)
        points = sorted(set([0, *samples, duration_ms]))
        return {
            "frames": frames, "metadata": metadata,
            "coverage": {
                "kind": "video", "complete": False, "method": "point_samples",
                "duration_ms": duration_ms, "sampled_frame_ms": samples,
                "requested_frame_ms": requested,
                "uninspected_intervals": [{"start_ms": start, "end_ms": end} for start, end in zip(points, points[1:]) if end > start],
                "continuous_exposure_checked": False,
            },
            "warnings": ["video_is_sampled_unobserved_intervals_require_review", "audio_and_exact_exposure_duration_not_inspected"],
        }
    except Exception:
        for path in generated:
            path.unlink(missing_ok=True)
        raise
