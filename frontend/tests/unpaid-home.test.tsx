import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { MoveCreditView, type MoveCreditInput } from "../src/ui/MoveCreditView";
import { AmountKeypad } from "../src/ui/AmountKeypad";
import { RecordHomeView, type RecentEntryView } from "../src/ui/RecordHomeView";
import { LedgerView } from "../src/ui/LedgerView";
import { buildCreditMessage, daysAgoLabel, daysBetween } from "../src/utils/creditMessage";
import type { SettlementView } from "../src/ui/types";

const noop = () => undefined;
const RECENT: RecentEntryView[] = [{ id: "1", counterparty: "골목카페", summary: "레몬 5킬로", amount: 45000, credit: false, dateLabel: "오늘" }];

function home(props: Partial<Parameters<typeof RecordHomeView>[0]>) {
  return renderToStaticMarkup(
    <RecordHomeView signedIn unpaidTotal={null} recent={[]} onRecord={noop} onSubmitText={noop} onOpenLedger={noop} onOpenEntry={noop} {...props} />,
  );
}

test("first use shows an empty 받을 돈 card in place, then the invitation", () => {
  const html = home({ firstUse: true, onMoveExisting: noop });
  assert.match(html, /아직 없어요/);
  assert.ok(html.indexOf('aria-label="받을 돈"') < html.indexOf("못 받은 돈부터 적어두세요"));
  assert.match(html, /이미 있는 외상 옮기기/);
  assert.doesNotMatch(html, /animate-pulse|rounded-lg bg-bg/, "text only — no bar that reads as loading");
  assert.doesNotMatch(home({}), /이미 있는 외상 옮기기/, "hidden until the feature is wired");
});

test("with 외상 the 받을 돈 card leads and the record panel is docked last", () => {
  const html = home({ unpaidTotal: 2383000, unpaidNote: "7곳 · 최장 32일", recent: RECENT });
  assert.ok(html.indexOf('aria-label="받을 돈"') < html.indexOf('aria-label="최근 기록"'));
  assert.ok(html.indexOf('aria-label="최근 기록"') < html.indexOf("눌러서 말하기"), "record panel after the list (sticky bottom)");
  assert.match(html, /2,383,000원/);
  assert.match(html, /7곳 · 최장 32일/);
  assert.doesNotMatch(html, /거래를 말로 적어 보세요/);
});

test("records but no 외상 says 받을 돈 없음, never 0원", () => {
  const html = home({ unpaidTotal: 0, recent: RECENT, cashCount: 3 });
  assert.match(html, /받을 돈 없음/);
  assert.match(html, /현금 거래 3건은 장부에서 볼 수 있어요/);
  assert.doesNotMatch(html, />0원</);
});

test("an empty monthly ledger does not claim that the user has never recorded a trade", () => {
  const html = home({ unpaidTotal: 0, recent: [], cashCount: 0 });
  assert.match(html, /이번 달 기록이 없어요/);
  assert.match(html, /받을 돈 없음/);
  assert.doesNotMatch(html, /못 받은 돈부터|아직 없어요|첫 거래/);
  assert.match(home({ firstUse: true }), /못 받은 돈부터/);
  assert.doesNotMatch(home({ firstUse: true, recent: RECENT }), /못 받은 돈부터/);
});

test("the record panel is the same in every state and turns into the text box while typing", () => {
  for (const html of [home({}), home({ unpaidTotal: 0, recent: RECENT }), home({ unpaidTotal: 5000, recent: RECENT })]) {
    assert.match(html, /눌러서 말하기/);
    assert.match(html, /글자로/);
  }
  const typing = home({ typing: true, unpaidTotal: 5000, recent: RECENT });
  assert.doesNotMatch(typing, /눌러서 말하기/);
  assert.match(typing, /후보 만들기/);
  assert.match(typing, />\s*말로\s*</);
  assert.ok(typing.indexOf('aria-label="받을 돈"') < typing.indexOf('aria-label="글자로 입력"'), "받을 돈 stays on top while typing");
});

test("signed-out and load failure keep the regular layout, not the first-use invitation", () => {
  assert.doesNotMatch(home({ signedIn: false }), /못 받은 돈부터/);
  assert.doesNotMatch(home({ loadFailed: true }), /못 받은 돈부터/);
  assert.doesNotMatch(home({ loading: true }), /못 받은 돈부터/);
});

test("ledger balance rows show 경과일 when the API sends it", () => {
  const html = renderToStaticMarkup(
    <LedgerView year={2026} month={10} selectedDay={19} today={19} marks={{}} entries={[]} totalUnpaid={720000}
      balances={[{ counterparty: "한솥밥식당", unpaid: 450000, oldestDays: 32, owedCount: 2 }, { counterparty: "맛나분식", unpaid: 270000 }]}
      onMonth={noop} onDay={noop} onOpenCounterparty={noop} onPayment={noop} onRecord={noop} />,
  );
  assert.match(html, /32일 경과 · 외상 2건/);
  assert.doesNotMatch(html, /최장/, "unknown counterparty dates prevent a global maximum");
  assert.equal((html.match(/일 경과/g) ?? []).length, 1, "no age line for a row without oldestDays");
});

test("a credit from today reads 오늘, not 0일 경과 (병철 #171 리뷰)", () => {
  const html = renderToStaticMarkup(
    <LedgerView year={2026} month={10} selectedDay={19} today={19} marks={{}} entries={[]} totalUnpaid={45000}
      balances={[{ counterparty: "가나상회", unpaid: 45000, oldestDays: 0, owedCount: 1 }]}
      onMonth={noop} onDay={noop} onOpenCounterparty={noop} onPayment={noop} onRecord={noop} />,
  );
  assert.match(html, /오늘 · 외상 1건/);
  assert.doesNotMatch(html, /0일 경과|최장 0일/);
});

const DETAIL: SettlementView = {
  counterparty: "동네국밥", creditTotal: 620000, paidTotal: 350000, unpaid: 270000,
  entries: [
    { id: "0", dateLabel: "9월 10일", total: 150000, allocated: 150000, remaining: 0 },
    { id: "1", dateLabel: "9월 22일", total: 320000, allocated: 200000, remaining: 120000 },
    { id: "2", dateLabel: "10월 10일", total: 150000, allocated: 0, remaining: 150000 },
  ],
  payments: [],
};

test("외상 내역 text lists only what is still owed, oldest first, with the total", () => {
  const text = buildCreditMessage(DETAIL, "새벽청과");
  assert.equal(text, [
    "동네국밥 사장님, 안녕하세요.",
    "외상 내역 정리해서 보내드립니다.",
    "",
    "· 9월 22일 320,000원 중 120,000원 남음",
    "· 10월 10일 150,000원",
    "합계 270,000원",
    "",
    "확인 부탁드립니다. 감사합니다.",
    "— 새벽청과",
  ].join("\n"));
  assert.doesNotMatch(buildCreditMessage(DETAIL), /—/, "no signature without a shop name");
});

test("옮기기 asks who first, then amount and 언제쯤, and sends one total", async () => {
  const sent: MoveCreditInput[] = [];
  let root: ReactTestRenderer | undefined;
  await act(async () => { root = create(<MoveCreditView recentCounterparties={["한솥밥식당"]} onBack={noop} onSubmit={(input) => sent.push(input)} />); });
  const button = (label: RegExp) => root!.root.findAll((node) => node.type === "button" && label.test(textOf(node))).at(0)!;
  assert.equal(button(/^다음$/).props.disabled, true, "no name yet");
  await act(async () => { button(/^한솥밥식당$/).props.onClick(); });
  await act(async () => { button(/^다음$/).props.onClick(); });
  await act(async () => { root!.root.findByType(AmountKeypad).props.onChange("450000"); });
  assert.equal(button(/언제쯤인지 골라 주세요$/).props.disabled, true, "amount alone is not enough");
  await act(async () => { button(/^지난달$/).props.onClick(); });
  const submit = button(/원 옮기기$/);
  assert.match(textOf(submit), /450,000원 옮기기/);
  await act(async () => { submit.props.onClick(); });
  assert.deepEqual(sent, [{ counterparty: "한솥밥식당", amount: 450000, when: "last_month" }]);
});

function textOf(node: { children: unknown[] }): string {
  return node.children.map((child) => (typeof child === "string" ? child : textOf(child as { children: unknown[] }))).join("");
}

test("day counts use KST calendar days", () => {
  assert.equal(daysBetween("2026-09-17", "2026-10-19"), 32);
  assert.equal(daysAgoLabel("2026-10-19", "2026-10-19"), "오늘");
  assert.equal(daysAgoLabel("2026-10-17", "2026-10-19"), "2일 전");
  assert.equal(daysBetween("2026-10-20", "2026-10-19"), 0, "never negative");
});
