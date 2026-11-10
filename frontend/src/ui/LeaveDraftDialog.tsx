import { ConfirmDialog } from "./ConfirmDialog";

interface LeaveDraftDialogProps {
  open: boolean;
  onStay: () => void;
  onLeave: () => void;
}

/**
 * 작성 중 나가기 확인 (CLAUDE.md §4 FE 호환 가드 3 — dirty /record·확인 화면의 뒤로가기만).
 * Not destructive: the draft stays on this device and 기록(홈) offers "이어서 확인하기".
 * Never used to block closing the KakaoTalk webview — recovery handles that.
 */
export function LeaveDraftDialog({ open, onStay, onLeave }: LeaveDraftDialogProps) {
  return (
    <ConfirmDialog
      open={open}
      title="작성 중인 장부가 있어요"
      message="나가도 내용은 이 기기에 남아 있어서, 기록 화면에서 이어서 확인할 수 있어요."
      cancelLabel="계속 확인"
      confirmLabel="나가기"
      onCancel={onStay}
      onConfirm={onLeave}
    />
  );
}
