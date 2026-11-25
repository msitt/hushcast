import { useEffect, useState, type InputHTMLAttributes } from "react";
import { CopyIcon, EyeIcon, EyeSlashIcon } from "@phosphor-icons/react";
import { useToasts } from "./Toasts";

// What the API sends in place of a stored secret.
export const SECRET_MASK = "••••••••";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "type" | "value"> & {
  value: string;
  // Fetches the stored secret when the value is the mask. Without it, only a
  // typed value can be shown or copied.
  reveal?: () => Promise<string>;
};

// Clipboard API needs a secure context, which a plain-HTTP LAN install isn't.
async function copyText(text: string): Promise<void> {
  if (navigator.clipboard && window.isSecureContext) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const el = document.createElement("textarea");
  el.value = text;
  el.setAttribute("readonly", "");
  el.style.position = "fixed";
  el.style.opacity = "0";
  document.body.appendChild(el);
  el.select();
  const ok = document.execCommand("copy");
  el.remove();
  if (!ok) throw new Error("Copy failed");
}

// API keys are not login credentials. A type="password" input makes password
// managers offer to save them on every form submit, so this is a text input
// masked with CSS and opted out of the common managers' autofill.
export function SecretInput({ className, value, reveal, onFocus, ...props }: Props) {
  const { toastError, toastSuccess } = useToasts();
  const [shown, setShown] = useState(false);
  // The stored secret, shown in place of the mask without touching the
  // parent's value, so revealing a key doesn't count as an edit.
  const [revealed, setRevealed] = useState<string | null>(null);

  useEffect(() => {
    if (value !== SECRET_MASK) setRevealed(null);
  }, [value]);

  const masked = value === SECRET_MASK;
  const display = masked && revealed !== null ? revealed : value;

  const resolve = async (): Promise<string> => {
    if (!masked) return value;
    if (revealed !== null) return revealed;
    if (!reveal) throw new Error("The saved key can't be shown here");
    const v = await reveal();
    setRevealed(v);
    return v;
  };

  const toggle = async () => {
    if (shown) {
      setShown(false);
      return;
    }
    try {
      await resolve();
      setShown(true);
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    }
  };

  const copy = async () => {
    try {
      const v = await resolve();
      if (!v) return;
      await copyText(v);
      toastSuccess("Copied to clipboard");
    } catch (err) {
      toastError(err instanceof Error ? err.message : String(err));
    }
  };

  const canAct = masked ? !!reveal : value !== "";
  const classes = ["secret-input", shown ? "" : "secret-masked", className ?? ""].filter(Boolean).join(" ");

  return (
    <span className="secret-field">
      <input
        {...props}
        value={shown ? display : value}
        type="text"
        className={classes}
        // Callers clear the mask on focus to make room for a new key. Not
        // while the revealed key is on screen, where focus means selecting it.
        onFocus={shown && masked ? undefined : onFocus}
        autoComplete="off"
        autoCorrect="off"
        autoCapitalize="off"
        spellCheck={false}
        data-bwignore="true"
        data-1p-ignore="true"
        data-lpignore="true"
        data-form-type="other"
      />
      <span className="secret-actions">
        <button
          type="button"
          className="icon-btn"
          onClick={toggle}
          disabled={!canAct}
          title={shown ? "Hide" : "Show"}
          aria-label={shown ? "Hide" : "Show"}
        >
          {shown ? <EyeSlashIcon size={16} /> : <EyeIcon size={16} />}
        </button>
        <button type="button" className="icon-btn" onClick={copy} disabled={!canAct} title="Copy" aria-label="Copy">
          <CopyIcon size={16} />
        </button>
      </span>
    </span>
  );
}
