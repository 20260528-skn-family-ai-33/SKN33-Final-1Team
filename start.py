"""Start the repository's React build and OCR API on the same local origin."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    if not (ROOT / "frontend" / "dist" / "index.html").is_file():
        raise SystemExit("React 빌드가 없습니다. 저장소에서 npm --prefix frontend ci 및 npm --prefix frontend run build를 실행하세요.")
    try:
        import uvicorn
    except ImportError:
        raise SystemExit("Python 의존성이 없습니다. python3.12 setup_local.py를 실행하세요.") from None
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    print(f"\n브라우저 주소: http://{args.host}:{args.port}", flush=True)
    print("실행 중에는 터미널에 입력 프롬프트가 돌아오지 않는 것이 정상입니다.", flush=True)
    print("이 터미널을 유지하고 브라우저를 여세요. Ctrl+C는 서버 종료입니다.\n", flush=True)
    uvicorn.run("backend.app.api:app", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
