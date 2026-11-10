type Tone = "status" | "unpaid" | "paid";

const TONES: Record<Tone, string> = {
  status: "bg-brand-soft text-brand",
  unpaid: "bg-unpaid-soft text-unpaid-text",
  paid: "bg-paid-soft text-paid",
};

/** Small pill (DESIGN.md §5-7): screen status, 미수금, 결제 완료. */
export function Badge({ tone, children }: { tone: Tone; children: string }) {
  return (
    <span className={`inline-flex items-center rounded-lg px-2.5 py-space-1 text-caption font-semibold ${TONES[tone]}`}>
      {children}
    </span>
  );
}
