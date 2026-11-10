import type { CandidateDraft } from "./candidateDraft";
import { won } from "../ui/types";
import { candidateView } from "./viewModels";

/** Read the heard utterance before asking the user to inspect the saved card values. */
export function guidanceSummary(draft: CandidateDraft, question?: string, utterance?: string, includeHeardQuestion = false): string {
  if (question !== undefined) return includeHeardQuestion && utterance?.trim()
    ? `${utterance.trim().replace(/[.!?。！？]+$/, "")}. ${question}` : question;
  if (utterance?.trim()) return `${utterance.trim().replace(/[.!?。！？]+$/, "")}. 이렇게 들었어요. 화면 내용이 맞으면 장부 확정을 눌러 주세요.`;
  const view = candidateView(draft, "", "");
  const amount = view.total == null ? "" : won(view.total);
  const payment = view.credit === null ? "" : view.credit ? "미수금" : "결제 완료";
  return `${[view.counterparty ?? "거래처 확인 필요", amount, payment].filter(Boolean).join(", ")}. 이 내용이 맞나요?`;
}

/** Fit speech requests into 80 code points, preferring clauses and preserving numeric tokens. */
export function splitGuidanceText(text: string): string[] {
  const characters = Array.from(text.trim());
  const parts: string[] = [];
  let start = 0;
  while (start < characters.length) {
    let end = Math.min(start + 80, characters.length);
    if (end < characters.length) {
      let clauseEnd = 0;
      let wordEnd = 0;
      for (let index = start; index < end; index++) {
        const char = characters[index];
        const insideNumber = /\d/.test(characters[index - 1] ?? "") && /\d/.test(characters[index + 1]);
        if (/[,.!?]/.test(char) && !insideNumber) clauseEnd = index + 1;
        if (/\s/.test(char)) wordEnd = index + 1;
      }
      end = clauseEnd || wordEnd || end;
      // With no clause/space, move a crossing number (including 원) to the next request.
      if (/[\d,.]/.test(characters[end - 1]) && /[\d,.원]/.test(characters[end])) {
        while (end > start && /[\d,.]/.test(characters[end - 1])) end--;
      }
      if (end === start) throw new Error("Numeric token exceeds the speech contract");
    }
    parts.push(characters.slice(start, end).join(""));
    start = end;
  }
  return parts;
}
