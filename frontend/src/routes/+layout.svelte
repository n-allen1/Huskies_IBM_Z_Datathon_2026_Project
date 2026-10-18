<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { STALL_MS, live } from '$lib/live.svelte';
  import { ageText } from '$lib/ops';
  import { resetDemoData } from '$lib/api/client';
  import { DEMO_CONTROLS_KEY, DEMO_RESET_NOTE_KEY, demoControlsFrom } from '$lib/demo';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';

  let { children } = $props();

  let now = $state(Date.now());

  // Demo controls: only in a tab opened with ?demo=1 (remembered for the tab), so visitors never see them.
  let demoControls = $state(false);
  let resetDialog = $state(false);
  let resetBusy = $state(false);
  let resetNote = $state<{ ok: boolean; text: string } | null>(null);
  let resetNoteTimer: ReturnType<typeof setTimeout> | undefined;

  async function resetDemo() {
    resetBusy = true;
    const result = await resetDemoData();
    resetBusy = false;
    resetDialog = false;
    if (result.ok) {
      // The seed has new incident ids, and the dispatch queue deliberately keeps what the operator was looking at, so start
      // this tab from scratch. Other open tabs are told to refetch by the server (stream.resync_required).
      try { sessionStorage.setItem(DEMO_RESET_NOTE_KEY, result.text); } catch { /* the note is a courtesy */ }
      location.reload();
      return;
    }
    resetNote = result;
    clearTimeout(resetNoteTimer);
    resetNoteTimer = setTimeout(() => (resetNote = null), 12000);
  }

  onMount(() => {
    try {
      demoControls = demoControlsFrom(location.search, sessionStorage.getItem(DEMO_CONTROLS_KEY));
      sessionStorage.setItem(DEMO_CONTROLS_KEY, demoControls ? '1' : '0');
    } catch {
      demoControls = demoControlsFrom(location.search, null); // storage blocked: honor the URL only
    }
    try {
      const note = sessionStorage.getItem(DEMO_RESET_NOTE_KEY);
      if (note) {
        sessionStorage.removeItem(DEMO_RESET_NOTE_KEY);
        resetNote = { ok: true, text: note };
        resetNoteTimer = setTimeout(() => (resetNote = null), 6000);
      }
    } catch { /* storage blocked: no note */ }
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

<!-- overflow-clip, not hidden: nothing may make the document taller than the viewport, and a clipped box
     is not a scroll container, so focusing or typing in a panel can only scroll that panel, never the app shell. -->
<div class="flex h-screen flex-col overflow-clip bg-app">
  <header class="flex h-12 shrink-0 items-center gap-4 border-b border-line bg-panel px-3">
    <a href="/dispatch" class="flex items-center gap-2 rounded-md pr-1 text-[0.95rem] font-semibold tracking-tight">
      <svg width="20" height="20" viewBox="0 0 20 20" aria-hidden="true" class="text-accent">
        <path d="M10 1.5 18 6v8l-8 4.5L2 14V6z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round" />
        <circle cx="10" cy="10" r="2.4" fill="currentColor" />
      </svg>
      DispatchZ
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
      title="DispatchZ is decision support: no real dispatch orders are issued."
    >
      ▲ SYNTHETIC DATA · SIMULATION ONLY
      <span class="sr-only">DispatchZ is decision support: no real dispatch orders are issued.</span>
    </span>

    <div class="ml-auto flex items-center gap-4 text-xs">
      {#if demoControls}
        <div class="flex items-center gap-2">
          {#if resetNote}
            <span class="font-medium {resetNote.ok ? 'text-ok' : 'text-danger'}" role={resetNote.ok ? 'status' : 'alert'}>{resetNote.text}</span>
          {/if}
          <button
            class="btn btn-sm border-warn/60 text-warn"
            title="Demo only: discard every change and restore the seed data"
            onclick={() => (resetDialog = true)}>DEMO · Reset data</button
          >
        </div>
      {/if}
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

<ConfirmDialog bind:open={resetDialog} title="Reset the demo data?" confirmText="Reset to seed data" danger busy={resetBusy} onConfirm={resetDemo}>
  <p>Every incident, recommendation, decision and unit status change made since the demo started is discarded, and the original seed data is restored.</p>
  <p class="mt-2">This affects everyone viewing this demo, not just this browser.</p>
</ConfirmDialog>
