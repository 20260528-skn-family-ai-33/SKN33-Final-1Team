"""The application uses an actual SDK stdio session, never a mocked tool response."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.inspector import ROOT, unavailable_results
from app.ocr import method_name


def mcp_environment():
    env = {'LATTE_RUNTIME_DIR': str(Path(os.environ.get('LATTE_RUNTIME_DIR', ROOT / 'data/runtime')).resolve()),
           'PYTHONUTF8': '1', 'PYTHONIOENCODING': 'utf-8'}
    for name in ('LATTE_OCR_BACKEND', 'LATTE_OCR_MODEL_DIR', 'LATTE_OCR_DEVICE'):
        if name in os.environ:
            env[name] = str(Path(os.environ[name]).expanduser().resolve()) if name.endswith('_DIR') else os.environ[name]
    return env


async def inspect_via_mcp_async(path: Path, rules: list) -> dict:
    trace = []
    params = StdioServerParameters(command=sys.executable, args=["-m", "app.mcp_server"], cwd=str(ROOT),
                                   env=mcp_environment())
    try:
        async with asyncio.timeout(210):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=180) as session:
                    start = time.monotonic()
                    initialized = await session.initialize()
                    trace.append({"method": "initialize", "status": "succeeded",
                                  "protocol_version": initialized.protocol_version,
                                  "server": initialized.server_info.name,
                                  "duration_ms": round((time.monotonic() - start) * 1000)})
                    listed = await session.list_tools()
                    names = [tool.name for tool in listed.tools]
                    trace.append({"method": "tools/list", "status": "succeeded", "tools": names})
                    if "inspect_image" not in names:
                        raise RuntimeError("MCP 서버에 inspect_image 도구가 없습니다.")
                    start = time.monotonic()
                    call_id = str(uuid.uuid4())
                    called = await session.call_tool("inspect_image", {"path": str(path), "rules": rules})
                    trace.append({"method": "tools/call", "tool": "inspect_image", "call_id": call_id,
                                  "status": "failed" if called.is_error else "succeeded",
                                  "duration_ms": round((time.monotonic() - start) * 1000)})
                    if called.is_error:
                        raise RuntimeError("MCP 도구 실행 오류")
                    payload = called.structured_content
                    if not payload:
                        texts = [part.text for part in called.content if part.type == "text"]
                        payload = json.loads("".join(texts))
                    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                        raise RuntimeError("MCP 검사 응답 형식 오류")
                    payload["trace"] = trace + payload.get("trace", [])
                    return payload
    except Exception as error:
        trace.append({"method": "mcp/session", "status": "failed", "error": str(error)[:1000]})
        return {"results": unavailable_results(rules, "MCP 검사 실행 실패: " + str(error)[:500]),
                "method": method_name(), "trace": trace}


def inspect_via_mcp(path: Path, rules: list) -> dict:
    """Synchronous entry point for the API's background worker thread."""
    return asyncio.run(inspect_via_mcp_async(path, rules))
