import { Mail, MessageCircle } from "lucide-react";
import { BottomSheet } from "./BottomSheet";
import { Button } from "./Button";
import { POLICY_META } from "./privacyPolicy";

/**
 * Contact channels. 카카오톡 채널 "에코빌" (@echobill, 10/10 PM 개설).
 * Channel home https://pf.kakao.com/_dTxnFX — public URL, not a secret.
 */
const KAKAO_CHANNEL_CHAT_URL = "https://pf.kakao.com/_dTxnFX/chat";
const CONTACT_EMAIL = POLICY_META.contact;

interface ContactSheetProps {
  open: boolean;
  /** e.g. "에코빌 v0.1" — prefilled in the mail body so we know which build they use. */
  appVersion: string;
  onClose: () => void;
}

/**
 * 문의하기 (DESIGN.md 마이페이지): no in-app form (no server work) —
 * 카카오톡 채널 1:1 chat as the main way (사장님은 이미 카톡 안), e-mail as the backup.
 */
export function ContactSheet({ open, appVersion, onClose }: ContactSheetProps) {
  const mailto =
    `mailto:${CONTACT_EMAIL}?subject=${encodeURIComponent("[에코빌 문의] ")}` +
    `&body=${encodeURIComponent(`\n\n\n---\n${appVersion}`)}`;

  return (
    <BottomSheet open={open} title="문의하기" onClose={onClose}>
      <p className="text-body text-text-sub">궁금한 점이나 불편한 점을 알려 주세요. 보통 1-2일 안에 답해 드려요.</p>
      <div className="mt-space-6 space-y-space-2">
        <a
          href={KAKAO_CHANNEL_CHAT_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="flex h-14 w-full items-center justify-center gap-space-2 rounded-xl bg-[#FEE500] text-button text-black/85"
        >
          <MessageCircle className="h-5 w-5" aria-hidden /> 카카오톡으로 문의하기
        </a>
        <Button variant="secondary" onClick={() => window.location.assign(mailto)}>
          <Mail className="h-5 w-5" aria-hidden /> 이메일로 문의하기
        </Button>
      </div>
      <p className="mt-space-3 text-center text-caption text-text-weak">{CONTACT_EMAIL}</p>
      <p className="mt-space-6 rounded-2xl bg-bg px-4 py-space-3 text-caption text-text-sub">탈퇴·정보 삭제 요청도 여기로 보내 주세요. 본인 확인 후 10일 안에 처리해요.</p>
    </BottomSheet>
  );
}
