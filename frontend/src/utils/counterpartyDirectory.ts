import { getLedger, getUnpaid } from "../api/client";

/** Inclusive 90 KST calendar days, including today. Month arithmetic never uses the host timezone. */
export function counterpartyWindow(today: string) {
  const end = new Date(`${today}T00:00:00Z`);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - 89);
  const first = start.toISOString().slice(0, 10);
  const months: string[] = [];
  for (const cursor = new Date(Date.UTC(start.getUTCFullYear(), start.getUTCMonth(), 1)); cursor <= end; cursor.setUTCMonth(cursor.getUTCMonth() + 1)) {
    months.push(cursor.toISOString().slice(0, 7));
  }
  return { first, today, months };
}

/** Both pickers share this list. Partial reads remain useful, but never drive fuzzy suggestions. */
export async function loadCounterpartyDirectory(today: string) {
  const { first, months } = counterpartyWindow(today);
  const sources = await Promise.allSettled([
    ...months.map(async (month) => {
      const ledger = await getLedger(month);
      return ledger.entries.filter((entry) => entry.status === "confirmed" && entry.kst_date >= first && entry.kst_date <= today)
        .map((entry) => ({ name: entry.counterparty?.trim() ?? "", date: entry.created_at }));
    }),
    getUnpaid().then((unpaid) => unpaid.counterparties.filter((row) => row.unpaid > 0)
      .map((row) => ({ name: row.counterparty.trim(), date: "" }))),
  ]);
  const rows = sources.flatMap((source) => source.status === "fulfilled" ? source.value : [])
    .filter((row) => row.name).sort((a, b) => b.date.localeCompare(a.date));
  return { names: [...new Set(rows.map((row) => row.name))], failed: sources.some((source) => source.status === "rejected") };
}
