"use client";

import { useState } from "react";
import Link from "next/link";

const TOUR = [
  ["/", "Mission control", "The weekly cycle live: which agent is working, what it costs. Try “Replay the last week”."],
  ["/calendar", "Calendar and review", "Every draft waits here for a person. Open one to see the editor's scores and the ad images."],
  ["/results", "Results", "Experiments with their uncertainty, and how well the synthetic panel predicted them."],
  ["/signals", "Signals", "What competitors, search and public discussion changed this week, each with its source."],
  ["/evals", "Evals", "The quality and red-team checks every change must pass, and a trace of a cycle."],
] as const;

/** The public demo's banner and a five-step tour. Everything is sample data and read-only. */
export function DemoBanner() {
  const [step, setStep] = useState<number | null>(null);
  const current = step === null ? null : TOUR[step];
  return (
    <div className="sticky top-0 z-40 border-b border-warn/30 bg-warn-soft px-4 py-2 text-sm text-warn">
      <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-2">
        <span><strong>Demo:</strong> a fictional brand with sample data. Read-only: actions are shown but not saved.</span>
        <button className="font-medium underline" onClick={() => setStep(step === null ? 0 : null)}>{step === null ? "Take the tour" : "End the tour"}</button>
      </div>
      {current && (
        <div className="mx-auto mt-2 max-w-6xl rounded-lg bg-surface p-3 text-ink shadow-sm">
          <p className="font-medium">{step! + 1} of {TOUR.length}: {current[1]}</p>
          <p className="mt-1 text-muted">{current[2]}</p>
          <div className="mt-2 flex gap-3">
            <Link href={current[0]} className="font-medium text-accent">Open {current[1]}</Link>
            {step! < TOUR.length - 1 ? <button className="font-medium" onClick={() => setStep(step! + 1)}>Next</button> : <button className="font-medium" onClick={() => setStep(null)}>Done</button>}
          </div>
        </div>
      )}
    </div>
  );
}

const THEMES = [["system", "Match my device"], ["light", "Light"], ["dark", "Dark"]] as const;

export function ThemePicker() {
  const [theme, setTheme] = useState(() => {
    try {
      return (typeof window !== "undefined" && window.localStorage.getItem("growthcrew.theme")) || "system";
    } catch {
      return "system";
    }
  });
  function choose(value: string) {
    setTheme(value);
    try {
      if (value === "system") window.localStorage.removeItem("growthcrew.theme");
      else window.localStorage.setItem("growthcrew.theme", value);
    } catch {
      /* storage unavailable: applies to this tab only */
    }
    if (value === "system") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = value;
  }
  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex gap-1 rounded-lg bg-raised p-1">
      {THEMES.map(([value, name]) => (
        <button key={value} role="radio" aria-checked={theme === value} onClick={() => choose(value)}
          className={`min-h-9 rounded-md px-3 text-sm font-medium ${theme === value ? "bg-surface shadow-sm" : "text-muted"}`}>
          {name}
        </button>
      ))}
    </div>
  );
}
