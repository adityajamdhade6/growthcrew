"use client";

import { useApi } from "@/lib/api";
import { label, money, useSession } from "@/lib/session";
import { Badge, Card, Empty, ErrorState, Loading, PageHeader, SectionTitle } from "@/components/ui";

type Result = { name: string; agent: string; status: string; metrics: Record<string, unknown>; notes: string[] };
type Scorecard = { generated_at?: string; mode?: string; source: string; results: Result[] };
type Node = { name: string; kind: string; status: string; duration_ms: number; attributes: Record<string, unknown>; children: Node[] };
type Trace = { cycle_id: number; model_calls: number; tool_calls: number; cost_usd: number; tree: Node[] };
type Mission = { timeline: { cycle_id: number } | null };

const TONE: Record<string, "good" | "bad" | "warn" | "neutral"> = { pass: "good", fail: "bad", pending: "warn", skipped: "neutral" };
const NAME: Record<string, string> = { pass: "Passed", fail: "Failed", pending: "Waiting on a person", skipped: "Not run" };

function Span({ node, depth }: { node: Node; depth: number }) {
  const cost = node.attributes.cost_usd as number | undefined;
  return (
    <>
      <li className="flex flex-wrap gap-x-3 py-0.5 text-sm tabular-nums" style={{ paddingLeft: depth * 16 }}>
        <span className={node.status === "ok" ? "" : "text-bad"}>{node.kind === "llm" ? `Model call: ${label(node.name)}` : label(node.name)}</span>
        <span className="text-muted">{(node.duration_ms / 1000).toFixed(1)}s</span>
        {cost ? <span className="text-muted">{money(cost)}</span> : null}
      </li>
      {node.children.filter((child) => child.kind !== "tool").map((child, index) => <Span key={index} node={child} depth={depth + 1} />)}
    </>
  );
}

function CycleTrace({ cycleId }: { cycleId: number }) {
  const trace = useApi<Trace>(`/cycles/${cycleId}/trace/summary`);
  if (trace.loading) return <Loading rows={2} />;
  if (trace.error) return <ErrorState error={trace.error} retry={trace.reload} />;
  const data = trace.data!;
  if (!data.tree.length) return <p className="text-sm italic text-muted">This cycle ran before tracing was added.</p>;
  return (
    <>
      <p className="mb-2 text-sm text-muted">{data.model_calls} model calls · {data.tool_calls} tool calls · {money(data.cost_usd)}</p>
      <ul className="max-h-96 overflow-y-auto">{data.tree.map((node, index) => <Span key={index} node={node} depth={0} />)}</ul>
    </>
  );
}

export default function Evals() {
  const { workspace } = useSession();
  const card = useApi<Scorecard>("/evals/scorecard");
  const mission = useApi<Mission>(`/workspaces/${workspace}/mission`);
  if (card.loading) return <Loading rows={4} />;
  if (card.error) return <ErrorState error={card.error} retry={card.reload} />;
  const data = card.data!;
  return (
    <>
      <PageHeader title="Evals" subtitle={`Quality and safety checks run on every change (${data.source}${data.generated_at ? `, ${data.generated_at.slice(0, 10)}` : ""}).`} />
      {!data.results.length ? (
        <Empty title="No scorecard yet" body="Run make eval to produce one." />
      ) : (
        <Card className="mb-4">
          <SectionTitle>Scorecard</SectionTitle>
          <ul className="divide-y divide-line">
            {data.results.map((result) => (
              <li key={result.name} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span className="text-sm">{label(result.name)}</span>
                <Badge tone={TONE[result.status]}>{NAME[result.status] ?? result.status}</Badge>
              </li>
            ))}
          </ul>
          <p className="mt-2 text-xs text-muted">Evals waiting on a person are not counted as passed.</p>
        </Card>
      )}
      <Card>
        <SectionTitle>Trace of the latest weekly cycle</SectionTitle>
        {mission.data?.timeline ? <CycleTrace cycleId={mission.data.timeline.cycle_id} /> : <p className="text-sm italic text-muted">No cycle has run yet.</p>}
      </Card>
    </>
  );
}
