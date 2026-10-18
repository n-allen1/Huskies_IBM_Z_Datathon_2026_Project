import { DEMO_TOKEN } from './api/client';
import type { EventEnvelope } from './api/types';
import { frameToEnvelope, parseSse } from './sse';

export type LiveStatus = 'connecting' | 'live' | 'reconnecting';
type Listener = (e: EventEnvelope) => void;

/** Three missed 15 s heartbeats. */
export const STALL_MS = 45_000;
/** A stream that stays open this long without any frame still counts as connected. */
const HEALTHY_AFTER_MS = 5_000;

/** Fetch-based SSE client (EventSource cannot send the bearer header the contract requires). */
class LiveConnection {
  status = $state<LiveStatus>('connecting');
  lastEventId = $state<string | undefined>(undefined);
  lastEventAt = $state<number | undefined>(undefined);
  /** When the current stream connected: silence is measured from here until the first frame arrives. */
  connectedAt = $state<number | undefined>(undefined);
  /** Bumped on stream.resync_required and after a reconnect: consumers should refetch. */
  resyncCount = $state(0);

  #listeners = new Set<Listener>();
  #abort: AbortController | null = null;
  #attempt = 0;

  on(fn: Listener): () => void {
    this.#listeners.add(fn);
    return () => this.#listeners.delete(fn);
  }

  start(): void {
    if (this.#abort) return;
    this.#abort = new AbortController();
    void this.#loop(this.#abort.signal);
  }

  stop(): void {
    this.#abort?.abort();
    this.#abort = null;
  }

  async #loop(signal: AbortSignal): Promise<void> {
    while (!signal.aborted) {
      const wasReconnect = this.#attempt > 0;
      try {
        const headers: Record<string, string> = {
          Authorization: `Bearer ${DEMO_TOKEN}`,
          Accept: 'text/event-stream'
        };
        if (this.lastEventId) headers['Last-Event-ID'] = this.lastEventId;
        // Per-connection controller so a silent stall can be cut without stopping the whole client.
        const conn = new AbortController();
        const stop = () => conn.abort();
        signal.addEventListener('abort', stop);
        const res = await fetch('/api/v1/events/stream', { headers, signal: conn.signal });
        if (!res.ok || !res.body) throw new Error(`stream HTTP ${res.status}`);
        this.connectedAt = Date.now();
        // A 200 alone is not health: a broken proxy or a non-streaming mock answers 200 and closes at once.
        // Only call the stream live after a valid frame or after it has stayed open for a few seconds, and only
        // then reset the backoff. Otherwise a connect/close loop would flap LIVE and OFFLINE every cycle.
        const healthy = () => {
          if (this.status === 'live') return;
          this.status = 'live';
          this.#attempt = 0;
          if (wasReconnect) this.resyncCount++;
        };
        const healthyTimer = setTimeout(healthy, HEALTHY_AFTER_MS);
        conn.signal.addEventListener('abort', () => clearTimeout(healthyTimer));
        // The server sends a heartbeat every 15 s (contract). Three missed beats means the connection is dead
        // even though the socket never closed (proxy or network drop): abort so the loop reconnects and resyncs.
        const watchdog = setInterval(() => {
          if (Date.now() - (this.lastEventAt ?? this.connectedAt ?? 0) > STALL_MS) conn.abort();
        }, 5000);
        conn.signal.addEventListener('abort', () => clearInterval(watchdog));
        const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
        let buf = '';
        try {
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buf += value;
            const { frames, rest } = parseSse(buf);
            buf = rest;
            for (const f of frames) {
              const env = frameToEnvelope(f);
              if (env) healthy();
              this.#dispatch(f.id, env);
            }
          }
        } finally {
          clearInterval(watchdog);
          clearTimeout(healthyTimer);
          signal.removeEventListener('abort', stop);
        }
      } catch {
        if (signal.aborted) return;
      }
      this.status = 'reconnecting';
      this.#attempt++;
      const delay = Math.min(15000, 1000 * 2 ** Math.min(this.#attempt, 4));
      await new Promise((r) => setTimeout(r, delay));
    }
  }

  #dispatch(id: string | undefined, env: EventEnvelope | null): void {
    if (!env) return;
    if (id) this.lastEventId = id;
    this.lastEventAt = Date.now();
    if (env.type === 'stream.resync_required') this.resyncCount++;
    if (env.type === 'stream.heartbeat') return;
    for (const fn of this.#listeners) fn(env);
  }
}

export const live = new LiveConnection();
