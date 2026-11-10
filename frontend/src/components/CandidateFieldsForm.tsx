import { useId } from "react";
import { Button } from "../ui/Button";
import { RecentCounterpartyPicker } from "./RecentCounterpartyPicker";
import type { CandidateDraft, DraftProblem, EditableItem } from "../utils/candidateDraft";

interface Props {
  draft: CandidateDraft;
  problemFields?: string[];
  problems?: DraftProblem[];
  onField: (field: "counterparty" | "total_amount" | "credit", value: string | boolean | null) => void;
  onItem: (index: number, field: keyof EditableItem, value: string) => void;
  onAddItem: () => void;
  onRemoveItem: (index: number) => void;
}

/** Editable candidate fields shared by review and 400 correction flows. */
export function CandidateFieldsForm({ draft, problemFields = [], problems = [], onField, onItem, onAddItem, onRemoveItem }: Props) {
  const id = useId();
  const flagged = (path: string) => problemFields.includes(path) || problems.some((problem) => problem.field === path);
  const errorId = (path: string) => `${id}-${path}-error`;
  const status = (path: string) => ({ "aria-invalid": flagged(path), "aria-describedby": flagged(path) ? errorId(path) : undefined });
  const feedback = (path: string) => flagged(path) ? <span id={errorId(path)} className="block text-caption text-danger">{problems.filter((problem) => problem.field === path).map((problem) => problem.message).join(" ") || "이 항목을 확인해 주세요."}</span> : null;
  const fieldClass = (path: string) => `field content-start${flagged(path) ? " field--problem" : ""}`;

  return (
    <div className="candidate-form">
      <label className={fieldClass("counterparty")}>
        <span>거래처 <span aria-hidden="true">*</span></span>
        <input value={draft.counterparty} aria-label="거래처" {...status("counterparty")} onChange={(event) => onField("counterparty", event.target.value)} autoComplete="off" />
        {feedback("counterparty")}
      </label>

      <RecentCounterpartyPicker value={draft.counterparty} onSelect={(name) => onField("counterparty", name)} />

      <div className="section-heading"><h2>품목</h2><span className="muted">최대 3개</span></div>
      {feedback("items")}
      {draft.items.map((item, index) => (
        <fieldset id={`candidate-item-${index}`} className={`item-card${flagged("items") ? " field--problem" : ""}`} key={index}>
          <legend>{index + 1}번째 품목</legend>
          <label className={fieldClass(`items[${index}].name`)}>
            <span>품목명 <span aria-hidden="true">*</span></span>
            <input value={item.name} aria-label="품목명" {...status(`items[${index}].name`)} onChange={(event) => onItem(index, "name", event.target.value)} />
            {feedback(`items[${index}].name`)}
          </label>
          <div className="field-grid">
            <label className={fieldClass(`items[${index}].qty`)}>
              <span>수량 <span aria-hidden="true">*</span></span>
              <input inputMode="decimal" value={item.qty} aria-label="수량" {...status(`items[${index}].qty`)} onChange={(event) => onItem(index, "qty", event.target.value)} placeholder="직접 입력" />
              {feedback(`items[${index}].qty`)}
            </label>
            <label className={fieldClass(`items[${index}].unit`)}>
              <span>단위</span>
              <input value={item.unit} aria-label="단위" {...status(`items[${index}].unit`)} onChange={(event) => onItem(index, "unit", event.target.value)} placeholder="선택 사항" />
              {feedback(`items[${index}].unit`)}
            </label>
            <label className={fieldClass(`items[${index}].unit_price`)}>
              <span>단가 (원)</span>
              <input inputMode="numeric" value={item.unit_price} aria-label="단가 (원)" {...status(`items[${index}].unit_price`)} onChange={(event) => onItem(index, "unit_price", event.target.value)} placeholder="없으면 비워 두세요" />
              {feedback(`items[${index}].unit_price`)}
            </label>
            <label className={fieldClass(`items[${index}].amount`)}>
              <span>품목 금액 (원)</span>
              <input inputMode="numeric" value={item.amount} aria-label="품목 금액 (원)" {...status(`items[${index}].amount`)} onChange={(event) => onItem(index, "amount", event.target.value)} placeholder="선택 사항" />
              {feedback(`items[${index}].amount`)}
            </label>
          </div>
          <button className="text-link item-card__remove" type="button" onClick={() => onRemoveItem(index)}>이 품목 지우기</button>
        </fieldset>
      ))}
      {draft.items.length < 3 && <Button data-add-item variant="secondary" type="button" onClick={onAddItem}>품목 추가</Button>}

      <label className={fieldClass("total_amount")}>
        <span>거래 총액 (원) <span aria-hidden="true">*</span></span>
        <input inputMode="numeric" value={draft.total_amount} aria-label="거래 총액 (원)" {...status("total_amount")} onChange={(event) => onField("total_amount", event.target.value)} placeholder="직접 확인해 주세요" />
        {feedback("total_amount")}
      </label>
      <fieldset tabIndex={-1} aria-describedby={flagged("credit") ? errorId("credit") : undefined} className={`choice-group${flagged("credit") ? " field--problem" : ""}`}>
        <legend>결제 상태 <span aria-hidden="true">*</span></legend>
        <label><input type="radio" {...status("credit")} name="credit" checked={draft.credit === true} onChange={() => onField("credit", true)} /> 미수금</label>
        <label><input type="radio" {...status("credit")} name="credit" checked={draft.credit === false} onChange={() => onField("credit", false)} /> 결제 완료</label>
        {feedback("credit")}
      </fieldset>
    </div>
  );
}
