import { useEffect, useState, type ReactNode } from "react";

export function TextResponse({ children }: { children: ReactNode }) {
  return <div className="text-response">{children}</div>;
}

// Adapted from AICSS StreamingText with reduced-motion support.
export function StreamingText({ text }: { text: string }) {
  const reduceMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
  const [shown, setShown] = useState(reduceMotion ? text : "");
  useEffect(() => {
    if (reduceMotion) { setShown(text); return; }
    let index = 0;
    const timer = window.setInterval(() => {
      index += 4;
      setShown(text.slice(0, index));
      if (index >= text.length) window.clearInterval(timer);
    }, 12);
    return () => window.clearInterval(timer);
  }, [reduceMotion, text]);
  return (
    <p className="streaming-text">
      {shown}
      {shown.length < text.length && <span aria-hidden="true" className="stream-caret" />}
    </p>
  );
}
