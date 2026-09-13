# src/receipt_evidence/cache.py
from __future__ import annotations
import json, threading
from pathlib import Path
from .models import ReceiptImage

PROMPT_VERSION = "p1"  # extract.py 프롬프트나 스키마를 바꾸면 올린다 → 이전 캐시가 자동으로 무효화됨

def cache_key(img: ReceiptImage) -> str:
    """원본 sha256. EXIF로 회전해 읽은 사진은 예전에 눕힌 채 읽은 결과와 섞이지 않게 방향값을 붙인다."""
    return img.sha256 if img.orientation in (0, 1) else f"{img.sha256}-o{img.orientation}"

class ExtractCache:
    """원본 영수증 sha256 → {transcript, data}. 추가 제출·재실행 시 새 영수증만 VLM으로 읽기 위한 캐시."""

    def __init__(self, root: Path, prompt_version: str = PROMPT_VERSION):
        self.dir = Path(root) / "extract" / prompt_version
        self.hits = 0
        self.misses = 0
        self._lock = threading.Lock()

    def _path(self, sha: str) -> Path:
        return self.dir / f"{sha}.json"

    def has(self, sha: str) -> bool:
        return self._path(sha).exists()

    def get(self, sha: str) -> dict | None:
        p = self._path(sha)
        with self._lock:
            if not p.exists():
                self.misses += 1
                return None
            self.hits += 1
        return json.loads(p.read_text(encoding="utf-8"))

    def put(self, sha: str, transcript: str, data: dict) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(sha).with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"transcript": transcript, "data": data}, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path(sha))
