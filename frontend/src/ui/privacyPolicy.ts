/**
 * 개인정보 처리방침 — v1.3 (11/3 PM: OpenAI 보내는 정보에 음성 인식용 최근 거래처 이름 추가 — STT_COUNTERPARTY_HINTS 켬, 사용자가 팀원뿐이라 즉시 시행).
 * v1.2 (10/26 PM: OpenAI 맡기는 일에 안내 문장 음성 변환 추가, #220 읽어 주기 — 업체 그대로, 사용자가 팀원뿐이라 즉시 시행).
 * v1.1 (10/23): 위탁 업체에 Google 추가. v1.0은 10/12 확정. The version string is saved as the signup consent record (DL-32),
 * so bump it whenever the wording changes.
 * One source for the login popup and the future /privacy page. Facts come from the code on main
 * (collected fields, STT handling, processors, retention); update this file when they change.
 * Operator: 아이펠 교육 기간 동안 팀 "말로해!"(대표 최승현), 교육 종료 후 PM 사업자명으로 개정.
 */

export type PolicyBlock =
  | { kind: "p"; text: string }
  | { kind: "list"; items: string[] }
  | { kind: "table"; head: string[]; rows: string[][] };

export interface PolicySection {
  id: string;
  title: string;
  blocks: PolicyBlock[];
}

export const POLICY_META = {
  version: "v1.3",
  effectiveDate: "2026년 11월 3일",
  operator: "말로해! 팀",
  representative: "최승현",
  service: "에코빌(EchoBill)",
  officer: "최승현",
  contact: "privacy@echobill.kr",
};

export const POLICY_INTRO =
  `${POLICY_META.operator}(대표 ${POLICY_META.representative}, 이하 "말로해!")는 ${POLICY_META.service} 서비스를 운영하면서 이용자의 개인정보를 ` +
  "「개인정보 보호법」에 따라 안전하게 처리합니다. 이 방침은 어떤 정보를 왜 받고, 언제까지 보관하며, 어디에 맡기는지 알려 드립니다.";

export const POLICY_SECTIONS: PolicySection[] = [
  {
    id: "purpose",
    title: "1. 개인정보를 처리하는 목적",
    blocks: [
      {
        kind: "list",
        items: [
          "회원 식별과 로그인 유지 (카카오 로그인)",
          "말하거나 적은 거래 내용을 장부로 정리하고, 받을 돈(미수금)과 입금을 관리",
          "세금계산서 모의 발행 (실제 국세청 발행이 아닌 연습용 기능)",
          "오류 대응과 서비스 안정성 확인",
        ],
      },
    ],
  },
  {
    id: "items",
    title: "2. 처리하는 개인정보 항목",
    blocks: [
      {
        kind: "table",
        head: ["구분", "항목"],
        rows: [
          ["로그인", "카카오 회원번호 (닉네임·이메일·프로필 사진은 받지 않습니다)"],
          ["가입 동의", "동의한 약관·처리방침 버전, 동의 시각, AI 개선 동의 여부와 바꾼 시각"],
          ["장부", "거래처 이름, 품목·수량·금액, 외상 여부, 입력한 문장(말한 내용을 글자로 바꾼 것 포함), 장부 후보를 고친 기록"],
          ["입금·세금계산서", "입금 거래처와 금액, 상호, 사업자등록번호(암호화해 저장하고 화면에는 일부만 표시)"],
          ["자동 생성", "로그인 쿠키, 처리 결과 기록(시각·결과 코드)"],
        ],
      },
      {
        kind: "p",
        text: "음성은 글자로 바꾸는 데만 쓰고 저장하지 않습니다. 변환이 끝나면 바로 버립니다.",
      },
    ],
  },
  {
    id: "retention",
    title: "3. 보유 기간",
    blocks: [
      {
        kind: "table",
        head: ["정보", "보유 기간"],
        rows: [
          ["음성", "저장하지 않음 (글자 변환 직후 삭제)"],
          ["중복 처리 방지 기록 (입력 문장·변환 결과 포함)", "24시간 후 자동 삭제"],
          ["회원·가입 동의·장부·입금·세금계산서 정보", "회원 탈퇴 시 또는 서비스 종료 후 30일 이내 파기"],
          ["AI 개선에 쓰는 기록 (동의한 경우만)", "최대 3개월 후 파기"],
          ["데이터베이스 백업 파일", "최대 30일 후 파기"],
        ],
      },
    ],
  },
  {
    id: "destroy",
    title: "4. 파기 절차와 방법",
    blocks: [
      {
        kind: "p",
        text: "보유 기간이 끝나거나 처리 목적을 이루면 지체 없이 파기합니다. 전자 파일은 복구할 수 없는 방법으로 지우고, 백업 파일은 만든 날부터 30일 안에 지웁니다.",
      },
    ],
  },
  {
    id: "third-party",
    title: "5. 제3자 제공",
    blocks: [{ kind: "p", text: "이용자의 개인정보를 다른 회사나 기관에 제공하지 않습니다. 법령에 따른 요청이 있을 때만 예외로 합니다." }],
  },
  {
    id: "outsourcing",
    title: "6. 처리 위탁과 국외 이전",
    blocks: [
      { kind: "p", text: "서비스 운영을 위해 아래 업체에 처리를 맡기며, 일부는 국외에서 처리됩니다." },
      {
        kind: "table",
        head: ["업체 (국가)", "맡기는 일", "보내는 정보", "보관", "업체 개인정보 문의"],
        rows: [
          [
            "OpenAI (미국)",
            "음성을 글자로 변환, 문장을 장부 항목으로 정리, 안내 문장(질문·거래 요약)을 음성으로 변환",
            "음성, 입력 문장, 안내 문장(거래처·품목·금액 요약), 더 잘 알아듣도록 함께 보내는 최근 거래처 이름",
            "모델 학습에 쓰지 않음. 남용 감시 목적으로 최대 30일 보관 후 삭제 (OpenAI 정책)",
            "dsar@openai.com",
          ],
          [
            "Google (미국)",
            "애매한 문장만 장부 항목 정리를 한 번 더 확인",
            "입력 문장 (음성은 글자로 바꾼 문장)",
            "모델 학습에 쓰지 않음. 남용 감시 목적으로 최대 55일 보관 후 삭제 (Google 유료 API 정책)",
            "policies.google.com/privacy",
          ],
          ["Railway (싱가포르)", "서버와 데이터베이스 운영", "2번 항목 전체", "이 방침의 보유 기간과 같음", "privacy@railway.com"],
          [
            "Cloudflare (미국, 전 세계 거점)",
            "접속 보호와 화면 전송",
            "접속 기록(IP 등)",
            "업체 정책에 따름",
            "privacyquestions@cloudflare.com",
          ],
        ],
      },
      {
        kind: "p",
        text: "국외 이전은 서비스 이용 시 암호화된 통신으로 이루어집니다. 이전을 원하지 않으면 서비스 이용을 멈추고 탈퇴를 요청할 수 있습니다.",
      },
    ],
  },
  {
    id: "ai",
    title: "7. AI 개선 활용",
    blocks: [
      {
        kind: "p",
        text: "[선택] AI 개선에 동의한 경우에만, 장부 후보를 고친 내용을 정리 정확도 개선에 씁니다. 거래처 이름 등 알아볼 수 있는 정보는 지우고(가명 처리) 회원 번호는 빼며, 최대 3개월 보관 후 파기합니다. 동의는 기본 꺼짐이고 마이페이지에서 언제든 끌 수 있으며, 끈 뒤의 기록은 쓰지 않습니다.",
      },
    ],
  },
  {
    id: "rights",
    title: "8. 이용자의 권리와 행사 방법",
    blocks: [
      {
        kind: "list",
        items: [
          "내 정보와 장부를 앱에서 언제든 확인할 수 있습니다.",
          "열람·정정·삭제·처리 정지와 회원 탈퇴는 마이페이지의 [탈퇴 요청]·[문의하기](카카오톡 채널·이메일) 또는 아래 연락처로 요청하면 본인 확인 후 10일 이내에 처리합니다.",
          "카카오 연결 끊기는 카카오 계정 설정에서도 할 수 있습니다.",
        ],
      },
    ],
  },
  {
    id: "safety",
    title: "9. 안전하게 지키기 위한 조치",
    blocks: [
      {
        kind: "list",
        items: [
          "모든 통신 암호화(HTTPS), 사업자등록번호 암호화 저장",
          "로그인 쿠키는 브라우저 스크립트가 읽을 수 없게 설정(HttpOnly·Secure)",
          "서버 기록에 사업자등록번호·입력 문장·로그인 토큰을 남기지 않음",
          "관리 계정 2단계 인증, 접근 권한 최소화",
        ],
      },
    ],
  },
  {
    id: "cookies",
    title: "10. 쿠키와 브라우저 저장",
    blocks: [
      {
        kind: "list",
        items: [
          "로그인 유지 쿠키 (24시간), 처음 가입할 때만 쓰는 임시 쿠키 (10분)",
          "작성 중인 장부 초안은 내 기기 브라우저에만 7일간 보관하고, 로그아웃하면 지웁니다.",
          "광고·분석용 추적 도구는 쓰지 않습니다.",
        ],
      },
    ],
  },
  {
    id: "officer",
    title: "11. 개인정보 보호책임자",
    blocks: [
      {
        kind: "table",
        head: ["구분", "내용"],
        rows: [
          ["책임자", `${POLICY_META.officer} (${POLICY_META.operator})`],
          ["연락처", POLICY_META.contact],
        ],
      },
      {
        kind: "p",
        text: "개인정보 침해 신고·상담: 개인정보침해신고센터(국번 없이 118), 개인정보분쟁조정위원회(1833-6972).",
      },
    ],
  },
  {
    id: "changes",
    title: "12. 방침의 변경",
    blocks: [
      {
        kind: "p",
        text: "내용이 바뀌면 시행 7일 전에 앱에서 알립니다. 운영 주체가 바뀌면 이 방침을 개정해 다시 알립니다.",
      },
      {
        kind: "list",
        items: [
          "v1.3 (2026년 11월 3일 시행): OpenAI에 보내는 정보에 음성 인식용 최근 거래처 이름 추가",
          "v1.2 (2026년 10월 26일 시행): OpenAI에 맡기는 일에 안내 문장 음성 변환 추가",
          "v1.1 (2026년 10월 23일 시행): 처리 위탁 업체에 Google 추가",
          "v1.0 (2026년 10월 15일 시행)",
        ],
      },
    ],
  },
];
