import { ArrowUpRight, Quote } from "lucide-react";
import { Link } from "react-router-dom";
import type { AnswerGroup } from "../types";

const ENUM_WORDS: Record<string, string> = {
  tool_loop: "looped on one tool",
  tool_error: "hit a tool error",
  hallucinated_tool: "claimed an action it never performed",
  wrong_answer: "answered the wrong question",
  context_overflow: "exceeded the context window",
  provider_error: "hit an upstream provider error",
  refusal: "refused",
  timeout: "timed out",
  "unmet request": "outside what the agent can do",
  hallucination: "stated something unverified",
  contradiction: "contradicted itself",
  unsatisfied: "left the user unsatisfied",
  "wrong action": "took the wrong action",
  resolved: "resolved",
};

function words(value?: string | null): string {
  if (!value) return "";
  return ENUM_WORDS[value] ?? value.replaceAll("_", " ");
}

/**
 * One emergent group, with what happened to it. The counts and quotes are the
 * point: a label alone is a guess, a label plus five real sentences and the
 * outcome of every conversation behind it is a finding.
 */
export function GroupCard({
  group,
  rank,
  showValue,
}: {
  group: AnswerGroup;
  rank: number;
  showValue: boolean;
}) {
  const profile = group.profile;
  // A group whose top outcome is "resolved" produced the chip twice: once from
  // top_outcome and once from the resolved count. Same words, same number.
  const seen = new Set<string>();
  const rawFacts: string[] = [];
  const facts: string[] = rawFacts;
  const push = (fact: string) => {
    if (!seen.has(fact)) {
      seen.add(fact);
      rawFacts.push(fact);
    }
  };
  if (profile) {
    if (profile.top_failure && profile.top_failure_count > 0) {
      push(`${profile.top_failure_count} ${words(profile.top_failure)}`);
    } else if (profile.top_outcome && profile.top_outcome_count > 0) {
      push(`${profile.top_outcome_count} ${words(profile.top_outcome)}`);
    }
    if (profile.gave_up > 0) push(`${profile.gave_up} gave up`);
    if (profile.resolved > 0) push(`${profile.resolved} resolved`);
    if (profile.avg_turns) push(`${profile.avg_turns} turns avg`);
    if (profile.cost_usd) push(`$${profile.cost_usd.toFixed(4)}`);
  }

  return (
    <article className="group-card">
      <header>
        <span className="group-rank mono">{String(rank).padStart(2, "0")}</span>
        <div className="group-title">
          <h3>{group.label}</h3>
          <p className="group-count">
            <strong>{group.count.toLocaleString()}</strong> conversations · {(group.share * 100).toFixed(1)}%
            {showValue && group.value_label ? ` · ${group.value_label}` : ""}
          </p>
        </div>
        <Link className="group-open" to={`/traces?trace_ids=${group.trace_ids.slice(0, 200).join(",")}`}>
          Open all {group.count.toLocaleString()} <ArrowUpRight size={14} />
        </Link>
      </header>

      {facts.length > 0 && (
        <ul className="group-facts">
          {facts.map((fact) => <li key={fact}>{fact}</li>)}
        </ul>
      )}

      {profile?.apps?.length ? (
        <p className="group-where">
          {profile.apps.length === 1 ? "Only in " : "Mostly in "}
          <span className="mono">{profile.apps.join(", ")}</span>
          {profile.tools?.length ? <> · tools <span className="mono">{profile.tools.join(", ")}</span></> : null}
        </p>
      ) : null}

      {group.quotes.length > 0 && (
        <div className="group-quotes">
          <span className="group-quotes-label"><Quote size={12} /> What they actually wrote</span>
          {group.quotes.map((quote) => <blockquote key={quote}>{quote}</blockquote>)}
        </div>
      )}

      {group.terms.length > 0 && (
        <p className="group-terms">
          Recurring wording: {group.terms.slice(0, 5).map((term) => (
            <span key={term} className="term-chip">{term}</span>
          ))}
        </p>
      )}
    </article>
  );
}
