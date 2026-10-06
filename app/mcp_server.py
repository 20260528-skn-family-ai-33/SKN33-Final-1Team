"""Local read-only MCP inspection tools. Run with python -m app.mcp_server."""
from pathlib import Path

from mcp.server import MCPServer

from app.inspector import inspect_image as run_inspection, read_ocr

mcp = MCPServer("latte-local-inspection")


@mcp.tool()
def inspect_image(path: str, rules: list[dict]) -> dict:
    """Inspect an allowed PNG/JPEG using real local OCR; visual rules remain uninspected."""
    return run_inspection(Path(path), rules)


@mcp.tool()
def read_image_text(path: str) -> dict:
    """Read Korean/English text and normalized top-left bounding boxes from an allowed image."""
    return read_ocr(Path(path))


if __name__ == "__main__":
    mcp.run(transport="stdio")
