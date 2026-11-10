/** DL-20: one transaction/payment is 1원-1억 원; accumulated debt may exceed it. */
// Keep this DL-20 contract aligned with backend AMOUNT_MAX_KRW when changing deployment limits.
export const MAX_AMOUNT_KRW = 100_000_000;

/** A replay may use its original amount even if the current new-input limit differs. */
export function isValidAmount(value: string, maximum = MAX_AMOUNT_KRW): boolean {
  const amount = Number(value);
  return /^\d+$/.test(value) && Number.isSafeInteger(amount) && amount >= 1 && amount <= maximum;
}
