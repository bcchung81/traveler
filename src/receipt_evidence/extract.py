# src/receipt_evidence/extract.py
from __future__ import annotations
import json, logging, re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Any
import httpx
from .cache import ExtractCache, cache_key
from .models import Category, Receipt, ReceiptImage
from .vlm import VlmClient, image_content

log = logging.getLogger("receipt_evidence")
_STR = {"type": ["string", "null"]}
RECEIPT_SCHEMA: dict = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "category": {"type": "string", "enum": [c.value for c in Category]},
        "merchant": _STR, "business_no": _STR, "amount": {"type": ["integer", "null"]},
        "paid_at": _STR, "service_date": _STR, "service_end_date": _STR,
        "origin": _STR, "destination": _STR, "seat_class": _STR, "train_no": _STR,
        "approval_no": _STR, "card_masked": _STR, "payer_name": _STR, "region": _STR,
        "nights": {"type": ["integer", "null"]},
    },
    "required": ["category", "merchant", "business_no", "amount", "paid_at", "service_date", "service_end_date",
                 "origin", "destination", "seat_class", "train_no", "approval_no", "card_masked", "payer_name", "region", "nights"],
}
TRANSCRIBE_PROMPT = "이 영수증 이미지에 보이는 모든 텍스트를 위에서 아래로, 왼쪽에서 오른쪽으로 빠짐없이 전사하세요. 숫자·날짜·번호는 원문 그대로 적고 해석이나 요약을 하지 마세요."
STRUCTURE_PROMPT = (
    "아래 전사문과 이미지를 근거로 영수증 정보를 JSON으로 추출하세요. 규칙: (1) 전사문에 없는 값은 null. "
    "(2) category는 철도(KTX·SRT·기차)/버스/항공/택시/숙박(호텔·모텔·숙소 예약)/식사/기타/미상 중 하나. "
    "(3) amount는 최종 결제금액을 원 단위 정수로. (4) paid_at은 승인·결제 일시, service_date는 운행일 또는 체크인일, "
    "service_end_date는 체크아웃일. (5) origin/destination은 출발·도착역, seat_class는 일반실/특실, train_no는 열차 종류와 번호. "
    "(6) card_masked는 표기된 마스킹 그대로. (7) region은 숙박지 시·도명(모르면 null). nights는 숙박 밤 수. "
    "(8) 날짜는 YYYY-MM-DD, 일시는 YYYY-MM-DD HH:MM 형식으로 쓰세요.\n\n[전사문]\n"
)
# 2026-07-09 / 2026.07.08 22:10 / 2026/08/21 16:44:06 / 2026년 06월 11일 (목) 08:31 모두 허용
_DT = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?(?:[^\d]{0,6}?(\d{1,2}):(\d{2})(?::(\d{2}))?)?")

def parse_datetime(s: Any) -> datetime | None:
    if not isinstance(s, str):
        return None
    m = _DT.search(s)
    if not m:
        return None
    y, mo, d, hh, mm, ss = m.groups()
    try:
        return datetime(int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    except ValueError:
        return None

def parse_date(s: Any) -> date | None:
    dt = parse_datetime(s)
    return dt.date() if dt else None

def mask_card(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"(\d{6})\d{8,10}(\d{2})", lambda m: m.group(1) + "*" * 6 + m.group(2), s)

def parse_json_loose(text: str) -> dict:
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise
        return json.loads(t[a:b + 1])

def _int(v: Any) -> int | None:
    if v is None:
        return None
    digits = re.sub(r"[^\d]", "", str(v))
    return int(digits) if digits else None

def to_receipt(image_id: str, data: dict, transcript_path: str) -> Receipt:
    try:
        cat = Category(data.get("category"))
    except ValueError:
        cat = Category.UNKNOWN
    s = lambda k: (str(data[k]).strip() or None) if data.get(k) not in (None, "") else None
    return Receipt(
        receipt_id=image_id, image_id=image_id, category=cat, merchant=s("merchant"), business_no=s("business_no"),
        amount=_int(data.get("amount")), paid_at=parse_datetime(data.get("paid_at")), service_date=parse_date(data.get("service_date")),
        service_end_date=parse_date(data.get("service_end_date")), origin=s("origin"), destination=s("destination"),
        seat_class=s("seat_class"), train_no=s("train_no"), approval_no=s("approval_no"), card_masked=mask_card(s("card_masked")),
        payer_name=s("payer_name"), region=s("region"), nights=_int(data.get("nights")), transcript_path=transcript_path, raw=data)

# VLM이 '기타/미상'으로 답했을 때만 쓰는 결정론 보정. 결제대행 메일처럼 영수증에 종류가 드러나지 않는 경우를 잡는다.
CATEGORY_KEYWORDS: list[tuple[Category, tuple[str, ...]]] = [
    (Category.LODGING, ("여기어때", "야놀자", "에어비앤비", "airbnb", "아고다", "agoda", "부킹닷컴", "booking.com", "호텔스닷컴",
                        "hotels.com", "호텔", "모텔", "리조트", "게스트하우스", "숙박", "객실", "체크인")),
    (Category.RAIL, ("코레일", "한국철도공사", "korail", "srt", "에스알", "ktx", "승차권")),
    (Category.TAXI, ("택시", "카카오t", "kakao t", "우티", "uber")),
    (Category.BUS, ("고속버스", "시외버스", "버스타고")),
    (Category.AIR, ("탑승권", "대한항공", "아시아나", "제주항공", "진에어", "티웨이", "에어부산")),
]

def infer_category(category: Category, merchant: str | None, transcript: str) -> tuple[Category, str | None]:
    """(보정된 구분, 근거 키워드). VLM이 구분을 정했으면 그대로 둔다."""
    if category not in (Category.OTHER, Category.UNKNOWN):
        return category, None
    hay = f"{merchant or ''}\n{transcript}".lower()
    for cat, words in CATEGORY_KEYWORDS:
        hit = next((w for w in words if w.lower() in hay), None)
        if hit:
            return cat, hit
    return category, None

_DT_ALL = re.compile(r"(\d{4})\s*[.\-/년]\s*(\d{1,2})\s*[.\-/월]\s*(\d{1,2})\s*일?[^\d\n]{0,6}?(\d{1,2}):(\d{2})")

def fill_payment_time(r: Receipt, transcript: str) -> Receipt:
    """모델이 결제일시를 날짜만 줬고(시각 표기 없음) 전사문에 같은 날짜의 시각이 있으면 그 시각을 쓴다(4B가 '승인일자'를 고르는 경우)."""
    raw = r.raw.get("paid_at")
    if r.paid_at is None or (isinstance(raw, str) and ":" in raw):
        return r
    for m in _DT_ALL.finditer(transcript):
        y, mo, d, hh, mm = (int(x) for x in m.groups())
        if (y, mo, d) == (r.paid_at.year, r.paid_at.month, r.paid_at.day) and hh < 24 and mm < 60:
            return r.model_copy(update={"paid_at": r.paid_at.replace(hour=hh, minute=mm), "raw": r.raw | {"paid_at_time_from": m.group(0)}})
    return r

def _structure(vlm: VlmClient, pngs: list[Path], transcript: str) -> dict | None:
    content = [{"type": "text", "text": STRUCTURE_PROMPT + transcript}] + [image_content(p) for p in pngs]
    user = {"role": "user", "content": content}
    for mode in ("json_schema", "json_object", "json_schema"):
        try:
            text = vlm.chat([user], json_schema=RECEIPT_SCHEMA, max_tokens=1024, schema_mode=mode)
            return parse_json_loose(text)
        except (json.JSONDecodeError, ValueError, httpx.HTTPStatusError):  # 400(스키마 미지원)도 다음 모드로 폴백
            continue
    return None

def group_by_source(images: list[ReceiptImage]) -> list[list[ReceiptImage]]:
    groups: dict[str, list[ReceiptImage]] = {}
    for img in images:
        groups.setdefault(img.sha256, []).append(img)
    return [sorted(g, key=lambda i: i.page) for g in groups.values()]

def _extract_group(vlm: VlmClient, group: list[ReceiptImage], tdir: Path, cache: ExtractCache | None) -> Receipt:
    first = group[0]
    tpath = tdir / f"{first.image_id}.txt"
    key = cache_key(first)
    cached = cache.get(key) if cache is not None else None
    error = None
    if cached is not None:
        transcript, data = cached["transcript"], cached["data"]
    else:
        try:
            pages = []
            for img in group:
                text = vlm.chat([{"role": "user", "content": [{"type": "text", "text": TRANSCRIBE_PROMPT}, image_content(Path(img.png_path))]}], max_tokens=2048)
                pages.append(text if len(group) == 1 else f"[{img.page}쪽]\n{text}")
            transcript = "\n\n".join(pages)
            data = _structure(vlm, [Path(i.png_path) for i in group], transcript)
        except Exception as e:  # 영수증 한 장의 실패(연결 끊김 등)가 출장 전체를 멈추지 않게 한다. 캐시에 넣지 않아 다음에 다시 읽는다
            log.warning("영수증 읽기 실패 %s: %s", first.source_path, e)
            transcript, data, error = "", None, f"{type(e).__name__}: {e}"
        if cache is not None and data is not None:
            cache.put(key, transcript, data)
    tpath.write_text(transcript, encoding="utf-8")
    if data is None:
        return Receipt(receipt_id=first.image_id, image_id=first.image_id, sha256=first.sha256, transcript_path=str(tpath),
                       warnings=["EXTRACT_FAILED"], confidence=0.0, raw={"error": error} if error else {})
    r = fill_payment_time(to_receipt(first.image_id, data, str(tpath)), transcript)
    category, hit = infer_category(r.category, r.merchant, transcript)
    if hit:
        r = r.model_copy(update={"category": category, "raw": r.raw | {"category_inferred_from": hit}})
    return r.model_copy(update={"sha256": first.sha256})

def extract_receipts(vlm: VlmClient, images: list[ReceiptImage], out_dir: Path, *, cache: ExtractCache | None = None,
                     workers: int = 1) -> list[Receipt]:
    tdir = out_dir / "transcripts"
    tdir.mkdir(parents=True, exist_ok=True)
    groups = group_by_source(images)
    if workers <= 1:
        receipts = [_extract_group(vlm, g, tdir, cache) for g in groups]
    else:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            receipts = list(ex.map(lambda g: _extract_group(vlm, g, tdir, cache), groups))
    (out_dir / "receipts.json").write_text(json.dumps([r.model_dump(mode="json") for r in receipts], ensure_ascii=False, indent=2), encoding="utf-8")
    return receipts
