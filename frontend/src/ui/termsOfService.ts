/**
 * 이용약관 · AI 개선 동의 안내 — v1.0 (10/12 PM 확정). The version is saved as the signup consent record.
 * Same block format as privacyPolicy.ts so DocumentSheet renders both.
 */
import { POLICY_META, type PolicySection } from "./privacyPolicy";

export const TERMS_META = {
  version: "v1.0",
  // Fixed: the privacy policy moved to v1.1 (10/23) but the terms did not change
  effectiveDate: "2026년 10월 15일",
};

export const TERMS_INTRO =
  `이 약관은 ${POLICY_META.operator}(대표 ${POLICY_META.representative}, 이하 "말로해!")가 운영하는 ${POLICY_META.service} 서비스를 이용할 때의 약속을 정합니다.`;

export const TERMS_SECTIONS: PolicySection[] = [
  {
    id: "service",
    title: "1. 서비스 내용",
    blocks: [
      {
        kind: "list",
        items: [
          "말하거나 적은 거래 내용을 장부 후보로 정리하고, 이용자가 확인한 뒤 장부에 저장합니다.",
          "받을 돈(미수금)과 입금 내역을 관리합니다.",
          "세금계산서는 연습용 모의 발행만 제공합니다. 실제 국세청 발행이나 신고가 아닙니다.",
        ],
      },
    ],
  },
  {
    id: "join",
    title: "2. 가입과 탈퇴",
    blocks: [
      {
        kind: "list",
        items: [
          "카카오 계정으로 시작하고, 이 약관과 필수 안내에 동의하면 가입됩니다. 만 14세 이상만 가입할 수 있습니다.",
          "탈퇴는 마이페이지의 [탈퇴 요청]이나 문의로 신청하면 본인 확인 후 10일 이내에 처리하고, 장부와 정보를 지웁니다.",
        ],
      },
    ],
  },
  {
    id: "duty",
    title: "3. 이용자가 지킬 것",
    blocks: [
      {
        kind: "list",
        items: [
          "본인의 거래만 기록하고, 다른 사람의 계정을 쓰지 않습니다.",
          "장부는 이용자가 확인하고 저장한 내용이 기준입니다. 저장 전에 금액·거래처·외상 여부를 확인해 주세요.",
          "서비스를 방해하거나 정상적이지 않은 방법으로 접근하지 않습니다.",
        ],
      },
    ],
  },
  {
    id: "ai-limit",
    title: "4. AI 정리 결과에 대하여",
    blocks: [
      {
        kind: "p",
        text: "AI가 정리한 내용은 후보일 뿐이며 틀릴 수 있습니다. 확실하지 않으면 다시 묻거나 비워 두고, 이용자가 확인해 저장한 뒤에만 장부가 됩니다.",
      },
    ],
  },
  {
    id: "change-stop",
    title: "5. 서비스 변경과 중단",
    blocks: [
      {
        kind: "p",
        text: "서비스는 개발 중이라 기능이 바뀌거나 중단될 수 있습니다. 중요한 변경이나 종료는 7일 전에 앱에서 알리고, 종료 시 정보는 개인정보 처리방침에 따라 파기합니다.",
      },
    ],
  },
  {
    id: "liability",
    title: "6. 책임의 한계",
    blocks: [
      {
        kind: "p",
        text: "말로해!는 고의나 중대한 과실이 없는 한, 이용자가 확인하지 않고 저장한 장부 내용이나 천재지변·통신 장애로 생긴 손해에 책임지지 않습니다. 장부는 참고용 기록이며 세무 신고의 근거 서류를 대신하지 않습니다.",
      },
    ],
  },
  {
    id: "terms-change",
    title: "7. 약관 변경과 분쟁",
    blocks: [
      {
        kind: "list",
        items: [
          "약관을 바꾸면 시행 7일 전에 알리고, 동의하지 않으면 탈퇴할 수 있습니다.",
          "분쟁은 먼저 문의로 해결하고, 해결되지 않으면 대한민국 법에 따릅니다.",
          `문의: ${POLICY_META.contact}`,
        ],
      },
    ],
  },
];

export const AI_CONSENT_SECTIONS: PolicySection[] = [
  {
    id: "ai-consent",
    title: "AI 개선에 도움 주기 (선택)",
    blocks: [
      {
        kind: "table",
        head: ["구분", "내용"],
        rows: [
          ["쓰는 정보", "장부 후보를 고친 내용(거래처 이름 등 알아볼 수 있는 정보는 지움)"],
          ["목적", "말을 장부로 정리하는 정확도 개선"],
          ["보관", "최대 3개월 후 파기"],
          ["거부", "동의하지 않아도 모든 기능을 똑같이 쓸 수 있어요. 마이페이지에서 언제든 끌 수 있어요"],
        ],
      },
    ],
  },
];
