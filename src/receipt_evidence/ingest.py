# src/receipt_evidence/ingest.py
from __future__ import annotations
import hashlib, json
from pathlib import Path
import pymupdf
from PIL import Image
from .models import ReceiptImage

SUPPORTED = {".jpg", ".jpeg", ".png", ".pdf"}

def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def is_blank(png: Path, dark_ratio: float = 0.0001) -> bool:
    # 실측(200dpi): 텍스트 한 줄 페이지 0.00035, 토스 메일 푸터 페이지 0.00345, 빈 페이지 0.0
    # 푸터처럼 내용이 있는 쪽은 남기고, extract 단계에서 같은 원본 파일의 쪽을 영수증 1건으로 묶는다
    img = Image.open(png).convert("L")
    hist = img.histogram()
    dark = sum(hist[:200])
    return dark / (img.width * img.height) < dark_ratio

def normalize_image(src: Path, dst: Path, max_side: int = 2048) -> tuple[int, int]:
    img = Image.open(src).convert("RGB")
    scale = min(1.0, max_side / max(img.size))
    if scale < 1.0:
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.LANCZOS)
    dst.parent.mkdir(parents=True, exist_ok=True)
    img.save(dst, "PNG")
    return img.width, img.height

def render_pdf(pdf: Path, out_dir: Path, dpi: int = 200) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = sha256_file(pdf)[:12]
    paths: list[Path] = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            p = out_dir / f"{stem}-p{i}.png"
            page.get_pixmap(dpi=dpi).save(p)
            if is_blank(p):
                p.unlink()
                continue
            paths.append(p)
    return paths

def ingest(data_dir: Path, out_dir: Path) -> list[ReceiptImage]:
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    result: list[ReceiptImage] = []
    for src in sorted(p for p in data_dir.iterdir() if p.suffix.lower() in SUPPORTED and not p.name.startswith(".")):
        sha = sha256_file(src)
        if sha in seen:
            continue
        seen.add(sha)
        if src.suffix.lower() == ".pdf":
            rendered = render_pdf(src, out_dir / "_pdf_pages")
            for p in rendered:
                page = int(p.stem.rsplit("-p", 1)[1])
                dst = img_dir / f"{sha[:12]}-p{page}.png"
                w, h = normalize_image(p, dst)
                result.append(ReceiptImage(image_id=dst.stem, source_path=str(src), page=page, png_path=str(dst), sha256=sha, width=w, height=h))
        else:
            dst = img_dir / f"{sha[:12]}-p1.png"
            w, h = normalize_image(src, dst)
            result.append(ReceiptImage(image_id=dst.stem, source_path=str(src), page=1, png_path=str(dst), sha256=sha, width=w, height=h))
    (out_dir / "manifest.json").write_text(json.dumps([r.model_dump() for r in result], ensure_ascii=False, indent=2), encoding="utf-8")
    return result
