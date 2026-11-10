import { useRef } from "react";
import { BottomSheet } from "./BottomSheet";
import { Button } from "./Button";
import { POLICY_INTRO, POLICY_META, POLICY_SECTIONS, type PolicyBlock, type PolicySection } from "./privacyPolicy";
import { AI_CONSENT_SECTIONS, TERMS_INTRO, TERMS_META, TERMS_SECTIONS } from "./termsOfService";

export type DocumentKind = "privacy" | "terms" | "ai";

const DOCUMENTS: Record<DocumentKind, { title: string; meta?: string; intro?: string; sections: PolicySection[] }> = {
  privacy: {
    title: "개인정보 처리방침",
    meta: `${POLICY_META.version} · 시행 ${POLICY_META.effectiveDate}`,
    intro: POLICY_INTRO,
    sections: POLICY_SECTIONS,
  },
  terms: {
    title: "이용약관",
    meta: `${TERMS_META.version} · 시행 ${TERMS_META.effectiveDate}`,
    intro: TERMS_INTRO,
    sections: TERMS_SECTIONS,
  },
  ai: { title: "AI 개선 동의 안내", sections: AI_CONSENT_SECTIONS },
};

interface DocumentSheetProps {
  /** Which document to show; null = closed. */
  doc: DocumentKind | null;
  onClose: () => void;
}

/**
 * Full-height reading sheet for 개인정보 처리방침 · 이용약관 · AI 개선 동의 안내 (DESIGN.md 로그인·약관 동의).
 * Table of contents: a numbered list in a white box with a thin border, one row per section (no hidden sideways scroll, 10/12 PM);
 * a row jumps inside the sheet (shown when 3+ sections). Tables become stacked cards.
 */
export function DocumentSheet({ doc, onClose }: DocumentSheetProps) {
  const sectionRefs = useRef<Record<string, HTMLElement | null>>({});
  const current = doc ? DOCUMENTS[doc] : null;
  const sections = current?.sections ?? [];

  return (
    <BottomSheet
      open={!!current}
      tall={sections.length > 2}
      title={current?.title ?? ""}
      onClose={onClose}
      footer={<Button onClick={onClose}>확인</Button>}
    >
      {current?.meta && <p className="text-caption text-text-weak">{current.meta}</p>}
      {current?.intro && <p className="mt-space-3 text-body text-text">{current.intro}</p>}

      {sections.length > 2 && (
      <nav aria-label="목차" className="mt-space-5 rounded-2xl border border-line-strong bg-surface px-2 py-space-2">
        <ul className="m-0 list-none p-0">
          {sections.map((section) => (
            <li key={section.id}>
              <button
                onClick={() => sectionRefs.current[section.id]?.scrollIntoView({ behavior: "smooth", block: "start" })}
                className="flex min-h-11 w-full items-center rounded-xl px-3 py-space-2 text-left text-body text-text-strong active:bg-bg"
              >
                {section.title}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      )}

      {sections.map((section) => (
        <section
          key={section.id}
          ref={(el) => {
            sectionRefs.current[section.id] = el;
          }}
          className={`scroll-mt-2 ${sections.length > 1 ? "pt-space-7" : "pt-space-2"}`}
          aria-labelledby={`policy-${section.id}`}
        >
          <h3 id={`policy-${section.id}`} className="text-subtitle text-text-strong">
            {section.title}
          </h3>
          <div className="mt-space-3 space-y-space-3">
            {section.blocks.map((block, index) => (
              <Block key={index} block={block} />
            ))}
          </div>
        </section>
      ))}
    </BottomSheet>
  );
}

function Block({ block }: { block: PolicyBlock }) {
  if (block.kind === "p") return <p className="text-body text-text">{block.text}</p>;
  if (block.kind === "list") {
    return (
      <ul className="m-0 list-disc space-y-space-1.5 pl-5 text-body text-text marker:text-text-weak">
        {block.items.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    );
  }
  // Table → one card per row: first cell as the card title, the rest as "열 이름 · 값" lines
  const [, ...restHead] = block.head;
  return (
    <div className="space-y-space-2">
      {block.rows.map((row) => {
        const [first, ...rest] = row;
        return (
          <div key={first} className="rounded-2xl bg-bg px-4 py-space-3">
            <p className="text-body font-semibold text-text-strong">{first}</p>
            {rest.map((cell, i) => (
              <p key={i} className="mt-space-1 text-caption text-text">
                {restHead.length > 1 && <span className="mr-1 font-semibold text-text-sub">{restHead[i]}</span>}
                {cell}
              </p>
            ))}
          </div>
        );
      })}
    </div>
  );
}
