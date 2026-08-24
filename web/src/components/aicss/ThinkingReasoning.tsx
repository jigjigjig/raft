import { ChevronDown, Sparkles } from "lucide-react";
import { useState, type ReactNode } from "react";

// Adapted from AICSS Thinking + Reasoning; controlled content replaces its demo timer.
export function ThinkingReasoning({ title, children, open = true }: { title: string; children: ReactNode; open?: boolean }) {
  const [expanded, setExpanded] = useState(open);
  return (
    <section className="thinking-reasoning">
      <button type="button" className="thinking-head" onClick={() => setExpanded((value) => !value)} aria-expanded={expanded}>
        <Sparkles size={15} />
        <span>{title}</span>
        <ChevronDown className={expanded ? "chevron open" : "chevron"} size={15} />
      </button>
      {expanded && <div className="thinking-body">{children}</div>}
    </section>
  );
}
