import { useLocation, useNavigate } from "react-router-dom";
import { BottomTab, type TabKey } from "../ui/BottomTab";
const paths: Record<TabKey, string> = { record: "/record", ledger: "/ledger", tax: "/tax-invoice" };

/** Keep the candidate leave guard when navigating through the new bottom tabs. */
export function AppTabs({ embedded = false }: { embedded?: boolean }) {
  const location = useLocation();
  const navigate = useNavigate();
  const active: TabKey = location.pathname.startsWith("/ledger") ? "ledger" : location.pathname.startsWith("/tax-invoice") ? "tax" : "record";
  return <BottomTab active={active} embedded={embedded} onSelect={(key) => {
    navigate(paths[key]);
  }} />;
}
