"""
EchoBill 코어 로직 터미널 시연 (10/7 멘토링, AI 개발 단계표 S0).

음성/텍스트 → (STT) → 정규화 → LLM(실패·5초 초과 시 Rule) → 검증 → 후보 저장
→ 400 보완 → 멱등 재전송 → 확정 → 미수 → FIFO 입금까지 실제 API로 한 번에 돌린다.

실행 (저장소 루트, 두 터미널):

    # 1) 서버 — 개발 모드 + LLM·정규화 켜기 (backend/.env에 OPENAI_API_KEY)
    cd backend
    APP_ENV=development LLM_PARSE_ENABLED=true KO_NUMERAL_NORMALIZE=true \
      AUTH_COOKIE_SECURE=false DATABASE_URL=sqlite:///./demo.db uvicorn app.main:app --port 8000

    # 2) 시연
    python scripts/demo_core.py                      # 텍스트만
    python scripts/demo_core.py --audio rec.webm     # 녹음 파일 STT 포함

필요 패키지는 backend/requirements.txt의 httpx뿐이다. 매 실행마다 새 사용자로 시작해 이전 결과가 섞이지 않는다.
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import httpx

TEXT_OK = "대박마트에 계란 두판 사만오천원 달아놔"
TEXT_FOUR_ITEMS = "카페민트에 원두 두 봉지 사만원 우유 열 팩 이만원 시럽 세 병 만오천원 컵 한 줄 팔천원 합쳐서 팔만삼천원 외상"
TEXT_MISSING_CREDIT = "떡집사랑 가래떡 열 봉지 삼만원"
# OS 추정(mimetypes)은 .m4a를 audio/mp4a-latm 등으로 줘서 서버 허용 목록과 어긋난다 — 브라우저가 보내는 값으로 고정
AUDIO_MIME = {".webm": "audio/webm", ".m4a": "audio/mp4", ".mp4": "audio/mp4",
              ".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg"}
UNPAID_KEYS = ("counterparty", "credit_total", "paid_total", "unpaid")
SHOW_KEYS = ("utterance_text", "counterparty", "items", "total_amount", "credit",
             "parser_type", "is_fallback_used", "processing_time_ms", "status")


def step(title: str) -> None:
    """Print a section heading."""
    print(f"\n━━ {title}")


def show(res: httpx.Response, keys: tuple[str, ...] = SHOW_KEYS) -> dict:
    """Print status and the fields worth seeing on screen; return the JSON body."""
    body = res.json()
    picked = {k: body[k] for k in keys if k in body}
    if "error_code" in body:
        picked = {k: body.get(k) for k in ("error_code", "message", "problem_fields", "partial_candidate")}
    print(f"   HTTP {res.status_code}")
    print("   " + json.dumps(picked, ensure_ascii=False, indent=2).replace("\n", "\n   "))
    return body


class Demo:
    """Thin client that adds auth and a fresh X-Idempotency-Key to every state-changing call."""

    def __init__(self, base_url: str) -> None:
        self.http = httpx.Client(base_url=base_url, timeout=60)
        self.user_id = f"demo-{uuid.uuid4().hex[:8]}"
        self.headers: dict[str, str] = {}

    def login(self) -> None:
        """dev-token works only when the server runs with APP_ENV=development (DL-21 4-5)."""
        res = self.http.post("/api/v1/auth/dev-token", json={"user_id": self.user_id})
        if res.status_code != 200:
            sys.exit("dev-token 실패 — 서버를 APP_ENV=development로 실행했는지 확인하세요.")
        self.headers = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def post(self, path: str, key: str | None = None, **kwargs) -> httpx.Response:
        """POST with a new idempotency key unless one is given (to show replay)."""
        headers = {**self.headers, "X-Idempotency-Key": key or str(uuid.uuid4())}
        return self.http.post(path, headers=headers, **kwargs)

    def get(self, path: str) -> httpx.Response:
        return self.http.get(path, headers=self.headers)


def main() -> None:
    parser = argparse.ArgumentParser(description="EchoBill 코어 로직 시연")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--audio", type=Path, help="녹음 파일 (webm·m4a 등) — 있으면 음성 입력 단계 포함")
    args = parser.parse_args()

    demo = Demo(args.base_url)
    try:
        demo.http.get("/health").raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"서버에 연결할 수 없습니다: {args.base_url} — 위 docstring의 실행 방법을 확인하세요.")
    demo.login()
    print(f"사용자: {demo.user_id}")

    if args.audio:
        step(f"0. 음성 입력 — {args.audio.name} → Whisper STT → LLM → 후보")
        mime = AUDIO_MIME.get(args.audio.suffix.lower(), "audio/webm")
        with args.audio.open("rb") as f:
            show(demo.post("/api/v1/ledger/parse-audio", files={"file": (args.audio.name, f, mime)}))

    step(f'1. 텍스트 입력 → 후보 (확정 전이라 장부에는 없음)\n   "{TEXT_OK}"')
    key = str(uuid.uuid4())
    first = show(demo.post("/api/v1/ledger/parse", key=key, json={"utterance_text": TEXT_OK}))
    entry_id = first.get("entry_id")

    step("2. 같은 X-Idempotency-Key로 재전송 (네트워크 끊김 재시도) → 같은 후보, 중복 저장 없음")
    again = demo.post("/api/v1/ledger/parse", key=key, json={"utterance_text": TEXT_OK}).json()
    print(f"   첫 entry_id {entry_id}\n   재전송 entry_id {again.get('entry_id')} → {'같음 ✅' if again.get('entry_id') == entry_id else '다름 ❌'}")

    step(f'3. 400 보완 — 품목 4개는 일부만 저장하지 않음\n   "{TEXT_FOUR_ITEMS}"')
    show(demo.post("/api/v1/ledger/parse", json={"utterance_text": TEXT_FOUR_ITEMS}))

    step(f'4. 400 보완 — 외상·현금을 말하지 않으면 지어내지 않고 되물음\n   "{TEXT_MISSING_CREDIT}"')
    show(demo.post("/api/v1/ledger/parse", json={"utterance_text": TEXT_MISSING_CREDIT}))

    if not entry_id:
        sys.exit("\n1단계 후보가 없어 확정 이후 단계를 건너뜁니다.")

    step("5. 미수 — 아직 후보라 0원 (candidate는 집계 제외)")
    show(demo.get("/api/v1/dashboard/unpaid"), ("total_unpaid", "counterparties"))

    step("6. 사용자 확정 → confirmed (feedback API가 유일한 확정 경로)")
    show(demo.post(f"/api/v1/ledger/{entry_id}/feedback", json={"action": "confirm"}))

    step("7. 한 번 더 확정 → 409 (이미 확정된 장부)")
    show(demo.post(f"/api/v1/ledger/{entry_id}/feedback", json={"action": "confirm"}))

    step("8. 미수 반영")
    unpaid = show(demo.get("/api/v1/dashboard/unpaid"), ("total_unpaid", "counterparties"))

    counterparty = first.get("counterparty")
    total = unpaid.get("total_unpaid") or 0
    if counterparty and total:
        step(f"9. 초과 입금 {total + 1:,}원 → 400 OVERPAYMENT (저장 안 함)")
        show(demo.post("/api/v1/payments", json={"counterparty": counterparty, "amount": total + 1}))
        step(f"10. {counterparty} {total:,}원 입금 → FIFO 배분, 미수 0원")
        show(demo.post("/api/v1/payments", json={"counterparty": counterparty, "amount": total}), UNPAID_KEYS)
        show(demo.get("/api/v1/dashboard/unpaid"), ("total_unpaid", "counterparties"))

    print("\n시연 끝.")


if __name__ == "__main__":
    main()
