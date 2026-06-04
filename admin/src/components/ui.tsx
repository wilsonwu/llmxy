"use client";
import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

/* -------------------------------------------------------------------------- */
/*  Modal                                                                     */
/* -------------------------------------------------------------------------- */

export function Modal({
  open,
  onClose,
  title,
  children,
  footer,
  width = "w-[480px]",
  closeOnBackdrop = true,
}: {
  open: boolean;
  onClose: () => void;
  title?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  width?: string;
  closeOnBackdrop?: boolean;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const onCloseRef = useRef(onClose);

  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKey);
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const focusable = "input:not([type='hidden']):not([disabled]),select:not([disabled]),textarea:not([disabled]),button:not([disabled]):not([data-modal-close]),[tabindex]:not([tabindex='-1'])";
    const focusTimer = window.setTimeout(() => {
      const el = bodyRef.current?.querySelector<HTMLElement>(focusable)
        || ref.current?.querySelector<HTMLElement>(focusable);
      el?.focus();
    }, 0);
    return () => {
      window.clearTimeout(focusTimer);
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [open]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={() => closeOnBackdrop && onClose()}
      role="dialog"
      aria-modal="true"
      aria-label={typeof title === "string" ? title : undefined}
    >
      <div
        ref={ref}
        className={`relative max-h-[90vh] ${width} max-w-full overflow-y-auto rounded-lg border border-gray-200 bg-white p-5 shadow-xl`}
        onClick={(e) => e.stopPropagation()}
      >
        {title !== undefined && (
          <div className="mb-4 flex items-start justify-between gap-4">
            <h2 className="text-lg font-semibold">{title}</h2>
            <button
              type="button"
              aria-label="Close dialog"
              data-modal-close
              className="-mr-1 -mt-1 rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              onClick={onClose}
            >
              ×
            </button>
          </div>
        )}
        <div ref={bodyRef} className="space-y-3">{children}</div>
        {footer && <div className="mt-5 flex justify-end gap-2">{footer}</div>}
      </div>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/*  Toast + Confirm                                                           */
/* -------------------------------------------------------------------------- */

type ToastKind = "success" | "error" | "info";
type Toast = { id: number; kind: ToastKind; msg: string };

type ConfirmOpts = {
  title?: string;
  body?: ReactNode;
  confirmText?: string;
  cancelText?: string;
  danger?: boolean;
};

type Ctx = {
  toast: (msg: string, kind?: ToastKind) => void;
  confirm: (opts: ConfirmOpts) => Promise<boolean>;
};

const ToastCtx = createContext<Ctx | null>(null);

export function useToast() {
  const c = useContext(ToastCtx);
  if (!c) throw new Error("useToast must be used inside <ToastProvider>");
  return c;
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [confirmState, setConfirmState] = useState<
    (ConfirmOpts & { resolve: (v: boolean) => void }) | null
  >(null);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const remove = useCallback(
    (id: number) => setToasts((t) => t.filter((x) => x.id !== id)),
    [],
  );
  const toast = useCallback(
    (msg: string, kind: ToastKind = "info") => {
      const id = Date.now() + Math.random();
      setToasts((t) => [...t, { id, kind, msg }]);
      setTimeout(() => remove(id), 4000);
    },
    [remove],
  );

  const confirm = useCallback(
    (opts: ConfirmOpts) =>
      new Promise<boolean>((resolve) => {
        setConfirmState({ ...opts, resolve });
      }),
    [],
  );

  const value = useMemo<Ctx>(() => ({ toast, confirm }), [toast, confirm]);

  return (
    <ToastCtx.Provider value={value}>
      {children}
      {mounted &&
        createPortal(
          <div className="pointer-events-none fixed right-4 top-4 z-[100] flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
            {toasts.map((t) => (
              <div
                key={t.id}
                role="status"
                className={`pointer-events-auto flex items-start justify-between gap-3 rounded-md border px-3 py-2 text-sm shadow-md ${
                  t.kind === "success"
                    ? "border-green-200 bg-green-50 text-green-800"
                    : t.kind === "error"
                    ? "border-red-200 bg-red-50 text-red-800"
                    : "border-gray-200 bg-white text-gray-800"
                }`}
              >
                <span className="whitespace-pre-line">{t.msg}</span>
                <button
                  aria-label="Dismiss"
                  className="text-gray-400 hover:text-gray-700"
                  onClick={() => remove(t.id)}
                >
                  ×
                </button>
              </div>
            ))}
          </div>,
          document.body,
        )}
      {mounted && confirmState && (
        <Modal
          open={true}
          onClose={() => {
            confirmState.resolve(false);
            setConfirmState(null);
          }}
          title={confirmState.title || "Confirm"}
          width="w-[420px]"
          footer={
            <>
              <button
                className="btn-outline"
                onClick={() => {
                  confirmState.resolve(false);
                  setConfirmState(null);
                }}
              >
                {confirmState.cancelText || "Cancel"}
              </button>
              <button
                className={confirmState.danger ? "btn-primary !bg-red-600 hover:!bg-red-700" : "btn-primary"}
                onClick={() => {
                  confirmState.resolve(true);
                  setConfirmState(null);
                }}
              >
                {confirmState.confirmText || "Confirm"}
              </button>
            </>
          }
        >
          <div className="text-sm text-gray-700">{confirmState.body}</div>
        </Modal>
      )}
    </ToastCtx.Provider>
  );
}

/* -------------------------------------------------------------------------- */
/*  Badge                                                                     */
/* -------------------------------------------------------------------------- */

type BadgeTone =
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "neutral"
  | "brand"
  | "purple";

const BADGE_TONE: Record<BadgeTone, string> = {
  success: "bg-green-100 text-green-700",
  warning: "bg-amber-100 text-amber-800",
  danger: "bg-red-100 text-red-700",
  info: "bg-sky-100 text-sky-800",
  neutral: "bg-gray-100 text-gray-700",
  brand: "bg-brand-100 text-brand-700",
  purple: "bg-purple-100 text-purple-700",
};

export function Badge({
  tone = "neutral",
  children,
  className = "",
}: {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
}) {
  return <span className={`badge ${BADGE_TONE[tone]} ${className}`}>{children}</span>;
}

/* -------------------------------------------------------------------------- */
/*  List cells                                                                 */
/* -------------------------------------------------------------------------- */

export function ListCell({
  primary,
  secondary,
  className = "",
  align = "left",
}: {
  primary: ReactNode;
  secondary?: ReactNode;
  className?: string;
  align?: "left" | "right";
}) {
  const alignClass = align === "right" ? "items-end text-right" : "items-start text-left";
  return (
    <div className={`flex min-w-0 flex-col gap-1 ${alignClass} ${className}`}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5 text-sm font-medium leading-5 text-gray-900">
        {primary}
      </div>
      {secondary !== undefined && (
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs leading-4 text-gray-500">
          {secondary}
        </div>
      )}
    </div>
  );
}

export function ListMeta({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <span className={`inline-flex min-w-0 items-center gap-1 ${className}`}>{children}</span>;
}

export function ListActions({ children }: { children: ReactNode }) {
  return <div className="flex flex-nowrap items-center justify-end gap-1.5 whitespace-nowrap">{children}</div>;
}

export function IconButton({
  label,
  icon,
  tone = "neutral",
  onClick,
  disabled = false,
  className = "",
}: {
  label: string;
  icon: "edit" | "delete";
  tone?: "neutral" | "danger";
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}) {
  const toneClass = tone === "danger"
    ? "border-transparent text-red-600 hover:bg-red-50"
    : "border-gray-300 bg-white text-gray-700 hover:bg-gray-50";
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-600/40 focus-visible:ring-offset-1 disabled:cursor-not-allowed disabled:opacity-50 ${toneClass} ${className}`}
    >
      <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {icon === "edit" ? (
          <>
            <path d="M12 20h9" />
            <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" />
          </>
        ) : (
          <>
            <path d="M3 6h18" />
            <path d="M8 6V4h8v2" />
            <path d="M19 6l-1 14H6L5 6" />
            <path d="M10 11v5" />
            <path d="M14 11v5" />
          </>
        )}
      </svg>
    </button>
  );
}

/* -------------------------------------------------------------------------- */
/*  Skeleton / TableSkeleton / EmptyState / Spinner                           */
/* -------------------------------------------------------------------------- */

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-gray-200 ${className}`} />;
}

export function TableSkeleton({ cols, rows = 5 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r}>
          {Array.from({ length: cols }).map((_, c) => (
            <td key={c}>
              <Skeleton className="h-4 w-full max-w-[160px]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export function EmptyState({
  title,
  hint,
  action,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
      <p className="text-sm font-medium text-gray-700">{title}</p>
      {hint && <p className="max-w-md text-xs text-gray-500">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Spinner({ className = "h-4 w-4" }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      aria-label="Loading"
    >
      <circle cx="12" cy="12" r="10" stroke="currentColor" strokeOpacity="0.25" strokeWidth="4" />
      <path
        d="M22 12a10 10 0 0 1-10 10"
        stroke="currentColor"
        strokeWidth="4"
        strokeLinecap="round"
      />
    </svg>
  );
}
