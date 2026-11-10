import { Button } from "../ui/Button";
interface ErrorDialogProps {
  message: string;
  onClose: () => void;
  title?: string;
  actionLabel?: string;
  hint?: string;
  transcript?: string | null;
}

/** Centered text-only validation dialog; retains the underlying form. */
export function ErrorDialog({ message, onClose, title = "입력 내용을 확인해 주세요", actionLabel = "확인", hint, transcript }: ErrorDialogProps) {
  return (
    <div className="dialog-backdrop">
      <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="error-title" aria-describedby="error-message">
        <h2 id="error-title">{title}</h2>
        <div id="error-message"><p>{message}</p>{hint && <p>{hint}</p>}
          {transcript && <p>이렇게 들렸어요: {transcript}</p>}
        </div>
        <Button  type="button" onClick={onClose} autoFocus>{actionLabel}</Button>
      </div>
    </div>
  );
}
