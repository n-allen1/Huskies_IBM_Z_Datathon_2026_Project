<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { STALL_MS, live } from '$lib/live.svelte';
  import { ageText } from '$lib/ops';

  let { children } = $props();

  let now = $state(Date.now());

  onMount(() => {
    live.start();
    const t = setInterval(() => (now = Date.now()), 1000);
    return () => {
      live.stop();
      clearInterval(t);
    };
  });

  const nav = [
    { href: '/dispatch', text: 'Dispatch' },
    { href: '/intake', text: 'Intake' }
  ];

  // Silence runs from the last frame, or from connect time if nothing has arrived yet.
  const lastSeen = $derived(live.lastEventAt ?? live.connectedAt);
  const silence = $derived(lastSeen ? now - lastSeen : undefined);
  const health = $derived<'live' | 'stalled' | 'connecting' | 'lost'>(
    live.status === 'reconnecting'
      ? 'lost'
      : live.status === 'connecting'
        ? 'connecting'
        : silence !== undefined && silence > STALL_MS
          ? 'stalled'
          : 'live'
  );
  const lastSignal = $derived(silence === undefined ? 'no signal received yet' : live.lastEventAt ? `last signal ${ageText(silence)} ago` : `connected ${ageText(silence)} ago, no signal yet`);
  const clock = $derived(
    new Date(now).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })
  );
</script>

<div class="flex h-screen flex-col bg-app">
  <header class="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-3">
    <a href="/dispatch" class="flex items-center gap-2 rounded-md pr-1 text-[0.95rem] font-semibold tracking-tight">
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" class="text-accent">
        <path d="M10 1.5 18 6v8l-8 4.5L2 14V6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
        <circle cx="10" cy="10" r="2.4" fill="currentColor" />
      </svg>
      ResponseMatch
    </a>
    <nav aria-label="Main" class="flex gap-0.5">
      {#each nav as n}
        {@const current = page.url.pathname.startsWith(n.href)}
        <a
          href={n.href}
          class="rounded-md px-3 py-1.5 text-sm font-medium {current ? 'bg-raised text-fg' : 'text-muted hover:bg-raised hover:text-fg'}"
          aria-current={current ? 'page' : undefined}>{n.text}</a
        >
      {/each}
    </nav>

    <span
      class="ml-2 inline-flex items-center gap-1.5 rounded border border-warn/60 bg-warn-soft px-2 py-0.5 text-[0.7rem] font-semibold tracking-wide text-warn"
      title="ResponseMatch is decision support: no real dispatch orders are issued."
    >
      ▲ SYNTHETIC DATA · SIMULATION ONLY
      <span class="sr-only">ResponseMatch is decision support: no real dispatch orders are issued.</span>
    </span>

    <div class="ml-auto flex items-center gap-4 text-xs">
      <span class="font-mono text-sm text-muted" aria-label="Local time">{clock}</span>
      <p
        class="flex items-center gap-2 rounded-md border px-2.5 py-1 font-semibold
          {health === 'live' ? 'border-line-strong text-fg' : health === 'lost' ? 'border-danger bg-danger-soft text-danger' : 'border-warn bg-warn-soft text-warn'}"
        role="status"
        aria-live="polite"
      >
        <span aria-hidden="true">{health === 'live' ? '●' : health === 'lost' ? '✕' : '▲'}</span>
        {#if health === 'live'}
          <span class="text-ok">LIVE</span><span class="font-normal text-muted">{live.lastEventAt === undefined ? 'connected, awaiting first heartbeat' : lastSignal}</span>
        {:else if health === 'connecting'}
          CONNECTING…
        {:else if health === 'stalled'}
          NO SIGNAL <span class="font-normal">{lastSignal}</span>
        {:else}
          OFFLINE <span class="font-normal">reconnecting · {lastSignal}</span>
        {/if}
      </p>
    </div>
  </header>

  {#if health === 'lost' || health === 'stalled'}
    <div class="shrink-0 border-b border-danger/60 bg-danger-soft px-4 py-1.5 text-sm text-fg" role="alert">
      <strong class="text-danger">Live updates unavailable.</strong>
      Incidents, units and recommendations shown may be out of date ({lastSignal}). Reconnecting automatically; verify critical details before acting.
    </div>
  {/if}

  <main class="min-h-0 flex-1">
    {@render children()}
  </main>
</div>
