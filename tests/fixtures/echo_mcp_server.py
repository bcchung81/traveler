# tests/fixtures/echo_mcp_server.py
"""오프라인 테스트용 MCP stdio 서버. 실행: python echo_mcp_server.py"""
import os
from mcp.server.mcpserver import MCPServer

mcp = MCPServer("echo")

@mcp.tool()
def echo(text: str) -> str:
    return f"echo:{text}"

@mcp.tool()
def pid() -> str:
    return str(os.getpid())

@mcp.tool()
def boom() -> str:
    raise ValueError("boom")

if __name__ == "__main__":
    mcp.run()
