import { useEffect, useLayoutEffect, useRef } from "react";
import { won } from "./types";

const useFitEffect = typeof document !== "undefined" && typeof document.createElement === "function" ? useLayoutEffect : useEffect;

/** Keep the full amount and 원 together, fitting the actual available space. */
export function AmountText({ value }: { value: number }) {
  const box = useRef<HTMLSpanElement>(null);
  const text = useRef<HTMLSpanElement>(null);
  useFitEffect(() => {
    const container = box.current;
    const label = text.current;
    if (!container || !label) return;
    let active = true;
    const fit = () => {
      if (!active || !container.clientWidth) return;
      label.style.fontSize = "40px";
      const width = label.getBoundingClientRect().width;
      if (width > container.clientWidth) {
        label.style.fontSize = `${Math.max(18, Math.floor(40 * container.clientWidth / width))}px`;
      }
    };
    fit();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(fit);
    observer?.observe(container);
    window.addEventListener("resize", fit);
    void document.fonts?.ready.then(fit);
    return () => { active = false; observer?.disconnect(); window.removeEventListener("resize", fit); };
  }, [value]);
  return <span ref={box} className="block min-h-12 w-full tabular-nums">
    <span ref={text} style={{ display: "inline-block", whiteSpace: "nowrap", fontSize: 40 }}>{won(value)}</span>
  </span>;
}
