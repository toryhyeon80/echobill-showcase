import { Button } from "../ui/Button";
import { useRecentCounterparties } from "../utils/useRecentCounterparties";

interface Props {
  value: string;
  onSelect: (name: string) => void;
}

/** Explicit selection only: recent names help correction without changing the candidate automatically. */
export function RecentCounterpartyPicker({ value, onSelect }: Props) {
  const recent = useRecentCounterparties();
  if (!recent.available) return null;
  return <section className="mb-space-3" aria-label="최근 거래처">
    <p className="mb-space-2 text-caption text-text-sub">최근 거래처</p>
    {recent.loading ? <p className="text-caption text-text-sub" role="status">최근 거래처를 불러오고 있어요.</p>
      : <>
      {recent.failed && <div>
        <p className="text-caption text-text-sub" role="status">최근 거래처를 불러오지 못했어요. 직접 입력해 주세요.</p>
        <Button variant="text" onClick={recent.retry}>최근 거래처 다시 불러오기</Button>
      </div>}
      {recent.names.length ? <div className="flex flex-wrap gap-space-2">
        {recent.names.slice(0, 5).map((name) => <Button key={name} variant="small" aria-pressed={value.trim() === name}
          className={`!h-auto min-h-12 max-w-full whitespace-normal break-words py-space-2 text-left ${value.trim() === name ? "ring-2 ring-brand" : ""}`}
          onClick={() => onSelect(name)}>{name}</Button>)}
      </div> : !recent.failed && <p className="text-caption text-text-sub">최근 기록한 거래처가 없어요. 직접 입력해 주세요.</p>}
      </>}
  </section>;
}
