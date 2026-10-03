"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useMemo, useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, useSession } from "@/lib/session";
import { Badge, Button, Empty, ErrorState, Loading, PageHeader, statusTone } from "@/components/ui";
import { ReviewPanel } from "@/components/review-panel";

type Item = {
  id: number;
  piece: string;
  group: string;
  content_type: string;
  angle: string | null;
  date: string;
  status: string;
  lowest_score: number;
  passed_critic: boolean;
  preview: string;
};

const iso = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const parse = (value: string) => new Date(`${value}T12:00:00`);
function monday(date: Date) {
  const copy = new Date(date);
  copy.setDate(copy.getDate() - ((copy.getDay() + 6) % 7));
  return copy;
}

function groupsOf(items: Item[]): Item[][] {
  const groups = new Map<string, Item[]>();
  for (const item of items) groups.set(item.group, [...(groups.get(item.group) ?? []), item]);
  return [...groups.values()];
}

function Calendar() {
  const { workspace, reloadWorkspaces } = useSession();
  const board = useApi<{ drafts: Item[] }>(`/workspaces/${workspace}/board`, 15000);
  const [open, setOpen] = useState<number | null>(null);
  const [onlyReview, setOnlyReview] = useState(useSearchParams().get("review") === "1");
  const [weekStart, setWeekStart] = useState<Date | null>(null);
  const [dragging, setDragging] = useState<number | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [moved, setMoved] = useState<Record<number, string>>({});

  const drafts = useMemo(() => (board.data?.drafts ?? []).map((item) => ({ ...item, date: moved[item.id] ?? item.date })), [board.data, moved]);
  // Open on the week that needs the reviewer, falling back to the current week.
  const start = useMemo(() => {
    if (weekStart) return weekStart;
    const pending = drafts.find((item) => item.status === "pending_approval");
    return monday(pending ? parse(pending.date) : new Date());
  }, [weekStart, drafts]);
  const days = Array.from({ length: 7 }, (_, index) => { const day = new Date(start); day.setDate(day.getDate() + index); return day; });

  async function move(id: number, date: string) {
    const item = drafts.find((draft) => draft.id === id);
    if (!item || item.date === date) return;
    setMoved((current) => ({ ...current, [id]: date }));
    setError(null);
    try {
      await api(`/drafts/${id}/schedule`, { method: "PATCH", body: { date } });
      board.reload();
    } catch (caught) {
      setMoved((current) => { const next = { ...current }; delete next[id]; return next; });
      setError(caught as ApiError);
    }
  }

  if (board.loading && !board.data) return <Loading rows={4} />;
  if (board.error) return <ErrorState error={board.error} retry={board.reload} />;
  const pendingCount = drafts.filter((item) => item.status === "pending_approval").length;
  const shown = drafts.filter((item) => !onlyReview || ["pending_approval", "blocked"].includes(item.status));
  const today = iso(new Date());
  const range = `${days[0].toLocaleDateString(undefined, { month: "short", day: "numeric" })} to ${days[6].toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;

  return (
    <>
      <PageHeader
        title="Content calendar"
        subtitle={pendingCount ? `${pendingCount} draft${pendingCount === 1 ? "" : "s"} waiting for your approval` : "Nothing is waiting for you"}
        actions={
          <>
            <Button aria-pressed={onlyReview} variant={onlyReview ? "primary" : "secondary"} onClick={() => setOnlyReview(!onlyReview)}>Needs review</Button>
            <div className="flex items-center gap-1">
              <Button aria-label="Previous week" onClick={() => { const day = new Date(start); day.setDate(day.getDate() - 7); setWeekStart(day); }}>←</Button>
              <span className="min-w-36 text-center text-sm font-medium">{range}</span>
              <Button aria-label="Next week" onClick={() => { const day = new Date(start); day.setDate(day.getDate() + 7); setWeekStart(day); }}>→</Button>
            </div>
          </>
        }
      />
      {error && <div className="mb-3"><ErrorState error={error} /></div>}
      {drafts.length === 0 ? (
        <Empty title="No content yet" body="Drafts appear here once the team has run a weekly cycle. Each one waits for your approval before it is scheduled." />
      ) : (
        <div className="grid gap-3 md:grid-cols-7 md:gap-2">
          {days.map((day) => {
            const key = iso(day);
            const items = shown.filter((item) => item.date === key);
            return (
              <div
                key={key}
                onDragOver={(event) => { event.preventDefault(); setOver(key); }}
                onDragLeave={() => setOver((current) => (current === key ? null : current))}
                onDrop={() => { if (dragging) move(dragging, key); setDragging(null); setOver(null); }}
                className={`rounded-xl border p-2 md:min-h-72 ${over === key && dragging ? "border-accent bg-accent-soft" : "border-line bg-surface"} ${items.length === 0 ? "hidden md:block" : ""}`}
              >
                <p className={`mb-2 px-1 text-xs font-semibold ${key === today ? "text-accent" : "text-muted"}`}>
                  {day.toLocaleDateString(undefined, { weekday: "short" })} <span className="font-normal">{day.getDate()}</span>{key === today && " · today"}
                </p>
                <div className="space-y-2">
                  {groupsOf(items).map((group) => {
                    const cards = group.map((item) => (
                      <button
                        key={item.id}
                        draggable={!["published", "measured", "rejected"].includes(item.status)}
                        onDragStart={() => setDragging(item.id)}
                        onDragEnd={() => { setDragging(null); setOver(null); }}
                        onClick={() => setOpen(item.id)}
                        className={`block w-full rounded-lg border border-line bg-bg p-2.5 text-left hover:border-accent ${dragging === item.id ? "opacity-40" : ""} ${item.status === "rejected" ? "opacity-60" : ""}`}
                      >
                        <span className="flex flex-wrap items-center gap-1.5">
                          <span className="text-xs font-semibold">{group.length > 1 && item.angle ? `${label(item.angle)} angle` : label(item.content_type)}</span>
                          {group.length === 1 && item.angle && <span className="text-xs text-muted">{label(item.angle)}</span>}
                        </span>
                        <span className="mt-1 line-clamp-2 block text-xs text-muted md:line-clamp-3">{item.preview}</span>
                        <span className="mt-2 flex flex-wrap items-center gap-1.5">
                          <Badge tone={statusTone(item.status)}>{item.status === "pending_approval" ? "Needs you" : label(item.status)}</Badge>
                          {!item.passed_critic && item.status === "pending_approval" && <Badge tone="bad">Editor: {item.lowest_score}/10</Badge>}
                        </span>
                      </button>
                    ));
                    // Variants of one A/B test sit together, so they read as one decision.
                    return group.length > 1 ? (
                      <div key={group[0].group} className="rounded-lg border border-accent/30 bg-accent-soft/40 p-1.5">
                        <p className="mb-1.5 px-1 text-[11px] font-semibold uppercase tracking-wide text-accent">
                          {label(group[0].content_type)} · A/B test, {group.length} variants
                        </p>
                        <div className="space-y-1.5">{cards}</div>
                      </div>
                    ) : (
                      cards
                    );
                  })}
                </div>
              </div>
            );
          })}
          {shown.filter((item) => days.some((day) => iso(day) === item.date)).length === 0 && (
            <p className="rounded-xl border border-dashed border-line bg-surface px-4 py-8 text-center text-sm text-muted md:hidden">Nothing {onlyReview ? "to review " : ""}this week.</p>
          )}
        </div>
      )}
      <p className="mt-3 hidden text-xs text-muted md:block">Drag a card to another day to reschedule it.</p>
      {open !== null && (
        <ReviewPanel
          key={open}
          draftId={open}
          queue={drafts.filter((item) => item.status === "pending_approval").sort((a, b) => a.date.localeCompare(b.date) || a.id - b.id).map((item) => item.id)}
          onOpen={setOpen}
          onClose={() => setOpen(null)}
          onChanged={() => { board.reload(); reloadWorkspaces(); }}
        />
      )}
    </>
  );
}

export default function Page() {
  return (
    <Suspense fallback={<Loading />}>
      <Calendar />
    </Suspense>
  );
}
