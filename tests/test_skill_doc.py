# tests/test_skill_doc.py
from pathlib import Path

def test_skill_md_mentions_contract():
    md = Path(".claude/skills/receipt-evidence/SKILL.md").read_text(encoding="utf-8")
    assert md.startswith("---\nname: receipt-evidence")
    for needle in ("receipt-evidence run", "data/<출장자>/<출장>/", "traveler.yaml", "trip.yaml", "overrides.yaml", "start_vlm.sh", "summary-", "receipt-evidence web"):
        assert needle in md, needle

def test_readme_mentions_layout_and_tests():
    md = Path("README.md").read_text(encoding="utf-8")
    for needle in ("uv sync", "scripts/start_vlm.sh", "data/<출장자>/<출장>/", "uv run pytest -m integration", "receipt-evidence web"):
        assert needle in md, needle
