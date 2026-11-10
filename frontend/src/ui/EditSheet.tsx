import { useEffect, useRef, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { BottomSheet } from "./BottomSheet";
import { Button } from "./Button";
import { UnderlineField } from "./UnderlineField";
import type { CandidateView, ItemView } from "./types";

const MAX_ITEMS = 3;
const EMPTY_ITEM: ItemView = { name: null, qty: null, unit: null, unitPrice: null, amount: null };

interface EditSheetProps {
  open: boolean;
  candidate: CandidateView;
  /** Field paths the server flagged, e.g. ["credit", "items[0].qty"] — shown in red. */
  problemFields?: string[];
  /** Item to scroll to when opened from an item row's ✏️. */
  focusItem?: number | null;
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (next: CandidateView) => void;
}

/** Full edit form in a bottom sheet (DESIGN.md §5-2). Layout only — validation stays on the server. */
export function EditSheet({ open, candidate, problemFields = [], focusItem = null, submitting, onClose, onSubmit }: EditSheetProps) {
  const [draft, setDraft] = useState<CandidateView>(candidate);
  const itemRefs = useRef<(HTMLFieldSetElement | null)[]>([]);
  useEffect(() => {
    if (open) setDraft(candidate);
  }, [open, candidate]);
  useEffect(() => {
    if (open && focusItem != null) itemRefs.current[focusItem]?.scrollIntoView({ block: "center" });
  }, [open, focusItem]);

  const flagged = (path: string) => problemFields.includes(path);
  const setItem = (index: number, patch: Partial<ItemView>) =>
    setDraft((prev) => ({ ...prev, items: prev.items.map((item, i) => (i === index ? { ...item, ...patch } : item)) }));

  return (
    <BottomSheet
      open={open}
      title="내용 수정하기"
      onClose={onClose}
      footer={
        <Button loading={submitting} loadingLabel="확인하는 중…" onClick={() => onSubmit(draft)}>
          수정 완료
        </Button>
      }
    >
      <UnderlineField
        label="거래처"
        value={draft.counterparty ?? ""}
        onChange={(v) => setDraft({ ...draft, counterparty: v || null })}
        error={flagged("counterparty") ? "거래처를 확인해 주세요" : null}
      />

      {draft.items.map((item, index) => (
        <fieldset
          key={index}
          ref={(el) => {
            itemRefs.current[index] = el;
          }}
          className="m-0 mt-space-8 border-0 p-0"
        >
          <legend className="sr-only">{index + 1}번째 품목</legend>
          <div className="flex items-center justify-between">
            <span className={`text-body font-semibold ${focusItem === index ? "text-brand" : "text-text-strong"}`}>{index + 1}번째 품목</span>
            {draft.items.length > 1 && (
              <button
                aria-label={`${index + 1}번째 품목 지우기`}
                onClick={() => setDraft({ ...draft, items: draft.items.filter((_, i) => i !== index) })}
                className="flex h-12 w-12 items-center justify-center text-text-weak"
              >
                <Trash2 className="h-5 w-5" />
              </button>
            )}
          </div>
          <div className="space-y-space-6">
            <UnderlineField
              label="품목명"
              value={item.name ?? ""}
              onChange={(v) => setItem(index, { name: v || null })}
              error={flagged(`items[${index}].name`) ? "품목명을 확인해 주세요" : null}
            />
            <div className="grid grid-cols-2 gap-space-4">
              <UnderlineField
                label="수량"
                decimal
                value={item.qty?.toString() ?? ""}
                onChange={(v) => setItem(index, { qty: v ? Number(v) : null })}
                error={flagged(`items[${index}].qty`) ? "수량을 확인해 주세요" : null}
              />
              <UnderlineField label="단위" value={item.unit ?? ""} placeholder="예: 박스" onChange={(v) => setItem(index, { unit: v || null })} />
            </div>
            <UnderlineField
              label="품목 금액"
              numeric
              suffix="원"
              value={item.amount?.toString() ?? ""}
              placeholder="모르면 비워 두세요"
              onChange={(v) => setItem(index, { amount: v ? Number(v) : null })}
              error={flagged(`items[${index}].amount`) ? "금액을 확인해 주세요" : null}
            />
          </div>
        </fieldset>
      ))}

      {draft.items.length < MAX_ITEMS && (
        <Button variant="small" className="mt-space-6" onClick={() => setDraft({ ...draft, items: [...draft.items, { ...EMPTY_ITEM }] })}>
          <Plus className="h-4 w-4" aria-hidden /> 품목 추가 (최대 3개)
        </Button>
      )}

      <div className="mt-space-8">
        <UnderlineField
          label="총액"
          numeric
          suffix="원"
          value={draft.total?.toString() ?? ""}
          onChange={(v) => setDraft({ ...draft, total: v ? Number(v) : null })}
          error={flagged("total_amount") ? "총액을 확인해 주세요" : null}
        />
      </div>

      <fieldset className="m-0 mt-space-8 border-0 p-0">
        <legend className={`mb-space-2 p-0 text-caption font-semibold ${flagged("credit") ? "text-danger" : "text-text-sub"}`}>결제 상태</legend>
        <div className="grid grid-cols-2 gap-space-3">
          {[
            { value: true, label: "미수금" },
            { value: false, label: "결제 완료" },
          ].map(({ value, label }) => {
            const selected = draft.credit === value;
            return (
              <button
                key={label}
                aria-pressed={selected}
                onClick={() => setDraft({ ...draft, credit: value })}
                className={`h-14 rounded-xl text-button ${
                  selected ? (value ? "bg-unpaid-soft text-unpaid-text ring-2 ring-unpaid" : "bg-paid-soft text-paid ring-2 ring-paid") : "bg-bg text-text-strong"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
      </fieldset>
    </BottomSheet>
  );
}
