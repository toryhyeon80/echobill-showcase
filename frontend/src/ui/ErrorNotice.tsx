import { useEffect, useState } from "react";
import { BookOpen, Keyboard, RotateCw } from "lucide-react";
import { HomeIcon } from "./HomeIcon";
import { MicIcon } from "./MicIcon";
import { Button } from "./Button";
import { NoticeView } from "./NoticeView";

/**
 * Non-input failures (CLAUDE.md §4 HTTP 표) — each has its own words, never the 400 "고쳐 주세요" screen:
 * network = 응답 없음 · server = 500 · rate = 429 (Retry-After) · tooLong = 413
 * notFound = 404 (없음·남의 것·지운 것 구분 안 함, DL-21) · expired = 409 7일 지난 후보 (DL-22)
 * conflict = 409 같은 요청 키에 다른 내용 · notConfirmed = 409 ENTRY_NOT_CONFIRMED (확정 전 세금계산서)
 */
export type ErrorKind = "network" | "server" | "rate" | "tooLong" | "notFound" | "expired" | "conflict" | "notConfirmed" | "alreadyConfirmed";

interface ErrorNoticeProps {
  kind: ErrorKind;
  /** 429 Retry-After seconds. */
  retryAfter?: number;
  retrying?: boolean;
  onRetry: () => void;
  /** 413 · 7일 지난 후보: switch to text input. */
  onType?: () => void;
  onClose: () => void;
}

const COPY: Record<ErrorKind, { title: string; message: string; tip?: { label: string; lines: string[] } }> = {
  network: {
    title: "인터넷 연결을 확인해 주세요",
    message: "서버에 닿지 못했어요. 와이파이나 데이터가 켜져 있는지 확인한 뒤 다시 시도해 주세요.",
    tip: { label: "적은 내용은 그대로 있어요", lines: ["연결되면 [다시 시도]만 누르면 돼요. 처음부터 다시 말하지 않아도 돼요."] },
  },
  server: {
    title: "잠시 문제가 생겼어요",
    message: "말씀하신 내용에는 문제가 없어요. 에코빌 쪽 오류라 잠시 후 다시 시도해 주세요.",
    tip: { label: "계속 안 되면", lines: ["마이페이지의 [문의하기]로 알려 주세요."] },
  },
  rate: {
    title: "잠시 후 다시 시도해 주세요",
    message: "짧은 시간에 요청이 많이 들어왔어요. 조금 기다리면 다시 쓸 수 있어요.",
  },
  tooLong: {
    title: "녹음이 너무 길어요",
    message: "한 번에 30초까지 말할 수 있어요. 거래 한 건씩 짧게 나눠 말하거나 글자로 적어 주세요.",
  },
  notFound: {
    title: "찾을 수 없어요",
    message: "없거나 이미 지워진 내용이에요. 기록 화면에서 다시 확인해 주세요.",
  },
  expired: {
    title: "7일이 지난 기록이에요",
    message: "확인하지 않은 기록은 7일 동안만 확정할 수 있어요. 다시 말하거나 적어서 새로 만들어 주세요.",
  },
  conflict: {
    title: "이미 처리된 요청이에요",
    message: "같은 요청이 다른 내용으로 먼저 처리됐어요. 장부를 확인한 뒤 필요하면 새로 입력해 주세요.",
  },
  alreadyConfirmed: {
    title: "이미 장부에 저장된 내용이에요",
    message: "장부에서 확인해 주세요",
  },
  notConfirmed: {
    title: "장부를 먼저 확정해 주세요",
    message: "확정한 장부만 세금계산서 모의 발행을 할 수 있어요. 확인 화면에서 [장부 확정]을 눌러 주세요.",
  },
};

/** Error screens on the shared text-only NoticeView (DESIGN.md 오류 화면). */
export function ErrorNotice({ kind, retryAfter = 0, retrying = false, onRetry, onType, onClose }: ErrorNoticeProps) {
  const copy = COPY[kind];
  // 429: one timer that unlocks the button — no per-second polling (CLAUDE.md §4 FE)
  const [waiting, setWaiting] = useState(kind === "rate" && retryAfter > 0);
  useEffect(() => {
    setWaiting(kind === "rate" && retryAfter > 0);
    if (kind !== "rate" || retryAfter <= 0) return;
    const id = window.setTimeout(() => setWaiting(false), retryAfter * 1000);
    return () => window.clearTimeout(id);
  }, [kind, retryAfter]);

  const tip =
    kind === "rate"
      ? { label: "기다릴 시간", lines: [retryAfter > 0 ? `약 ${retryAfter < 60 ? `${retryAfter}초` : `${Math.ceil(retryAfter / 60)}분`} 뒤에 다시 할 수 있어요` : "잠시 뒤에 다시 할 수 있어요"] }
      : copy.tip ?? null;

  return (
    <NoticeView
      title={copy.title}
      message={copy.message}
      tip={tip}
      onClose={onClose}
      actions={
        kind === "notFound" ? (
          <Button onClick={onRetry}>
            <HomeIcon className="h-5 w-5" /> 기록 화면으로
          </Button>
        ) : kind === "conflict" || kind === "notConfirmed" || kind === "alreadyConfirmed" ? (
          <Button onClick={onRetry}>
            <BookOpen className="h-5 w-5" aria-hidden /> 장부 확인하기
          </Button>
        ) : kind === "expired" ? (
          <div className="flex gap-space-2">
            <Button variant="secondary" className="!w-[30%]" aria-label="글자로 입력하기" onClick={onType}>
              <Keyboard className="h-5 w-5" aria-hidden /> 글자로
            </Button>
            <Button className="!w-[70%]" onClick={onRetry}>
              <MicIcon className="h-5 w-5" /> 다시 말하기
            </Button>
          </div>
        ) : kind === "tooLong" ? (
          <div className="flex gap-space-2">
            <Button variant="secondary" className="!w-[30%]" aria-label="다시 말하기" onClick={onRetry}>
              <MicIcon className="h-5 w-5" /> 다시
            </Button>
            <Button className="!w-[70%]" onClick={onType}>
              <Keyboard className="h-5 w-5" aria-hidden /> 글자로 입력하기
            </Button>
          </div>
        ) : (
          <Button disabled={waiting || retrying} loading={retrying} loadingLabel="다시 확인 중…" onClick={onRetry}>
            <RotateCw className="h-5 w-5" aria-hidden /> {waiting ? "조금만 기다려 주세요" : "다시 시도"}
          </Button>
        )
      }
    />
  );
}
