# src/receipt_evidence/ingest.py
from __future__ import annotations
import hashlib, json
from collections.abc import Callable
from pathlib import Path
import pymupdf
from PIL import Image, ImageOps
from .models import ReceiptImage

try:  # 아이폰 기본 사진 형식(HEIC)
    import pillow_heif
    pillow_heif.register_heif_opener()
    _HEIF = {".heic", ".heif"}
except ImportError:  # pragma: no cover
    _HEIF = set()

SUPPORTED = {".jpg", ".jpeg", ".png", ".pdf"} | _HEIF
INGEST_VERSION = "i2"  # 이미지 변환 방식을 바꾸면 올린다 → 예전 manifest의 이미지를 재사용하지 않음

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

def exif_orientation(src: Path) -> int:
    try:
        with Image.open(src) as im:
            return int(im.getexif().get(0x0112, 1) or 1)
    except (OSError, ValueError):
        return 1

def normalize_image(src: Path, dst: Path, max_side: int = 2048) -> tuple[int, int]:
    with Image.open(src) as im:
        img = ImageOps.exif_transpose(im).convert("RGB")  # 휴대폰 사진의 회전 정보(EXIF)를 반영해 세운다
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

def _previous(out_dir: Path) -> dict[str, list[ReceiptImage]]:
    """지난번 manifest에서 같은 변환 방식으로 만든 이미지(파일이 남아 있는 것)만 원본 해시별로 모은다."""
    try:
        items = [ReceiptImage.model_validate(d) for d in json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))]
    except (FileNotFoundError, ValueError):
        return {}
    prev: dict[str, list[ReceiptImage]] = {}
    for img in items:
        prev.setdefault(img.sha256, []).append(img)
    return {sha: imgs for sha, imgs in prev.items()
            if all(i.ingest_version == INGEST_VERSION and Path(i.png_path).exists() for i in imgs)}

def ingest(data_dir: Path, out_dir: Path, sha: Callable[[Path], str] = sha256_file) -> list[ReceiptImage]:
    img_dir = out_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)
    prev = _previous(out_dir)
    seen: set[str] = set()
    result: list[ReceiptImage] = []
    for src in sorted(p for p in data_dir.iterdir() if p.suffix.lower() in SUPPORTED and not p.name.startswith(".")):
        digest = sha(src)
        if digest in seen:
            continue
        seen.add(digest)
        if digest in prev:
            result += [i.model_copy(update={"source_path": str(src)}) for i in sorted(prev[digest], key=lambda i: i.page)]
            continue
        common = dict(source_path=str(src), sha256=digest, ingest_version=INGEST_VERSION)
        if src.suffix.lower() == ".pdf":
            rendered = render_pdf(src, out_dir / "_pdf_pages")
            for p in rendered:
                page = int(p.stem.rsplit("-p", 1)[1])
                dst = img_dir / f"{digest[:12]}-p{page}.png"
                w, h = normalize_image(p, dst)
                result.append(ReceiptImage(image_id=dst.stem, page=page, png_path=str(dst), width=w, height=h, **common))
        else:
            dst = img_dir / f"{digest[:12]}-p1.png"
            w, h = normalize_image(src, dst)
            result.append(ReceiptImage(image_id=dst.stem, page=1, png_path=str(dst), width=w, height=h, orientation=exif_orientation(src), **common))
    (out_dir / "manifest.json").write_text(json.dumps([r.model_dump() for r in result], ensure_ascii=False, indent=2), encoding="utf-8")
    return result
