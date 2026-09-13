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

def test_factory_commands_pin_versions_and_env(monkeypatch):
    for k in ("KOREAN_LAW_MCP", "KORDOC_MCP", "LAW_OC"):
        monkeypatch.delenv(k, raising=False)
    l, k = law_caller(), kordoc_caller()
    assert isinstance(l, StdioToolCaller) and l.command == "npx" and l.lazy and k.lazy
    assert l.args == ["-y", "--prefer-offline", "korean-law-mcp@4.13.0"] and l.env == {"LAW_OC": "kca-api"}
    assert k.args == ["-y", "--prefer-offline", "kordoc@4.13.1", "mcp"] and k.env is None
    monkeypatch.setenv("KOREAN_LAW_MCP", "korean-law-mcp@9.9.9"); monkeypatch.setenv("LAW_OC", "mine"); monkeypatch.setenv("KORDOC_MCP", "kordoc@1.0.0")
    assert law_caller().args[-1] == "korean-law-mcp@9.9.9" and law_caller().env == {"LAW_OC": "mine"} and kordoc_caller().args[2] == "kordoc@1.0.0"

def test_lazy_session_starts_on_first_call_only():
    with StdioToolCaller(sys.executable, ECHO, lazy=True) as c:
        assert c._thread is None  # with 진입만으로는 서버를 띄우지 않는다
        p1 = c.call_many([("pid", {})])[0].text
        p2 = c.call_many([("pid", {})])[0].text
    assert p1 == p2 and c._thread is None
    with StdioToolCaller(sys.executable, ["/nonexistent/server.py"], lazy=True) as bad:
        pass  # 한 번도 부르지 않으면 실패할 일도 없다

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
