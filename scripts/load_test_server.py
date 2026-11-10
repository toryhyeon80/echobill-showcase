"""
동시 접속 시험용 서버 (PM 10/21) — 운영과 같은 단일 uvicorn 프로세스에 STT·LLM만 가짜 지연으로 바꿔 띄운다.

비용 없음: 음성 인식은 STT_FAKE_SECONDS(기본 8초) 기다린 뒤 고정 문장을, LLM은 LLM_FAKE_SECONDS(기본 1.5초)
기다린 뒤 실패로 끝나 Rule 폴백(HTTP 200)으로 간다. 나머지(인증·멱등·DB·검증·후보 저장)는 운영 코드 그대로.

실행 (저장소 루트):

    # 운영과 같은 Postgres로 (DB 연결 수 확인용) — 운영 DB 금지, 로컬 Docker 등
    DATABASE_URL=postgresql+psycopg://echobill:echobill@localhost:5432/loadtest \\
      backend/.venv/bin/python scripts/load_test_server.py --port 8010

    # 다른 터미널
    backend/.venv/bin/python scripts/load_test.py --base http://127.0.0.1:8010 --audio 20 30

운영 DB·운영 서버 주소면 시작하지 않는다.
"""

import argparse
import os
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

FAKE_TEXT = "대박마트에 계란 두판 사만오천원 달아놔"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()

    url = os.environ.get("DATABASE_URL", "")
    if "railway" in url or "rlwy" in url:
        print("[중단] 운영(Railway) DB 주소입니다 — 로컬 DB로만 시험합니다")
        return 1
    os.environ.update(APP_ENV="development", LLM_PARSE_ENABLED="true", AUTH_COOKIE_SECURE="false",
                      OPENAI_API_KEY="sk-load-test-not-used")  # LLM은 아래에서 가짜로 바뀌어 키를 쓰지 않음
    stt_seconds = float(os.environ.get("STT_FAKE_SECONDS", "8"))
    llm_seconds = float(os.environ.get("LLM_FAKE_SECONDS", "1.5"))

    import uvicorn
    from app.api import ledger
    from app.db.session import engine, init_db
    from app.services import parse_llm

    def fake_transcribe(audio, filename, mime, **_kwargs):
        time.sleep(stt_seconds)
        return FAKE_TEXT

    def fake_llm(utterance_text, **_kwargs):
        time.sleep(llm_seconds)
        raise parse_llm.LLMParseError("load test — Rule fallback after the fake LLM delay")

    ledger.transcribe = fake_transcribe
    parse_llm.parse_utterance_with_llm = fake_llm
    init_db()  # a throwaway test DB: create the tables directly (no Alembic history needed)
    pool = engine.pool
    print(f"[서버] DB {engine.dialect.name} · 연결 풀 {type(pool).__name__} "
          f"size={getattr(pool, 'size', lambda: '-')()} overflow={getattr(pool, '_max_overflow', '-')} "
          f"timeout={getattr(pool, '_timeout', '-')}s · STT {stt_seconds:g}s · LLM {llm_seconds:g}s · 프로세스 1개")
    from app.main import app

    uvicorn.run(app, host="127.0.0.1", port=args.port, workers=1, log_level="warning")
    return 0


if __name__ == "__main__":
    sys.exit(main())
