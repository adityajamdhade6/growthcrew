"use client";

import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, plural, useSession } from "@/lib/session";
import { Badge, Button, Card, Empty, ErrorState, Loading, Notice, PageHeader, Tabs } from "@/components/ui";

type Source = { url: string; date: string };
type Signal = {
  id: number;
  created_at: string;
  monitor: string;
  category: string;
  title: string;
  summary: string;
  suggested_response: string;
  sources: Source[];
  warning: string;
  status: string;
  decided_by: string;
  score: number;
};
type View = "new" | "sent" | "dismissed";

const MONITORS: Record<string, string> = { competitor: "Competitors", seo: "SEO", social: "Social listening" };
const URGENT = new Set(["price_change", "positioning", "spike"]);

function host(url: string) {
  if (url.startsWith("workspace://")) return "your upload";
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

function SignalCard({ signal, onDecide, busy }: { signal: Signal; onDecide?: (action: "send" | "dismiss") => void; busy: boolean }) {
  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={URGENT.has(signal.category) ? "warn" : "neutral"}>{label(signal.category)}</Badge>
        <span className="text-xs text-muted">{MONITORS[signal.monitor] ?? label(signal.monitor)}</span>
      </div>
      <p className="mt-2 font-medium">{signal.title}</p>
      <p className="mt-1 text-sm text-muted">{signal.summary}</p>
      {signal.suggested_response && (
        <p className="mt-2 text-sm">
          <span className="font-medium">Suggested response: </span>
          {signal.suggested_response}
        </p>
      )}
      {signal.warning && (
        <div className="mt-3">
          <Notice tone="warn">{signal.warning}</Notice>
        </div>
      )}
      <ul className="mt-3 space-y-1 text-sm">
        {signal.sources.map((source) => (
          <li key={source.url} className="flex min-w-0 gap-2">
            <span className="shrink-0 tabular-nums text-muted">{source.date}</span>
            {source.url.startsWith("http") ? (
              <a href={source.url} target="_blank" rel="noopener noreferrer nofollow" className="truncate text-accent underline-offset-2 hover:underline">
                {host(source.url)}
              </a>
            ) : (
              <span className="truncate text-muted">{host(source.url)}</span>
            )}
          </li>
        ))}
      </ul>
      {onDecide ? (
        <div className="mt-4 flex flex-wrap gap-2">
          <Button variant="primary" busy={busy} onClick={() => onDecide("send")} className="min-h-11 flex-1 sm:flex-none">
            Send to strategist
          </Button>
          <Button busy={busy} onClick={() => onDecide("dismiss")} className="min-h-11 flex-1 sm:flex-none">
            Dismiss
          </Button>
        </div>
      ) : (
        signal.decided_by && <p className="mt-3 text-xs text-muted">By {signal.decided_by}</p>
      )}
    </Card>
  );
}

export default function SignalsPage() {
  const { workspace } = useSession();
  const [view, setView] = useState<View>("new");
  const list = useApi<Signal[]>(`/workspaces/${workspace}/signals?status=${view}`);
  const [busy, setBusy] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [started, setStarted] = useState(false);

  async function decide(id: number, action: "send" | "dismiss") {
    setBusy(id);
    setError(null);
    try {
      await api(`/signals/${id}`, { method: "POST", body: { action } });
      list.reload();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(null);
    }
  }

  async function runNow() {
    setRunning(true);
    setError(null);
    try {
      await api(`/workspaces/${workspace}/monitor`, { method: "POST" });
      setStarted(true);
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <PageHeader
        title="Signals"
        subtitle="What changed around you this week: competitors, search and public discussion. Every item cites its source and date."
        actions={
          <Button busy={running} onClick={runNow}>
            Check now
          </Button>
        }
      />
      {started && (
        <div className="mb-4">
          <Notice>The monitors are running. New signals appear here when they finish.</Notice>
        </div>
      )}
      {error && (
        <div className="mb-4">
          <ErrorState error={error} />
        </div>
      )}
      <Tabs
        value={view}
        onChange={setView}
        options={[
          { value: "new", label: "Inbox" },
          { value: "sent", label: "Sent to strategist" },
          { value: "dismissed", label: "Dismissed" },
        ]}
      />
      {list.loading ? (
        <Loading rows={3} />
      ) : list.error ? (
        <ErrorState error={list.error} retry={list.reload} />
      ) : !list.data?.length ? (
        <Empty
          title={view === "new" ? "Nothing new" : `Nothing ${view === "sent" ? "sent" : "dismissed"} yet`}
          body={
            view === "new"
              ? "The monitors run weekly. Add competitors to the brand brain, Search Console and keyword exports, or social exports to give them something to watch."
              : "Signals you act on in the inbox are kept here."
          }
        />
      ) : (
        <>
          {view === "new" && (
            <p className="mb-3 text-sm text-muted">
              {plural(list.data.length, "signal")}, most important first. Dismissing a kind of signal ranks it lower next time.
            </p>
          )}
          <div className="space-y-3">
            {list.data.map((signal) => (
              <SignalCard
                key={signal.id}
                signal={signal}
                busy={busy === signal.id}
                onDecide={view === "new" ? (action) => decide(signal.id, action) : undefined}
              />
            ))}
          </div>
        </>
      )}
    </>
  );
}
