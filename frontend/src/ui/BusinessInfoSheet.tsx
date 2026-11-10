import { useEffect, useRef, useState } from "react";
import { BottomSheet } from "./BottomSheet";
import { Button } from "./Button";
import { UnderlineField } from "./UnderlineField";
import { exceedsTextLimit, NAME_MAX_CHARS } from "../utils/textLimits";

interface BusinessInfoSheetProps {
  open: boolean;
  /** Existing values when editing (the number comes back masked from the server, so it starts empty). */
  initialName?: string;
  submitting?: boolean;
  /** Server 400 INVALID_BUSINESS_NUMBER etc. */
  error?: string | null;
  onClose: () => void;
  onSubmit: (value: { name: string; numberDigits: string }) => void;
}

/** "1234567890" → "123-45-67890" while typing. */
const formatBusinessNumber = (d: string) =>
  d.length <= 3 ? d : d.length <= 5 ? `${d.slice(0, 3)}-${d.slice(3)}` : `${d.slice(0, 3)}-${d.slice(3, 5)}-${d.slice(5)}`;

/**
 * 사업자 정보 등록·수정 (DESIGN.md 세금계산서): 상호 + 사업자등록번호(자동 하이픈, 10자리).
 * Opened once before the first 모의 발행 (400 BUSINESS_INFO_REQUIRED) or from 마이페이지.
 */
export function BusinessInfoSheet({ open, initialName = "", submitting, error, onClose, onSubmit }: BusinessInfoSheetProps) {
  const [name, setName] = useState(initialName);
  const [digits, setDigits] = useState("");
  const edited = useRef(false);
  useEffect(() => {
    if (!open) { edited.current = false; return; }
    if (!edited.current) {
      setName(initialName);
      setDigits("");
    }
  }, [open, initialName]);

  const nameError = exceedsTextLimit(name, NAME_MAX_CHARS) ? `상호는 ${NAME_MAX_CHARS}자까지 입력해 주세요.` : null;
  const ready = name.trim().length > 0 && !nameError && digits.length === 10;
  return (
    <BottomSheet
      open={open}
      title="사업자 정보"
      onClose={onClose}
      footer={
        <Button disabled={!ready} loading={submitting} loadingLabel="확인하는 중…" onClick={() => onSubmit({ name: name.trim(), numberDigits: digits })}>
          저장하기
        </Button>
      }
    >
      <p className="text-body text-text-sub">세금계산서 모의 발행에 써요. 사업자등록번호는 암호화해 보관하고 화면에는 일부만 보여요.</p>
      <fieldset className="design-fields mt-space-6 space-y-space-6" disabled={submitting}>
        <UnderlineField label="상호" value={name} onChange={(value) => { edited.current = true; setName(value); }} error={nameError} placeholder="예: 솔바람식자재" />
        <UnderlineField
          label="사업자등록번호"
          value={digits}
          onChange={(value) => { edited.current = true; setDigits(value); }}
          digitsFormat={{ maxDigits: 10, format: formatBusinessNumber }}
          placeholder="000-00-00000"
          error={error ?? null}
          hint={digits.length > 0 && digits.length < 10 ? `${10 - digits.length}자리 더 입력해 주세요` : undefined}
        />
      </fieldset>
    </BottomSheet>
  );
}
