"use client";

import { useState } from "react";
import { useApi } from "@/lib/api";
import { label, plural } from "@/lib/session";
import { Badge, Card, ErrorState, Loading, Notice, SectionTitle } from "@/components/ui";

type Point = { experiment: string; date: string; variants: number; correlation: number; top_pick_right: boolean; predicted: string[]; actual: string[] };
type Accuracy = {
  tests_compared: number;
  mean_correlation: number;
  top_pick_hit_rate: number;
  chance_hit_rate: number;
  trust: string;
  message: string;
  points: Point[];
};
type VariantStats = { label: string; respondents: number; mean_stop: number; stop_sd: number; click_share: number; prob_first: number; prob_last: number; top_objections: string[]; confusing: string[] };
type DraftPanel = {
  ran: boolean;
  trust?: string;
  prediction?: { ranking: string[]; variants: VariantStats[]; respondents: number; disagreement: number };
  recommendation?: { drop: string[]; reason: string };
  accuracy_message?: string;
};

const TRUST_TONE: Record<string, "good" | "warn" | "neutral"> = { useful: "good", low: "warn", untested: "neutral" };
const TRUST_LABEL: Record<string, string> = { useful: "Matches real results", low: "Down-weighted", untested: "Not yet checked" };
const pct = (value: number) => (value > 0.99 ? "over 99%" : `${Math.round(value * 100)}%`);

/** Rank correlation per real test, over time: one dot per test, -1 to 1, zero marked. */
function AccuracyChart({ points }: { points: Point[] }) {
  const [hover, setHover] = useState<number | null>(null);
  const width = 560;
  const height = 180;
  const pad = { left: 36, right: 12, top: 12, bottom: 24 };
  const x = (index: number) => pad.left + (points.length === 1 ? (width - pad.left - pad.right) / 2 : (index * (width - pad.left - pad.right)) / (points.length - 1));
  const y = (value: number) => pad.top + ((1 - value) / 2) * (height - pad.top - pad.bottom);
  const active = hover === null ? null : points[hover];
  return (
    <div className="relative">
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Rank correlation between the panel's prediction and the real result, per test">
        {[1, 0, -1].map((tick) => (
          <g key={tick}>
            <line x1={pad.left} x2={width - pad.right} y1={y(tick)} y2={y(tick)} className={tick === 0 ? "stroke-muted" : "stroke-line"} strokeWidth={1} strokeDasharray={tick === 0 ? "4 4" : undefined} />
            <text x={pad.left - 8} y={y(tick) + 4} textAnchor="end" className="fill-muted text-[11px] tabular-nums">{tick}</text>
          </g>
        ))}
        {points.map((point, index) => (
          <g key={point.experiment + point.date} onMouseEnter={() => setHover(index)} onMouseLeave={() => setHover(null)} onFocus={() => setHover(index)} onBlur={() => setHover(null)} tabIndex={0}>
            <circle cx={x(index)} cy={y(point.correlation)} r={14} fill="transparent" />
            <circle cx={x(index)} cy={y(point.correlation)} r={5} className="fill-accent stroke-surface" strokeWidth={2} />
          </g>
        ))}
        <text x={pad.left} y={height - 6} className="fill-muted text-[11px]">Earliest test</text>
        <text x={width - pad.right} y={height - 6} textAnchor="end" className="fill-muted text-[11px]">Latest</text>
      </svg>
      {active && (
        <div className="pointer-events-none absolute left-1/2 top-0 -translate-x-1/2 rounded-lg border border-line bg-surface px-3 py-2 text-xs shadow-sm">
          <p className="font-medium">{active.experiment} · {active.date.slice(0, 10)}</p>
          <p className="tabular-nums">Rank correlation {active.correlation.toFixed(2)} · top pick {active.top_pick_right ? "right" : "wrong"}</p>
          <p className="text-muted">Predicted {active.predicted.map(label).join(" > ")}; real {active.actual.map(label).join(" > ")}</p>
        </div>
      )}
    </div>
  );
}

export function PanelAccuracy({ workspace }: { workspace: string }) {
  const data = useApi<{ accuracy: Accuracy; personas: unknown[] }>(`/workspaces/${workspace}/panel`);
  if (data.loading && !data.data) return <Loading rows={2} />;
  if (data.error) return <ErrorState error={data.error} retry={data.reload} />;
  const { accuracy, personas } = data.data!;
  return (
    <Card>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <SectionTitle>Panel accuracy</SectionTitle>
        <Badge tone={TRUST_TONE[accuracy.trust]}>{TRUST_LABEL[accuracy.trust]}</Badge>
      </div>
      <p className="text-sm text-muted">
        Before each test, a panel of {plural(personas.length, "synthetic persona")} predicts which variant will win. Once the real test is judged, the prediction is scored against it.
      </p>
      <p className="mt-2 text-sm">{accuracy.message}</p>
      {accuracy.points.length > 0 ? (
        <>
          <div className="mt-3 grid grid-cols-3 gap-2 text-sm tabular-nums">
            <div><p className="text-xs text-muted">Tests compared</p><p className="text-lg font-semibold">{accuracy.tests_compared}</p></div>
            <div><p className="text-xs text-muted">Average rank correlation</p><p className="text-lg font-semibold">{accuracy.mean_correlation.toFixed(2)}</p></div>
            <div><p className="text-xs text-muted">Top pick right</p><p className="text-lg font-semibold">{pct(accuracy.top_pick_hit_rate)}</p><p className="text-xs text-muted">chance: {pct(accuracy.chance_hit_rate)}</p></div>
          </div>
          <div className="mt-3"><AccuracyChart points={accuracy.points} /></div>
          <details className="mt-2 text-sm">
            <summary className="cursor-pointer font-medium">As a table</summary>
            <table className="mt-2 w-full text-left text-xs tabular-nums">
              <thead className="text-muted"><tr><th className="py-1 font-medium">Test</th><th className="font-medium">Correlation</th><th className="font-medium">Top pick</th></tr></thead>
              <tbody>{accuracy.points.map((point) => <tr key={point.experiment + point.date} className="border-t border-line"><td className="py-1">{point.experiment}</td><td>{point.correlation.toFixed(2)}</td><td>{point.top_pick_right ? "Right" : "Wrong"}</td></tr>)}</tbody>
            </table>
          </details>
        </>
      ) : (
        <p className="mt-2 text-sm italic text-muted">No pre-tested experiment has a final result yet.</p>
      )}
    </Card>
  );
}

export function PanelPrediction({ draftId }: { draftId: number }) {
  const data = useApi<DraftPanel>(`/drafts/${draftId}/panel`);
  if (data.loading && !data.data) return null;
  if (data.error) return <ErrorState error={data.error} retry={data.reload} />;
  const panel = data.data!;
  if (!panel.ran || !panel.prediction) return null;
  const { prediction, recommendation } = panel;
  return (
    <details className="rounded-xl border border-line px-4 py-3 text-sm">
      <summary className="cursor-pointer font-medium">
        Synthetic panel predicts {prediction.ranking.map(label).join(" > ")}{" "}
        <Badge tone={TRUST_TONE[panel.trust ?? "untested"]}>{TRUST_LABEL[panel.trust ?? "untested"]}</Badge>
      </summary>
      <p className="mt-2 text-muted">{panel.accuracy_message} The real test decides the winner.</p>
      {recommendation && recommendation.drop.length > 0 && (
        <div className="mt-2"><Notice tone="warn">{recommendation.reason}</Notice></div>
      )}
      <ul className="mt-2 space-y-2">
        {prediction.variants.map((variant) => (
          <li key={variant.label}>
            <p className="font-medium">{label(variant.label)}</p>
            <p className="tabular-nums text-muted">
              {pct(variant.click_share)} would click · stop score {variant.mean_stop} of 7 (spread {variant.stop_sd}) · {pct(variant.prob_first)} chance it ranks first
            </p>
            {variant.top_objections.length > 0 && <p className="text-muted">Main objection: “{variant.top_objections[0]}”</p>}
          </li>
        ))}
      </ul>
      <p className="mt-2 text-xs text-muted">{prediction.respondents} personas answered; {pct(prediction.disagreement)} preferred a different variant from the panel&apos;s pick.</p>
    </details>
  );
}
