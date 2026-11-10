import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { noop } from "./review-fixture";

test("text entry requests focus and keeps only its focused field visible while the keyboard resizes", async () => {
  const viewportDescriptor = Object.getOwnPropertyDescriptor(window, "visualViewport");
  const focusDescriptor = Object.getOwnPropertyDescriptor(document, "activeElement");
  const viewport = new EventTarget();
  const listeners = new Set<EventListenerOrEventListenerObject>();
  const addListener = viewport.addEventListener.bind(viewport);
  const removeListener = viewport.removeEventListener.bind(viewport);
  viewport.addEventListener = (type, listener, options) => {
    if (type === "resize" && listener) listeners.add(listener);
    addListener(type, listener, options);
  };
  viewport.removeEventListener = (type, listener, options) => {
    if (type === "resize" && listener) listeners.delete(listener);
    removeListener(type, listener, options);
  };
  Object.defineProperty(window, "visualViewport", { configurable: true, value: viewport });
  let scrolled = 0, root!: ReactTestRenderer;
  const field = { scrollIntoView: (options: ScrollIntoViewOptions) => {
    assert.equal(options.block, "center"); scrolled++;
  } };
  const props = { unpaidTotal: 0, recent: [], onRecord: noop, onSubmitText: noop, onOpenLedger: noop, onOpenEntry: noop };
  try {
    await act(async () => { root = create(<RecordHomeView {...props} />, { createNodeMock: (element) => element.type === "textarea" ? field : null }); });
    act(() => root.root.findAllByType("button").find((button) => button.children.some((child) => typeof child === "string" && child.trim() === "글자로"))!.props.onClick());
    const textarea = root.root.findByType("textarea");
    assert.equal(textarea.props.autoFocus, true);
    assert.equal(listeners.size, 1);
    Object.defineProperty(document, "activeElement", { configurable: true, value: field });
    act(() => textarea.props.onFocus());
    assert.equal(scrolled, 1);
    act(() => viewport.dispatchEvent(new Event("resize")));
    assert.equal(scrolled, 2);
    Object.defineProperty(document, "activeElement", { configurable: true, value: null });
    act(() => viewport.dispatchEvent(new Event("resize")));
    assert.equal(scrolled, 2, "resizing cannot scroll a field that lost focus");
    Object.defineProperty(document, "activeElement", { configurable: true, value: field });
    act(() => root.root.findAllByType("button").find((button) => button.children.some((child) => typeof child === "string" && child.trim() === "말로"))!.props.onClick());
    assert.equal(listeners.size, 0, "closing text entry releases its listener independently of the DOM ref");
    act(() => viewport.dispatchEvent(new Event("resize")));
    assert.equal(scrolled, 2, "leaving text input removes the viewport listener");
  } finally {
    act(() => root?.unmount());
    assert.equal(listeners.size, 0);
    if (viewportDescriptor) Object.defineProperty(window, "visualViewport", viewportDescriptor);
    else Reflect.deleteProperty(window, "visualViewport");
    if (focusDescriptor) Object.defineProperty(document, "activeElement", focusDescriptor);
    else Reflect.deleteProperty(document, "activeElement");
  }
});
