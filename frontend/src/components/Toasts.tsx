import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { XIcon } from "@phosphor-icons/react";

interface Toast {
  id: number;
  kind: "error" | "success" | "info";
  text: string;
}

interface ToastCtx {
  toastError: (text: string) => void;
  toastSuccess: (text: string) => void;
  toastInfo: (text: string) => void;
}

const Ctx = createContext<ToastCtx | null>(null);

export function useToasts(): ToastCtx {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useToasts outside ToastProvider");
  return ctx;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const push = useCallback((kind: Toast["kind"], text: string) => {
    const id = nextId.current++;
    setToasts((t) => [...t, { id, kind, text }]);
    if (kind !== "error") {
      window.setTimeout(() => setToasts((t) => t.filter((x) => x.id !== id)), 4000);
    }
  }, []);

  const dismiss = (id: number) => setToasts((t) => t.filter((x) => x.id !== id));

  const value: ToastCtx = {
    toastError: useCallback((t: string) => push("error", t), [push]),
    toastSuccess: useCallback((t: string) => push("success", t), [push]),
    toastInfo: useCallback((t: string) => push("info", t), [push]),
  };

  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toast-stack" role="status">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>
            <span className="toast-text">{t.text}</span>
            <button className="toast-close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              <XIcon size={14} weight="bold" />
            </button>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
