"use client";

import { useState } from "react";
import { api, ApiError, useApi } from "@/lib/api";
import { label, plural, useSession } from "@/lib/session";
import { Badge, Button, Card, Empty, ErrorState, Loading, PageHeader, SectionTitle } from "@/components/ui";

type RuleEvent = { at: string; status: string; lift_pct: number; probability: number; note: string };
type Rule = {
  id: number;
  statement: string;
  status: string;
  lift_pct: number;
  probability: number;
  pieces_with: number;
  pieces_without: number;
  found_on: string;
  history: RuleEvent[];
};
type Playbook = {
  as_of: string;
  pieces_remembered: number;
  active: Rule[];
  weakening: Rule[];
  candidate: Rule[];
  retired: Rule[];
  learned: { days: number; found: number; still_active: number; retired: number };
  by_version: { prompt_version: string; strategy_version: number; content_type: string; pieces: number; rate_pct: number }[];
};
type Proposal = { id: number; rule: string; times_seen: number };

const sure = (probability: number) => (probability > 0.99 ? "over 99%" : `${Math.round(probability * 100)}%`);

function RuleCard({ rule }: { rule: Rule }) {
  const tone = rule.status === "active" ? "good" : rule.status === "retired" ? "neutral" : "warn";
  return (
    <Card>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="font-medium">{rule.statement}</p>
        <Badge tone={tone}>{label(rule.status)}</Badge>
      </div>
      <p className="mt-1 text-sm tabular-nums text-muted">
        +{Math.abs(Math.round(rule.lift_pct))}% · {rule.pieces_with} pieces with it, {rule.pieces_without} without ·{" "}
        {sure(rule.probability)} probability it is real · found {rule.found_on.slice(0, 10)}
      </p>
      <details className="mt-2">
        <summary className="cursor-pointer text-sm font-medium text-accent">
          History ({plural(rule.history.length, "weekly run")})
        </summary>
        <ol className="mt-2 space-y-1 text-sm">
          {rule.history.map((event) => (
            <li key={event.at} className="flex flex-wrap gap-x-3 tabular-nums">
              <span className="text-muted">{event.at.slice(0, 10)}</span>
              <span>{label(event.status)}</span>
              <span>
                {event.lift_pct > 0 ? "+" : ""}
                {Math.round(event.lift_pct)}%, {Math.round(event.probability * 100)}%
              </span>
              <span className="text-muted">{event.note}</span>
            </li>
          ))}
        </ol>
      </details>
    </Card>
  );
}

function Proposals({ workspace }: { workspace: string }) {
  const proposals = useApi<Proposal[]>(`/workspaces/${workspace}/voice-proposals`);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  if (!proposals.data?.length) return null;

  async function decide(id: number, accept: boolean) {
    setBusy(id);
    setError(null);
    try {
      await api(`/workspaces/${workspace}/voice-proposals/${id}`, { method: "POST", body: { accept } });
      proposals.reload();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="mb-6">
      <SectionTitle>Voice rules proposed from your edits</SectionTitle>
      {error && (
        <div className="mb-3">
          <ErrorState error={error} />
        </div>
      )}
      <div className="space-y-3">
        {proposals.data.map((proposal) => (
          <Card key={proposal.id} className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="font-medium">{proposal.rule}</p>
              <p className="text-sm text-muted">
                You made this edit {proposal.times_seen} times. Add it to the brand voice?
              </p>
            </div>
            <div className="flex gap-2">
              <Button busy={busy === proposal.id} onClick={() => decide(proposal.id, false)}>
                Not a rule
              </Button>
              <Button variant="primary" busy={busy === proposal.id} onClick={() => decide(proposal.id, true)}>
                Add the rule
              </Button>
            </div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Rules({ title, rules, empty }: { title: string; rules: Rule[]; empty?: string }) {
  if (!rules.length && !empty) return null;
  return (
    <div>
      <SectionTitle>{title}</SectionTitle>
      <div className="space-y-3">
        {rules.length ? (
          rules.map((rule) => <RuleCard key={rule.id} rule={rule} />)
        ) : (
          <p className="text-sm italic text-muted">{empty}</p>
        )}
      </div>
    </div>
  );
}

export default function PlaybookPage() {
  const { workspace } = useSession();
  const book = useApi<Playbook>(`/workspaces/${workspace}/playbook`);
  if (book.loading) return <Loading rows={4} />;
  if (book.error) return <ErrorState error={book.error} retry={book.reload} />;
  const data = book.data!;
  const watching = [...data.weakening, ...data.candidate];
  const nothing = data.active.length + watching.length + data.retired.length === 0;
  const stats: [string, number][] = [
    [`Found in ${data.learned.days} days`, data.learned.found],
    ["Still holding", data.learned.still_active],
    ["Retired", data.learned.retired],
  ];
  return (
    <>
      <PageHeader
        title="Playbook"
        subtitle={`What has worked for this brand, from ${plural(data.pieces_remembered, "published piece")}. Patterns are leads, not proof.`}
      />
      <Proposals key={workspace} workspace={workspace} />
      {nothing ? (
        <Empty
          title="No patterns yet"
          body="Once pieces are published and their results uploaded, the weekly run looks for what keeps working. A pattern must hold twice to become a rule."
        />
      ) : (
        <div className="space-y-6">
          <div className="grid gap-3 sm:grid-cols-3">
            {stats.map(([name, value]) => (
              <Card key={name}>
                <p className="text-xs font-semibold uppercase text-muted">{name}</p>
                <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
              </Card>
            ))}
          </div>
          <Rules title="Active rules: the writer follows these" rules={data.active} empty="No rule is holding right now." />
          <Rules title="Out of use, being watched" rules={watching} />
          <Rules title="Retired" rules={data.retired} />
          <Card>
            <SectionTitle>Performance by prompt and strategy version</SectionTitle>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[26rem] text-left text-sm">
                <thead className="text-xs text-muted">
                  <tr>
                    <th className="py-1 font-medium">Writer prompt</th>
                    <th className="py-1 font-medium">Strategy</th>
                    <th className="py-1 font-medium">Content type</th>
                    <th className="py-1 text-right font-medium">Pieces</th>
                    <th className="py-1 text-right font-medium">Rate</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_version.map((row) => (
                    <tr key={row.prompt_version + row.strategy_version + row.content_type} className="border-t border-line tabular-nums">
                      <td className="py-1.5 font-mono text-xs">{row.prompt_version}</td>
                      <td className="py-1.5">v{row.strategy_version}</td>
                      <td className="py-1.5">{label(row.content_type)}</td>
                      <td className="py-1.5 text-right">{row.pieces}</td>
                      <td className="py-1.5 text-right">{row.rate_pct}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-xs text-muted">A before-and-after comparison across versions, not a test.</p>
          </Card>
        </div>
      )}
    </>
  );
}
