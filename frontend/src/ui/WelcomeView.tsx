import { CheckCircle2, ClipboardCheck, BookOpen } from "lucide-react";
import { MicIcon } from "./MicIcon";
import { Button } from "./Button";

interface WelcomeViewProps {
  onStart: () => void;
}

const STEPS = [
  { Icon: MicIcon, title: "말로 적어요", text: "“솔바람에 배추 세 박스 사만 오천 원 외상”처럼 말해 주세요" },
  { Icon: ClipboardCheck, title: "확인해요", text: "정리된 내용을 보고 틀린 곳만 고쳐요" },
  { Icon: BookOpen, title: "장부에 쌓여요", text: "받을 돈과 입금이 한눈에 보여요" },
];

/** Right after sign-up (DESIGN.md 가입 완료): greeting, 3-step how-to, one CTA. Consent lives in the agreement sheet. */
export function WelcomeView({ onStart }: WelcomeViewProps) {
  return (
    <div className="eb flex min-h-[100dvh] flex-col bg-surface px-page pt-[env(safe-area-inset-top)]">
      <main className="flex-1 pt-[12dvh]">
        <CheckCircle2 className="h-12 w-12 text-brand" aria-hidden />
        <h1 className="mt-space-4 text-title text-text-strong">
          가입이 끝났어요
          <br />
          이제 말로 장부를 적어 보세요
        </h1>

        <ol className="m-0 mt-space-8 list-none space-y-space-5 p-0">
          {STEPS.map(({ Icon, title, text }, index) => (
            <li key={title} className="flex gap-space-4">
              <span className="flex h-12 w-12 shrink-0 items-center justify-center rounded-2xl bg-brand-soft text-brand">
                <Icon className="h-6 w-6" aria-hidden />
              </span>
              <span>
                <span className="block text-body font-semibold text-text-strong">
                  {index + 1}. {title}
                </span>
                <span className="mt-0.5 block text-caption text-text-sub">{text}</span>
              </span>
            </li>
          ))}
        </ol>
      </main>

      <footer className="pb-[calc(16px+env(safe-area-inset-bottom))]">
        <Button onClick={onStart}>말로 첫 기록하기</Button>
      </footer>
    </div>
  );
}
