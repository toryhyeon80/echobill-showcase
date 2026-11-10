import { useEffect, type RefObject } from "react";
const layers: HTMLElement[] = [];
/** Escape belongs only to the visible top sheet, including nested document viewers. */
export function isTopModal(layer: HTMLElement | null): boolean { return layer !== null && layers.at(-1) === layer; }
const selector = 'button:not(:disabled), a[href], input:not(:disabled), textarea:not(:disabled), select:not(:disabled), [tabindex="0"]';
let bodyOverflow = "";
/** Keep focus in the top sheet and restore the trigger and background scrolling on close. */
export function useModalFocus(open: boolean, ref: RefObject<HTMLElement>) {
  useEffect(() => {
    const layer = ref.current;
    if (!open || !layer) return;
    const trigger = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    if (!layers.length) { bodyOverflow = document.body.style.overflow; document.body.style.overflow = "hidden"; }
    layers.push(layer);
    const controls = () => Array.from(layer.querySelectorAll<HTMLElement>(selector)).filter((node) => node.getClientRects().length > 0);
    (controls()[0] ?? layer).focus();
    const onKey = (event: KeyboardEvent) => {
      if (layers.at(-1) !== layer || event.key !== "Tab") return;
      const items = controls();
      if (!items.length) { event.preventDefault(); layer.focus(); return; }
      const index = items.indexOf(document.activeElement as HTMLElement);
      if (event.shiftKey && index <= 0) { event.preventDefault(); items.at(-1)?.focus(); }
      else if (!event.shiftKey && (index < 0 || index === items.length - 1)) { event.preventDefault(); items[0].focus(); }
    };
    const onFocus = (event: FocusEvent) => {
      if (layers.at(-1) === layer && !layer.contains(event.target as Node)) (controls()[0] ?? layer).focus();
    };
    document.addEventListener("keydown", onKey); document.addEventListener("focusin", onFocus);
    return () => {
      document.removeEventListener("keydown", onKey); document.removeEventListener("focusin", onFocus);
      const index = layers.indexOf(layer); if (index >= 0) layers.splice(index, 1);
      if (!layers.length) document.body.style.overflow = bodyOverflow;
      if (trigger?.isConnected) trigger.focus();
    };
  }, [open, ref]);
}
