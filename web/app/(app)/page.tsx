"use client";

import Link from "next/link";
import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, money, useSession } from "@/lib/session";
import { Badge, Button, Card, Empty, ErrorState, Loading, Notice, PageHeader, SectionTitle, statusTone } from "@/components/ui";

type AgentRun = { agent: string; llm_calls: number; input_tokens: number; output_tokens: number; cost_usd: number };
type Step = { stage: string; status: string; detail: string; seconds: number | null; agents: AgentRun[] };
type Timeline = {
  cycle_id: number;
  stage: string;
  halted_reason: string | null;
  live_cost_usd: number;
  steps: Step[];
  awaiting_approval: { draft_id: number; piece: string; lowest_score: number; passed_critic: boolean }[];
  blocked_by_guardrails: { draft_id: number; piece: string }[];
  alerts: string[];
};
type Mission = { timeline: Timeline | null; budget: { weekly_limit_usd: number; spent_this_week_usd: number } };

// Which stages each team member works in.
const TEAM = [
  { agent: "research", name: "Researcher", stages: ["research"], does: "Reads competitors, reviews and the market" },
  { agent: "strategist", name: "Strategist", stages: ["strategy_check"], does: "Keeps the strategy current" },
  { agent: "content", name: "Writer", stages: ["content_plan", "drafting"], does: "Plans and drafts this week's content" },
  { agent: "critic", name: "Editor", stages: ["drafting", "critic"], does: "Scores every draft and sends edits back" },
];
const ORDER = ["research", "strategy_check", "content_plan", "drafting", "critic", "awaiting_approval", "scheduled", "published", "measured"];

function memberState(timeline: Timeline | null, stages: string[]) {
  if (!timeline) return { text: "Idle", tone: "neutral" as const, step: undefined };
  const steps = timeline.steps.filter((step) => stages.includes(step.stage));
  const running = steps.find((step) => step.status === "running");
  if (running) return { text: "Working", tone: "accent" as const, step: running };
  const stopped = steps.find((step) => ["failed", "blocked"].includes(step.status));
  if (stopped) return { text: "Stopped", tone: "bad" as const, step: stopped };
  const last = steps.at(-1);
  if (last) return { text: last.status === "skipped" ? "Nothing to do" : "Done", tone: "good" as const, step: last };
  return { text: "Waiting", tone: "neutral" as const, step: undefined };
}

export default function MissionControl() {
  const { workspace } = useSession();
  const mission = useApi<Mission>(`/workspaces/${workspace}/mission`, 4000);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<ApiError | null>(null);

  async function act(path: string) {
    setBusy(true);
    setActionError(null);
    try {
      await api(path, { method: "POST" });
      mission.reload();
    } catch (caught) {
      setActionError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  if (mission.loading && !mission.data) return <Loading rows={4} />;
  if (mission.error) return <ErrorState error={mission.error} retry={mission.reload} />;
  const { timeline, budget } = mission.data!;
  const working = timeline?.steps.some((step) => step.status === "running") ?? false;
  const spentShare = Math.min(100, (budget.spent_this_week_usd / Math.max(budget.weekly_limit_usd, 0.01)) * 100);
  const pending = timeline?.awaiting_approval ?? [];
  const blocked = timeline?.blocked_by_guardrails ?? [];
  const needsReview = pending.length > 0;

  return (
    <>
      <PageHeader
        title="Mission control"
        subtitle={timeline ? `This week's cycle · ${label(timeline.stage)}` : "No cycle has run yet"}
        actions={
          // While drafts wait for approval, reviewing them is the main job, not starting again.
          <Button variant={needsReview ? "secondary" : "primary"} busy={busy} disabled={working} onClick={() => act(`/workspaces/${workspace}/cycles`)}>
            {working ? "Team is working" : needsReview ? "Start a new cycle anyway" : "Start this week's cycle"}
          </Button>
        }
      />
      {actionError && <div className="mb-4"><ErrorState error={actionError} /></div>}

      {timeline?.halted_reason && (
        <div className="mb-4 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-bad/30 bg-bad-soft px-4 py-3 text-sm text-bad">
          <span><strong>The cycle stopped.</strong> {timeline.halted_reason}</span>
          <Button variant="secondary" busy={busy} onClick={() => act(`/cycles/${timeline.cycle_id}/resume`)}>Resume</Button>
        </div>
      )}

      {(pending.length > 0 || blocked.length > 0) && (
        <Card className="mb-4 border-accent/40">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-medium">
                {pending.length} draft{pending.length === 1 ? "" : "s"} waiting for your approval
              </p>
              <p className="mt-0.5 text-sm text-muted">
                Nothing is scheduled or published until you approve it.
                {blocked.length > 0 && ` ${blocked.length} more blocked by a guardrail.`}
              </p>
            </div>
            <Link href="/calendar?review=1" className="inline-flex min-h-10 items-center rounded-lg bg-accent px-3.5 text-sm font-medium text-on-accent">
              Review drafts
            </Link>
          </div>
        </Card>
      )}

      <div className="mb-4 grid gap-4 sm:grid-cols-2">
        <Card>
          <SectionTitle>Cost so far this cycle</SectionTitle>
          <p className="text-3xl font-semibold tabular-nums">{money(timeline?.live_cost_usd ?? 0)}</p>
          <p className="mt-1 text-sm text-muted">Model spend only, updated as the team works.</p>
        </Card>
        <Card>
          <SectionTitle>Weekly budget</SectionTitle>
          <p className="text-sm">
            <span className="text-3xl font-semibold tabular-nums">{money(budget.spent_this_week_usd)}</span>
            <span className="text-muted"> of {money(budget.weekly_limit_usd)}</span>
          </p>
          <div className="mt-3 h-2 overflow-hidden rounded-full bg-raised" role="meter" aria-valuenow={Math.round(spentShare)} aria-valuemin={0} aria-valuemax={100} aria-label="Share of weekly budget spent">
            <div className={`h-full rounded-full ${spentShare >= 90 ? "bg-bad" : "bg-accent"}`} style={{ width: `${spentShare}%` }} />
          </div>
          <p className="mt-2 text-sm text-muted">The team stops and alerts you if this is reached.</p>
        </Card>
      </div>

      <SectionTitle>The team</SectionTitle>
      <div className="mb-6 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {TEAM.map((member) => {
          const state = memberState(timeline, member.stages);
          const runs = timeline?.steps.flatMap((step) => step.agents).filter((run) => run.agent === member.agent) ?? [];
          const cost = runs.reduce((sum, run) => sum + run.cost_usd, 0);
          const calls = runs.reduce((sum, run) => sum + run.llm_calls, 0);
          return (
            <Card key={member.agent} className="flex flex-col">
              <div className="flex items-center justify-between gap-2">
                <p className="font-medium">{member.name}</p>
                <Badge tone={state.tone}>
                  {state.text === "Working" && <span className="mr-1.5 inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-accent" />}
                  {state.text}
                </Badge>
              </div>
              <p className="mt-2 flex-1 text-sm text-muted">{state.step?.detail || member.does}</p>
              <p className="mt-3 border-t border-line pt-2 text-xs tabular-nums text-muted">
                {calls} model calls · {money(cost)}
              </p>
            </Card>
          );
        })}
      </div>

      <SectionTitle>Timeline</SectionTitle>
      {!timeline ? (
        <Empty
          title="The team has not started yet"
          body="Start this week's cycle and you will see each step here as it happens: research, strategy check, content plan, drafting and the editor's review."
        />
      ) : (
        <Card className="p-0 sm:p-0">
          <ol>
            {ORDER.map((stage, index) => {
              const step = timeline.steps.findLast((item) => item.stage === stage);
              const reached = ORDER.indexOf(timeline.stage) >= index;
              const human = index >= ORDER.indexOf("awaiting_approval");
              const status = step?.status ?? (timeline.stage === stage ? "current" : reached ? "done" : "upcoming");
              return (
                <li key={stage} className="flex gap-3 border-b border-line px-4 py-3 last:border-0 sm:px-5">
                  <span className={`mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ${status === "upcoming" ? "border border-line" : status === "current" || status === "running" ? "bg-accent" : ["failed", "blocked"].includes(status) ? "bg-bad" : "bg-good"}`} />
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={`text-sm font-medium ${status === "upcoming" ? "text-muted" : ""}`}>{label(stage)}</span>
                      {step && <Badge tone={statusTone(step.status)}>{label(step.status)}</Badge>}
                      {human && <Badge>{status === "current" ? "Waiting on you" : "Needs you"}</Badge>}
                      {step?.seconds != null && <span className="text-xs tabular-nums text-muted">{Math.round(step.seconds)}s</span>}
                    </div>
                    {step?.detail && <p className="mt-1 text-sm text-muted">{step.detail}</p>}
                    {step?.agents.filter((run) => run.llm_calls > 0).map((run) => (
                      <p key={run.agent} className="mt-1 text-xs tabular-nums text-muted">
                        {label(run.agent)}: {run.llm_calls} calls · {(run.input_tokens + run.output_tokens).toLocaleString()} tokens · {money(run.cost_usd)}
                      </p>
                    ))}
                  </div>
                </li>
              );
            })}
          </ol>
        </Card>
      )}
      {timeline?.alerts.map((alert) => <div key={alert} className="mt-3"><Notice tone="bad">{alert}</Notice></div>)}
    </>
  );
}
