interface SkeletonProps {
  className?: string;
}

/** Grey placeholder bar (DESIGN.md 5-8): same size as the real content, soft pulse, still for reduced motion. */
export function Skeleton({ className = "" }: SkeletonProps) {
  return <span aria-hidden className={`block rounded-lg bg-bg motion-safe:animate-pulse ${className}`} />;
}

/** N list rows shaped like 거래처 + 요약 / 금액 rows, for lists that are still loading. */
export function SkeletonRows({ rows = 3 }: { rows?: number }) {
  return (
    <div role="status" aria-label="불러오는 중">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center justify-between gap-space-3 border-b border-line py-space-4 last:border-b-0">
          <span className="flex-1 space-y-space-2">
            <Skeleton className="h-5 w-32" />
            <Skeleton className="h-4 w-48" />
          </span>
          <Skeleton className="h-5 w-20" />
        </div>
      ))}
    </div>
  );
}
