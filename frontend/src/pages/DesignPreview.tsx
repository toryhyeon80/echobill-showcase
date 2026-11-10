import { useEffect, useState } from "react";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { BottomTab, type TabKey } from "../ui/BottomTab";
import { ConfirmView } from "../ui/ConfirmView";
import { EditSheet } from "../ui/EditSheet";
import { LoginView } from "../ui/LoginView";
import { MyPageView } from "../ui/MyPageView";
import { DocumentSheet, type DocumentKind } from "../ui/DocumentSheet";
import { TermsAgreementSheet } from "../ui/TermsAgreementSheet";
import { WelcomeView } from "../ui/WelcomeView";
import { LedgerView } from "../ui/LedgerView";
import { SettlementSheet } from "../ui/SettlementSheet";
import { PaymentView } from "../ui/PaymentView";
import { MoveCreditView } from "../ui/MoveCreditView";
import { TaxInvoiceView } from "../ui/TaxInvoiceView";
import { BusinessInfoSheet } from "../ui/BusinessInfoSheet";
import { RetryNotice, type RetryCode } from "../ui/RetryNotice";
import { ErrorNotice, type ErrorKind } from "../ui/ErrorNotice";
import { LeaveDraftDialog } from "../ui/LeaveDraftDialog";
import { Toast, useToast } from "../ui/Toast";
import { ContactSheet } from "../ui/ContactSheet";
import { won } from "../ui/types";
import type { CounterpartyBalanceView, InvoiceRowView, LedgerEntryView, SettlementView } from "../ui/types";
import { GreetingView } from "../ui/GreetingView";
import { RecordHomeView, type RecentEntryView } from "../ui/RecordHomeView";
import { RecordingOverlay, type RecordingState } from "../ui/RecordingOverlay";
import { RotateNotice } from "../ui/RotateNotice";
import { DotsSpinner } from "../ui/DotsSpinner";
import { RepairDialog } from "../components/RepairDialog";
import { createCandidateDraft } from "../utils/candidateDraft";
import { TaxMonthControl } from "../ui/TaxMonthControl";
import type { CandidateView, ClarifyQuestion } from "../ui/types";

/**
 * Dev-only design preview (/design). Mock data, no API calls — for PM visual review of
 * DESIGN.md screens before 예령님 wires real data. Excluded from production builds (App.tsx).
 */

const BASE: CandidateView = {
  counterparty: "김사장님네 식당",
  items: [
    { name: "배추", qty: 3, unit: "박스", unitPrice: 15000, amount: 45000 },
    { name: "무", qty: 2, unit: "개", unitPrice: null, amount: 4000 },
  ],
  total: 49000,
  credit: null,
  dateLabel: "10월 8일 (목) · 매출",
  utterance: "김사장님네 식당에 배추 세 박스 사만오천 원, 무 두 개 사천 원",
};

const QUESTIONS: Record<string, ClarifyQuestion> = {
  credit: { field: "credit", question: "외상이에요, 현금이에요?", heard: BASE.utterance, voiceEnabled: true },
  qty: { field: "qty", question: "배추 몇 박스예요?", itemName: "배추", unit: "박스", voiceEnabled: true },
  total: { field: "total", question: "전부 얼마예요?", amountHints: [45000, 49000], voiceEnabled: true },
  counterparty: { field: "counterparty", question: "어느 집이에요?", recentCounterparties: ["솔바람", "새벽상회", "떡집사랑"], voiceEnabled: false },
  mismatch: { field: "mismatch", question: "합계가 4만 9천 원이에요, 4만 5천 원이에요?", choices: [49000, 45000], voiceEnabled: true },
};

type Scenario = keyof typeof QUESTIONS | "ready";

/** Korean labels for the preview switcher (dev only). */
const SCENARIO_LABELS: Record<Scenario, string> = {
  credit: "외상·현금 질문",
  qty: "수량 질문",
  total: "총액 질문",
  counterparty: "거래처 질문",
  mismatch: "합계 불일치",
  ready: "모두 채워짐",
};

function ConfirmPreview({ onExit, onOpenLedger }: { onExit: () => void; onOpenLedger: () => void }) {
  const [leaveOpen, setLeaveOpen] = useState(false);
  const { toast, show, clear } = useToast();
  const [scenario, setScenario] = useState<Scenario>(() => new URLSearchParams(location.search).get("scenario") === "ready" ? "ready" : "credit");
  const [candidate, setCandidate] = useState<CandidateView>(() => scenario === "ready" ? { ...BASE, credit: true } : BASE);
  const [editOpen, setEditOpen] = useState(false);
  const [discardOpen, setDiscardOpen] = useState(false);
  const [voiceState, setVoiceState] = useState<"idle" | "listening" | "processing" | "failed">("idle");
  const [confirming, setConfirming] = useState(false);
  const [focusItem, setFocusItem] = useState<number | null>(null);
  const [tab, setTab] = useState<TabKey>("record");

  const pick = (next: Scenario) => {
    setScenario(next);
    setCandidate(next === "ready" ? { ...BASE, credit: true } : BASE);
    setVoiceState("idle");
  };
  const question = scenario === "ready" ? null : QUESTIONS[scenario];

  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="미리보기 전환 (개발용)">
        {(["credit", "qty", "total", "counterparty", "mismatch", "ready"] as Scenario[]).map((key) => (
          <button key={key} onClick={() => pick(key)} className={`rounded-lg px-2.5 py-1.5 ${scenario === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {SCENARIO_LABELS[key]}
          </button>
        ))}
        <button onClick={() => setVoiceState(voiceState === "failed" ? "idle" : "failed")} className="rounded-lg bg-bg px-2.5 py-1.5">
          {voiceState === "failed" ? "음성 답 실패 끄기" : "음성 답 실패 보기"}
        </button>
      </div>

      <ConfirmView
        candidate={candidate}
        question={question}
        confirming={confirming}
        voiceState={voiceState}
        bottomTab={<BottomTab embedded active={tab} onSelect={setTab} />}
        voiceError={voiceState === "failed" ? "잘 안 들렸어요. 아래에서 눌러서 골라 주세요." : null}
        onBack={() => setLeaveOpen(true)}
        onAnswer={(value) => {
          if (typeof value === "boolean") setCandidate({ ...candidate, credit: value });
          setScenario("ready");
        }}
        onVoiceAnswer={() => {
          setVoiceState("listening");
          window.setTimeout(() => setVoiceState("processing"), 1500);
          window.setTimeout(() => setVoiceState("idle"), 2500);
        }}
        onCreditChange={(credit) => {
          setCandidate({ ...candidate, credit });
          if (scenario === "credit") setScenario("ready");
        }}
        onEditItem={(index) => {
          setFocusItem(index);
          setEditOpen(true);
        }}
        onEdit={() => {
          setFocusItem(null);
          setEditOpen(true);
        }}
        onDiscard={() => setDiscardOpen(true)}
        onConfirm={() => {
          setConfirming(true);
          window.setTimeout(() => {
            setConfirming(false);
            show({ text: "장부에 저장했어요", action: { label: "장부 보기", onClick: onOpenLedger } });
          }, 1500);
        }}
      />

      <EditSheet
        open={editOpen}
        candidate={candidate}
        problemFields={candidate.credit === null ? ["credit"] : []}
        focusItem={focusItem}
        onClose={() => setEditOpen(false)}
        onSubmit={(next) => {
          setCandidate(next);
          setEditOpen(false);
          if (next.credit !== null) setScenario("ready");
        }}
      />
      <ConfirmDialog
        open={discardOpen}
        title="이 후보를 버릴까요?"
        message="버리면 장부에 기록되지 않아요. 다시 말해서 새로 만들 수 있어요."
        confirmLabel="버리기"
        destructive
        onCancel={() => setDiscardOpen(false)}
        onConfirm={() => setDiscardOpen(false)}
      />
      <LeaveDraftDialog open={leaveOpen} onStay={() => setLeaveOpen(false)} onLeave={onExit} />
      <Toast message={toast} onDone={clear} offset={200} />
    </div>
  );
}

type LoginState = "default" | "checking" | "signup" | "error" | "disabled";
const LOGIN_LABELS: Record<LoginState, string> = {
  default: "기본",
  checking: "확인 중",
  signup: "처음 가입(약관 동의)",
  error: "실패",
  disabled: "카카오 설정 없음",
};

/** Preview flow: [카카오로 시작하기] → 확인 중 → (처음이면) 약관 동의 → 가입 완료 화면. 가입된 회원은 바로 기록(홈). */
function LoginPreview({ onSignedUp, onSignedIn }: { onSignedUp: () => void; onSignedIn: () => void }) {
  const [state, setState] = useState<LoginState>("default");
  const [doc, setDoc] = useState<DocumentKind | null>(null);
  const [submitting, setSubmitting] = useState(false);
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="로그인 상황 전환 (개발용)">
        {(Object.keys(LOGIN_LABELS) as LoginState[]).map((key) => (
          <button key={key} onClick={() => setState(key)} className={`rounded-lg px-2.5 py-1.5 ${state === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {LOGIN_LABELS[key]}
          </button>
        ))}
        <button onClick={onSignedIn} className="rounded-lg bg-bg px-2.5 py-1.5">
          가입된 회원 → 인사 → 홈
        </button>
      </div>
      <LoginView
        checking={state === "checking"}
        error={state === "error" ? "로그인을 완료하지 못했어요. 다시 시도해 주세요." : null}
        disabled={state === "disabled"}
        onKakaoLogin={() => {
          setState("checking");
          window.setTimeout(() => setState("signup"), 1200); // preview: treat as a first-time user
        }}
        onOpenPrivacy={() => setDoc("privacy")}
      />
      <TermsAgreementSheet
        open={state === "signup"}
        submitting={submitting}
        onClose={() => setState("default")}
        onOpenDocument={setDoc}
        onAgree={() => {
          setSubmitting(true);
          window.setTimeout(() => {
            setSubmitting(false);
            onSignedUp();
          }, 1000);
        }}
      />
      <DocumentSheet doc={doc} onClose={() => setDoc(null)} />
    </div>
  );
}

const LEDGER_ENTRIES: Record<number, LedgerEntryView[]> = {
  8: [
    { id: "a", counterparty: "김사장님네 식당", summary: "배추 3박스 · 무 2개", amount: 49000, credit: true },
    { id: "b", counterparty: "솔바람", summary: "대파 2단", amount: 8000, credit: false },
  ],
  7: [{ id: "c", counterparty: "새벽상회", summary: "고등어 10마리", amount: 120000, credit: true }],
  2: [{ id: "d", counterparty: "솔바람", summary: "양파 2망", amount: 18000, credit: false }],
  26: [],
};
// Demo account numbers (backend/app/services/demo_seed.py, PM 10/19) — 7곳 · 2,383,000원 · 최장 32일
const BALANCES: CounterpartyBalanceView[] = [
  { counterparty: "새봄반찬가게", unpaid: 570000, oldestDays: 8, owedCount: 2 },
  { counterparty: "홍성반점", unpaid: 500000, oldestDays: 21, owedCount: 2 },
  { counterparty: "한솥밥식당", unpaid: 450000, oldestDays: 32, owedCount: 2 },
  { counterparty: "동네국밥", unpaid: 270000, oldestDays: 27, owedCount: 2 },
  { counterparty: "소담한정식", unpaid: 240000, oldestDays: 4, owedCount: 1 },
  { counterparty: "바다횟집", unpaid: 185000, oldestDays: 14, owedCount: 2 },
  { counterparty: "맛나분식", unpaid: 168000, oldestDays: 11, owedCount: 2 },
];
const TOTAL_UNPAID = 2383000;
// Partly paid (FIFO) so the 외상 내역 보내기 text shows "… 중 … 남음"
const SETTLEMENT: SettlementView = {
  counterparty: "동네국밥",
  creditTotal: 470000,
  paidTotal: 200000,
  unpaid: 270000,
  oldestDays: 27,
  entries: [
    { id: "1", dateLabel: "9월 22일 (화)", total: 320000, allocated: 200000, remaining: 120000 },
    { id: "2", dateLabel: "10월 10일 (토)", total: 150000, allocated: 0, remaining: 150000 },
  ],
  payments: [{ dateLabel: "10월 4일 (일)", amount: 200000 }],
};

type LedgerState = "default" | "none" | "loading";
function LedgerPreview({ onTab, onRecord, onOpenMyPage }: { onTab: (tab: TabKey) => void; onRecord: () => void; onOpenMyPage: () => void }) {
  const [state, setState] = useState<LedgerState>("default");
  const [ym, setYm] = useState({ year: 2026, month: 10 });
  const [day, setDay] = useState(8);
  const [detail, setDetail] = useState<SettlementView | null>(null);
  const [payOpen, setPayOpen] = useState(false);
  const [who, setWho] = useState("");
  const [amount, setAmount] = useState("");
  const [paying, setPaying] = useState(false);
  const { toast, show, clear } = useToast();
  const none = state === "none";
  const balances = none ? [] : BALANCES;
  const isOct = ym.year === 2026 && ym.month === 10;
  const openPayment = (name: string) => {
    setDetail(null);
    setWho(name);
    setAmount("");
    setPayOpen(true);
  };
  return (
    <div className="preview-tab-screen">
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="장부 상황 전환 (개발용)">
        {([["default", "기본"], ["none", "받을 돈 없음"], ["loading", "불러오는 중"]] as [LedgerState, string][]).map(([key, label]) => (
          <button key={key} onClick={() => setState(key)} className={`rounded-lg px-2.5 py-1.5 ${state === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {label}
          </button>
        ))}
        <button onClick={() => setDay(15)} className="rounded-lg bg-bg px-2.5 py-1.5">
          기록 없는 날
        </button>
      </div>
      <LedgerView
        onOpenMyPage={onOpenMyPage}
        year={ym.year}
        month={ym.month}
        selectedDay={day}
        today={isOct ? 9 : null}
        marks={isOct ? { 2: { paid: true }, 3: { unpaid: true }, 5: { paid: true }, 7: { unpaid: true }, 8: { unpaid: true, paid: true } } : {}}
        entries={isOct ? LEDGER_ENTRIES[day] ?? [] : []}
        totalUnpaid={none ? 0 : TOTAL_UNPAID}
        balances={balances}
        onMonth={(offset) => {
          const d = new Date(ym.year, ym.month - 1 + offset, 1);
          setYm({ year: d.getFullYear(), month: d.getMonth() + 1 });
          setDay(1);
        }}
        onDay={setDay}
        onOpenCounterparty={() => setDetail(SETTLEMENT)}
        onPayment={() => openPayment(balances[0]?.counterparty ?? "")}
        onRecord={onRecord}
        loading={state === "loading"}
      />
      <BottomTab active="ledger" onSelect={onTab} />
      <Toast message={toast} onDone={clear} />
      <SettlementSheet
        detail={detail}
        onClose={() => setDetail(null)}
        onPayment={openPayment}
        shopName="새벽청과"
        onCopied={() => show({ text: "복사했어요 · 카톡 대화방에 붙여 넣으세요" })}
      />
      {payOpen && (
        <PaymentView
          balances={balances}
          counterparty={who}
          amount={amount}
          submitting={paying}
          onCounterparty={(name) => {
            setWho(name);
            setAmount("");
          }}
          onAmount={setAmount}
          onBack={() => setPayOpen(false)}
          onSubmit={() => {
            setPaying(true);
            window.setTimeout(() => {
              setPaying(false);
              setPayOpen(false);
              show({ text: `${who}에서 ${won(Number(amount))} 입금을 등록했어요` });
            }, 1200);
          }}
        />
      )}
    </div>
  );
}

const INVOICES: InvoiceRowView[] = [
  { entryId: "t1", dateLabel: "10월 8일", counterparty: "김사장님네 식당", amount: 49000, status: "none" },
  { entryId: "t2", dateLabel: "10월 7일", counterparty: "새벽상회", amount: 120000, status: "none" },
  { entryId: "t3", dateLabel: "10월 5일", counterparty: "해담정육", amount: 40000, status: "failed", failReason: "모의 발행 서버 응답 없음" },
  { entryId: "t4", dateLabel: "10월 2일", counterparty: "솔바람", amount: 18000, status: "issued" },
];

type TaxState = "registered" | "empty" | "fail";
function TaxPreview({ onTab, onRecord, onOpenMyPage }: { onTab: (tab: TabKey) => void; onRecord: () => void; onOpenMyPage: () => void }) {
  const [month, setMonth] = useState("2026-10");
  const [state, setState] = useState<TaxState>(() => new URLSearchParams(location.search).get("business") === "empty" ? "empty" : "registered");
  const [rows, setRows] = useState<InvoiceRowView[]>(() => new URLSearchParams(location.search).get("rows") === "one" ? INVOICES.slice(0, 1) : INVOICES);
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [bizOpen, setBizOpen] = useState(false);
  const [registered, setRegistered] = useState(state !== "empty");
  const { toast, show, clear } = useToast();
  const setStatus = (id: string, status: InvoiceRowView["status"], failReason?: string) =>
    setRows((prev) => prev.map((row) => (row.entryId === id ? { ...row, status, failReason } : row)));
  const pick = (next: TaxState) => {
    setState(next);
    setRegistered(next !== "empty");
    setMonth("2026-10");
    setRows(INVOICES);
  };
  return (
    <div className="preview-tab-screen">
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="세금계산서 상황 전환 (개발용)">
        {([["registered", "사업자 등록됨"], ["empty", "사업자 미등록"], ["fail", "발행이 실패하는 경우"]] as [TaxState, string][]).map(([key, label]) => (
          <button key={key} onClick={() => pick(key)} className={`rounded-lg px-2.5 py-1.5 ${state === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {label}
          </button>
        ))}
      </div>
      <TaxInvoiceView
        monthControl={<TaxMonthControl value={month} onChange={(value) => { setMonth(value); setRows(value === "2026-10" ? INVOICES : []); }} />}
        onOpenMyPage={onOpenMyPage}
        business={registered ? { name: "솔바람식자재", numberMasked: "123-45-*****" } : null}
        rows={rows}
        onRegisterBusiness={() => setBizOpen(true)}
        onIssue={(id) => setConfirmId(id)}
        onRecord={onRecord}
      />
      <BottomTab active="tax" onSelect={onTab} />
      <Toast message={toast} onDone={clear} />
      <ConfirmDialog
        open={!!confirmId}
        title="모의 세금계산서를 발행할까요?"
        message="연습용 발행이라 실제로 국세청에는 보내지 않아요."
        confirmLabel="모의 발행"
        onCancel={() => setConfirmId(null)}
        onConfirm={() => {
          const id = confirmId!;
          setConfirmId(null);
          setStatus(id, "requested");
          window.setTimeout(() => {
            if (state === "fail") return setStatus(id, "failed", "모의 발행 서버 응답 없음");
            setStatus(id, "issued");
            show({ text: "모의 발행을 마쳤어요" });
          }, 2500);
        }}
      />
      <BusinessInfoSheet
        open={bizOpen}
        initialName={registered ? "솔바람식자재" : ""}
        onClose={() => setBizOpen(false)}
        onSubmit={() => {
          setRegistered(true);
          setBizOpen(false);
          show({ text: "사업자 정보를 저장했어요" });
        }}
      />
    </div>
  );
}

const RETRY_SAMPLES: Record<RetryCode, { label: string; heard: string }> = {
  EXCEED_MAX_ITEMS: { label: "품목 4개 이상", heard: "솔바람에 배추 세 박스 무 두 개 대파 한 단 양파 두 망 외상" },
  MULTIPLE_COUNTERPARTIES_DETECTED: { label: "거래처 여러 곳", heard: "솔바람이랑 새벽상회에 배추 세 박스씩 외상" },
  UNSUPPORTED_TRANSACTION: { label: "지원 안 하는 거래", heard: "새벽상회에서 고등어 열 마리 사 왔어 칠만 원" },
};

function RetryPreview({ onDone }: { onDone: () => void }) {
  const [code, setCode] = useState<RetryCode>("EXCEED_MAX_ITEMS");
  const [voice, setVoice] = useState(true);
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="다시 말하기 안내 상황 전환 (개발용)">
        {(Object.keys(RETRY_SAMPLES) as RetryCode[]).map((key) => (
          <button key={key} onClick={() => setCode(key)} className={`rounded-lg px-2.5 py-1.5 ${code === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {RETRY_SAMPLES[key].label}
          </button>
        ))}
        <button onClick={() => setVoice(!voice)} className="rounded-lg bg-bg px-2.5 py-1.5">
          입력: {voice ? "음성" : "글자"}
        </button>
      </div>
      <div className="relative h-[100dvh] [transform:translateZ(0)]">
        <RetryNotice code={code} heard={RETRY_SAMPLES[code].heard} fromVoice={voice} onRetry={onDone} onType={onDone} onClose={onDone} />
      </div>
    </div>
  );
}

const ERROR_LABELS: Record<ErrorKind, string> = {
  network: "네트워크 끊김",
  server: "서버 장애(500)",
  rate: "잠시 후 다시(429)",
  tooLong: "녹음이 너무 김(413)",
  notFound: "찾을 수 없음(404)",
  expired: "7일 지난 후보(409)",
  conflict: "이미 처리됨(409)",
  alreadyConfirmed: "이미 확정된 보완(409)",
  notConfirmed: "확정 전 발행(409)",
};

function ErrorPreview({ onDone }: { onDone: () => void }) {
  const [kind, setKind] = useState<ErrorKind>("network");
  const [run, setRun] = useState(0);
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="오류 화면 전환 (개발용)">
        {(Object.keys(ERROR_LABELS) as ErrorKind[]).map((key) => (
          <button key={key} onClick={() => { setKind(key); setRun(run + 1); }} className={`rounded-lg px-2.5 py-1.5 ${kind === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {ERROR_LABELS[key]}
          </button>
        ))}
      </div>
      <div className="relative h-[100dvh] [transform:translateZ(0)]">
        <ErrorNotice key={run} kind={kind} retryAfter={kind === "rate" ? 5 : 0} onRetry={onDone} onType={onDone} onClose={onDone} />
      </div>
    </div>
  );
}

type Screen = "loading" | "login" | "welcome" | "greeting" | "home" | "confirm" | "clarify" | "retry" | "error" | "ledger" | "tax" | "mypage";
const SCREEN_LABELS: Record<Screen, string> = {
  loading: "로딩",
  login: "로그인",
  welcome: "가입 완료",
  greeting: "로그인 인사",
  home: "기록(홈)",
  confirm: "기록(확인)",
  clarify: "되묻기(400 보완)",
  retry: "다시 말하기 안내",
  error: "오류 화면",
  ledger: "장부",
  tax: "세금계산서",
  mypage: "마이페이지",
};

/** Preview the actual unsaved 400 correction flow, separately from a saved candidate question. */
function RepairPreview({ onDone }: { onDone: () => void }) {
  const [draft, setDraft] = useState(() => createCandidateDraft({ counterparty: BASE.counterparty, total_amount: BASE.total, credit: null,
    items: BASE.items.map((item) => ({ name: item.name ?? "", qty: item.qty, unit: item.unit, unit_price: item.unitPrice, amount: item.amount })) }));
  return <RepairDialog message="빠진 내용을 알려 주세요." errorCode="MISSING_REQUIRED_FIELD" problemFields={["credit"]}
    draft={draft} busy={false} submitError={null} onChange={setDraft} onClose={onDone} onSubmit={onDone} />;
}

function GreetingPreview({ onDone }: { onDone: () => void }) {
  const [named, setNamed] = useState(true);
  const [run, setRun] = useState(0); // remount to replay
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="인사 상황 전환 (개발용)">
        {([[true, "상호 등록됨"], [false, "상호 없음"]] as [boolean, string][]).map(([key, label]) => (
          <button key={label} onClick={() => { setNamed(key); setRun(run + 1); }} className={`rounded-lg px-2.5 py-1.5 ${named === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {label}
          </button>
        ))}
        <button onClick={() => setRun(run + 1)} className="rounded-lg bg-bg px-2.5 py-1.5">
          다시 보기
        </button>
      </div>
      <GreetingView key={run} businessName={named ? "솔바람식자재" : null} onDone={onDone} durationMs={60000} /* preview: stays until tapped (real: 1.4s) */ />
    </div>
  );
}

function WelcomePreview({ onStart }: { onStart: () => void }) {
  return <WelcomeView onStart={onStart} />;
}

type MyState = "registered" | "empty";
/** Exercise both proposed entry points without saving or pretending a server contract exists. */
function MoveCreditPreview({ onClose, onReviewed, recentCounterparties = [] }: { onClose: () => void; onReviewed: () => void; recentCounterparties?: string[] }) {
  return <MoveCreditView recentCounterparties={recentCounterparties} onBack={onClose}
    onSubmit={() => { onClose(); onReviewed(); }} />;
}

function MyPagePreview({ onBack }: { onBack: () => void }) {
  const [state, setState] = useState<MyState>("registered");
  const [consent, setConsent] = useState(false);
  const [guidanceSound, setGuidanceSound] = useState(true);
  const [moveOpen, setMoveOpen] = useState(false);
  const { toast, show, clear } = useToast();
  const [doc, setDoc] = useState<DocumentKind | null>(null);
  const [bizOpen, setBizOpen] = useState(false);
  const [contactOpen, setContactOpen] = useState(false);
  const [dialog, setDialog] = useState<"logout" | "withdraw" | null>(null);
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="마이페이지 상황 전환 (개발용)">
        {([["registered", "사업자 등록됨"], ["empty", "사업자 미등록"]] as [MyState, string][]).map(([key, label]) => (
          <button key={key} onClick={() => setState(key)} className={`rounded-lg px-2.5 py-1.5 ${state === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {label}
          </button>
        ))}
      </div>
      <MyPageView
        business={state === "registered" ? { name: "솔바람식자재", numberMasked: "123-45-*****" } : null}
        aiConsent={consent}
          guidanceSound={guidanceSound}
          onGuidanceSoundChange={setGuidanceSound}
        onAiConsentChange={setConsent}
        onBack={onBack}
        onEditBusiness={() => setBizOpen(true)}
        onOpenPrivacy={() => setDoc("privacy")}
        onMoveExisting={() => setMoveOpen(true)}
        onContact={() => setContactOpen(true)}
        onLogout={() => setDialog("logout")}
        onRequestWithdrawal={() => setDialog("withdraw")}
        footnote="말로해! 팀 · 에코빌 v0.1"
      />
      <DocumentSheet doc={doc} onClose={() => setDoc(null)} />
      {moveOpen && <MoveCreditPreview recentCounterparties={["한솥밥식당", "동네국밥", "홍성반점"]} onClose={() => setMoveOpen(false)} onReviewed={() => show({ text: "미리보기 완료 · 실제로 저장하지 않았어요" })} />}
      <Toast message={toast} onDone={clear} />
      <ContactSheet open={contactOpen} appVersion="에코빌 v0.1" onClose={() => setContactOpen(false)} />
      <BusinessInfoSheet open={bizOpen} initialName={state === "registered" ? "솔바람식자재" : ""} onClose={() => setBizOpen(false)} onSubmit={() => setBizOpen(false)} />
      <ConfirmDialog
        open={dialog === "logout"}
        title="로그아웃할까요?"
        message="작성 중인 장부 초안은 이 기기에서 지워져요."
        confirmLabel="로그아웃"
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
      <ConfirmDialog
        open={dialog === "withdraw"}
        title="탈퇴를 요청할까요?"
        message="요청을 받으면 본인 확인 후 10일 안에 장부와 정보를 모두 지워요. 지운 정보는 되돌릴 수 없어요."
        confirmLabel="탈퇴 요청"
        destructive
        onCancel={() => setDialog(null)}
        onConfirm={() => setDialog(null)}
      />
    </div>
  );
}

const RECENT: RecentEntryView[] = [
  { id: "1", counterparty: "골목카페", summary: "레몬 5킬로", amount: 45000, credit: false, dateLabel: "오늘" },
  { id: "2", counterparty: "새봄반찬가게", summary: "고춧가루 5근 외 1건", amount: 210000, credit: true, dateLabel: "1일 전" },
  { id: "3", counterparty: "바다횟집", summary: "상추 10박스", amount: 85000, credit: true, dateLabel: "2일 전" },
];
const RECENT_CASH: RecentEntryView[] = [
  { id: "1", counterparty: "골목카페", summary: "레몬 5킬로", amount: 45000, credit: false, dateLabel: "오늘" },
  { id: "2", counterparty: "바다횟집", summary: "쪽파 20단", amount: 60000, credit: false, dateLabel: "5일 전" },
  { id: "3", counterparty: "맛나분식", summary: "떡볶이떡 10킬로", amount: 30000, credit: false, dateLabel: "10일 전" },
];

type HomeState = "default" | "noCredit" | "first" | "draft" | "loading";
const HOME_LABELS: Record<HomeState, string> = {
  default: "외상 있음", noCredit: "외상 없음", first: "처음 사용", draft: "확인 안 한 기록 있음", loading: "불러오는 중",
};

function HomePreview({ onOpenConfirm, onOpenMyPage, onTab }: { onOpenConfirm: () => void; onOpenMyPage: () => void; onTab: (tab: TabKey) => void }) {
  const [state, setState] = useState<HomeState>(() => new URLSearchParams(location.search).get("homeState") === "first" ? "first" : "default");
  const first = state === "first";
  const [recording, setRecording] = useState<RecordingState | null>(null);
  const [seconds, setSeconds] = useState(0);
  const [typing, setTyping] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [skippedExisting, setSkippedExisting] = useState(false);
  const { toast, show, clear } = useToast();
  // Preview-only timer while "recording" (the real page uses MediaRecorder start time)
  useEffect(() => {
    if (recording !== "recording") return;
    const id = window.setInterval(() => setSeconds((s) => s + 1), 1000);
    return () => window.clearInterval(id);
  }, [recording]);
  const startRecording = () => {
    setSeconds(0);
    setRecording("recording");
  };
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm" aria-label="홈 상황 전환 (개발용)">
        {(Object.keys(HOME_LABELS) as HomeState[]).map((key) => (
          <button key={key} onClick={() => setState(key)} className={`rounded-lg px-2.5 py-1.5 ${state === key ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
            {HOME_LABELS[key]}
          </button>
        ))}
        <button onClick={() => setRecording("failed")} className="rounded-lg bg-bg px-2.5 py-1.5">
          녹음 실패 보기
        </button>
      </div>
      <RecordHomeView
        firstUse={first}
        unpaidTotal={first ? null : state === "noCredit" ? 0 : TOTAL_UNPAID}
        unpaidNote="7곳 · 최장 32일"
        cashCount={3}
        onMoveExisting={() => setMoveOpen(true)}
        onSkipExisting={skippedExisting ? undefined : () => { setSkippedExisting(true); startRecording(); }}
        recent={first ? [] : state === "noCredit" ? RECENT_CASH : RECENT}
        draftLabel={state === "draft" ? "김사장님네 식당 · 49,000원" : null}
        onRecord={startRecording}
        typing={typing}
        onTypingChange={setTyping}
        onSubmitText={onOpenConfirm}
        onResumeDraft={onOpenConfirm}
        onOpenLedger={() => undefined}
        onOpenEntry={() => undefined}
        onOpenMyPage={onOpenMyPage}
        loading={state === "loading"}
      />
      <BottomTab active="record" onSelect={onTab} />
      <Toast message={toast} onDone={clear} offset={180} />
      {moveOpen && <MoveCreditPreview recentCounterparties={first ? [] : ["한솥밥식당", "동네국밥", "홍성반점"]} onClose={() => setMoveOpen(false)} onReviewed={() => show({ text: "미리보기 완료 · 실제로 저장하지 않았어요" })} />}
      {recording && (
        <RecordingOverlay
          state={recording}
          seconds={seconds}
          onStop={() => {
            setRecording("processing");
            window.setTimeout(() => {
              setRecording(null);
              onOpenConfirm();
            }, 1800);
          }}
          onCancel={() => setRecording(null)}
          onRetry={startRecording}
          onTypeInstead={() => {
            setRecording(null);
            setTyping(true);
          }}
        />
      )}
    </div>
  );
}

/** Full-screen loading (app start, page data): dots in the middle, a hint after 3 seconds (DESIGN.md §5-8). */
function LoadingPreview() {
  const [slow, setSlow] = useState(false);
  return (
    <div>
      <div className="mb-4 flex flex-wrap gap-1.5 rounded-2xl bg-white p-2 text-caption text-text-sub shadow-sm">
        <button onClick={() => setSlow(!slow)} className={`rounded-lg px-2.5 py-1.5 ${slow ? "bg-brand font-semibold text-white" : "bg-bg"}`}>
          3초 넘음
        </button>
      </div>
      <div className="flex min-h-[100dvh] flex-col items-center justify-center gap-5 bg-surface">
        <DotsSpinner label="불러오는 중" />
        <p className={`text-body text-text-sub ${slow ? "" : "invisible"}`}>조금만 기다려 주세요</p>
      </div>
    </div>
  );
}

export default function DesignPreview() {
  const [screen, setScreen] = useState<Screen>(() => {
    const value = new URLSearchParams(location.search).get("screen");
    return value && value in SCREEN_LABELS ? value as Screen : "home";
  });
  const compare = new URLSearchParams(location.search).has("compare");
  const goTab = (tab: TabKey) => setScreen(tab === "record" ? "home" : tab === "ledger" ? "ledger" : "tax");
  // 마이페이지 ‹ returns to the tab it was opened from (same as the real page)
  const [myPageFrom, setMyPageFrom] = useState<Screen>("home");
  const openMyPage = (from: Screen) => {
    setMyPageFrom(from);
    setScreen("mypage");
  };
  return (
    // Preview chrome (dev only): grey page, white menu cards, a gap before the actual screen
    <div className={compare ? "eb design-preview app-frame" : "eb design-preview mx-auto min-h-screen max-w-md bg-[#d9dde3] px-3 pt-3"}>
      <RotateNotice />
      {!compare && <p className="mb-1.5 px-1 text-caption font-semibold text-text-sub">화면</p>}
      {!compare && <div className="mb-2 grid grid-cols-3 gap-1 rounded-2xl bg-white p-1.5 text-caption shadow-sm" aria-label="화면 전환 (개발용)">
        {(Object.keys(SCREEN_LABELS) as Screen[]).map((key) => (
          <button key={key} onClick={() => setScreen(key)} className={`rounded-lg px-2 py-1.5 font-semibold ${screen === key ? "bg-text-strong text-white" : "text-text-sub"}`}>
            {SCREEN_LABELS[key]}
          </button>
        ))}
      </div>}
      {screen === "loading" && <LoadingPreview />}
      {screen === "login" && <LoginPreview onSignedUp={() => setScreen("welcome")} onSignedIn={() => setScreen("greeting")} />}
      {screen === "welcome" && <WelcomePreview onStart={() => setScreen("home")} />}
      {screen === "greeting" && <GreetingPreview onDone={() => setScreen("home")} />}
      {screen === "home" && <HomePreview onOpenConfirm={() => setScreen("confirm")} onOpenMyPage={() => openMyPage("home")} onTab={goTab} />}
      {screen === "ledger" && <LedgerPreview onTab={goTab} onRecord={() => setScreen("home")} onOpenMyPage={() => openMyPage("ledger")} />}
      {screen === "tax" && <TaxPreview onTab={goTab} onRecord={() => setScreen("home")} onOpenMyPage={() => openMyPage("tax")} />}
      {screen === "retry" && <RetryPreview onDone={() => setScreen("home")} />}
      {screen === "error" && <ErrorPreview onDone={() => setScreen("home")} />}
      {screen === "mypage" && <MyPagePreview onBack={() => setScreen(myPageFrom)} />}
      {screen === "confirm" && <ConfirmPreview onExit={() => setScreen("home")} onOpenLedger={() => setScreen("ledger")} />}
      {screen === "clarify" && <RepairPreview onDone={() => setScreen("home")} />}
    </div>
  );
}
