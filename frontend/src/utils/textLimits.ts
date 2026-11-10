/** Match CandidateInput/CandidateItemInput and BusinessInfoRequest in the backend schemas. */
export const NAME_MAX_CHARS = 200;
export const UNIT_MAX_CHARS = 50;

/** Submitted fields are trimmed; Python/Pydantic counts Unicode code points, not UTF-16 units. */
export function exceedsTextLimit(value: string, maximum: number): boolean {
  return [...value.trim()].length > maximum;
}
