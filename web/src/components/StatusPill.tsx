export function StatusPill({ value }: { value: string }) {
  const tone = ["resolved", "complete", "ok", "clean"].includes(value) ? "good" : ["error", "failed", "wrong answer", "hallucination"].includes(value) ? "bad" : "neutral";
  return <span className={`status-pill ${tone}`}>{value.replaceAll("_", " ")}</span>;
}
