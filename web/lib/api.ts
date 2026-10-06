"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const TOKEN_KEY = "growthcrew.token";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

export function getToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setToken(token: string | null) {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* storage unavailable: the session lasts until the tab closes */
  }
}

type Options = { method?: string; body?: unknown; csv?: string };

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const token = getToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  let body: string | undefined;
  if (options.csv !== undefined) {
    headers["Content-Type"] = "text/csv";
    body = options.csv;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { method: options.method ?? "GET", headers, body });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (response.status === 401 && !path.startsWith("/auth/login")) {
    setToken(null);
    window.location.assign("/login");
    throw new ApiError(401, "Your session has ended. Sign in again.");
  }
  if (!response.ok) {
    let message = `Something went wrong (${response.status}).`;
    try {
      const data = await response.json();
      if (typeof data.detail === "string") message = data.detail;
    } catch {
      if (response.status >= 500) message = "The server is not responding. Is the API running?";
    }
    throw new ApiError(response.status, message);
  }
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? response.json() : response.text()) as Promise<T>;
}

export type Loaded<T> = {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload: () => void;
};

/** Fetch a path, optionally re-fetching every `poll` ms. A null path fetches nothing. */
export function useApi<T>(path: string | null, poll?: number): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(path !== null);
  const current = useRef(path);

  const load = useCallback(
    async (quiet: boolean) => {
      if (path === null) return;
      if (!quiet) setLoading(true);
      try {
        const result = await api<T>(path);
        if (current.current === path) {
          setData(result);
          setError(null);
        }
      } catch (caught) {
        // A background refresh that fails keeps showing the last good data.
        if (current.current === path && !quiet) setError(caught as ApiError);
      } finally {
        if (current.current === path) setLoading(false);
      }
    },
    [path],
  );

  useEffect(() => {
    current.current = path;
    setData(undefined);
    setError(null);
    load(false);
    if (!poll || path === null) return;
    const timer = window.setInterval(() => {
      if (document.visibilityState === "visible") load(true);
    }, poll);
    return () => window.clearInterval(timer);
  }, [path, poll, load]);

  return { data, error, loading, reload: () => load(true) };
}

/** Fetch a protected file (an image) and return an object URL for it, or throw ApiError. */
export async function apiBlobUrl(path: string): Promise<string> {
  const token = getToken();
  let response: Response;
  try {
    response = await fetch(`/api${path}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
  } catch {
    throw new ApiError(0, "Cannot reach the server. Check your connection and try again.");
  }
  if (!response.ok) throw new ApiError(response.status, `Could not load the image (${response.status}).`);
  return URL.createObjectURL(await response.blob());
}

/**
 * Subscribe to a server-sent event stream (with the bearer token, which EventSource cannot
 * send). Calls `onData` with each event's JSON; reconnects after a drop; falls back to
 * polling `path` without `/stream` if streaming is unavailable.
 */
export function useStream<T>(path: string | null, fallback: string | null): Loaded<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<ApiError | null>(null);
  const [failed, setFailed] = useState(false);
  const polled = useApi<T>(failed ? fallback : null, 4000);

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    let stopped = false;
    async function run() {
      while (!stopped) {
        try {
          const token = getToken();
          const response = await fetch(`/api${path}`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
            signal: controller.signal,
          });
          if (!response.ok || !response.body) throw new ApiError(response.status, `Live updates unavailable (${response.status}).`);
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            let cut;
            while ((cut = buffer.indexOf("\n\n")) >= 0) {
              const chunk = buffer.slice(0, cut);
              buffer = buffer.slice(cut + 2);
              const line = chunk.split("\n").find((part) => part.startsWith("data: "));
              if (line) {
                setData(JSON.parse(line.slice(6)) as T);
                setError(null);
              }
            }
          }
        } catch (caught) {
          if (stopped) return;
          if (caught instanceof ApiError && caught.status >= 400 && caught.status < 500) {
            setError(caught);
            setFailed(true);
            return;
          }
        }
        await new Promise((resolve) => setTimeout(resolve, 2000));
      }
    }
    run();
    return () => {
      stopped = true;
      controller.abort();
    };
  }, [path]);

  if (failed) return polled;
  return { data, error, loading: data === undefined && !error, reload: () => undefined };
}
