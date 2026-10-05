"use client";

import { createContext, useContext } from "react";

export type Workspace = {
  workspace: string;
  name: string;
  pending_approvals: number;
  onboarding: string;
};

export type Session = {
  user: { email: string; is_admin: boolean };
  workspaces: Workspace[];
  workspace: string;
  setWorkspace: (name: string) => void;
  reloadWorkspaces: () => void;
  signOut: () => void;
};

export const SessionContext = createContext<Session | null>(null);

export function useSession(): Session {
  const session = useContext(SessionContext);
  if (!session) throw new Error("useSession must be used inside the app layout");
  return session;
}

const NAMES: Record<string, string> = {
  linkedin_post: "LinkedIn post",
  x_thread: "X thread",
  ad: "Ad",
  landing_hero: "Landing page hero",
  cold_email_sequence: "Cold email sequence",
  blog_article: "Blog article",
  pending_approval: "Needs your approval",
  accepted_partial: "Accepted in part",
  social_proof: "Social proof",
  ai_cliche: "No clichés",
  price_change: "Price change",
  copy_tweak: "Copy tweak",
  new_post: "New blog post",
  new_ads: "New ads",
  keyword_gap: "Content gap",
  ranking_move: "Ranking move",
  seo: "SEO",
};

export const label = (value: string) =>
  NAMES[value] ?? value.replace(/[_.]/g, " ").replace(/^\w/, (letter) => letter.toUpperCase());

export const plural = (count: number, word: string) =>
  `${count.toLocaleString()} ${word}${count === 1 ? "" : "s"}`;

export const money = (value: number) => `$${value.toFixed(2)}`;
