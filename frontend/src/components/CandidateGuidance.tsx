import { RotateCw } from "lucide-react";
import type { CandidateDraft } from "../utils/candidateDraft";
import { guidanceSummary } from "../utils/guidanceText";
import { prepareGuidanceAudio, readGuidance } from "../utils/guidanceAudio";
import { useGuidanceAudio } from "../utils/useGuidanceAudio";
import { GuidanceSoundButton } from "./GuidanceSoundButton";

/** Read an unconfirmed candidate; replay and mute never submit ledger feedback. */
export function CandidateGuidance({ draft, utterance, identity, active }: { draft: CandidateDraft; utterance?: string; identity: string; active: boolean }) {
  const summary = guidanceSummary(draft, undefined, utterance);
  useGuidanceAudio(summary, identity, active);
  return <div className="flex flex-wrap items-center justify-end gap-space-2">
    <GuidanceSoundButton />
    <button type="button" disabled={!active} aria-label="거래 요약 다시 듣기"
      className="flex min-h-12 items-center gap-space-2 rounded-xl px-3 text-caption text-text-sub active:bg-bg"
      onClick={() => { prepareGuidanceAudio(); void readGuidance(summary); }}>
      <RotateCw className="h-5 w-5" aria-hidden />다시 듣기
    </button>
  </div>;
}
