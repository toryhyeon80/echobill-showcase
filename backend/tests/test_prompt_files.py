"""Prompt files must not reuse Golden or Seed sentences (A-4a 2-2, CLAUDE.md: Golden few-shot 금지).

Compares Hangul only (digits, spaces and symbols removed): keeping digits would flag common
fragments such as "000원외상". Any shared run of 6+ Hangul characters counts as an overlap.
"""

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILES = sorted((REPO_ROOT / "configs" / "prompts").glob("*.txt"))
GOLDEN_DEV = REPO_ROOT / "data" / "golden" / "golden_dev.jsonl"
SEED_FILES = sorted((REPO_ROOT / "data" / "seed").glob("*.jsonl"))
RUN_LENGTH = 6


def _hangul_only(text: str) -> str:
    return re.sub(r"[^가-힣]", "", text)


def _runs(text: str) -> set[str]:
    """All Hangul runs of RUN_LENGTH; two texts share a 6+ run iff they share one of these."""
    hangul = _hangul_only(text)
    return {hangul[i:i + RUN_LENGTH] for i in range(len(hangul) - RUN_LENGTH + 1)}


def _utterance_runs(paths: list[Path]) -> set[str]:
    runs: set[str] = set()
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                record = json.loads(line)
                runs |= _runs(record.get("utterance_text") or "")
    return runs


def test_prompt_files_are_present() -> None:
    names = {path.name for path in PROMPT_FILES}
    assert {"parse_llm_system.txt", "parse_llm_system_v2.txt", "parse_llm_system_p3.txt",
            "parse_llm_system_p3a.txt"} <= names


@pytest.mark.parametrize("prompt", PROMPT_FILES, ids=lambda path: path.name)
def test_prompt_shares_no_hangul_run_with_golden_dev(prompt: Path) -> None:
    overlap = sorted(_runs(prompt.read_text(encoding="utf-8")) & _utterance_runs([GOLDEN_DEV]))
    assert overlap == [], f"{prompt.name} shares Golden fragments: {overlap[:10]}"


@pytest.mark.skipif(not SEED_FILES, reason="no data/seed/*.jsonl in the repo")
@pytest.mark.parametrize("prompt", PROMPT_FILES, ids=lambda path: path.name)
def test_prompt_shares_no_hangul_run_with_seed(prompt: Path) -> None:
    overlap = sorted(_runs(prompt.read_text(encoding="utf-8")) & _utterance_runs(SEED_FILES))
    assert overlap == [], f"{prompt.name} shares Seed fragments: {overlap[:10]}"
