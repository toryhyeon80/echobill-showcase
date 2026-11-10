/** 거래처 이름 제안 기준: 10/22 측정으로 확정(수경 결과표 v2.2). */
const SUGGESTION_POLICY = { minScore: 0.75, nearBest: 0.04, maxCandidates: 3 };
// Absorb arithmetic rounding only; this is far below score gaps from the 300-jamo limit.
const SCORE_GAP_TOLERANCE = 1e-12;

const initials = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ";
const vowels = "ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ";
const finals = " ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ";
const normalize = (name: string) => name.normalize("NFC").trim().replace(/\s+/g, "").toLocaleLowerCase("ko-KR");

function jamo(name: string): string[] {
  return [...normalize(name)].flatMap((char) => {
    const code = char.charCodeAt(0) - 0xac00;
    if (code < 0 || code >= 11172) return [char];
    return [initials[Math.floor(code / 588)], vowels[Math.floor(code / 28) % 21], ...(code % 28 ? [finals[code % 28]] : [])];
  });
}

/** Weighted edit distance; sound-alike consonants are close but are never equal. */
export function counterpartySimilarity(left: string, right: string): number {
  const a = jamo(left), b = jamo(right);
  // Bound quadratic work on malformed/oversized names; never truncate to a false exact match.
  if (!a.length || !b.length || a.length > 300 || b.length > 300) return 0;
  let previous = Array.from({ length: b.length + 1 }, (_, index) => index);
  for (let i = 0; i < a.length; i++) {
    const row = [i + 1];
    for (let j = 0; j < b.length; j++) {
      const substitution = a[i] === b[j] ? 0 : ["ㄱㅋㄲ", "ㅅㅈㅆ", "ㄷㅌ"].some((group) => group.includes(a[i]) && group.includes(b[j])) ? 0.35 : 1;
      row.push(Math.min(row[j] + 1, previous[j + 1] + 1, previous[j] + substitution));
    }
    previous = row;
  }
  return 1 - previous[b.length] / Math.max(a.length, b.length);
}

/** Abstain on exact names, blank input, weak matches or more ambiguity than the UI can show. */
export function suggestCounterparties(value: string, names: string[]): string[] {
  const input = normalize(value);
  const unique = [...new Map(names.map((name) => [normalize(name), name.trim()])).values()].filter(Boolean);
  if (!input || unique.some((name) => normalize(name) === input)) return [];
  const ranked = unique.map((name) => ({ name, score: counterpartySimilarity(value, name) }))
    .filter(({ score }) => score >= SUGGESTION_POLICY.minScore).sort((a, b) => b.score - a.score);
  const close = ranked.filter(({ score }) => ranked[0].score - score <= SUGGESTION_POLICY.nearBest + SCORE_GAP_TOLERANCE);
  return close.length <= SUGGESTION_POLICY.maxCandidates ? close.map(({ name }) => name) : [];
}
