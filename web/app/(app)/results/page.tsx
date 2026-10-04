"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, useSession } from "@/lib/session";
import { Badge, Button, Card, Empty, ErrorState, Loading, Notice, PageHeader, SectionTitle, statusTone, Tabs } from "@/components/ui";

type Row = { id: string; dimension: string; value: string; metric: string; pieces: number; trials: number; successes: number; rate_pct: number };
type Arm = { label: string; trials: number; successes: number; rate_pct: number };
type Uncertainty = { leader: string; prob_best: Record<string, number>; expected_loss_pct: Record<string, number>; lift_low_pct: number | null; lift_high_pct: number | null; sample_size: number };
type Readout = { id: string; kind: string; name: string; title: string; uncertainty: Uncertainty; metric: string; arms: Arm[]; status: string; winner: string | null; lift_pct: number | null; note: string; preregistered: boolean; hypothesis: string; planned_per_variant: number | null; more_needed: number; guardrail_flags: string[]; next_split: Record<string, number> | null };
type Analysis = {
  window_start: string | null;
  window_end: string | null;
  rows_used: number;
  unmatched_rows: number;
  performance: Row[];
  readouts: Readout[];
  anomalies: { id: string; series: string; date: string; value: number; typical: number; change_pct: number; direction: string }[];
};
type LogEntry = {
  kind: string;
  date: string;
  window?: string;
  detail?: string;
  what_worked?: { statement: string; confidence: string }[];
  what_didnt?: { statement: string; confidence: string }[];
  changes?: { id: string; change: string; rationale: string; decision: string; reason: string }[];
};

/** Horizontal bars for one measure: one hue, value labelled at the end of each bar. */
function Bars({ rows, unit = "%", muted = false }: { rows: { label: string; value: number; detail: string; mark?: string }[]; unit?: string; muted?: boolean }) {
  const max = Math.max(...rows.map((row) => row.value), 0.0001);
  const [hover, setHover] = useState<string | null>(null);
  return (
    <div className="space-y-2">
      {rows.map((row) => (
        <div key={row.label} className="grid grid-cols-[minmax(5rem,9rem)_1fr] items-center gap-3 text-sm" onMouseEnter={() => setHover(row.label)} onMouseLeave={() => setHover(null)}>
          <span className="truncate text-right text-muted" title={row.label}>{row.label}</span>
          <span className="flex items-center gap-2">
            {/* Too little data: hatched and faded, so a tall bar does not read as a result. */}
            <span
              className={`h-5 rounded-r transition-opacity ${muted ? "border border-dashed border-muted" : "bg-series"}`}
              style={{
                width: `${Math.max((row.value / max) * 78, 0.5)}%`,
                opacity: hover && hover !== row.label ? 0.45 : 1,
                backgroundImage: muted ? "repeating-linear-gradient(135deg, var(--muted) 0 1px, transparent 1px 6px)" : undefined,
              }}
            />
            <span className={`whitespace-nowrap tabular-nums ${muted ? "text-muted" : ""}`}>
              <strong>{row.value}{unit}</strong>
              {row.mark && <span className="ml-1.5"><Badge tone="good">{row.mark}</Badge></span>}
              {hover === row.label && <span className="ml-2 text-xs text-muted">{row.detail}</span>}
            </span>
          </span>
        </div>
      ))}
    </div>
  );
}

function Performance({ rows }: { rows: Row[] }) {
  const [dimension, setDimension] = useState<"angle" | "pillar" | "channel" | "format">("angle");
  const [table, setTable] = useState(false);
  const metrics = [...new Set(rows.map((row) => row.metric))];
  const [metric, setMetric] = useState(metrics.includes("click-through rate") ? "click-through rate" : metrics[0]);
  const shown = rows.filter((row) => row.dimension === dimension && row.metric === metric).sort((a, b) => b.rate_pct - a.rate_pct);
  return (
    <Card>
      <div className="mb-1 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-semibold">{label(metric)} by {dimension}</h2>
        <select aria-label="Metric" value={metric} onChange={(event) => setMetric(event.target.value)} className="rounded-lg border border-line bg-surface px-2 py-1.5 text-sm">
          {metrics.map((name) => <option key={name} value={name}>{label(name)}</option>)}
        </select>
      </div>
      <Tabs value={dimension} onChange={setDimension} options={(["angle", "pillar", "channel", "format"] as const).map((value) => ({ value, label: label(value) }))} />
      {shown.length === 0 ? (
        <p className="text-sm italic text-muted">No pieces measured on this metric yet.</p>
      ) : table ? (
        <table className="w-full text-left text-sm">
          <thead className="text-xs text-muted"><tr><th className="py-1 font-medium">{label(dimension)}</th><th className="py-1 text-right font-medium">Rate</th><th className="py-1 text-right font-medium">Count</th><th className="py-1 text-right font-medium">Pieces</th></tr></thead>
          <tbody>{shown.map((row) => <tr key={row.id} className="border-t border-line tabular-nums"><td className="py-1.5">{label(row.value)}</td><td className="py-1.5 text-right">{row.rate_pct}%</td><td className="py-1.5 text-right">{row.successes.toLocaleString()} of {row.trials.toLocaleString()}</td><td className="py-1.5 text-right">{row.pieces}</td></tr>)}</tbody>
        </table>
      ) : (
        <Bars rows={shown.map((row) => ({ label: label(row.value), value: row.rate_pct, detail: `${row.successes.toLocaleString()} of ${row.trials.toLocaleString()} · ${row.pieces} piece${row.pieces === 1 ? "" : "s"}` }))} />
      )}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-xs text-muted">
        <span>Differences here are descriptive. Only the experiment results below say whether a gap is real.</span>
        <button onClick={() => setTable(!table)} className="font-medium text-accent">{table ? "Show as chart" : "Show as table"}</button>
      </div>
    </Card>
  );
}

function ReadoutCard({ readout }: { readout: Readout }) {
  const tooEarly = readout.status === "not_enough_data";
  const u = readout.uncertainty;
  const descriptive = readout.status === "not_preregistered";
  const held = readout.status === "significant" && readout.guardrail_flags.length > 0;
  const verdict = held ? `${label(readout.winner!)} wins, held: hurts a guardrail` : readout.status === "significant" ? `${label(readout.winner!)} wins` : tooEarly ? "Keep running" : descriptive ? "Descriptive only" : "No clear difference";
  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="font-medium">{readout.kind === "ab_test" ? "A/B test" : "Comparison"}: {readout.title}</p>
          <p className="text-xs text-muted">{label(readout.metric)}{readout.kind === "observational" && " · different pieces, so treat as a lead, not proof"}</p>
        </div>
        <Badge tone={held ? "bad" : readout.status === "significant" ? "good" : tooEarly ? "warn" : "neutral"}>{verdict}</Badge>
      </div>
      <Bars muted={tooEarly || descriptive} rows={readout.arms.map((arm) => ({ label: label(arm.label), value: arm.rate_pct, detail: `${arm.successes.toLocaleString()} of ${arm.trials.toLocaleString()}`, mark: arm.label === readout.winner ? "Winner" : undefined }))} />
      {/* Uncertainty is shown with every verdict, so "wins" is never a bare label. */}
      <p className="mt-3 text-sm tabular-nums">
        {u.prob_best[u.leader] > 0.999 ? "Over 99.9" : Math.round(u.prob_best[u.leader] * 100)}% probability that {label(u.leader).toLowerCase()} is best
        {u.lift_low_pct !== null && ` · lift ${u.lift_low_pct > 0 ? "+" : ""}${Math.round(u.lift_low_pct)}% to ${u.lift_high_pct! > 0 ? "+" : ""}${Math.round(u.lift_high_pct!)}% (95% interval)`}
        {` · sample ${u.sample_size.toLocaleString()}`}
      </p>
      <p className="mt-1 text-sm text-muted">
        {readout.status === "significant"
          ? `Choosing ${label(readout.winner!).toLowerCase()} is expected to cost ${u.expected_loss_pct[readout.winner!]}% of the rate if that is wrong.`
          : tooEarly
            ? `Too early to call, whatever the bars suggest. About ${readout.more_needed.toLocaleString()} more needed; ${readout.planned_per_variant?.toLocaleString()} per variant were planned.`
            : descriptive
              ? "These were never registered as a test, so no winner is called. Register one to find out."
              : "The planned sample is in and nothing separates the variants. Treat them as equivalent."}
      </p>
      {readout.guardrail_flags.map((flag) => <p key={flag} className="mt-2 text-sm text-bad">Guardrail: {flag}</p>)}
      {readout.next_split && (
        <p className="mt-2 text-sm">
          <span className="font-medium">Next week&apos;s budget split:</span>{" "}
          {Object.entries(readout.next_split).map(([name, share]) => `${label(name)} ${Math.round(share * 100)}%`).join(" · ")}
        </p>
      )}
      {readout.preregistered && <p className="mt-2 text-xs text-muted">Pre-registered: {readout.hypothesis}</p>}
    </Card>
  );
}

export default function Results() {
  const { workspace } = useSession();
  const analysis = useApi<Analysis>(`/workspaces/${workspace}/analysis`);
  const log = useApi<LogEntry[]>(`/workspaces/${workspace}/learning-log.json`);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function analyse() {
    setBusy(true);
    setError(null);
    try {
      await api(`/workspaces/${workspace}/analysis`, { method: "POST" });
      log.reload();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  if (analysis.loading || log.loading) return <Loading rows={4} />;
  if (analysis.error) return <ErrorState error={analysis.error} retry={analysis.reload} />;
  const data = analysis.data!;
  return (
    <>
      <PageHeader
        title="Results and learnings"
        subtitle={data.window_start ? `Week of ${data.window_start} to ${data.window_end} · ${data.rows_used} rows matched to a piece` : "No performance data yet"}
        actions={data.rows_used > 0 && <Button variant="primary" busy={busy} onClick={analyse}>Write this week&apos;s learnings</Button>}
      />
      {error && <div className="mb-4"><ErrorState error={error} /></div>}
      {data.rows_used === 0 ? (
        <Empty
          title="Nothing to measure yet"
          body="Upload an export from LinkedIn, Search Console, GA4, your email tool or your ad platform. Results are matched to each piece by its tracking key or published link."
          action={<Link href="/settings#sources" className="inline-flex min-h-10 items-center rounded-lg bg-accent px-3.5 text-sm font-medium text-on-accent">Upload data</Link>}
        />
      ) : (
        <div className="space-y-4">
          {data.unmatched_rows > 0 && <Notice tone="warn">{data.unmatched_rows} uploaded rows could not be matched to a piece and are left out. Add the tracking key to the campaign or ad name, or record the published link.</Notice>}
          <Performance rows={data.performance} />
          <SectionTitle>Experiment results</SectionTitle>
          {data.readouts.length === 0 && <p className="text-sm italic text-muted">No tests have data yet.</p>}
          <div className="grid gap-4 lg:grid-cols-2">{data.readouts.map((readout) => <ReadoutCard key={readout.id} readout={readout} />)}</div>
          {data.anomalies.length > 0 && (
            <Card>
              <SectionTitle>Unusual days</SectionTitle>
              {data.anomalies.map((anomaly) => (
                <p key={anomaly.id} className="text-sm"><Badge tone="warn">{label(anomaly.direction)}</Badge> {label(anomaly.series)} on {anomaly.date}: {Math.round(anomaly.value).toLocaleString()} against a typical {Math.round(anomaly.typical).toLocaleString()} ({anomaly.change_pct > 0 ? "+" : ""}{anomaly.change_pct}%)</p>
              ))}
            </Card>
          )}
        </div>
      )}

      <div className="mt-8">
        <SectionTitle>Learning log: how the strategy changed, and why</SectionTitle>
        {log.error ? <ErrorState error={log.error} retry={log.reload} /> : !log.data?.length ? (
          <Empty title="Nothing logged yet" body="Each week the analyst proposes changes and the strategist accepts or rejects each one with a reason. The record builds up here." />
        ) : (
          <ol className="space-y-3">
            {log.data.map((entry, index) => (
              <li key={index}>
                <Card>
                  {entry.kind === "learnings" ? (
                    <>
                      <p className="font-medium">Week of {entry.window}</p>
                      <div className="mt-3 grid gap-4 md:grid-cols-2">
                        <div><p className="mb-1 text-xs font-semibold uppercase text-muted">What worked</p><ul className="list-disc space-y-1 pl-5 text-sm">{entry.what_worked!.map((item) => <li key={item.statement}>{item.statement} <span className="text-muted">({item.confidence} confidence)</span></li>)}</ul></div>
                        <div><p className="mb-1 text-xs font-semibold uppercase text-muted">What did not</p><ul className="list-disc space-y-1 pl-5 text-sm">{entry.what_didnt!.map((item) => <li key={item.statement}>{item.statement} <span className="text-muted">({item.confidence} confidence)</span></li>)}</ul></div>
                      </div>
                      <p className="mb-1 mt-4 text-xs font-semibold uppercase text-muted">Changes proposed, and the strategist&apos;s ruling</p>
                      {entry.changes!.map((change) => (
                        <div key={change.id} className="mt-2 text-sm">
                          <p><Badge tone={statusTone(change.decision)}>{label(change.decision)}</Badge> {change.change}</p>
                          <p className="mt-0.5 text-muted">{change.reason || change.rationale}</p>
                        </div>
                      ))}
                    </>
                  ) : (
                    <p className="text-sm"><span className="font-medium">{entry.date} · {entry.kind === "voice" ? "Brand voice" : "Strategy check"}</span><br /><span className="text-muted">{entry.detail}</span></p>
                  )}
                </Card>
              </li>
            ))}
          </ol>
        )}
      </div>
    </>
  );
}
