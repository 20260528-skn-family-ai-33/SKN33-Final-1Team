"""명시적인 준비 명령에서만 고정 revision의 Paddle 모델을 다운로드한다."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL = "korean_PP-OCRv5_mobile_rec"
REVISION = "c02ecaf1f22bfd1c618cce154fd19185b47e663a"
DETECTION_MODEL = "PP-OCRv5_server_det"
ENGLISH_MODEL = 'en_PP-OCRv5_mobile_rec'
FILES = ("inference.json", "inference.pdiparams", "inference.yml")
# 공식 저장소의 고정 commit 파일 해시이며 실행 중 새 모델을 받지 않는다.
MODEL_SPECS = {
    ENGLISH_MODEL: {'revision': '267c36e24c331595590fe7bd72bde2436fd286f2', 'sha256': {
        'inference.json': 'fd1b6ec722ea841a72d3ba43e527df1d1066d5d7808e0503ee3eec7265188753',
        'inference.pdiparams': '3ec8a97ed6cefe8568d3e2ee90bb193299b566a7661aa4fd52d224b96b59f66b',
        'inference.yml': '27e91d0582f40168aa218303c76e184bc78fa7a5d105aad0cfbad8458b441067',
    }},
    MODEL: {"revision": REVISION, "sha256": {
        "inference.json": "562404e3c590c50c93778d5f0a94df21b47b5ab8f3ea6d47c7f8a7930c3bc844",
        "inference.pdiparams": "cac3e5f12cf04aaa77f6a5bc704e4e736ef2908476551891d84b41b4e9090462",
        "inference.yml": "f757fa1c40e99edcf27e9cce879b93eb2a51fa46f5ef39095689b8c37dd75998",
    }},
    DETECTION_MODEL: {"revision": "ca867c897ecbca8873081573a802ad70d499cb94", "sha256": {
        "inference.json": "af5876933d8806a1b50d895867e0781e135cd92ff37381992828fc8d1b842d28",
        "inference.pdiparams": "183146fe9d9910352f68482f623bcbbb9fa7b9e8fa1463b9ad288cef00524d2d",
        "inference.yml": "28fb721efc3634fc8aa677e474b9602cb815a91cf569ef357a7a553d7b3ce685",
    }},
}


def file_hash(path):
    """큰 가중치를 메모리에 한꺼번에 올리지 않고 SHA-256을 계산한다."""
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_model(directory, model):
    """manifest와 실제 파일 모두 고정된 공식 모델 식별자·해시와 비교한다."""
    directory = Path(directory)
    spec = MODEL_SPECS[model]
    identity = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    if (not isinstance(identity, dict) or identity.get("model") != model
            or identity.get("repository") != f"PaddlePaddle/{model}"
            or identity.get("revision") != spec["revision"] or identity.get("sha256") != spec["sha256"]):
        raise ValueError("Paddle model manifest mismatch")
    for name, expected in spec["sha256"].items():
        if file_hash(directory / name) != expected:
            raise ValueError("Paddle model hash mismatch")
    return identity


def prepare_model(model):
    """유효한 기존 파일은 유지하고 검증된 다운로드 완료 후 manifest를 남긴다."""
    spec = MODEL_SPECS[model]
    directory = ROOT / ".runtime/paddle-models" / model
    directory.mkdir(parents=True, exist_ok=True)
    for name, expected in spec["sha256"].items():
        target = directory / name
        if target.is_file() and file_hash(target) == expected:
            print(f"기존 파일 확인: {model}/{name}", flush=True)
            continue
        url = f"https://huggingface.co/PaddlePaddle/{model}/resolve/{spec['revision']}/{name}"
        temp = directory / (name + ".download")
        try:
            with urllib.request.urlopen(url, timeout=90) as response, temp.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            if file_hash(temp) != expected:
                raise ValueError("Downloaded Paddle model hash mismatch")
            temp.replace(target)
        finally:
            temp.unlink(missing_ok=True)
        print(f"준비 완료: {model}/{name}", flush=True)
    manifest = {"model": model, "repository": f"PaddlePaddle/{model}", **spec}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    verify_model(directory, model)
    print(f"모델 준비 완료: {directory}")


def prepare(include_detection=False, include_english=False):
    """기존 한국어 인식 모델과 요청된 경우에만 탐지 모델을 준비한다."""
    prepare_model(MODEL)
    if include_detection:
        prepare_model(DETECTION_MODEL)
    if include_english:
        prepare_model(ENGLISH_MODEL)


def main():
    """전체 OCR에 필요한 탐지 모델은 명시한 옵션으로만 추가한다."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-detection", action="store_true", help="Paddle 단독 경로의 탐지 모델도 준비")
    parser.add_argument('--include-english', action='store_true', help='조건별 영문 재인식 모델도 준비')
    args = parser.parse_args()
    prepare(include_detection=args.include_detection, include_english=args.include_english)


if __name__ == "__main__":
    main()
