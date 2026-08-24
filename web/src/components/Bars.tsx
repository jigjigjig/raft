type Row = { key: string; label: string; value: number; caption?: string };

/**
 * A ranked bar list. Bars are proportional to the largest value in the set, so
 * the reader compares magnitudes rather than reading numbers off a legend.
 */
export function Bars({
  rows,
  onSelect,
  emptyLabel = "Nothing to show",
}: {
  rows: Row[];
  onSelect?: (key: string) => void;
  emptyLabel?: string;
}) {
  if (!rows.length) return <div className="empty-state small">{emptyLabel}</div>;
  const peak = Math.max(...rows.map((row) => row.value), 1);
  return (
    <ol className="bars">
      {rows.map((row) => {
        const content = (
          <>
            <span className="bar-label" title={row.label}>{row.label}</span>
            <span className="bar-track" aria-hidden="true">
              <span className="bar-fill" style={{ width: `${Math.max(2, (row.value / peak) * 100)}%` }} />
            </span>
            <span className="bar-value mono">{row.value.toLocaleString()}</span>
            {row.caption && <span className="bar-caption mono">{row.caption}</span>}
          </>
        );
        return (
          <li key={row.key}>
            {onSelect ? (
              <button type="button" onClick={() => onSelect(row.key)}>{content}</button>
            ) : (
              <div>{content}</div>
            )}
          </li>
        );
      })}
    </ol>
  );
}
