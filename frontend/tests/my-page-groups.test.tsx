import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { MyPageView } from "../src/ui/MyPageView";
import { Toggle } from "../src/ui/Toggle";

for (const available of [true, false]) {
  test(`account menus are grouped ledger/settings/information without losing actions: entry=${available}`, () => {
    const actions: string[] = [];
    const action = (name: string) => () => { actions.push(name); };
    let root!: ReactTestRenderer;
    act(() => { root = create(<MyPageView business={null} aiConsent={false} showAiConsent={available} consentBusy
      footnote="test" onBack={action("back")} onEditBusiness={action("business")}
      onMoveExisting={available ? action("move") : undefined} onOpenTerms={available ? action("terms") : undefined}
      onOpenPrivacy={action("privacy")} onContact={action("contact")} onLogout={action("logout")}
      onRequestWithdrawal={action("withdrawal")} onAiConsentChange={action("consent")}
      onGuidanceSoundChange={action("guidance")} onRecordingCueChange={action("cue")}
      onAutoStopRecordingChange={action("silence")} onCounterpartySuggestionsChange={action("suggestions")} />); });
    try {
      assert.deepEqual(root.root.findAllByType("h2").map(heading => heading.children.join("")), available ? ["장부", "설정", "안내"] : ["설정", "안내"]);
      const settings = root.root.findByProps({ "aria-label": "설정" });
      assert.deepEqual(settings.findAllByType(Toggle).map(toggle => toggle.props.label),
        ["음성 안내 켜기/끄기", "녹음 시작 알림", "조용해지면 녹음 끝내기", "비슷한 거래처 제안", "AI 개선에 도움 주기"]);
      assert.equal(settings.findAllByType(Toggle).at(-1)!.props.disabled, true);
      const information = root.root.findByProps({ "aria-label": "안내" });
      assert.equal(information.findAllByType("span").some(span => span.children.includes("있던 외상 옮기기")), false);
      if (available) {
        const ledger = root.root.findByProps({ "aria-label": "장부" });
        act(() => ledger.findByType("button").props.onClick());
        assert.equal(actions[0], "move");
      } else assert.equal(settings.findAllByProps({ hidden: true }).length, 1);
      for (const label of ["이용약관", "개인정보 처리방침", "문의하기", "로그아웃"]) {
        const row = information.findAllByType("button").find(button => button.findAllByType("span").some(span => span.children.includes(label)));
        if (label === "이용약관" && !available) { assert.equal(row, undefined); continue; }
        act(() => row!.props.onClick());
      }
      assert.deepEqual(actions, available ? ["move", "terms", "privacy", "contact", "logout"] : ["privacy", "contact", "logout"]);
      act(() => settings.findAllByType(Toggle)[0].props.onChange(false));
      assert.equal(actions.at(-1), "guidance");
    } finally { act(() => root.unmount()); }
  });
}
