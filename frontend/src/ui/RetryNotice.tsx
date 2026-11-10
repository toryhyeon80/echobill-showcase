import { Keyboard } from "lucide-react";
import { MicIcon } from "./MicIcon";
import { Button } from "./Button";
import { NoticeView } from "./NoticeView";

/** Server 400 codes that cannot be fixed with one question — the user has to say it again (DL-30, CLAUDE.md §4-2). */
export type RetryCode = "EXCEED_MAX_ITEMS" | "MULTIPLE_COUNTERPARTIES_DETECTED" | "UNSUPPORTED_TRANSACTION";

const COPY: Record<RetryCode, { title: string; message: string; tip: { label: string; lines: string[] } }> = {
  EXCEED_MAX_ITEMS: {
    title: "한 번에 품목 3개까지 적을 수 있어요",
    message: "품목이 많으면 나눠서 두 번 말해 주세요. 일부만 저장하지 않아요.",
    tip: { label: "이렇게 나눠 말해 보세요", lines: ["① 솔바람에 배추 세 박스, 무 두 개, 대파 한 단 외상", "② 솔바람에 양파 두 망 외상"] },
  },
  MULTIPLE_COUNTERPARTIES_DETECTED: {
    title: "한 번에 한 곳만 적을 수 있어요",
    message: "거래처가 두 곳 이상 들렸어요. 거래처마다 따로 말해 주세요.",
    tip: { label: "이렇게 나눠 말해 보세요", lines: ["① 솔바람에 배추 세 박스 사만 오천 원 외상", "② 새벽상회에 배추 세 박스 사만 오천 원 외상"] },
  },
  UNSUPPORTED_TRANSACTION: {
    title: "아직 적을 수 없는 거래예요",
    message: "지금은 판 거래만 적을 수 있어요. 사 온 거래, 반품·취소는 아직 지원하지 않아요.",
    tip: {
      label: "일부만 받았다면",
      lines: ["전체를 외상으로 적고, 받은 돈은 장부에서 [입금]으로 등록해 주세요."],
    },
  },
};

interface RetryNoticeProps {
  code: RetryCode;
  /** utterance_text (voice: what STT heard; text: what was typed). */
  heard?: string | null;
  fromVoice?: boolean;
  onRetry: () => void;
  onType: () => void;
  onClose: () => void;
}

/** "다시 말해 주세요" screen for 400s that one question cannot fix. Same 30:70 button row as the recording screen. */
export function RetryNotice({ code, heard, fromVoice = true, onRetry, onType, onClose }: RetryNoticeProps) {
  const copy = COPY[code];
  return (
    <NoticeView
      title={copy.title}
      message={copy.message}
      heard={heard ? { label: fromVoice ? "이렇게 들렸어요" : "적은 내용", text: heard } : null}
      tip={copy.tip}
      onClose={onClose}
      actions={
        <div className="flex gap-space-2">
          <Button variant="secondary" className="!w-[30%]" aria-label="글자로 입력하기" onClick={onType}>
            <Keyboard className="h-5 w-5" aria-hidden /> 글자로
          </Button>
          <Button className="!w-[70%]" onClick={onRetry}>
            <MicIcon className="h-5 w-5" /> 다시 말하기
          </Button>
        </div>
      }
    />
  );
}
