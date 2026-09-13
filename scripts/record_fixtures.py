# scripts/record_fixtures.py
"""실제 korean-law MCP 응답을 tests/fixtures/law/ 에 녹화한다. 실행: uv run python scripts/record_fixtures.py"""
from pathlib import Path
from receipt_evidence.mcp_client import law_caller

LAW = "공무원 여비 규정"
OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "law"

def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with law_caller() as caller:
        search = caller.call_many([("search_law", {"query": LAW, "display": 5})])[0].text
        (OUT / "search_law.txt").write_text(search, encoding="utf-8")
        calls = [("get_annexes", {"lawName": LAW, "annexNo": "1", "knd": "1"}), ("get_annexes", {"lawName": LAW, "annexNo": "2", "knd": "1"})]
        calls += [("get_law_text", {"mst": "287535", "jo": jo}) for jo in ("제12조", "제13조", "제16조", "제18조")]
        names = ["annex1.html", "annex2.html", "jo12.txt", "jo13.txt", "jo16.txt", "jo18.txt"]
        for name, res in zip(names, caller.call_many(calls)):
            if res.is_error:
                raise SystemExit(f"{name}: {res.text}")
            (OUT / name).write_text(res.text, encoding="utf-8")
            print("saved", name, len(res.text))

if __name__ == "__main__":
    main()
