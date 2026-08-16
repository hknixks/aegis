export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="flex items-center gap-3 rounded-lg border border-risk-critical/40 bg-risk-critical/10 p-4 text-sm text-risk-critical"
      data-testid="error-banner"
      role="alert"
    >
      <span aria-hidden="true" className="text-lg">
        ✕
      </span>
      <div>
        <div className="font-semibold uppercase tracking-widest">Aegis Unavailable</div>
        <div className="mt-1 text-console-text">{message}</div>
      </div>
    </div>
  );
}
