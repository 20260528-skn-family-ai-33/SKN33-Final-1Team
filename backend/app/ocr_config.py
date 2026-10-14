"""실행 검사와 API에서 사용할 OCR 경로 선택 기준을 함께 관리한다."""
import os


def selected_pipeline():
    """명시한 설정을 우선하고, 로컬은 Paddle 단독·RunPod는 기존 경로를 기본으로 쓴다."""
    target = os.getenv("RUN_TARGET", "local").strip().lower()
    default = "baseline" if target == "runpod" else "paddle-only"
    return os.getenv("OCR_PIPELINE", default).strip().lower()
