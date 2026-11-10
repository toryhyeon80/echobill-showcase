"""Application settings loaded from environment."""

from functools import lru_cache
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_JWT_SECRET = "change-me-dev-only-not-for-production"
JWT_SECRET_MIN_LENGTH = 32  # HS256 key ≥ 256 bits
# Public dev-only Fernet key — production must set BUSINESS_NUMBER_KEY (startup check below)
DEV_BUSINESS_NUMBER_KEY = "K1YZi17yf7EUfXevFZZcg_9dBbwtj8jAA5jW4TfiQRA="


class Settings(BaseSettings):
    """Runtime configuration for EchoBill backend."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # a startup error must not print the env values it was given (OPENAI_API_KEY leaked into one, PM 10/12)
        hide_input_in_errors=True,
    )

    app_name: str = "EchoBill"
    # fail closed: unset APP_ENV must not enable dev-token on a deploy (DL-21 4-5)
    app_env: str = "production"
    debug: bool = True

    # 비밀값은 SecretStr — repr·로그·model_dump에 ********** 로만 보임, 쓸 때 .get_secret_value() (PR #94와 같은 방식)
    jwt_secret: SecretStr = SecretStr(DEV_JWT_SECRET)
    jwt_access_ttl_hours: int = 24

    # 사업자번호 암호화 키 (SPEC §5.4 「암호화 저장」) — Fernet 키, 운영은 env 필수
    business_number_key: SecretStr = SecretStr(DEV_BUSINESS_NUMBER_KEY)

    # 세금계산서 Mock (SPEC §5.4) — 시연용 결과 모드: issue(모의 완료) | fail | delay
    tax_mock_mode: str = "issue"
    tax_mock_delay_seconds: int = 10

    # D07 / DL-21 카카오 로그인 — 키·시크릿은 backend/.env · Railway env에만
    # REST API 키는 비밀 아님: 브라우저 인가 URL의 client_id로 공개됨(FE VITE_KAKAO_REST_API_KEY) → str 유지
    kakao_rest_api_key: str = ""
    kakao_client_secret: SecretStr = SecretStr("")  # 콘솔에서 클라이언트 시크릿 활성화됨 → 토큰 요청에 필수
    kakao_redirect_uri: str = "http://localhost:5173/auth/kakao/callback"
    kakao_auth_base_url: str = "https://kauth.kakao.com"
    kakao_api_base_url: str = "https://kapi.kakao.com"
    kakao_timeout_seconds: float = 5.0

    # DL-12 JWT 쿠키 — 운영은 Secure 필수, 로컬 http 개발만 false
    auth_cookie_name: str = "echobill_access_token"
    auth_cookie_secure: bool = True
    # 가입 흐름 (PM 10/8): 처음 온 카카오 회원은 약관 동의 전까지 merchant를 만들지 않고
    # 짧은 가입용 토큰(HttpOnly, /auth/signup 경로에서만 전송)만 받는다
    signup_cookie_name: str = "echobill_signup_token"
    signup_token_ttl_minutes: int = 10

    # DL-20 (D04-5 · D04-9) — env로 조정 가능, 코드에 박지 않는다
    utterance_max_chars: int = 500
    amount_min_krw: int = 1
    amount_max_krw: int = 100_000_000
    qty_max: int = 100_000

    # DL-22 — 같은 키 재요청 보관 24h · 후보 생성 7일 후 확정 차단
    idempotency_ttl_hours: int = 24
    # 응답 없이 이보다 오래된 예약은 중단된 요청으로 보고 다시 실행 (워커 종료 대비)
    idempotency_pending_seconds: int = 60
    # 24h 지난 기록 자동 정리 주기 (요청 처리 중 기회가 될 때, 프로세스당 최대 1회/주기)
    idempotency_purge_interval_seconds: int = 600
    # 사용자별 상한 (PM #14) — 수치는 D09에서 PM이 정함. 0 = 끔. "일" = 지난 24시간(롤링), KST 달력 하루 아님
    # 텍스트(/parse)와 음성(/parse-audio)은 따로 센다 — 음성은 음성 상한만 (PM 10/6)
    parse_limit_per_minute: int = 0
    parse_limit_per_day: int = 0
    audio_limit_per_minute: int = 0
    audio_limit_per_day: int = 0
    # DL-31 음성 되묻기 답 — 켤 칸 목록(credit,qty,total,counterparty). 비우면 전부 끔(기본).
    # 10/12 칸별 판정(해석 성공 90%↑·틀린 값 채움 0건)을 통과한 칸만 운영에서 켠다
    voice_answer_fields: str = ""
    candidate_confirm_days: int = 7

    database_url: str = "sqlite:///./echobill_dev.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    openai_api_key: SecretStr | None = None  # repr/log shows ********** — read with .get_secret_value()
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 5.0
    llm_max_completion_tokens: int = 800
    # SDK retries restart the per-attempt timeout; keep 0 so one call honours the 5s Rule fallback.
    llm_max_retries: int = 0
    llm_temperature: float = 0.0
    # Prompt text lives in configs/ (CLAUDE.md rule 13); None uses configs/prompts/parse_llm_system.txt.
    llm_system_prompt_file: str | None = None
    # DL-30: 모델이 거래 종류(sale·purchase·return)도 답하는 스키마. 기본 False = main 스키마 그대로 (p3 프롬프트 측정용)
    llm_transaction_type: bool = False
    # 한글 수사 정규화 (DL-05: 숫자·단위만). Seed v1 결과로 기본 True (DL-26, PM 예외 승인)
    ko_numeral_normalize: bool = True
    # 금액·외상 Rule 후처리 (A-4c). 기본 True (DL-26). 측정은 llm_postprocess=false로 원 출력을 저장하고 score.py --postprocess로 채점
    llm_postprocess: bool = True
    # /parse에 LLM 연결 — 기본 꺼짐(테스트·로컬 .env 키로 실수 호출 방지). 켜면 실패·5초 초과 시 Rule 폴백
    llm_parse_enabled: bool = False
    # 캐스케이드 (팀 회의 10/23) — 의심 신호 3개 중 하나가 뜨면 두 번째 모델에도 묻고, 두 답이 다른 칸만 비워 되묻기.
    # 저장값은 운영 모델 답 그대로 (두 번째 모델 값으로 바꾸지 않음). 기본 꺼짐. 두 번째 모델 실패·마감 초과 → 운영 답 그대로.
    # 켜면 사장님 문장이 Google에도 전송 → 처리방침 위탁 업체에 Google 추가 후 켤 것
    cascade_enabled: bool = False
    cascade_model: str = "gemini-3.1-flash-lite"  # 10/21~22 Seed v2 측정 모델 (유료 등급)
    cascade_timeout_seconds: float = 3.0  # 신호가 뜬 문장만 더 걸림 (Gemini 유료 p95 1.6초)
    gemini_api_key: SecretStr | None = None

    # STT (DL-16: Whisper로 시작, 업체·모델 교체는 env) — 원본 오디오는 STT 후 즉시 폐기 (CLAUDE.md §4-10)
    # 업체 (PM 10/19): "openai"(기본, stt_model 사용) | "clova"(CLOVA Speech 단문 인식 — webm·mp4는 wav로 변환해 보냄).
    # 운영에서 바꾸면 처리방침 위탁 업체 목록도 함께 바꾼다
    stt_provider: Literal["openai", "clova"] = "openai"
    clova_speech_invoke_url: str = ""  # 빌더 설정 > 연동 정보의 Invoke URL (/recog/v1/stt는 붙어 있어도 되고 없어도 됨)
    clova_speech_secret_key: SecretStr = SecretStr("")
    stt_model: str = "whisper-1"
    # STT 낱말 도움말(prompt) 파일 — 비우면 끔(기본, prompt 인자 자체를 안 넘김). 켤 때 configs/prompts/stt_prompt_ko.txt
    # 음성 답(DL-31)도 같은 STT를 거침 — 켜기 전에 무음·잡음 합성 녹음 STT 확인(eval/run_stt_noise.py)
    stt_prompt_file: str = ""
    # 사장님 본인 거래처 이름을 STT 도움말 뒤에 "거래처: …"로 덧붙임 (PM 10/19, 음성 인식 비교 ② 거래처 힌트).
    # 기본 꺼짐 — 꺼져 있으면 요청이 지금과 똑같다. 본인 확정 장부의 최근 거래처만 (후보·삭제 건·다른 사장님 제외)
    stt_counterparty_hints: bool = False
    stt_counterparty_hint_limit: int = 20  # 이름 줄은 120자에서도 끊김 (whisper-1 도움말 한도 224토큰)
    # STT 전체 호출 마감 (Whisper 연결+업로드+응답). 서버 처리 최악 ≈ STT 10 + LLM 5 = 15초.
    # FE /parse-audio 타임아웃은 여기에 "사용자 → 서버 업로드 시간"을 더해야 한다 (1분 ≈1.6MB, 느린 LTE 최대 ~8초)
    # → FE 음성 요청 타임아웃 25초 이상 (FE #49는 30초, 텍스트 /parse는 15초 유지). FE와 숫자를 맞출 것
    stt_timeout_seconds: float = 10.0
    stt_max_bytes: int = 5_000_000  # iPhone 1분 ≈ 1.6MB (10/1 실기기 시험)
    # 읽어 주기 TTS (PM 10/26) — 되묻기 질문·실패 안내를 서버에서 mp3로 (같은 OPENAI_API_KEY).
    # 말투 지시는 configs 파일 (실패 안내 파일과 같은 목소리). 비우면 configs/prompts/tts_instructions_ko.txt
    tts_model: str = "gpt-4o-mini-tts"
    tts_voice: str = "marin"
    tts_instructions_file: str | None = None
    tts_max_chars: int = 80  # 되묻기 질문·확인 화면 요약(30~60자)만, 실패 안내는 FE 고정 파일 (PM 10/26)
    tts_timeout_seconds: float = 8.0  # 실측 1.3~4.3초 (27자 4.3초), 실패하면 FE는 글씨만 보여 줌 (PM 10/26)
    # 사용자별 상한 — parse·음성과 따로 셈. 0 = 끔
    speak_limit_per_minute: int = 0
    speak_limit_per_day: int = 0
    # Cloudflare 우회 차단 (PM #11) — Cloudflare가 붙이는 비밀 헤더 값. 비어 있으면 끔(기본).
    # 켜는 순서: Cloudflare 헤더 설정 먼저 → 그다음 Railway에 같은 값 (반대로 하면 전부 403)
    origin_secret: SecretStr = SecretStr("")
    origin_secret_header: str = "X-Origin-Secret"
    # 요청 본문 상한 (PM #13) — 넘으면 본문을 읽기 전에 413. 음성(/parse-audio)은 stt_max_bytes + multipart 여유
    max_body_bytes: int = 64 * 1024
    audio_body_margin_bytes: int = 1024 * 1024

    @property
    def max_audio_body_bytes(self) -> int:
        """Follows STT_MAX_BYTES so raising the STT cap never gets blocked by the size gate."""
        return self.stt_max_bytes + self.audio_body_margin_bytes

    @property
    def cors_origin_list(self) -> list[str]:
        """Parse comma-separated CORS origins."""
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _cascade_needs_key(self) -> "Settings":
        """CASCADE_ENABLED without a key would silently never ask — fail at startup instead."""
        if self.cascade_enabled and not (self.gemini_api_key and self.gemini_api_key.get_secret_value()):
            raise ValueError("CASCADE_ENABLED=true needs GEMINI_API_KEY")
        return self

    @model_validator(mode="after")
    def _require_real_secrets(self) -> "Settings":
        """Fail at startup outside development if a secret is a public dev default or too short."""
        jwt_secret = self.jwt_secret.get_secret_value()
        business_number_key = self.business_number_key.get_secret_value()
        if self.app_env != "development" and (
            jwt_secret == DEV_JWT_SECRET or len(jwt_secret) < JWT_SECRET_MIN_LENGTH
        ):
            raise ValueError(
                f"JWT_SECRET must be set (≥{JWT_SECRET_MIN_LENGTH} chars) when APP_ENV is not development"
            )
        if self.app_env != "development" and business_number_key == DEV_BUSINESS_NUMBER_KEY:
            raise ValueError("BUSINESS_NUMBER_KEY must be set when APP_ENV is not development")
        try:
            Fernet(business_number_key.encode())
        except ValueError as exc:  # not urlsafe-base64 32 bytes → every tax request would 500
            raise ValueError("BUSINESS_NUMBER_KEY must be a Fernet key (urlsafe base64, 32 bytes)") from exc
        return self

    @property
    def voice_answer_enabled(self) -> frozenset[str]:
        """Fields whose voice answer is on (DL-31). Others are refused — FE shows buttons/keypad."""
        return frozenset(f.strip() for f in self.voice_answer_fields.split(",") if f.strip())

    @property
    def api_docs_enabled(self) -> bool:
        """/docs · /redoc · /openapi.json only in development — no public API map in production."""
        return self.app_env == "development"

    @property
    def dev_token_enabled(self) -> bool:
        """DL-21 4-5: dev-token is an auth bypass — development env only."""
        return self.app_env == "development"


@lru_cache
def get_settings() -> Settings:
    """Cached settings singleton."""
    return Settings()
