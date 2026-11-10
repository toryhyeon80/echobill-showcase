import { AppHeader } from "./AppHeader";
import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight, FileText, LogOut, Mail, Store, UserRound } from "lucide-react";
import { Button } from "./Button";
import { SectionGap } from "./SectionGap";
import { Toggle } from "./Toggle";

interface MyPageViewProps {
  /** null = 아직 등록 안 함 (세금계산서 모의 발행 전에 등록). */
  business: { name: string; numberMasked: string } | null;
  aiConsent: boolean;
  guidanceSound?: boolean;
  onGuidanceSoundChange?: (enabled: boolean) => void;
  recordingCue?: boolean;
  autoStopRecording?: boolean;
  onAutoStopRecordingChange?: (enabled: boolean) => void;
  counterpartySuggestions?: boolean;
  onCounterpartySuggestionsChange?: (enabled: boolean) => void;
  onRecordingCueChange?: (enabled: boolean) => void;
  showAiConsent?: boolean;
  consentBusy?: boolean;
  onAiConsentChange: (value: boolean) => void;
  /** Open the existing-credit entry from the first ledger group. */
  onMoveExisting?: () => void;
  onBack: () => void;
  onEditBusiness: () => void;
  onOpenPrivacy: () => void;
  onOpenTerms?: () => void;
  onContact: () => void;
  onLogout: () => void;
  /** 탈퇴는 요청 접수 → 본인 확인 → 처리 (MVP: 자동 탈퇴 없음). */
  onRequestWithdrawal: () => void;
  /** e.g. "말로해! 팀 · 에코빌 v0.1" */
  footnote: string;
}

/**
 * 마이페이지 — sub screen from the 기록(홈) header person icon (no extra bottom tab, SPEC 3 tabs).
 * Sticky header with the scroll shadow, sections split by grey bands.
 */
export function MyPageView(props: MyPageViewProps) {

  return (
    <div className="eb flex min-h-[100dvh] flex-col bg-surface">
      <AppHeader><button onClick={props.onBack} className="flex h-12 items-center gap-0.5 rounded-xl text-body font-medium text-text-strong">
          <ChevronLeft className="h-6 w-6" aria-hidden /> 마이페이지
        </button></AppHeader>

      <main className="flex-1">
        <section className="flex items-center gap-space-4 px-page pt-space-4 pb-space-7" aria-label="내 계정">
          <span className="flex h-14 w-14 items-center justify-center rounded-full bg-[#FEE500]">
            <UserRound className="h-7 w-7 text-black/80" aria-hidden />
          </span>
          <span>
            <span className="block text-subtitle text-text-strong">사장님</span>
            <span className="mt-0.5 block text-caption text-text-sub">카카오 계정으로 로그인 중</span>
          </span>
        </section>

        {props.onMoveExisting && <>
          <SectionGap />
          <section className="py-space-2" aria-label="장부">
            <h2 className="px-page py-space-4 text-subtitle text-text-strong">장부</h2>
            <MenuRow icon={<Store className="h-5 w-5" aria-hidden />} label="있던 외상 옮기기" onClick={props.onMoveExisting} />
          </section>
        </>}

        <SectionGap />
        <section className="px-page py-space-6" aria-label="설정">
          <h2 className="mb-space-5 text-subtitle text-text-strong">설정</h2>
          <div className="space-y-space-5">
            {props.onGuidanceSoundChange && <Toggle checked={props.guidanceSound ?? true} onChange={props.onGuidanceSoundChange}
              label="음성 안내 켜기/끄기" description="질문과 확인 내용을 읽어 드려요. 이 기기에만 적용돼요." />}
            {props.onRecordingCueChange && <Toggle checked={props.recordingCue ?? true} onChange={props.onRecordingCueChange}
              label="녹음 시작 알림" description="말하기 시작할 때 짧은 소리와 진동으로 알려 드려요. 이 기기에만 적용돼요." />}
            {props.onRecordingCueChange && props.onAutoStopRecordingChange && <Toggle checked={props.autoStopRecording ?? false} onChange={props.onAutoStopRecordingChange}
              label="조용해지면 녹음 끝내기" description="말한 뒤 잠시 조용해지면 녹음을 끝내요. 다 말했어요 버튼도 언제든 누를 수 있어요. 이 기기에만 적용돼요." />}
            {props.onCounterpartySuggestionsChange && <Toggle checked={props.counterpartySuggestions ?? false} onChange={props.onCounterpartySuggestionsChange}
              label="비슷한 거래처 제안" description="기록한 거래처 중 비슷한 이름을 보여 드려요. 직접 눌러야 바뀌어요. 이 기기에만 적용돼요." />}
            <div hidden={props.showAiConsent === false}>
              <Toggle checked={props.aiConsent} disabled={props.consentBusy} onChange={props.onAiConsentChange}
                label="AI 개선에 도움 주기" description="고친 내용을 이름 등을 지운 뒤 인식 개선에만 써요. 끄면 그때부터 쓰지 않아요." />
            </div>
          </div>
        </section>

        <SectionGap />
        <section className="py-space-2" aria-label="안내">
          <h2 className="px-page py-space-4 text-subtitle text-text-strong">안내</h2>
          <section className="px-page pb-space-6" aria-label="사업자 정보">
            <h3 className="text-subtitle text-text-strong">사업자 정보</h3>
            <p className="mt-space-1 text-caption text-text-sub">세금계산서 모의 발행에 써요</p>
            {props.business ? (
              <div className="mt-space-4 flex items-center justify-between gap-space-3">
                <span>
                  <span className="block text-body font-semibold text-text-strong">{props.business.name}</span>
                  <span className="mt-0.5 block text-caption tabular-nums text-text-sub">{props.business.numberMasked}</span>
                </span>
                <Button variant="small" onClick={props.onEditBusiness}>
                  수정
                </Button>
              </div>
            ) : (
              <Button variant="small" className="mt-space-4" onClick={props.onEditBusiness}>
                <Store className="h-4 w-4" aria-hidden /> 사업자 정보 등록하기
              </Button>
            )}
          </section>
          {props.onOpenTerms && <MenuRow icon={<FileText className="h-5 w-5" aria-hidden />} label="이용약관" onClick={props.onOpenTerms} />}
          <MenuRow icon={<FileText className="h-5 w-5" aria-hidden />} label="개인정보 처리방침" onClick={props.onOpenPrivacy} />
          <MenuRow icon={<Mail className="h-5 w-5" aria-hidden />} label="문의하기" onClick={props.onContact} />
          <MenuRow icon={<LogOut className="h-5 w-5" aria-hidden />} label="로그아웃" onClick={props.onLogout} />
        </section>

        <SectionGap />
        <section className="px-page pt-space-6 pb-10 text-center" aria-label="탈퇴">
          {/* Deliberately quiet (10/9 PM): grey underlined text, still a 48px tap target; the confirm dialog carries the warning */}
          <p className="text-caption text-text-weak">{props.footnote}</p>
          <button onClick={props.onRequestWithdrawal} className="mt-space-2 inline-flex min-h-12 items-center px-2 text-caption text-text-weak underline underline-offset-4">
            탈퇴 요청
          </button>
        </section>
      </main>
    </div>
  );
}

function MenuRow({ icon, label, onClick }: { icon: ReactNode; label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="flex h-14 w-full items-center gap-space-3 px-page text-left text-body text-text-strong active:bg-bg">
      <span className="text-text-sub">{icon}</span>
      <span className="flex-1">{label}</span>
      <ChevronRight className="h-5 w-5 text-text-weak" aria-hidden />
    </button>
  );
}
