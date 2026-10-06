"use client";

import { useEffect, useState } from "react";
import { wordDiff } from "@/lib/diff";
import { api, ApiError, useApi } from "@/lib/api";
import { label } from "@/lib/session";
import { Badge, Button, ErrorState, Field, inputClass, Loading, Notice, Sheet, statusTone } from "@/components/ui";
import { AdImages, LandingPreview } from "@/components/creatives";
import { PanelPrediction } from "@/components/panel";

type Review = {
  id: number;
  piece: string;
  content_type: string;
  angle: string | null;
  status: string;
  calendar: { id: number; status: string; scheduled_for: string } | null;
  text: string;
  edited: boolean;
  metadata: { messaging_pillar?: string; target_persona?: string; cta?: string; hypothesis?: string };
  scores: Record<string, number>;
  rounds: number;
  edits: { line: number; criterion: string; original: string; suggestion: string; reason: string }[];
  violations: { rule: string; line: number; excerpt: string; reason: string }[];
  first_draft: string | null;
  tracking_key: string;
  memory: { examples?: { text: string; rate_pct: number; metric: string; score: number; published_on: string }[]; rules?: string[] };
  variants: { id: number; angle: string; status: string; lowest_score: number }[];
};

type Mode = "view" | "edit" | "reject" | "publish";

export function ReviewPanel(props: { draftId: number; queue: number[]; onClose: () => void; onChanged: () => void; onOpen: (id: number) => void }) {
  const review = useApi<Review>(`/drafts/${props.draftId}/review`);
  const [mode, setMode] = useState<Mode>("view");
  const [text, setText] = useState("");
  const [comment, setComment] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState("");
  const [showFirst, setShowFirst] = useState(false);
  const data = review.data;
  // The other drafts still waiting, so a reviewer can go straight to the next one.
  const [decided, setDecided] = useState(false);
  const remaining = props.queue.filter((id) => id !== props.draftId);
  const position = props.queue.indexOf(props.draftId);

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    setError(null);
    try {
      const result = (await action()) as { voice_rules_proposed?: string[] } | undefined;
      const learned = result?.voice_rules_proposed ?? [];
      setNotice(learned.length ? `${done} Proposed as a brand voice rule, waiting in the Playbook: ${learned.join("; ")}` : done);
      setMode("view");
      setDecided(true);
      review.reload();
      props.onChanged();
    } catch (caught) {
      setError(caught as ApiError);
    } finally {
      setBusy(false);
    }
  }

  // Keyboard shortcuts on the review queue: A approve, R reject, E edit, J/K next/previous.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;
      if (!data || mode !== "view" || busy) return;
      const key = event.key.toLowerCase();
      const at = props.queue.indexOf(props.draftId);
      if (key === "a" && data.status === "pending_approval") decide("approved");
      else if (key === "r" && ["pending_approval", "blocked"].includes(data.status)) { setComment(""); setMode("reject"); }
      else if (key === "e" && ["pending_approval", "blocked"].includes(data.status)) { setText(data.text); setComment(""); setMode("edit"); }
      else if (key === "j" && at >= 0 && at < props.queue.length - 1) props.onOpen(props.queue[at + 1]);
      else if (key === "k" && at > 0) props.onOpen(props.queue[at - 1]);
      else return;
      event.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const decide = (decision: string, extra: object = {}) =>
    run(() => api(`/drafts/${props.draftId}/decision`, { method: "POST", body: { decision, comment, ...extra } }),
      decision === "rejected" ? "Rejected." : decision === "edited" ? "Saved with your edits and approved." : "Approved and added to the calendar.");

  let footer: React.ReactNode = null;
  if (data && mode === "view" && decided && remaining.length > 0)
    footer = (
      <div className="grid grid-cols-[auto_1fr] gap-2">
        <Button onClick={props.onClose}>Done for now</Button>
        <Button variant="primary" onClick={() => props.onOpen(remaining[0])}>Next draft · {remaining.length} left</Button>
      </div>
    );
  else if (data && mode === "view" && decided && data.status !== "blocked" && data.calendar?.status !== "scheduled")
    footer = <Button variant="primary" className="w-full" onClick={props.onClose}>All caught up</Button>;
  else if (data && mode === "view") {
    if (data.status === "pending_approval")
      footer = (
        <div className="grid grid-cols-3 gap-2">
          <Button variant="danger" onClick={() => { setComment(""); setMode("reject"); }}>Reject</Button>
          <Button onClick={() => { setText(data.text); setComment(""); setMode("edit"); }}>Edit</Button>
          <Button variant="primary" busy={busy} onClick={() => decide("approved")}>Approve</Button>
        </div>
      );
    else if (data.status === "blocked")
      footer = (
        <div className="grid grid-cols-2 gap-2">
          <Button variant="danger" onClick={() => { setComment(""); setMode("reject"); }}>Reject</Button>
          <Button variant="primary" onClick={() => { setText(data.text); setComment(""); setMode("edit"); }}>Edit to fix</Button>
        </div>
      );
    else if (data.calendar?.status === "scheduled")
      footer = (
        <div className="grid grid-cols-2 gap-2">
          <Button onClick={async () => { await navigator.clipboard.writeText(data.text); setNotice("Copied to the clipboard."); }}>Copy text</Button>
          <Button variant="primary" onClick={() => { setUrl(""); setMode("publish"); }}>I published this</Button>
        </div>
      );
  } else if (data && mode === "edit")
    footer = (
      <div className="grid grid-cols-2 gap-2">
        <Button onClick={() => setMode("view")}>Cancel</Button>
        <Button variant="primary" busy={busy} disabled={text === data.text} onClick={() => decide("edited", { edited_text: text })}>Save and approve</Button>
      </div>
    );
  else if (data && mode === "reject")
    footer = (
      <div className="grid grid-cols-2 gap-2">
        <Button onClick={() => setMode("view")}>Cancel</Button>
        <Button variant="danger" busy={busy} onClick={() => decide("rejected")}>Reject this draft</Button>
      </div>
    );
  else if (data && mode === "publish")
    footer = (
      <div className="grid grid-cols-2 gap-2">
        <Button onClick={() => setMode("view")}>Cancel</Button>
        <Button variant="primary" busy={busy} onClick={() => run(() => api(`/calendar/${data.calendar!.id}/publish`, { method: "POST", body: { published_by: "me", confirm: true, url: url || null } }), "Marked as published.")}>
          Confirm it is live
        </Button>
      </div>
    );

  return (
    <Sheet title={data ? `${label(data.content_type)}${data.angle ? ` · ${label(data.angle)} angle` : ""}` : "Draft"} onClose={props.onClose} footer={footer}>
      {review.loading && !data && <Loading rows={2} />}
      {review.error && <ErrorState error={review.error} retry={review.reload} />}
      {data && (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone={statusTone(data.calendar?.status ?? data.status)}>{label(data.calendar?.status ?? data.status)}</Badge>
            {data.edited && <Badge>Edited by you</Badge>}
            <span className="text-xs text-muted">{data.rounds} editor round{data.rounds === 1 ? "" : "s"}</span>
            {position >= 0 && props.queue.length > 1 && <span className="ml-auto text-xs font-medium text-muted">{position + 1} of {props.queue.length} to review</span>}
            <span className="hidden w-full text-xs text-muted md:block">Keys: A approve · R reject · E edit · J/K next and previous</span>
          </div>
          {notice && <Notice tone="good">{notice}</Notice>}
          {error && <ErrorState error={error} />}

          {data.variants.length > 0 && (
            <div>
              <p className="mb-1.5 text-xs font-semibold uppercase text-muted">Variants in this test</p>
              <div className="flex flex-wrap gap-2">
                <span className="rounded-lg border-2 border-accent px-3 py-1.5 text-sm font-medium">{label(data.angle ?? "this")}</span>
                {data.variants.map((variant) => (
                  <button key={variant.id} onClick={() => props.onOpen(variant.id)} className="rounded-lg border border-line px-3 py-1.5 text-sm hover:bg-raised">
                    {label(variant.angle)} <span className="text-muted">· {label(variant.status)}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          {data.variants.length > 0 && <PanelPrediction draftId={data.id} />}

          {data.violations.length > 0 && (
            <Notice tone="bad">
              <strong>Blocked by a guardrail.</strong> It cannot be approved as written.
              <ul className="mt-1 list-disc pl-5">{data.violations.map((violation) => <li key={violation.excerpt}>Line {violation.line}: {violation.reason} (“{violation.excerpt}”)</li>)}</ul>
            </Notice>
          )}

          {mode === "edit" ? (
            <div className="space-y-3">
              <Field label="Your version">
                <textarea className={`${inputClass} font-mono`} rows={12} value={text} onChange={(event) => setText(event.target.value)} />
              </Field>
              {text !== data.text && (
                <div>
                  <p className="mb-1 text-xs font-semibold uppercase text-muted">Your changes</p>
                  <div className="whitespace-pre-wrap rounded-xl border border-line bg-bg px-4 py-3 text-[15px] leading-relaxed" aria-label="Tracked changes">
                    {wordDiff(data.text, text).map((piece, index) =>
                      piece.kind === "same" ? <span key={index}>{piece.text}</span>
                      : piece.kind === "removed" ? <del key={index} className="bg-bad-soft text-bad decoration-bad/70">{piece.text}</del>
                      : <ins key={index} className="bg-good-soft text-good no-underline">{piece.text}</ins>)}
                  </div>
                </div>
              )}
              <Field label="Why the change?" hint="Optional. Recurring edits become brand voice rules.">
                <input className={inputClass} value={comment} onChange={(event) => setComment(event.target.value)} />
              </Field>
            </div>
          ) : (
            <div>
              <div className="whitespace-pre-wrap rounded-xl border border-line bg-bg px-4 py-3 text-[15px] leading-relaxed">{showFirst && data.first_draft ? data.first_draft : data.text}</div>
              {data.first_draft && (
                <button onClick={() => setShowFirst(!showFirst)} className="mt-1.5 text-sm font-medium text-accent">
                  {showFirst ? "Show the final version" : "Show the first draft, before the editor"}
                </button>
              )}
            </div>
          )}

          {mode === "view" && data.content_type === "ad" && <AdImages draftId={data.id} disabled={data.status === "rejected"} />}
          {mode === "view" && data.content_type === "landing_hero" && <LandingPreview draftId={data.id} />}

          {mode === "reject" && (
            <Field label="What is wrong with it?" hint="Optional, but it helps the team.">
              <textarea className={inputClass} rows={3} value={comment} onChange={(event) => setComment(event.target.value)} />
            </Field>
          )}
          {mode === "publish" && (
            <div className="space-y-3">
              <Notice>GrowthCrew does not post for you yet. Confirm here once you have published this yourself.</Notice>
              <Field label="Where is it live?" hint="Optional. The link lets us match its results.">
                <input className={inputClass} type="url" placeholder="https://" value={url} onChange={(event) => setUrl(event.target.value)} />
              </Field>
            </div>
          )}

          {Object.keys(data.scores).length > 0 && (
            <div>
              <p className="mb-2 text-xs font-semibold uppercase text-muted">Editor&apos;s scores (8 or more passes)</p>
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-3">
                {Object.entries(data.scores).map(([name, score]) => (
                  <div key={name} className="text-sm">
                    <div className="flex justify-between"><span>{label(name === "ai_cliche" ? "No clichés" : name)}</span><span className={`font-semibold tabular-nums ${score < 8 ? "text-bad" : ""}`}>{score}</span></div>
                    <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-raised"><div className={`h-full rounded-full ${score < 8 ? "bg-bad" : "bg-good"}`} style={{ width: `${score * 10}%` }} /></div>
                  </div>
                ))}
              </div>
            </div>
          )}

          <dl className="space-y-2 rounded-xl bg-raised px-4 py-3 text-sm">
            {([["Serves pillar", data.metadata.messaging_pillar], ["Written for", data.metadata.target_persona], ["Call to action", data.metadata.cta], ["Tests the hypothesis", data.metadata.hypothesis]] as const).filter(([, value]) => value).map(([name, value]) => (
              <div key={name}><dt className="text-xs font-semibold uppercase text-muted">{name}</dt><dd>{value}</dd></div>
            ))}
            <div><dt className="text-xs font-semibold uppercase text-muted">Tracking key</dt><dd className="break-all font-mono text-xs">{data.tracking_key}</dd></div>
          </dl>

          {((data.memory?.examples?.length ?? 0) > 0 || (data.memory?.rules?.length ?? 0) > 0) && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">
                Written with {data.memory.examples?.length ?? 0} past winner{data.memory.examples?.length === 1 ? "" : "s"} and {data.memory.rules?.length ?? 0} playbook rule{data.memory.rules?.length === 1 ? "" : "s"}
              </summary>
              <ul className="mt-2 space-y-1.5 text-sm">
                {data.memory.rules?.map((rule) => <li key={rule}><span className="font-medium">Rule:</span> {rule}</li>)}
                {data.memory.examples?.map((example, index) => (
                  <li key={index} className="text-muted">
                    <span className="font-medium text-ink">Past winner</span> ({example.rate_pct}% {example.metric}, {example.score.toFixed(1)}x the brand average): “{example.text.split("\n")[0]}”
                  </li>
                ))}
              </ul>
            </details>
          )}

          {data.edits.length > 0 && (
            <details>
              <summary className="cursor-pointer text-sm font-medium">What the editor changed ({data.edits.length})</summary>
              <ul className="mt-2 space-y-2 text-sm">
                {data.edits.map((edit, index) => (
                  <li key={index} className="rounded-lg border border-line px-3 py-2">
                    <p className="text-muted">Line {edit.line} · {label(edit.criterion)}: {edit.reason}</p>
                    <p className="mt-1 line-through decoration-bad/60">{edit.original}</p>
                    <p className="mt-0.5">{edit.suggestion}</p>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </Sheet>
  );
}
