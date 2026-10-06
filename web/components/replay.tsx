"use client";

import { useEffect, useRef, useState } from "react";
import { useApi } from "@/lib/api";
import { money } from "@/lib/session";
import { Badge, Button, Card, ErrorState, Loading, SectionTitle } from "@/components/ui";

type Event = { at: number; kind: "stage" | "llm" | "draft"; name: string; duration: number; cost_usd: number; detail: string };
type Replay = { cycle_id: number | null; seconds?: number; events: Event[] };

const LENGTH = 60;

/** Plays the last traced cycle back in 60 seconds: stages, model calls, drafts, running cost. */
export function ReplayWeek({ workspace, onClose }: { workspace: string; onClose: () => void }) {
  const replay = useApi<Replay>(`/workspaces/${workspace}/replay`);
  const [elapsed, setElapsed] = useState(0);
  const [playing, setPlaying] = useState(true);
  const started = useRef<number | null>(null);

  useEffect(() => {
    if (!playing || !replay.data?.events.length) return;
    started.current = performance.now() - elapsed * 1000;
    let frame = 0;
    const tick = () => {
      const now = (performance.now() - (started.current ?? 0)) / 1000;
      setElapsed(Math.min(LENGTH, now));
      if (now < LENGTH) frame = requestAnimationFrame(tick);
      else setPlaying(false);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [playing, replay.data]);

  if (replay.loading) return <Loading rows={2} />;
  if (replay.error) return <ErrorState error={replay.error} retry={replay.reload} />;
  const data = replay.data!;
  if (!data.events.length)
    return (
      <Card>
        <p className="text-sm text-muted">No traced cycle to replay yet. Run a cycle first.</p>
        <Button className="mt-3" onClick={onClose}>Close</Button>
      </Card>
    );
  const scale = (data.seconds || 1) / LENGTH;
  const now = elapsed * scale;
  const shown = data.events.filter((event) => event.at <= now);
  const stage = [...shown].reverse().find((event) => event.kind === "stage");
  const cost = shown.reduce((sum, event) => sum + (event.kind === "llm" ? event.cost_usd : 0), 0);
  const calls = shown.filter((event) => event.kind === "llm").length;
  const drafts = shown.filter((event) => event.kind === "draft");
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Replay of cycle {data.cycle_id}</SectionTitle>
        <div className="flex gap-2">
          <Button onClick={() => { setElapsed(0); setPlaying(true); }}>Restart</Button>
          <Button onClick={() => setPlaying(!playing)}>{playing ? "Pause" : "Play"}</Button>
          <Button onClick={onClose}>Close</Button>
        </div>
      </div>
      <div className="mt-3 h-2 overflow-hidden rounded-full bg-raised" role="progressbar" aria-valuenow={Math.round((elapsed / LENGTH) * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div className="h-full rounded-full bg-accent" style={{ width: `${(elapsed / LENGTH) * 100}%` }} />
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3 text-sm tabular-nums">
        <div><p className="text-xs text-muted">Now</p><p className="font-medium">{stage?.name ?? "Starting"}</p></div>
        <div><p className="text-xs text-muted">Model calls</p><p className="font-medium">{calls}</p></div>
        <div><p className="text-xs text-muted">Cost so far</p><p className="font-medium">{money(cost)}</p></div>
      </div>
      <ol className="mt-3 max-h-56 space-y-1 overflow-y-auto text-sm" aria-live="polite">
        {shown.filter((event) => event.kind !== "llm").slice(-8).map((event, index) => (
          <li key={`${event.kind}-${event.name}-${index}`} className="flex items-center gap-2">
            <Badge tone={event.kind === "draft" ? "accent" : "neutral"}>{event.kind === "draft" ? "Draft" : "Stage"}</Badge>
            <span>{event.name}</span>
            {event.detail && <span className="truncate text-muted">{event.detail}</span>}
          </li>
        ))}
      </ol>
      {drafts.length > 0 && <p className="mt-2 text-sm text-muted">{drafts.length} drafts waiting for a person to approve.</p>}
      <p className="mt-2 text-xs text-muted">A recorded cycle, sped up to {LENGTH} seconds. Nothing is run again.</p>
    </Card>
  );
}
