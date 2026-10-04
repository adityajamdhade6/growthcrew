"use client";

import { useEffect, useRef } from "react";
import type { ApiError } from "@/lib/api";

type Tone = "neutral" | "accent" | "good" | "warn" | "bad";

const TONES: Record<Tone, string> = {
  neutral: "bg-raised text-muted",
  accent: "bg-accent-soft text-accent",
  good: "bg-good-soft text-good",
  warn: "bg-warn-soft text-warn",
  bad: "bg-bad-soft text-bad",
};

export function Badge({ tone = "neutral", children }: { tone?: Tone; children: React.ReactNode }) {
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "ghost";
  busy?: boolean;
};

export function Button({ variant = "secondary", busy, className = "", children, ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-accent text-on-accent hover:opacity-90",
    secondary: "border border-line bg-surface text-ink hover:bg-raised",
    danger: "border border-bad/40 bg-surface text-bad hover:bg-bad-soft",
    ghost: "text-muted hover:bg-raised hover:text-ink",
  }[variant];
  return (
    <button
      {...rest}
      disabled={rest.disabled || busy}
      className={`inline-flex min-h-10 items-center justify-center gap-2 rounded-lg px-3.5 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {busy ? "Working…" : children}
    </button>
  );
}

export function Card({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return <section className={`rounded-xl border border-line bg-surface p-4 sm:p-5 ${className}`}>{children}</section>;
}

export function PageHeader(props: { title: string; subtitle?: string; actions?: React.ReactNode }) {
  return (
    <header className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight sm:text-2xl">{props.title}</h1>
        {props.subtitle && <p className="mt-1 text-sm text-muted">{props.subtitle}</p>}
      </div>
      {props.actions && <div className="flex flex-wrap gap-2">{props.actions}</div>}
    </header>
  );
}

export function SectionTitle({ children }: { children: React.ReactNode }) {
  return <h2 className="mb-3 text-xs font-semibold uppercase tracking-wider text-muted">{children}</h2>;
}

export function Loading({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" role="status" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div key={index} className="h-20 animate-pulse rounded-xl border border-line bg-surface" />
      ))}
    </div>
  );
}

export function Empty(props: { title: string; body: string; action?: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-surface px-6 py-10 text-center">
      <p className="font-medium">{props.title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted">{props.body}</p>
      {props.action && <div className="mt-4 flex justify-center">{props.action}</div>}
    </div>
  );
}

export function ErrorState({ error, retry }: { error: ApiError | Error; retry?: () => void }) {
  return (
    <div role="alert" className="rounded-xl border border-bad/30 bg-bad-soft px-4 py-3 text-sm text-bad">
      <p className="font-medium">That did not work</p>
      <p className="mt-0.5">{error.message}</p>
      {retry && (
        <button onClick={retry} className="mt-2 font-medium underline underline-offset-2">
          Try again
        </button>
      )}
    </div>
  );
}

export function Notice({ tone = "accent", children }: { tone?: Tone; children: React.ReactNode }) {
  return <div className={`rounded-lg px-3.5 py-2.5 text-sm ${TONES[tone]}`}>{children}</div>;
}

export function Field(props: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="font-medium">{props.label}</span>
      {props.hint && <span className="ml-2 text-muted">{props.hint}</span>}
      <div className="mt-1.5">{props.children}</div>
    </label>
  );
}

export const inputClass =
  "w-full rounded-lg border border-line bg-surface px-3 py-2 text-base sm:text-sm placeholder:text-muted";

export function Tabs<T extends string>(props: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
}) {
  return (
    <div role="tablist" className="mb-5 inline-flex max-w-full gap-1 overflow-x-auto rounded-lg bg-raised p-1">
      {props.options.map((option) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={props.value === option.value}
          onClick={() => props.onChange(option.value)}
          className={`min-h-9 whitespace-nowrap rounded-md px-3 text-sm font-medium ${
            props.value === option.value ? "bg-surface text-ink shadow-sm" : "text-muted hover:text-ink"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A panel that slides in from the right on desktop and fills the screen on a phone. */
export function Sheet(props: {
  title: string;
  onClose: () => void;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const { onClose } = props;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    panel.current?.focus();
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [onClose]);
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button aria-label="Close" className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div
        ref={panel}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label={props.title}
        className="relative flex h-dvh w-full flex-col bg-surface shadow-2xl outline-none sm:max-w-xl sm:border-l sm:border-line"
      >
        <div className="flex items-center justify-between gap-3 border-b border-line px-4 py-3">
          <h2 className="truncate text-base font-semibold">{props.title}</h2>
          <Button variant="ghost" onClick={onClose} aria-label="Close panel">
            Close
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto px-4 py-4">{props.children}</div>
        {props.footer && (
          <div className="border-t border-line bg-surface px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
            {props.footer}
          </div>
        )}
      </div>
    </div>
  );
}

export function statusTone(status: string): Tone {
  if (["approved", "published", "measured", "done", "significant", "accepted", "confirmed"].includes(status))
    return "good";
  if (["rejected", "blocked", "failed"].includes(status)) return "bad";
  if (["pending_approval", "pending", "running", "scheduled", "inferred", "accepted_partial"].includes(status))
    return "warn";
  return "neutral";
}
