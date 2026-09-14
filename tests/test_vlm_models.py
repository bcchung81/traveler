# tests/test_vlm_models.py — 운영 로컬 AI 모델(Qwen3-VL 4B 기본) 설정이 스크립트·코드·캐시에서 일치하는지
import re, subprocess
from pathlib import Path
import pytest
from receipt_evidence import vlm_models
from receipt_evidence.cache import ExtractCache

ROOT = Path(__file__).resolve().parents[1]

def test_default_variant_is_4b_and_matches_start_script(monkeypatch):
    monkeypatch.delenv("VLM_VARIANT", raising=False)
    assert vlm_models.DEFAULT_VARIANT == "4b" and vlm_models.current().key == "4b"
    script = (ROOT / "scripts" / "start_vlm.sh").read_text(encoding="utf-8")
    assert re.search(r'VARIANT="\$\{VLM_VARIANT:-4b\}"', script)  # 스크립트 기본값도 4b
    for v in vlm_models.VARIANTS.values():
        assert v.repo in script and v.model_file in script and v.mmproj_file in script
    assert 'VLM_VARIANT="${VLM_VARIANT:-4b}"' in (ROOT / "run-app.sh").read_text(encoding="utf-8")
    monkeypatch.setenv("VLM_VARIANT", "8b")
    assert vlm_models.current().key == "8b"
    monkeypatch.setenv("VLM_VARIANT", "xl")
    with pytest.raises(ValueError, match="4b"):
        vlm_models.current()

def test_model_paths_resolve_from_any_snapshot(tmp_path):
    v = vlm_models.VARIANTS["4b"]
    snap = tmp_path / f"models--{v.repo.replace('/', '--')}" / "snapshots" / "abc123"
    snap.mkdir(parents=True); (snap / v.model_file).write_bytes(b"x"); (snap / v.mmproj_file).write_bytes(b"x")
    assert v.paths(tmp_path) == (snap / v.model_file, snap / v.mmproj_file)
    assert vlm_models.VARIANTS["8b"].paths(tmp_path) == (None, None)

def test_start_script_dry_run_prints_4b_paths(tmp_path):
    v = vlm_models.VARIANTS["4b"]
    snap = tmp_path / "hub" / f"models--{v.repo.replace('/', '--')}" / "snapshots" / "s1"
    snap.mkdir(parents=True); (snap / v.model_file).write_bytes(b"x"); (snap / v.mmproj_file).write_bytes(b"x")
    out = subprocess.run(["bash", str(ROOT / "scripts" / "start_vlm.sh")], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HF_HUB_CACHE": str(tmp_path / "hub"), "VLM_DRY_RUN": "1"})
    assert out.returncode == 0 and str(snap / v.model_file) in out.stdout and str(snap / v.mmproj_file) in out.stdout
    bad = subprocess.run(["bash", str(ROOT / "scripts" / "start_vlm.sh")], capture_output=True, text=True,
                         env={"PATH": "/usr/bin:/bin", "HF_HUB_CACHE": str(tmp_path / "hub"), "VLM_DRY_RUN": "1", "VLM_VARIANT": "8b"})
    assert bad.returncode != 0 and "찾지 못했어요" in bad.stderr

def test_extract_cache_is_separated_per_model(tmp_path, monkeypatch):
    monkeypatch.delenv("VLM_VARIANT", raising=False)
    c4 = ExtractCache(tmp_path)
    c4.put("abc", "t", {"amount": 1})
    assert c4.dir == tmp_path / "extract" / "p1" / "4b" and c4.has("abc")
    monkeypatch.setenv("VLM_VARIANT", "8b")
    c8 = ExtractCache(tmp_path)
    assert not c8.has("abc")  # 모델을 바꾸면 다른 모델이 읽은 결과를 쓰지 않는다
    (tmp_path / "extract" / "p1" / "legacy.json").write_text('{"transcript": "", "data": {}}', encoding="utf-8")
    assert c8.has("legacy") and c8.get("legacy") == {"transcript": "", "data": {}}  # 8B로 읽어 둔 예전 캐시(모델 폴더 이전)
    monkeypatch.setenv("VLM_VARIANT", "4b")
    assert not ExtractCache(tmp_path).has("legacy")
