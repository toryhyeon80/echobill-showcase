"""jev_probe 고정 테스트 (PM 10/23 #196 요청).

- Dev·Seed v2 밖의 문장은 거부하는지 (Golden Test·Seed v1은 밖으로 못 나감)
- --dry-run이면 API를 부르지 않고 키도 읽지 않으며 파일도 만들지 않는지
- 실제 실행 경로(가짜 서버)에서 10번만 부르고, 키 값이 결과 파일에 남지 않는지
API 호출 없음(0원).
"""

import json
from pathlib import Path

import pytest

import eval.run_baseline as runner
from eval import jev_probe as jev

SEED_IDS = [i for i in jev.ORDER if i.startswith("seed2-")]
DEV_IDS = [i for i in jev.ORDER if not i.startswith("seed2-")]


@pytest.fixture(autouse=True)
def _clean_git_tree(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runner, "_git_state", lambda: {"git_commit": "abc1234", "git_dirty": False})


def _row(record_id: str, split: str) -> dict:
    return {"id": record_id, "utterance_text": f"{record_id} 테스트 문장", "split": split}


def _write(path: Path, rows: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    return path


@pytest.fixture
def data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """저장소 대신 임시 폴더를 저장소처럼 쓴다. Dev 4문장은 가짜 golden_dev, Seed v2 6문장은 seed 파일."""
    monkeypatch.setattr(jev, "REPO_ROOT", tmp_path)
    _write(tmp_path / "data" / "golden" / "golden_dev.jsonl", [_row(i, runner.GEMINI_ALLOWED_SPLIT) for i in DEV_IDS])
    return _write(tmp_path / "seed_v2_improve.jsonl", [_row(i, runner.GEMINI_SEED_V2_SPLIT) for i in SEED_IDS])


def test_ten_sentences_from_dev_and_seed_v2_are_accepted(data: Path):
    records = jev.load_sentences(data)
    assert [r["id"] for r in records] == jev.ORDER
    assert len(records) == 10


def test_a_sentence_outside_dev_and_seed_v2_is_refused(tmp_path: Path, data: Path):
    """최종 Golden처럼 허용 데이터가 아닌 split이면 보내지 않는다."""
    rows = [_row(i, runner.GEMINI_SEED_V2_SPLIT) for i in SEED_IDS]
    rows[0]["split"] = "최종 Golden"
    seed = _write(tmp_path / "bad_seed.jsonl", rows)
    with pytest.raises(ValueError, match="Dev·Seed v2가 아닌 문장"):
        jev.load_sentences(seed)


def test_a_seed_v1_style_id_is_refused_even_if_split_says_seed(tmp_path: Path, data: Path, monkeypatch: pytest.MonkeyPatch):
    """split이 Seed여도 ID가 seed2-로 시작하지 않으면(= Seed v1) 거부."""
    monkeypatch.setattr(jev, "ORDER", [*jev.ORDER, "seed1-001"])
    monkeypatch.setitem(jev.GOLD, "seed1-001", ("sale", None))
    rows = [_row(i, runner.GEMINI_SEED_V2_SPLIT) for i in [*SEED_IDS, "seed1-001"]]
    seed = _write(tmp_path / "mixed_seed.jsonl", rows)
    with pytest.raises(ValueError, match="seed1-001"):
        jev.load_sentences(seed)


def test_a_missing_sentence_stops_the_run(tmp_path: Path, data: Path):
    seed = _write(tmp_path / "short_seed.jsonl", [_row(i, runner.GEMINI_SEED_V2_SPLIT) for i in SEED_IDS[:-1]])
    with pytest.raises(ValueError, match="문장을 찾지 못함"):
        jev.load_sentences(seed)


def test_dry_run_never_calls_the_api_reads_no_key_and_writes_nothing(tmp_path: Path, data: Path,
                                                                   monkeypatch: pytest.MonkeyPatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("dry-run must not touch the network or the key")

    monkeypatch.setattr(jev.httpx, "Client", forbidden)
    monkeypatch.setattr(jev, "call_jev", forbidden)
    monkeypatch.setattr(jev, "load_key", forbidden)

    out = tmp_path / "out"
    result = jev.run(seed_file=data, output_dir=out, dry_run=True)

    assert result["dry_run"] is True
    assert result["estimate"]["usd"] <= result["estimate"]["cap_usd"]
    assert not out.exists()


class _FakeResponse:
    status_code = 200
    text = ""

    def json(self) -> dict:
        return {"model": "jev-1.13.0", "usage": {"input_tokens": 100, "output_tokens": 0},
                "answers": {"transaction_type": {"choice": "sale", "confidence": 0.9, "probabilities": {}},
                            "credit_status": {"choice": "paid", "confidence": 0.9, "probabilities": {}}}}


class _FakeClient:
    def __init__(self):
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        return _FakeResponse()


def test_real_run_calls_once_per_sentence_and_never_writes_the_key(tmp_path: Path, data: Path):
    client = _FakeClient()
    out = tmp_path / "out"
    summary = jev.run(seed_file=data, output_dir=out, dry_run=False, client=client, key="SECRET-KEY-VALUE")

    assert client.calls == 10
    assert summary["calls"] == {"total": 10, "ok": 10, "failed": 0}
    assert summary["usage"]["input_tokens"] == 1000
    for path in out.iterdir():
        assert "SECRET-KEY-VALUE" not in path.read_text(encoding="utf-8")


def test_a_run_over_the_cost_cap_stops_before_any_call(tmp_path: Path, data: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(jev, "COST_CAP_USD", 0.0)
    client = _FakeClient()
    with pytest.raises(RuntimeError, match="상한"):
        jev.run(seed_file=data, output_dir=tmp_path / "out", dry_run=False, client=client, key="k")
    assert client.calls == 0
