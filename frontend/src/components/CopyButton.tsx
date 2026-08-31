import { useRef, useState } from "react";

export function CopyButton({
  text,
  label = "Copy",
  small,
  className = "",
  onCopied,
}: {
  // A function defers building large payloads (transcripts, raw JSON) until click.
  text: string | (() => string);
  label?: string;
  small?: boolean;
  className?: string;
  onCopied?: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | undefined>(undefined);

  const copy = async () => {
    const value = typeof text === "function" ? text() : text;
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // Fallback for non-secure contexts
      const ta = document.createElement("textarea");
      ta.value = value;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setCopied(true);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1500);
    onCopied?.();
  };

  const title = typeof text === "string" && text.length <= 200 ? text : undefined;
  return (
    <button
      className={`btn ${small ? "btn-small" : ""} ${className}`.trim()}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        void copy();
      }}
      title={title}
    >
      {copied ? "Copied!" : label}
    </button>
  );
}

// A .log-pre block with a copy button floated in its top-right corner.
export function CopyPre({ text }: { text: string }) {
  return (
    <div className="copy-wrap">
      <CopyButton text={text} small className="copy-overlay" />
      <pre className="log-pre">{text}</pre>
    </div>
  );
}
