import { forwardRef, useImperativeHandle, useRef, type FormEvent } from "react";
import { ArrowUp, Sparkles } from "lucide-react";

// Adapted from the MIT-licensed AICSS PromptInput composition (aicss.dev).
export type PromptInputHandle = { focus: () => void };

type Props = {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  pending?: boolean;
  placeholder?: string;
};

export const PromptInput = forwardRef<PromptInputHandle, Props>(function PromptInput(
  { value, onChange, onSubmit, pending = false, placeholder = "Ask a question about your traces…" },
  ref,
) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  useImperativeHandle(ref, () => ({ focus: () => textareaRef.current?.focus() }), []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (value.trim() && !pending) onSubmit();
  };

  return (
    <form className="prompt-input" onSubmit={submit}>
      <textarea
        ref={textareaRef}
        aria-label="Question for Raft"
        rows={3}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            submit(event);
          }
        }}
        placeholder={placeholder}
      />
      <div className="prompt-actions">
        <span className="prompt-hint"><Sparkles size={14} /> Evidence-backed answers</span>
        <button className="send-button" type="submit" disabled={!value.trim() || pending} aria-label="Ask Raft">
          <ArrowUp size={17} strokeWidth={2.2} />
        </button>
      </div>
    </form>
  );
});
