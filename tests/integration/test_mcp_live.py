# tests/integration/test_mcp_live.py
import pytest
from receipt_evidence.mcp_client import law_caller, law_oc, kordoc_caller

@pytest.mark.integration
@pytest.mark.skipif(not law_oc(), reason="LAW_OC(법제처 인증키) 미설정")
def test_live_search_law():
    r = law_caller().call_many([("search_law", {"query": "공무원 여비 규정", "display": 5})])[0]
    assert not r.is_error and "MST: 287535" in r.text and "[현행]" in r.text

@pytest.mark.integration
def test_live_kordoc_generate_in_session(tmp_path):
    with kordoc_caller() as doc:
        for i in range(2):
            out = tmp_path / f"t{i}.hwpx"
            r = doc.call_many([("generate_document", {"markdown": "# 제목\n> 테스트하고자 함\n## 본문\n### 항목\n- 내용", "output_path": str(out), "preset": "보고서"})])[0]
            assert not r.is_error and out.exists()
