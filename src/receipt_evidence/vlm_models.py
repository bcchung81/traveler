# src/receipt_evidence/vlm_models.py
"""영수증을 읽는 로컬 AI 모델(Qwen3-VL GGUF) 선택. 운영 기본은 4B — scripts/start_vlm.sh·run-app.sh와 같은 VLM_VARIANT를 쓴다.
2026-09-14 시연 영수증 비교: 4B Q4_K_M은 8B와 판정·금액이 같고 처리 시간 약 1/2~1/3(결제 시각은 전사문에서 보정)."""
from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_VARIANT = "4b"

@dataclass(frozen=True)
class VlmVariant:
    key: str
    label: str
    repo: str
    model_file: str
    mmproj_file: str

    def paths(self, hub: Path | None = None) -> tuple[Path | None, Path | None]:
        """허깅페이스 캐시의 스냅샷 중 두 파일이 모두 있는 곳(스냅샷 해시가 바뀌어도 찾는다)."""
        hub = Path(hub) if hub else hf_hub()
        for snap in sorted((hub / f"models--{self.repo.replace('/', '--')}" / "snapshots").glob("*")):
            if (snap / self.model_file).exists() and (snap / self.mmproj_file).exists():
                return snap / self.model_file, snap / self.mmproj_file
        return None, None

VARIANTS: dict[str, VlmVariant] = {
    "4b": VlmVariant("4b", "Qwen3-VL-4B-Instruct Q4_K_M", "Qwen/Qwen3-VL-4B-Instruct-GGUF", "Qwen3VL-4B-Instruct-Q4_K_M.gguf",
                     "mmproj-Qwen3VL-4B-Instruct-Q8_0.gguf"),
    "8b": VlmVariant("8b", "Qwen3-VL-8B-Instruct Q4_K_M", "Qwen/Qwen3-VL-8B-Instruct-GGUF", "Qwen3VL-8B-Instruct-Q4_K_M.gguf",
                     "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf"),
}

def hf_hub() -> Path:
    return Path(os.environ.get("HF_HUB_CACHE") or Path.home() / ".cache" / "huggingface" / "hub")

def current() -> VlmVariant:
    key = os.environ.get("VLM_VARIANT", DEFAULT_VARIANT).strip().lower() or DEFAULT_VARIANT
    if key not in VARIANTS:
        raise ValueError(f"VLM_VARIANT는 {', '.join(VARIANTS)} 중 하나여야 해요: {key!r}")
    return VARIANTS[key]
