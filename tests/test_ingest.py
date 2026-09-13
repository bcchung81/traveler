# tests/test_ingest.py
import json
import pymupdf
from pathlib import Path
from PIL import Image, ImageDraw
from receipt_evidence.ingest import ingest, is_blank, normalize_image, render_pdf, sha256_file

def _jpg(path: Path, size=(1440, 3088)):
    img = Image.new("RGB", size, "white"); ImageDraw.Draw(img).text((100, 100), "48,200", fill="black"); img.save(path, "JPEG")

def _pdf(path: Path):
    doc = pymupdf.open(); p = doc.new_page(); p.insert_text((72, 72), "100,000 won"); doc.new_page(); doc.save(path)

def test_normalize_resizes_long_side(tmp_path):
    _jpg(tmp_path / "a.jpg"); w, h = normalize_image(tmp_path / "a.jpg", tmp_path / "a.png")
    assert h == 2048 and w == 955

def test_render_pdf_drops_blank_page(tmp_path):
    _pdf(tmp_path / "r.pdf"); pages = render_pdf(tmp_path / "r.pdf", tmp_path / "pages")
    assert len(pages) == 1 and pages[0].name.endswith("-p1.png")

def test_is_blank(tmp_path):
    Image.new("RGB", (100, 100), "white").save(tmp_path / "b.png"); assert is_blank(tmp_path / "b.png")

def test_ingest_dedups_and_writes_manifest(tmp_path):
    d = tmp_path / "data"; d.mkdir(); _jpg(d / "x.jpg"); (d / "x_copy.jpg").write_bytes((d / "x.jpg").read_bytes()); _pdf(d / "r.pdf")
    imgs = ingest(d, tmp_path / "out")
    assert len(imgs) == 2 and (tmp_path / "out" / "manifest.json").exists()  # r.pdf 1쪽(2쪽은 빈 페이지) + x.jpg (x_copy.jpg는 중복)
    jpg = next(i for i in imgs if i.source_path.endswith("x.jpg"))
    assert jpg.sha256 == sha256_file(d / "x.jpg") and jpg.image_id == jpg.sha256[:12] + "-p1"
    assert [m["image_id"] for m in json.loads((tmp_path / "out" / "manifest.json").read_text())] == [i.image_id for i in imgs]
