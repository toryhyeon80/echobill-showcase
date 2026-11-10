import { useEffect, useRef, useState } from "react";
import { currentSessionEpoch } from "../api/client";
import { Button } from "../ui/Button";
import { suggestCounterparties } from "../utils/counterpartySuggestions";
import { trackSuggestion, useCounterpartySuggestions } from "../utils/counterpartySuggestionSettings";
import { useRecentCounterparties } from "../utils/useRecentCounterparties";

interface Props { value: string; onSelect: (name: string) => void; onEdit: () => void }

/** Suggestions change only the local review draft. The existing confirm action remains mandatory. */
export function CounterpartySuggestion({ value, onSelect }: Props) {
  const enabled = useCounterpartySuggestions();
  const recent = useRecentCounterparties(enabled);
  const epoch = currentSessionEpoch();
  const [selection, setSelection] = useState<{ before: string; after: string; epoch: number } | null>(null);
  const shown = useRef(new Set<string>());
  const names = enabled && recent.available && !recent.loading && !recent.failed ? suggestCounterparties(value, recent.names) : [];
  const signature = JSON.stringify([epoch, value, names]);
  useEffect(() => {
    if (names.length && !shown.current.has(signature)) { shown.current.add(signature); trackSuggestion("shown"); }
  }, [signature]);
  useEffect(() => {
    if (selection && (value !== selection.after || epoch !== selection.epoch || !enabled)) setSelection(null);
  }, [value, epoch, enabled, selection]);
  const canUndo = enabled && recent.available && selection?.epoch === epoch && value === selection.after;
  if (canUndo) return <div className="mt-space-3 mb-space-4 flex items-center justify-between gap-space-3 rounded-xl bg-bg px-space-3 py-space-2" role="status">
    <p className="min-w-0 text-caption text-text-sub">{selection.after}로 바꿨어요</p>
    <Button variant="small" className="shrink-0" onClick={() => { onSelect(selection.before); setSelection(null); trackSuggestion("undone"); }}>되돌리기</Button>
  </div>;
  if (!names.length) return null;
  return <section className="mt-space-3 mb-space-4 space-y-space-2" aria-label="거래처 이름 제안">
    {names.map((name) => <div key={name} className="flex items-center justify-between gap-space-3 rounded-xl bg-bg px-space-3 py-space-2">
      <p className="min-w-0 text-caption text-text-sub">{`혹시 ${name}인가요?`}</p>
      <Button variant="small" className="shrink-0" aria-label={name + " 선택"}
        onClick={() => { setSelection({ before: value, after: name, epoch }); onSelect(name); trackSuggestion("selected"); }}>선택</Button>
    </div>)}
  </section>;
}
