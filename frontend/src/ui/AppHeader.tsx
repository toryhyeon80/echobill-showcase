import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
  action?: ReactNode;
}

/** Shared 48px header; safe area is outside the fixed content row. */
export function AppHeader({ children, action }: Props) {
  return <header className="app-header">
    <div className="app-header__row">
      <div className="flex min-w-0 items-center gap-space-2">{children}</div>
      {action && <div className="flex shrink-0 items-center">{action}</div>}
    </div>
  </header>;
}
