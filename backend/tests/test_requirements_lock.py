"""Supply-chain guard (PM #16): requirements.txt is a hash-locked compile of requirements.in.

Docker and CI install requirements.txt; with hashes present pip refuses any tampered or unpinned
artifact. Regenerate after editing requirements.in:
    uv pip compile requirements.in --universal --python-version 3.12 --generate-hashes -o requirements.txt
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _names(text: str, pinned: bool) -> set[str]:
    names = set()
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith(("-", "--")):
            continue
        match = re.match(r"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*(==)?", line)
        if match and (not pinned or match.group(3)):
            names.add(re.sub(r"[-_.]+", "-", match.group(1)).lower())
    return names


def test_every_direct_dependency_is_pinned_in_lock() -> None:
    direct = _names((BACKEND / "requirements.in").read_text(encoding="utf-8"), pinned=False)
    locked = _names((BACKEND / "requirements.txt").read_text(encoding="utf-8"), pinned=True)
    missing = direct - locked
    assert not missing, f"requirements.in changed without regenerating requirements.txt: {sorted(missing)}"


def test_every_locked_package_has_hashes() -> None:
    text = (BACKEND / "requirements.txt").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    pins = [b for b in blocks if re.match(r"[A-Za-z0-9_.\-]+==", b)]
    assert pins
    unhashed = [b.split()[0] for b in pins if "--hash=sha256:" not in b]
    assert not unhashed, f"packages without hashes: {unhashed}"
