"""영상 로고 검수용 상주 OCR 프로세스. 자막 검사 OCR 모델을 한 번 올려 두고 이미지를 한 장씩 읽는다.

RunPod Pod 워커는 torch와 CUDA 라이브러리가 겹치지 않게 GPU용 Paddle을 따로 만든 환경(VIDEO_OCR_PYTHON)에 둔다.
그래서 로고 후보·정밀 대조의 OCR도 그 환경의 이 프로세스에 맡긴다(video_logo_models.ocr_predict).
주고받기(표준 입출력):
  입력  : JSON 한 줄 {"h": 높이, "w": 너비} 다음에 RGB uint8 바이트 h*w*3
  출력  : JSON 한 줄 {"texts": [...], "polys": [...], "scores": [...]} 또는 {"error": "..."}
Paddle이 표준 출력에 남기는 로그가 섞이지 않게, 응답은 원래 표준 출력으로만 쓰고 나머지는 표준 오류로 돌린다.
"""
import argparse
import json
import os
import sys

from scripts.prepare_paddle import DETECTION_MODEL, MODEL, verify_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--detection-dir", required=True)
    parser.add_argument("--recognition-dir", required=True)
    parser.add_argument("--device", choices=["cpu", "gpu"], default="gpu")
    args = parser.parse_args()
    reply = os.fdopen(os.dup(1), "w", encoding="utf-8")
    os.dup2(2, 1)
    verify_model(args.detection_dir, DETECTION_MODEL); verify_model(args.recognition_dir, MODEL)
    import numpy as np
    from backend.app.ocr.paddle_video_worker import build_model
    model = build_model(args.detection_dir, args.recognition_dir, device=args.device)
    reply.write(json.dumps({"ready": True}) + "\n"); reply.flush()
    source = sys.stdin.buffer
    while True:
        line = source.readline()
        if not line:
            break
        try:
            head = json.loads(line)
            h, w = int(head["h"]), int(head["w"])
            data = source.read(h * w * 3)
            if len(data) != h * w * 3:
                break
            rgb = np.frombuffer(data, np.uint8).reshape(h, w, 3)
            res = list(model.predict(input=rgb[:, :, ::-1].copy()))[0].json
            res = json.loads(res) if isinstance(res, str) else res
            res = res.get("res", res)
            out = {"texts": list(res["rec_texts"]), "polys": [np.asarray(p, float).tolist() for p in res["rec_polys"]],
                   "scores": [float(s) for s in res["rec_scores"]]}
        except Exception as exc:  # 한 장이 실패해도 프로세스는 살려 둔다
            out = {"error": f"{exc.__class__.__name__}: {exc}"[:300]}
        reply.write(json.dumps(out, ensure_ascii=False) + "\n"); reply.flush()


if __name__ == "__main__":
    main()
