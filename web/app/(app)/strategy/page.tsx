"use client";

import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, useSession } from "@/lib/session";
import { Badge, Button, Card, Empty, ErrorState, inputClass, Loading, Notice, PageHeader, SectionTitle, statusTone, Tabs } from "@/components/ui";

type Claim = { statement: string; source_url: string };
type Point = { text: string; support: string[] };
type Evidence = { id: string; text: string; source_url: string; quality: string };
type Comment = { id: number; section: string; comment: string; author: string; status: string; response: string };
type Research = {
  brief: {
    headline: string;
    learnings: { insight: string; why_it_matters: string; confidence: string; source_urls: string[] }[];
    open_questions: string[];
    verification: { statement: string; source_url: string; verdict: string; explanation: string }[];
    claims_dropped: number;
    sources_read: number;
  };
  teardowns: (Record<string, Claim[] | Claim | string | null> & { name: string; url: string })[];
  voice_of_customer: { top_pains: Claim[]; desired_outcomes: Claim[]; objections: Claim[]; themes: { theme: string; frequency: number; quotes: { text: string; source_url: string }[] }[] };
  market_signals: Record<string, Claim[]>;
};
type Strategy = {
  brand_name: string;
  issues: string[];
  evidence: Evidence[];
  positioning: { positioning_statement: string; market_category: Point } & Record<string, Point[] | Point | string>;
  jobs: Record<string, { statement: string; support: string[] }[]>;
  messaging_house: { core_message: Point; pillars: { message: string; support: string[]; proof_points: Point[] }[] };
  icp_priorities: { segment: string; rationale: string; support: string[] }[];
  channel_plan: { stages: { stage: string; objective: string; channels: { channel: string; tactic: string; timing: string; budget_pct: number; support: string[] }[]; kpis: { metric: string; target: string }[] }[] };
  content_pillars: { name: string; description: string; example_topics: string[]; support: string[] }[];
  experiments: { name: string; hypothesis: string; metric: string; minimum_sample: string; decision_rule: string; impact: number; confidence: number; ease: number; ice: number; support: string[] }[];
  kpis: { metric: string; baseline: string; target: string; timeframe: string; support: string[] }[];
  critique: { summary: string; weak_assumptions: Critique[]; missing_risks: Critique[] };
  revision: { changes: { critique_issue: string; decision: string; change_made: string; reason: string }[] };
};
type Critique = { section: string; issue: string; why_it_matters: string; suggested_fix: string; severity: string };

function Source({ url }: { url: string }) {
  if (!url.startsWith("http")) return <span className="text-xs text-muted">{url}</span>;
  let host = url;
  try {
    host = new URL(url).hostname.replace(/^www\./, "");
  } catch {
    /* keep the raw string */
  }
  return (
    <a href={url} target="_blank" rel="noreferrer" className="text-xs font-medium text-accent underline-offset-2 hover:underline">
      {host} ↗
    </a>
  );
}

function ClaimList({ claims }: { claims: Claim[] }) {
  if (!claims?.length) return <p className="text-sm italic text-muted">Nothing found.</p>;
  return (
    <ul className="space-y-2">
      {claims.map((claim, index) => (
        <li key={index} className="text-sm">
          {claim.statement} <Source url={claim.source_url} />
        </li>
      ))}
    </ul>
  );
}

function evidenceName(id: string) {
  const [kind, rest] = id.split(":");
  if (kind === "brain") return `Brain: ${rest.split(".").slice(-1)[0].replace(/_/g, " ")}`;
  if (kind === "learning") return `Research learning ${rest}`;
  if (kind === "claim") return `Research finding ${rest}`;
  if (kind === "voc") return "Customer quotes";
  return id;
}

/** Evidence chips: each opens the fact or finding the recommendation rests on. */
function Support({ ids, evidence }: { ids: string[]; evidence: Map<string, Evidence> }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!ids?.length) return <Badge tone="bad">No supporting evidence</Badge>;
  const item = open ? evidence.get(open) : undefined;
  return (
    <span className="mt-1 block">
      <span className="flex flex-wrap gap-1">
        {ids.map((id) => {
          // An unconfirmed brain fact is an assumption, so the chip says so.
          const assumed = evidence.get(id)?.quality.startsWith("inferred");
          return (
            <button key={id} onClick={() => setOpen(open === id ? null : id)} aria-expanded={open === id} className={`rounded-full px-2 py-0.5 text-xs ${open === id ? "bg-accent text-on-accent" : assumed ? "bg-warn-soft text-warn" : "bg-raised text-muted hover:text-ink"}`}>
              {evidenceName(id)}{assumed && " · unconfirmed"}
            </button>
          );
        })}
      </span>
      {item && (
        <span className="mt-1.5 block rounded-lg bg-raised px-3 py-2 text-xs text-muted">
          {item.text.slice(0, 320)} {item.quality && <em>({item.quality})</em>} {item.source_url && <Source url={item.source_url.split(", ")[0]} />}
        </span>
      )}
    </span>
  );
}

function ResearchView({ workspace }: { workspace: string }) {
  const research = useApi<Research>(`/workspaces/${workspace}/research`);
  if (research.loading) return <Loading />;
  if (research.error?.status === 404)
    return <Empty title="No research yet" body="The researcher runs at the start of each weekly cycle. Start a cycle from Mission control and the brief will appear here." />;
  if (research.error) return <ErrorState error={research.error} retry={research.reload} />;
  const { brief, voice_of_customer: voc, teardowns, market_signals } = research.data!;
  const flagged = brief.verification.filter((check) => check.verdict !== "supported");
  return (
    <div className="space-y-4">
      <Card>
        <SectionTitle>Research brief</SectionTitle>
        <p className="text-lg font-semibold">{brief.headline}</p>
        <ol className="mt-4 space-y-4">
          {brief.learnings.map((item, index) => (
            <li key={index} className="flex gap-3">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{index + 1}</span>
              <div>
                <p className="text-sm font-medium">{item.insight} <Badge tone={item.confidence === "high" ? "good" : item.confidence === "medium" ? "warn" : "neutral"}>{item.confidence} confidence</Badge></p>
                <p className="mt-0.5 text-sm text-muted">{item.why_it_matters}</p>
                <p className="mt-1 flex flex-wrap gap-x-3">{item.source_urls.map((url) => <Source key={url} url={url} />)}</p>
              </div>
            </li>
          ))}
        </ol>
        {brief.open_questions.length > 0 && (
          <div className="mt-4 border-t border-line pt-3">
            <p className="text-sm font-medium">Still unanswered</p>
            <ul className="mt-1 list-disc pl-5 text-sm text-muted">{brief.open_questions.map((question) => <li key={question}>{question}</li>)}</ul>
          </div>
        )}
        <p className="mt-4 text-xs text-muted">
          {brief.sources_read} sources read · {brief.verification.length} claims re-checked against their source, {flagged.length} flagged · {brief.claims_dropped} uncited claims removed
        </p>
        {flagged.map((check) => <div key={check.statement} className="mt-2"><Notice tone="warn">Mismatch: “{check.statement}”. {check.explanation}</Notice></div>)}
      </Card>
      <Card>
        <SectionTitle>Voice of customer</SectionTitle>
        <div className="grid gap-5 md:grid-cols-3">
          <div><p className="mb-2 text-sm font-medium">Top pains</p><ClaimList claims={voc.top_pains} /></div>
          <div><p className="mb-2 text-sm font-medium">Desired outcomes</p><ClaimList claims={voc.desired_outcomes} /></div>
          <div><p className="mb-2 text-sm font-medium">Objections</p><ClaimList claims={voc.objections} /></div>
        </div>
        {voc.themes.map((theme) => (
          <div key={theme.theme} className="mt-4 border-t border-line pt-3">
            <p className="text-sm font-medium">{theme.theme} <span className="font-normal text-muted">· {theme.frequency} quote{theme.frequency === 1 ? "" : "s"}</span></p>
            {theme.quotes.map((quote) => <blockquote key={quote.text} className="mt-2 border-l-2 border-line pl-3 text-sm">“{quote.text}” <Source url={quote.source_url} /></blockquote>)}
          </div>
        ))}
      </Card>
      <Card>
        <SectionTitle>Competitors</SectionTitle>
        {teardowns.length === 0 && <p className="text-sm italic text-muted">No competitor teardowns in this report.</p>}
        {teardowns.map((teardown) => (
          <details key={teardown.name} className="border-b border-line py-2 last:border-0">
            <summary className="cursor-pointer text-sm font-medium">{teardown.name}</summary>
            <div className="mt-2 space-y-3">
              {Object.entries(teardown).filter(([, value]) => Array.isArray(value) && value.length).map(([key, value]) => (
                <div key={key}><p className="mb-1 text-xs font-semibold uppercase text-muted">{label(key)}</p><ClaimList claims={value as Claim[]} /></div>
              ))}
            </div>
          </details>
        ))}
      </Card>
      <Card>
        <SectionTitle>Market signals</SectionTitle>
        <div className="grid gap-5 md:grid-cols-3">
          {Object.entries(market_signals).map(([key, claims]) => <div key={key}><p className="mb-2 text-sm font-medium">{label(key)}</p><ClaimList claims={claims} /></div>)}
        </div>
      </Card>
    </div>
  );
}

function Section(props: { id: string; title: string; workspace: string; comments: Comment[]; onSent: () => void; children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const mine = props.comments.filter((comment) => comment.section === props.id);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      await api(`/workspaces/${props.workspace}/strategy/comments`, { method: "POST", body: { section: props.id, comment: text } });
      setText("");
      setOpen(false);
      props.onSent();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 className="text-base font-semibold">{props.title}</h2>
        <Button variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}>Ask for a change</Button>
      </div>
      {open && (
        <div className="mb-4 space-y-2 rounded-lg bg-raised p-3">
          <textarea className={inputClass} rows={3} value={text} onChange={(event) => setText(event.target.value)} placeholder="What should the strategist change here, and why?" aria-label={`Comment on ${props.title}`} />
          {error && <ErrorState error={error} />}
          <div className="flex gap-2">
            <Button variant="primary" busy={busy} disabled={!text.trim()} onClick={send}>Send to the strategist</Button>
            <Button onClick={() => setOpen(false)}>Cancel</Button>
          </div>
        </div>
      )}
      {mine.map((comment) => (
        <div key={comment.id} className="mb-3 rounded-lg border border-line px-3 py-2 text-sm">
          <p><Badge tone={statusTone(comment.status)}>{comment.status === "pending" ? "Strategist is revising" : label(comment.status)}</Badge> <span className="text-muted">You asked:</span> {comment.comment}</p>
          {comment.response && <p className="mt-1 text-muted">{comment.status === "failed" ? "It failed: " : "What changed: "}{comment.response}</p>}
        </div>
      ))}
      {props.children}
    </Card>
  );
}

function StrategyView({ workspace }: { workspace: string }) {
  const [polling, setPolling] = useState(false);
  const result = useApi<{ strategy: Strategy; comments: Comment[] }>(`/workspaces/${workspace}/strategy`, polling ? 4000 : undefined);
  if (result.loading && !result.data) return <Loading />;
  if (result.error?.status === 404)
    return <Empty title="No strategy yet" body="The strategist writes one after the first research run, then updates it only when new evidence or your comments call for it." />;
  if (result.error) return <ErrorState error={result.error} retry={result.reload} />;
  const { strategy: doc, comments } = result.data!;
  const waiting = comments.some((comment) => comment.status === "pending");
  if (waiting !== polling) setPolling(waiting);
  const evidence = new Map(doc.evidence.map((item) => [item.id, item]));
  const shared = { workspace, comments, onSent: result.reload };
  const points = (items: Point[]) => (
    <ul className="space-y-3">{items.map((item) => <li key={item.text} className="text-sm">{item.text}<Support ids={item.support} evidence={evidence} /></li>)}</ul>
  );
  const pos = doc.positioning;
  return (
    <div className="space-y-4">
      {doc.issues.length > 0 && <Notice tone="warn"><strong>Needs attention before you rely on this:</strong> {doc.issues.join("; ")}</Notice>}
      <Section id="positioning" title="Positioning" {...shared}>
        <p className="mb-4 text-lg font-medium">{pos.positioning_statement}</p>
        <div className="grid gap-5 md:grid-cols-2">
          {(["competitive_alternatives", "unique_attributes", "value", "target_customers"] as const).map((key) => (
            <div key={key}><p className="mb-2 text-xs font-semibold uppercase text-muted">{label(key)}</p>{points(pos[key] as Point[])}</div>
          ))}
          <div><p className="mb-2 text-xs font-semibold uppercase text-muted">Market category</p>{points([pos.market_category])}</div>
        </div>
      </Section>
      <Section id="jobs" title="Jobs to be done" {...shared}>
        {Object.entries(doc.jobs).map(([kind, jobs]) => (
          <div key={kind} className="mb-3 last:mb-0">
            <p className="mb-1 text-xs font-semibold uppercase text-muted">{label(kind.replace("_jobs", ""))}</p>
            {jobs.length ? jobs.map((job) => <p key={job.statement} className="mb-2 text-sm">{job.statement}<Support ids={job.support} evidence={evidence} /></p>) : <p className="text-sm italic text-muted">None supported by the evidence.</p>}
          </div>
        ))}
      </Section>
      <Section id="messaging_house" title="Messaging house" {...shared}>
        <p className="mb-4 rounded-lg bg-accent-soft px-4 py-3 text-base font-medium text-accent">{doc.messaging_house.core_message.text}</p>
        <div className="grid gap-4 md:grid-cols-3">
          {doc.messaging_house.pillars.map((pillar) => (
            <div key={pillar.message} className="rounded-lg border border-line p-3">
              <p className="text-sm font-semibold">{pillar.message}</p>
              <Support ids={pillar.support} evidence={evidence} />
              <p className="mb-1 mt-3 text-xs font-semibold uppercase text-muted">Proof</p>
              {pillar.proof_points.length ? points(pillar.proof_points) : <p className="text-sm italic text-muted">No proof yet.</p>}
            </div>
          ))}
        </div>
      </Section>
      <Section id="channel_plan" title="90-day channel plan" {...shared}>
        <div className="space-y-4">
          {doc.channel_plan.stages.map((stage) => (
            <div key={stage.stage} className="rounded-lg border border-line p-3">
              <p className="text-sm font-semibold">{label(stage.stage)} <span className="font-normal text-muted">· {stage.objective}</span></p>
              {stage.channels.map((play) => (
                <div key={play.channel + play.tactic} className="mt-2 flex gap-3 text-sm">
                  <span className="w-12 shrink-0 text-right font-semibold tabular-nums">{play.budget_pct}%</span>
                  <span><strong>{play.channel}</strong>: {play.tactic} <span className="text-muted">({play.timing})</span><Support ids={play.support} evidence={evidence} /></span>
                </div>
              ))}
              <p className="mt-2 text-xs text-muted">{stage.kpis.map((kpi) => `${kpi.metric}: ${kpi.target}`).join(" · ")}</p>
            </div>
          ))}
        </div>
      </Section>
      <Section id="experiments" title="Experiments, ranked by ICE" {...shared}>
        <ol className="space-y-4">
          {doc.experiments.map((experiment, index) => (
            <li key={experiment.name} className="flex gap-3">
              <span className="flex h-9 w-9 shrink-0 flex-col items-center justify-center rounded-lg bg-raised text-xs font-semibold tabular-nums" title="ICE score">{experiment.ice}</span>
              <div className="text-sm">
                <p className="font-medium">{index + 1}. {experiment.name} <span className="font-normal text-muted">· impact {experiment.impact}, confidence {experiment.confidence}, ease {experiment.ease}</span></p>
                <p className="mt-0.5">{experiment.hypothesis}</p>
                <p className="mt-1 text-muted">Metric: {experiment.metric} · Minimum sample: {experiment.minimum_sample} · {experiment.decision_rule}</p>
                <Support ids={experiment.support} evidence={evidence} />
              </div>
            </li>
          ))}
        </ol>
      </Section>
      <Section id="priorities" title="Priorities, content pillars and KPIs" {...shared}>
        <p className="mb-2 text-xs font-semibold uppercase text-muted">Who to pursue first</p>
        <ol className="mb-4 list-decimal space-y-2 pl-5 text-sm">{doc.icp_priorities.map((icp) => <li key={icp.segment}><strong>{icp.segment}</strong>: {icp.rationale}<Support ids={icp.support} evidence={evidence} /></li>)}</ol>
        <p className="mb-2 text-xs font-semibold uppercase text-muted">Content pillars</p>
        <ul className="mb-4 space-y-2 text-sm">{doc.content_pillars.map((pillar) => <li key={pillar.name}><strong>{pillar.name}</strong>: {pillar.description} <span className="text-muted">e.g. {pillar.example_topics.join("; ")}</span><Support ids={pillar.support} evidence={evidence} /></li>)}</ul>
        <p className="mb-2 text-xs font-semibold uppercase text-muted">KPIs</p>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[28rem] text-left text-sm">
            <thead className="text-xs text-muted"><tr><th className="py-1 pr-3 font-medium">Metric</th><th className="py-1 pr-3 font-medium">Baseline</th><th className="py-1 pr-3 font-medium">Target</th><th className="py-1 font-medium">By</th></tr></thead>
            <tbody>{doc.kpis.map((kpi) => <tr key={kpi.metric} className="border-t border-line"><td className="py-2 pr-3 font-medium">{kpi.metric}</td><td className="py-2 pr-3">{kpi.baseline}</td><td className="py-2 pr-3">{kpi.target}</td><td className="py-2">{kpi.timeframe}</td></tr>)}</tbody>
          </table>
        </div>
      </Section>
      <Card>
        <SectionTitle>Devil&apos;s advocate review</SectionTitle>
        <p className="text-sm">{doc.critique.summary}</p>
        {[...doc.critique.weak_assumptions, ...doc.critique.missing_risks].map((point) => (
          <div key={point.issue} className="mt-3 border-t border-line pt-3 text-sm">
            <p><Badge tone={point.severity === "high" ? "bad" : point.severity === "medium" ? "warn" : "neutral"}>{point.severity}</Badge> <strong>{label(point.section)}</strong>: {point.issue}</p>
            <p className="mt-1 text-muted">{point.why_it_matters} Suggested: {point.suggested_fix}</p>
          </div>
        ))}
        <p className="mb-1 mt-4 text-xs font-semibold uppercase text-muted">What changed in response</p>
        {doc.revision.changes.map((change) => (
          <p key={change.critique_issue} className="mt-2 text-sm"><Badge tone={change.decision === "rejected" ? "bad" : "good"}>{label(change.decision)}</Badge> {change.critique_issue}. <span className="text-muted">{change.change_made} ({change.reason})</span></p>
        ))}
      </Card>
    </div>
  );
}

export default function StrategyPage() {
  const { workspace } = useSession();
  const [tab, setTab] = useState<"strategy" | "research">("strategy");
  return (
    <>
      <PageHeader title="Research and strategy" subtitle="Every recommendation shows the evidence behind it. Comment on a section to have it revised." />
      <Tabs value={tab} onChange={setTab} options={[{ value: "strategy", label: "Strategy" }, { value: "research", label: "Research brief" }]} />
      {tab === "strategy" ? <StrategyView key={workspace} workspace={workspace} /> : <ResearchView key={workspace} workspace={workspace} />}
    </>
  );
}
