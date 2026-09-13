# tests/test_export.py
from pathlib import Path
import pytest
from PIL import Image
from receipt_evidence.export import approved_total_from_md, export_hwpx, parse_md_tables, prepare_attachments, verify_hwpx
from receipt_evidence.mcp_client import FakeToolCaller
from receipt_evidence.models import Receipt, ReceiptImage

MD = "# 제목\n\n## 영수증별 상세\n| 연번 | 일자 | 구분 | 가맹점 | 승인번호 | 결제액 | 인정액 | 판정 | 근거 |\n| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n| 1 | 2026. 7. 9. | 철도운임 | 한국철도공사 | 55431218 | 48,200 | 48,200 | 지급 | 별표2 |\n| 합계 |  |  |  |  | 48,200 | 48,200 |  |  |\n"

def test_parse_tables_and_total():
    tables = parse_md_tables(MD)
    assert tables[0][0][0] == "연번" and tables[0][-1][0] == "합계" and approved_total_from_md(MD) == 48200

def test_parse_html_table_from_kordoc():
    head = "".join(f"<th>{h}</th>" for h in ["연번", "일자", "구분", "가맹점", "승인번호", "결제액", "인정액", "판정", "근거"])
    total = "<td>합계</td>" + "<td></td>" * 4 + "<td>48,200</td><td>48,200</td>" + "<td></td>" * 2
    assert approved_total_from_md(f"본문\n<table><tr>{head}</tr><tr>{total}</tr></table>\n") == 48200

def test_export_calls_generate_with_preset(tmp_path, trip):
    calls = FakeToolCaller({"generate_document": lambda a: Path(a["output_path"]).write_bytes(b"PK") and "ok"})
    out = export_hwpx(calls, MD, tmp_path / "e.hwpx", trip, tmp_path)
    name, args = calls.calls[0]
    assert out.exists() and name == "generate_document" and args["preset"] == "보고서" and args["image_dir"] == str(tmp_path)
    assert args["approval"] == ["담당", "팀장"] and args["org"] == trip.org
    assert args["cover"] is False and "date" not in args  # 표지를 켜면 빈 문서정보표가 결재란과 겹친다
    assert args["report_info"].endswith(f"{trip.org} 정백철)")
    export_hwpx(calls, MD, tmp_path / "e2.hwpx", trip.model_copy(update={"org": "", "dept": ""}), tmp_path)
    assert calls.calls[-1][1]["report_info"].endswith(", 정백철)") and "미기재" not in calls.calls[-1][1]["report_info"]

def test_verify_hwpx_compares_total(tmp_path):
    ok = verify_hwpx(FakeToolCaller({"parse_document": lambda a: MD}), tmp_path / "e.hwpx", 48200)
    bad = verify_hwpx(FakeToolCaller({"parse_document": lambda a: MD}), tmp_path / "e.hwpx", 1)
    assert ok["ok"] and ok["parsed_total"] == 48200 and not bad["ok"]

def test_export_error_raises(tmp_path, trip):
    with pytest.raises(RuntimeError):
        export_hwpx(FakeToolCaller({}), MD, tmp_path / "e.hwpx", trip, tmp_path)

def test_prepare_attachments_downscales_and_groups_pages(tmp_path):
    imgs = []
    for page in (1, 2):
        p = tmp_path / f"pdf-p{page}.png"; Image.new("RGB", (2400, 3200), "white").save(p)
        imgs.append(ReceiptImage(image_id=f"pdf-p{page}", source_path="stay.pdf", page=page, png_path=str(p), sha256="pdf", width=2400, height=3200))
    names = prepare_attachments(list(reversed(imgs)), [Receipt(receipt_id="pdf-p1", image_id="pdf-p1")], tmp_path / "att")
    assert names == {"pdf-p1": ["pdf-p1.jpg", "pdf-p2.jpg"]}
    with Image.open(tmp_path / "att" / "pdf-p2.jpg") as im:
        assert max(im.size) == 1600 and im.height / im.width <= 1.3

def test_tall_phone_screenshot_is_padded_to_fit_one_page(tmp_path):
    p = tmp_path / "ktx-p1.png"; Image.new("RGB", (1440, 3088), "black").save(p)
    img = ReceiptImage(image_id="ktx-p1", source_path="k.jpg", page=1, png_path=str(p), sha256="k", width=1440, height=3088)
    prepare_attachments([img], [Receipt(receipt_id="ktx-p1", image_id="ktx-p1")], tmp_path / "att")
    with Image.open(tmp_path / "att" / "ktx-p1.jpg") as im:
        assert im.size == (1231, 1600) and im.getpixel((5, 800))[0] > 240 and im.getpixel((615, 800))[0] < 15  # 좌우 흰 여백 + 가운데 원본
