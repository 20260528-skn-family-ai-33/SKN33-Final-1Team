FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    RUN_TARGET=local OCR_PIPELINE=paddle-only OCR_ALLOW_DOWNLOAD=0
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates libgomp1 libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*
COPY deploy/oci/requirements-api.txt requirements-paddle.txt /tmp/
RUN pip install --no-cache-dir -r /tmp/requirements-api.txt \
    && python -m venv /app/.runtime/paddle-env \
    && /app/.runtime/paddle-env/bin/pip install --no-cache-dir \
       --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/ paddlepaddle==3.3.1 \
    && /app/.runtime/paddle-env/bin/pip install --no-cache-dir -r /tmp/requirements-paddle.txt
COPY scripts/prepare_paddle.py scripts/prepare_paddle.py
RUN python scripts/prepare_paddle.py --include-detection
COPY backend/__init__.py backend/__init__.py
COPY backend/app backend/app
COPY data/img data/img
RUN useradd --uid 10001 --create-home review \
    && mkdir -p /app/.runtime/reviews /app/.runtime/paddlex /app/.runtime/model-cache \
    && chown review:review /app/.runtime/reviews /app/.runtime/paddlex /app/.runtime/model-cache
USER review
EXPOSE 8000
# 메모리 세션과 프로세스 내 OCR 잠금은 단일 서버 프로세스에서 공유한다.
CMD ["python", "-m", "uvicorn", "backend.app.api:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
