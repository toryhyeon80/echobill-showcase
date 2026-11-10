import type { ApiFailure } from "../utils/apiError";
import { NoticeView } from "../ui/NoticeView";
import { Button } from "../ui/Button";
import { RotateCw } from "lucide-react";
import { GuidanceSoundButton } from "./GuidanceSoundButton";
import { useGuidanceAudio } from "../utils/useGuidanceAudio";
import { prepareGuidanceAudio, readGuidance } from "../utils/guidanceAudio";

interface Props {
  message: string;
  redirect: ApiFailure["redirect"];
  onContinue: () => void;
  onClose: () => void;
}

/** Guide non-sale speech to an existing feature without requesting a corrected sale. */
export function NonSaleNotice({ message, redirect, onContinue, onClose }: Props) {
  useGuidanceAudio(message, `non-sale:${redirect}:${message}`);
  const label = redirect === "payment" ? "입금 등록하기" : redirect === "inquiry" ? "받을 돈 확인하기"
    : redirect === "moved_credit" ? "있던 외상 옮기기" : "장부 확인하기";
  return <NoticeView title="여기에서 확인해 주세요" message={message} onClose={onClose}
    actions={<div className="space-y-space-3">
      <div className="flex items-center justify-between">
        <button type="button" className="flex min-h-12 items-center gap-space-2 rounded-xl px-3 text-caption text-text-sub active:bg-bg"
          onClick={() => { prepareGuidanceAudio(); void readGuidance(message); }}>
          <RotateCw className="h-5 w-5" aria-hidden />다시 듣기
        </button>
        <GuidanceSoundButton />
      </div>
      <Button onClick={onContinue}>{label}</Button>
    </div>} />;
}
