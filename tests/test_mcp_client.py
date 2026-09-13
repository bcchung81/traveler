# tests/test_mcp_client.py
import sys
from pathlib import Path
from receipt_evidence.mcp_client import FakeToolCaller, McpResult, StdioToolCaller, kordoc_caller, law_caller

ECHO = [str(Path(__file__).parent / "fixtures" / "echo_mcp_server.py")]

def test_fake_caller_dispatches_and_records():
    f = FakeToolCaller({"search_law": lambda a: f"MST: 1 for {a['query']}"})
    res = f.call_many([("search_law", {"query": "공무원 여비 규정"})])
    assert res == [McpResult(text="MST: 1 for 공무원 여비 규정", is_error=False)] and f.calls[0][0] == "search_law"

def test_fake_caller_unknown_tool_is_error_and_context_manager():
    with FakeToolCaller({}) as f:
        assert f.call_many([("nope", {})])[0].is_error

def test_factory_commands():
    l, k = law_caller(), kordoc_caller()
    assert isinstance(l, StdioToolCaller) and l.command == "npx" and l.args == ["-y", "korean-law-mcp"] and l.env == {"LAW_OC": "kca-api"}
    assert k.args == ["-y", "kordoc", "mcp"] and k.env is None

def test_stdio_oneshot_spawns_per_call():
    c = StdioToolCaller(sys.executable, ECHO)
    p1 = c.call_many([("pid", {})])[0].text
    p2 = c.call_many([("pid", {})])[0].text
    assert p1.isdigit() and p1 != p2

def test_stdio_session_reuses_one_process_and_reports_tool_errors():
    with StdioToolCaller(sys.executable, ECHO) as c:
        a = c.call_many([("echo", {"text": "가"}), ("pid", {})])
        b = c.call_many([("pid", {}), ("boom", {})])
    assert a[0] == McpResult(text="echo:가", is_error=False) and a[1].text == b[0].text and b[1].is_error
