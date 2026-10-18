<script lang="ts">
  import { onMount, tick } from 'svelte';
  import { api, errorText, newIdempotencyKey } from '$lib/api/client';
  import type {
    Candidate, Classification, EventEnvelope, Facility, Incident, Recommendation, RequirementSet, Unit
  } from '$lib/api/types';
  import { formatEta, formatTime, label, priorityRank, waitMinutes } from '$lib/format';
  import { live } from '$lib/live.svelte';
  import {
    AWAITING_DISPATCH, CLOSED_STATUSES, PRIORITY, needsClassification, unitGroup, type Priority
  } from '$lib/ops';
  import IncidentMap from '$lib/components/IncidentMap.svelte';
  import PriorityBadge from '$lib/components/PriorityBadge.svelte';
  import ConfirmDialog from '$lib/components/ConfirmDialog.svelte';
  import LocationCorrectionDialog from '$lib/components/LocationCorrectionDialog.svelte';
  import { describeConfidence } from '$lib/location/state';

  // ---------- data ----------
  let queue = $state<Incident[]>([]);
  let pendingNew = $state<Incident[]>([]); // new arrivals wait here so the queue never reorders under the operator
  let units = $state<Unit[]>([]);
  let facilities = $state<Facility[]>([]);
  let loading = $state(true);
  let loadError = $state('');
  let loadedAt = $state<number | undefined>(undefined);
  let now = $state(Date.now());

  let selectedId = $state<string | undefined>(undefined);
  let focusToken = $state(0);
  const selected = $derived(queue.find((i) => i.incidentId === selectedId));

  let requirements = $state<RequirementSet | null>(null);
  let recommendation = $state<Recommendation | null>(null);
  let suggestion = $state<Classification | null>(null);
  let detailError = $state('');
  let detailLoading = $state(false);
  let busy = $state('');
  let staleReasons = $state<string[]>([]);

  // review form
  let decision = $state<'accept' | 'modify' | 'reject'>('accept');
  let chosen = $state<Record<string, boolean>>({});
  let explanation = $state('');
  let overrideReason = $state('');
  let reviewResult = $state<{ ok: boolean; text: string } | null>(null);
  let reviewed = $state(false);
  let reviewKey = newIdempotencyKey();
  let reviewDialog = $state(false);
  let oosTarget = $state<{ unitId: string; callSign: string } | null>(null);
  let oosDialog = $state(false);
  let locationDialog = $state(false);

  // queue view state
  type Sort = 'priority' | 'oldest' | 'newest';
  let sort = $state<Sort>('priority');
  let prioFilter = $state<Record<string, boolean>>({});
  let needsActionOnly = $state(false);
  let showClosed = $state(false);
  let search = $state('');
  let searchEl: HTMLInputElement;

  // layout state
  let leftOpen = $state(true);
  let rightOpen = $state(true);
  let view = $state<'queue' | 'map' | 'details'>('map'); // only used below the lg breakpoint

  // transient confirmation of actions that have no panel of their own
  let toast = $state<{ ok: boolean; text: string } | null>(null);
  let toastTimer: ReturnType<typeof setTimeout> | undefined;
  function notify(ok: boolean, text: string) {
    toast = { ok, text };
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => (toast = null), ok ? 6000 : 12000);
  }

  const candidates = $derived(recommendation?.candidates ?? []);
  const eligible = $derived(candidates.filter((c) => c.eligible).sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99)));
  const ineligible = $derived(candidates.filter((c) => !c.eligible));
  const chosenIds = $derived(Object.keys(chosen).filter((k) => chosen[k]));
  const chosenCandidates = $derived(chosenIds.map((id) => candidates.find((c) => c.unitId === id)).filter((c): c is Candidate => !!c));
  // What 'accept' assigns: the recommended package, which covers the mandatory requirements and so can differ from
  // the fastest unit (e.g. a BLS ambulance ranked #1 by ETA cannot cover an ALS call).
  const pkg = $derived(recommendation?.packages?.[0]);
  const recommendedIds = $derived(pkg?.unitIds ?? []);
  const matchesPackage = $derived(chosenIds.length === recommendedIds.length && recommendedIds.every((id) => chosen[id]));
  // Same rule as the backend: ineligible or non-recommended units are an override and need a reason.
  const needsOverride = $derived(chosenCandidates.some((c) => !c.eligible) || (chosenIds.length > 0 && !matchesPackage));
  const acceptBlocker = $derived(
    decision !== 'accept' ? '' : !recommendedIds.length ? 'No package was recommended; use Modify to choose units, or Reject.'
      : !matchesPackage ? 'The ticked units differ from the recommended package; use Modify, or select Accept again to restore it.' : ''
  );
  // Staleness only matters before a decision: once reviewed, the recommendation is history, and the
  // decision's own unit status changes (e.g. dispatched_simulated) must not flag it as outdated.
  const isStale = $derived(
    !reviewed &&
      (staleReasons.length > 0 ||
        !!recommendation?.provenance?.stale ||
        (recommendation?.expiresAt ? Date.parse(recommendation.expiresAt) < now : false))
  );
  const canSubmit = $derived(
    !!recommendation && !reviewed && busy !== 'review' && !acceptBlocker &&
      (decision === 'reject' || (chosenIds.length > 0 && (!needsOverride || overrideReason.trim().length > 0)))
  );

  // ---------- derived queue ----------
  const open = $derived(queue.filter((i) => !CLOSED_STATUSES.has(i.status)));
  const visible = $derived.by(() => {
    const q = search.trim().toLowerCase();
    const anyPrio = Object.values(prioFilter).some(Boolean);
    return queue.filter(
      (i) =>
        (showClosed || !CLOSED_STATUSES.has(i.status)) &&
        (!anyPrio || prioFilter[i.priority]) &&
        (!needsActionOnly || AWAITING_DISPATCH.has(i.status) || needsClassification(i)) &&
        (!q || `${i.number} ${i.narrative} ${i.location.address ?? ''} ${i.confirmedCategory ?? ''}`.toLowerCase().includes(q))
    );
  });
  const filtersActive = $derived(!!search || needsActionOnly || showClosed || Object.values(prioFilter).some(Boolean));

  const stats = $derived({
    p1: open.filter((i) => i.priority === 'P1').length,
    awaiting: open.filter((i) => AWAITING_DISPATCH.has(i.status)).length,
    unclassified: open.filter(needsClassification).length,
    available: units.filter((u) => unitGroup(u.status) === 'available').length,
    assigned: units.filter((u) => unitGroup(u.status) === 'assigned').length,
    unavailable: units.filter((u) => unitGroup(u.status) === 'unavailable').length,
    noPosition: units.filter((u) => !u.location).length
  });

  const byPriority = (a: Incident, b: Incident) =>
    (priorityRank[a.priority] ?? 9) - (priorityRank[b.priority] ?? 9) || a.createdAt.localeCompare(b.createdAt);
  function sortQueue(list: Incident[]): Incident[] {
    const l = [...list];
    if (sort === 'oldest') return l.sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    if (sort === 'newest') return l.sort((a, b) => b.createdAt.localeCompare(a.createdAt));
    return l.sort(byPriority);
  }

  // ---------- loading ----------
  async function loadAll() {
    const [inc, un, fac] = await Promise.all([
      api.GET('/api/v1/incidents', { params: { query: { limit: 100 } } }),
      api.GET('/api/v1/resources/units', { params: { query: { limit: 200 } } }),
      api.GET('/api/v1/resources/facilities', { params: { query: { limit: 200 } } })
    ]).catch((e) => {
      loadError = errorText(e);
      return [null, null, null] as const;
    });
    loading = false;
    if (!inc || !un || !fac) return;
    if (inc.error || un.error || fac.error) {
      loadError = errorText(inc.error ?? un.error ?? fac.error);
      return;
    }
    loadError = '';
    units = un.data.data;
    facilities = fac.data.data;
    mergeIncidents(inc.data.data);
    loadedAt = Date.now();
  }

  function mergeIncidents(fresh: Incident[]) {
    if (queue.length === 0) {
      queue = sortQueue(fresh);
      return;
    }
    const byId = new Map(fresh.map((i) => [i.incidentId, i]));
    queue = queue.map((i) => byId.get(i.incidentId) ?? i); // update in place, keep position
    const known = new Set([...queue, ...pendingNew].map((i) => i.incidentId));
    pendingNew = [...pendingNew, ...fresh.filter((i) => !known.has(i.incidentId))];
  }

  function showNew() {
    queue = sortQueue([...pendingNew, ...queue]);
    pendingNew = [];
  }

  /** The operator asked for a different order: that is the one time the list may reorder. */
  function resort() {
    queue = sortQueue(queue);
  }

  /** Queue or map-marker click: select (if new) and always center the map on the incident. */
  function pick(id: string) {
    if (id !== selectedId) void select(id);
    focusToken++;
    rightOpen = true;
    if (typeof window !== 'undefined' && window.innerWidth < 1024) view = 'details';
    void tick().then(() => document.getElementById(`queue-${id}`)?.scrollIntoView({ block: 'nearest' }));
  }

  async function select(id: string) {
    selectedId = id;
    requirements = recommendation = suggestion = null;
    staleReasons = [];
    reviewResult = null;
    reviewed = false;
    detailError = '';
    chosen = {};
    decision = 'accept';
    explanation = overrideReason = '';
    reviewKey = newIdempotencyKey();
    detailLoading = true;
    const path = { incidentId: id };
    const [req, recs] = await Promise.all([
      api.GET('/api/v1/incidents/{incidentId}/requirements', { params: { path } }),
      api.GET('/api/v1/incidents/{incidentId}/recommendations', { params: { path, query: { limit: 1, sort: '-createdAt' } } })
    ]).catch((e) => {
      detailError = errorText(e);
      return [null, null] as const;
    });
    if (selectedId !== id) return; // operator moved on
    detailLoading = false;
    if (req?.data) requirements = req.data;
    if (recs?.data?.data[0]) setRecommendation(recs.data.data[0]);
  }

  function setRecommendation(r: Recommendation) {
    recommendation = r;
    staleReasons = [];
    reviewResult = null;
    reviewed = false;
    decision = 'accept';
    explanation = overrideReason = '';
    chosen = Object.fromEntries((r.packages?.[0]?.unitIds ?? []).map((id) => [id, true]));
    reviewKey = newIdempotencyKey();
  }

  function chooseDecision(v: 'accept' | 'modify' | 'reject') {
    // Accept always means the recommended package, so selecting it restores those ticks.
    if (v === 'accept') chosen = Object.fromEntries(recommendedIds.map((id) => [id, true]));
  }

  // ---------- actions ----------
  async function suggestClassification() {
    if (!selected || busy) return;
    busy = 'classify';
    detailError = '';
    const { data, error } = await api
      .POST('/api/v1/incidents/{incidentId}/classifications', {
        params: { path: { incidentId: selected.incidentId } },
        body: { mode: 'infer' }
      })
      .catch((e) => ({ data: undefined, error: e }));
    busy = '';
    if (data) suggestion = data;
    else detailError = `Classification unavailable (${errorText(error)}). Confirm the category manually.`;
  }

  async function confirmSuggestion() {
    if (!selected || !suggestion || busy) return;
    busy = 'confirm';
    const { error } = await api.POST('/api/v1/incidents/{incidentId}/classifications', {
      params: { path: { incidentId: selected.incidentId } },
      body: {
        mode: 'confirm',
        category: suggestion.category,
        priority: suggestion.suggestedPriority,
        inferredClassificationId: suggestion.classificationId
      }
    });
    busy = '';
    if (error) detailError = errorText(error);
    else {
      suggestion = null;
      notify(true, `Classification confirmed for ${selected.number}.`);
      await loadAll();
      await select(selected.incidentId);
    }
  }

  async function generateRecommendation() {
    if (!selected || busy) return;
    busy = 'recommend';
    detailError = '';
    const { data, error } = await api
      .POST('/api/v1/incidents/{incidentId}/recommendations', {
        params: { path: { incidentId: selected.incidentId }, header: { 'Idempotency-Key': newIdempotencyKey() } },
        body: { scope: 'single_incident', maxCandidates: 10 }
      })
      .catch((e) => ({ data: undefined, error: e }));
    busy = '';
    if (data) setRecommendation(data);
    else detailError = errorText(error);
  }

  async function submitReview() {
    if (!recommendation || busy) return;
    busy = 'review';
    reviewResult = null;
    const { data, error } = await api
      .POST('/api/v1/recommendations/{recommendationId}/reviews', {
        params: { path: { recommendationId: recommendation.recommendationId }, header: { 'Idempotency-Key': reviewKey } },
        body: {
          decision,
          selectedUnitIds: decision === 'reject' ? [] : chosenIds,
          explanation: explanation || undefined,
          overrideReason: needsOverride && overrideReason ? overrideReason : undefined,
          humanConfirmed: true,
          recommendationVersionSeenAt: recommendation.createdAt
        }
      })
      .catch((e) => ({ data: undefined, error: e }));
    busy = '';
    reviewDialog = false;
    void tick().then(() => document.getElementById('review-result')?.scrollIntoView({ block: 'nearest' }));
    if (data) {
      reviewed = true;
      reviewResult = { ok: true, text: `Decision recorded (${decision}) at ${formatTime(data.createdAt)}. Simulated only: no real dispatch was issued.` };
    } else {
      // The idempotency key is kept so a retry cannot record the decision twice.
      reviewResult = { ok: false, text: `Not recorded: ${errorText(error)}. Nothing was dispatched. You can retry.` };
    }
  }

  async function markOutOfService() {
    if (!oosTarget || busy) return;
    const { unitId, callSign } = oosTarget;
    busy = `oos-${unitId}`;
    const { error } = await api.POST('/api/v1/resources/units/{unitId}/status-events', {
      params: { path: { unitId }, header: { 'Idempotency-Key': newIdempotencyKey() } },
      body: { status: 'out_of_service', reason: 'Simulated from dispatch dashboard' }
    });
    busy = '';
    oosDialog = false;
    if (error) notify(false, `Could not mark ${callSign} out of service: ${errorText(error)}`);
    else notify(true, `${callSign} marked out of service (simulated). Re-run the recommendation if it included this unit.`);
  }

  // ---------- live updates ----------
  function onEvent(e: EventEnvelope) {
    if (e.type === 'incident.created' || e.type === 'incident.updated') {
      void loadAll();
    } else if (e.type === 'unit.status_changed') {
      void loadAll();
      if (!reviewed && recommendation?.candidates.some((c) => c.unitId === e.entity.id)) {
        const c = recommendation.candidates.find((x) => x.unitId === e.entity.id);
        staleReasons = [...staleReasons, `Unit ${c?.callSign ?? e.entity.id} changed status`];
      }
    } else if (e.type === 'recommendation.invalidated' && e.entity.id === recommendation?.recommendationId) {
      staleReasons = [...staleReasons, 'The server invalidated this recommendation'];
    }
  }

  onMount(() => {
    void loadAll();
    const off = live.on(onEvent);
    const t = setInterval(() => (now = Date.now()), 30000);
    return () => {
      off();
      clearInterval(t);
      clearTimeout(toastTimer);
    };
  });

  let lastResync = live.resyncCount;
  $effect(() => {
    if (live.resyncCount !== lastResync) {
      lastResync = live.resyncCount;
      void loadAll();
    }
  });

  // ---------- keyboard ----------
  function onKey(e: KeyboardEvent) {
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    const t = e.target as HTMLElement | null;
    if (t && (t.matches('input, textarea, select, [contenteditable]') || t.closest('dialog'))) {
      if (e.key === 'Escape' && t === searchEl) search = '';
      return;
    }
    if (e.key === '/') {
      e.preventDefault();
      leftOpen = true;
      view = 'queue';
      void tick().then(() => searchEl?.focus());
    } else if (e.key === 'j' || e.key === 'k') {
      const list = visible;
      if (!list.length) return;
      const idx = list.findIndex((i) => i.incidentId === selectedId);
      const next = e.key === 'k' ? Math.min(list.length - 1, idx + 1) : Math.max(0, idx < 0 ? 0 : idx - 1);
      pick(list[next].incidentId);
    } else if (e.key === 'Escape' && selectedId) {
      selectedId = undefined;
    }
  }

  /** Counts are unknown (not zero) until the first successful load. */
  const n = (v: number) => (loadedAt ? v : '–');
  const checkMark = (s: string) => (s === 'available' ? '✔' : s === 'unverified' ? '?' : '✖');
  const priorities: Priority[] = ['P1', 'P2', 'P3', 'P4'];
  const pct = (n: number) => `${(n * 100).toFixed(0)}%`;
  const cols = $derived(
    `${leftOpen ? 'clamp(17rem, 19vw, 22rem)' : '0px'} minmax(0, 1fr) ${rightOpen ? 'clamp(23rem, 27vw, 30rem)' : '0px'}`
  );
</script>

<svelte:window onkeydown={onKey} />

{#snippet candidateRow(c: Candidate)}
  <li class="rounded-md border bg-app/40 {c.eligible ? 'border-line' : 'border-dashed border-line-strong'}">
    <div class="flex items-center gap-2 p-2">
      <input
        id="cand-{c.unitId}"
        type="checkbox"
        class="size-4 accent-[#4da8d8]"
        bind:checked={chosen[c.unitId]}
        disabled={reviewed}
        aria-label={c.eligible ? `Select ${c.callSign}` : `Override: select ineligible unit ${c.callSign}`}
      />
      <label for="cand-{c.unitId}" class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
        {#if c.eligible}<span class="font-mono text-xs text-muted">#{c.rank}</span>{/if}
        <span class="text-sm font-semibold">{c.callSign}</span>
        {#if recommendedIds.includes(c.unitId)}<span class="badge border-ai text-ai">★ Recommended</span>{/if}
        {#if c.eligible}
          <span class="badge border-ok/60 text-ok">✔ Eligible</span>
        {:else}
          <span class="badge border-danger/60 text-danger">✖ Ineligible</span>
        {/if}
      </label>
      <span class="ml-auto text-right">
        <span class="block text-sm font-semibold">{formatEta(c.etaSeconds)}</span>
        <span class="block text-[0.65rem] text-muted">{label(c.etaMethod ?? 'unknown')}</span>
      </span>
    </div>
    {#if c.ineligibleReasons?.length}
      <ul class="mx-2 mb-2 list-disc pl-5 text-xs text-danger">{#each c.ineligibleReasons as r}<li>{r}</li>{/each}</ul>
    {/if}
    <details class="border-t border-line text-xs">
      <summary class="cursor-pointer px-2 py-1.5 text-muted hover:text-fg">Why and capability checks ({c.checks.length})</summary>
      <div class="px-2 pb-2">
        <ul class="flex flex-wrap gap-1">
          {#each c.checks as ch}
            <li class="badge {ch.state === 'available' ? 'text-ok' : 'text-danger'}" title={ch.evidence ?? ''}>
              {checkMark(ch.state)} {ch.capabilityId}: {ch.state}{ch.mandatory ? '' : ' (optional)'}
            </li>
          {/each}
        </ul>
        {#if c.rankingFactors?.length}
          <p class="mt-1.5 text-muted">Ranking factors:</p>
          <ul class="mt-0.5 list-disc space-y-0.5 pl-4 text-muted">
            {#each c.rankingFactors as f}
              <li>
                <span class="text-fg">{f.factor.replaceAll('_', ' ')}{f.value ? ': ' + f.value : ''}</span>
                {#if f.note}<span class="block">{f.note}</span>{/if}
              </li>
            {/each}
          </ul>
        {/if}
        {#if c.coverageImpact}<p class="mt-1 text-muted">Coverage impact: {c.coverageImpact}</p>{/if}
        <button
          class="btn btn-sm mt-2"
          disabled={busy !== ''}
          onclick={() => { oosTarget = { unitId: c.unitId, callSign: c.callSign }; oosDialog = true; }}
        >
          Simulate: mark out of service…
        </button>
      </div>
    </details>
  </li>
{/snippet}

<div class="flex h-full flex-col">
<div class="flex shrink-0 gap-1 border-b border-line bg-panel p-1.5 lg:hidden" role="tablist" aria-label="Dispatch view">
  {#each [['queue', 'Queue'], ['map', 'Map'], ['details', 'Details']] as [k, t]}
    <button role="tab" aria-selected={view === k} class="btn flex-1 {view === k ? 'border-accent bg-accent-soft' : ''}" onclick={() => (view = k as typeof view)}>{t}</button>
  {/each}
</div>

<div class="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[var(--cols)]" style="--cols: {cols}">
  <!-- Left: queue -->
  <section
    class="min-h-0 flex-col overflow-hidden border-r border-line bg-panel {view === 'queue' ? 'flex' : 'max-lg:hidden lg:flex'}"
    aria-labelledby="qh"
    inert={!leftOpen}
  >
    <div class="shrink-0 space-y-2 border-b border-line p-3">
      <div class="flex items-baseline justify-between gap-2">
        <h1 id="qh" class="text-sm font-semibold">Incident queue</h1>
        <p class="text-xs text-muted" aria-live="polite">
          {visible.length}{visible.length !== open.length || showClosed ? ` of ${queue.length}` : ''} shown
        </p>
      </div>
      {#if pendingNew.length}
        <button class="btn btn-primary w-full" onclick={showNew}>
          Show {pendingNew.length} new incident{pendingNew.length > 1 ? 's' : ''}
        </button>
      {/if}
      <div class="relative">
        <label for="q-search" class="sr-only">Search incidents</label>
        <input id="q-search" bind:this={searchEl} bind:value={search} class="field pr-8" type="search" placeholder="Search number, address, text" />
        <span class="kbd pointer-events-none absolute top-1/2 right-2 -translate-y-1/2" aria-hidden="true">/</span>
      </div>
      <div class="flex flex-wrap items-center gap-1" role="group" aria-label="Filter by priority">
        {#each priorities as p}
          <button class="chip" aria-pressed={!!prioFilter[p]} onclick={() => (prioFilter[p] = !prioFilter[p])}>{p}</button>
        {/each}
        <button class="chip" aria-pressed={needsActionOnly} onclick={() => (needsActionOnly = !needsActionOnly)} title="Awaiting dispatch or classification not confirmed">Needs action</button>
      </div>
      <div class="flex items-center justify-between gap-2">
        <label class="flex items-center gap-1.5 text-xs text-muted">
          <input type="checkbox" class="accent-[#4da8d8]" bind:checked={showClosed} /> Show closed
        </label>
        <label class="flex items-center gap-1.5 text-xs text-muted">
          Sort
          <select class="rounded-md border border-line-strong bg-app px-1.5 py-1 text-xs text-fg" bind:value={sort} onchange={resort}>
            <option value="priority">Priority</option>
            <option value="oldest">Longest waiting</option>
            <option value="newest">Newest</option>
          </select>
        </label>
      </div>
    </div>

    {#if loadError && queue.length}
      <div class="shrink-0 border-b border-danger/60 bg-danger-soft p-2 text-xs" role="alert">
        <strong class="text-danger">Refresh failed.</strong> Showing data from {formatTime(new Date(loadedAt ?? Date.now()).toISOString())}. {loadError}
        <button class="btn btn-sm ml-1" onclick={loadAll}>Retry</button>
      </div>
    {/if}

    <div class="min-h-0 flex-1 overflow-y-auto">
      {#if loading}
        <ul aria-hidden="true">
          {#each [0, 1, 2, 3, 4] as n (n)}
            <li class="space-y-2 border-b border-line p-3"><div class="h-3 w-1/2 rounded bg-raised"></div><div class="h-3 w-3/4 rounded bg-raised"></div></li>
          {/each}
        </ul>
        <p class="sr-only" role="status">Loading incidents…</p>
      {:else if loadError && !queue.length}
        <div class="p-3 text-sm" role="alert">
          <p class="font-medium text-danger">✖ Could not load data</p>
          <p class="mt-1 text-xs text-muted">{loadError}</p>
          <button class="btn mt-2" onclick={loadAll}>Retry</button>
        </div>
      {:else if queue.length === 0}
        <p class="p-4 text-sm text-muted">No incidents. New incidents appear here as they are created.</p>
      {:else if visible.length === 0}
        <div class="p-4 text-sm text-muted">
          <p>No incidents match the current filters.</p>
          {#if filtersActive}
            <button class="btn btn-sm mt-2" onclick={() => { search = ''; needsActionOnly = false; showClosed = false; prioFilter = {}; }}>Clear filters</button>
          {/if}
        </div>
      {:else}
        <ul>
          {#each visible as i (i.incidentId)}
            {@const isSel = i.incidentId === selectedId}
            {@const closed = CLOSED_STATUSES.has(i.status)}
            <li id="queue-{i.incidentId}">
              <button
                class="relative block w-full border-b border-line py-2.5 pr-3 pl-4 text-left hover:bg-raised {isSel ? 'bg-accent-soft hover:bg-accent-soft' : ''} {closed ? 'opacity-60' : ''}"
                aria-current={isSel ? 'true' : undefined}
                onclick={() => pick(i.incidentId)}
              >
                <span class="absolute inset-y-0 left-0 w-1" style="background: {PRIORITY[i.priority].color}" aria-hidden="true"></span>
                {#if isSel}<span class="absolute inset-y-0 right-0 w-0.5 bg-accent" aria-hidden="true"></span>{/if}
                <span class="flex items-center gap-2">
                  <PriorityBadge priority={i.priority} confirmed={i.priorityConfirmed ?? false} />
                  <span class="font-mono text-xs text-muted">{i.number.replace('INC-2026-', '')}</span>
                  <span class="ml-auto text-xs font-semibold {waitMinutes(i.createdAt, now) >= 15 && !closed ? 'text-warn' : 'text-muted'}" title="Time since created">{waitMinutes(i.createdAt, now)} min</span>
                </span>
                <span class="mt-1 flex flex-wrap items-center gap-x-1.5 text-xs text-muted">
                  <span class="font-medium text-fg">{i.confirmedCategory ? label(i.confirmedCategory) : 'Unclassified'}</span>
                  <span aria-hidden="true">·</span>
                  <span>{label(i.status)}</span>
                </span>
                <span class="mt-0.5 block truncate text-sm">{i.narrative}</span>
                {#if i.location.address}<span class="block truncate text-xs text-muted">{i.location.address}</span>{/if}
                {#if needsClassification(i) && !closed}
                  <span class="badge mt-1 border-warn/60 text-warn">▲ Needs classification</span>
                {/if}
              </button>
            </li>
          {/each}
        </ul>
      {/if}
    </div>
    <p class="shrink-0 border-t border-line px-3 py-1.5 text-[0.7rem] text-faint">
      <span class="kbd">K</span> <span class="kbd">J</span> next / previous · <span class="kbd">/</span> search ·
      {loadedAt ? `refreshed ${formatTime(new Date(loadedAt).toISOString())}` : 'not loaded yet'}
    </p>
  </section>

  <!-- Center: operations strip + map -->
  <section class="min-h-0 min-w-0 flex-col {view === 'map' ? 'flex' : 'max-lg:hidden lg:flex'}" aria-label="Map and operational overview">
    <div class="flex shrink-0 items-center gap-3 border-b border-line bg-panel px-2 py-1.5 text-xs whitespace-nowrap">
      <button class="btn btn-ghost btn-sm max-lg:hidden" aria-expanded={leftOpen} aria-controls="qh" onclick={() => (leftOpen = !leftOpen)}>
        {leftOpen ? '◀ Queue' : '▶ Queue'}
      </button>
      <div class="flex min-w-0 flex-1 items-center gap-3 overflow-x-auto">
      <dl class="flex items-center gap-4">
        <div class="flex items-baseline gap-1.5" title="Open incidents with priority P1">
          <dt class="text-muted">P1 open</dt><dd class="text-base font-bold {stats.p1 ? 'text-p1' : 'text-fg'}">{n(stats.p1)}</dd>
        </div>
        <div class="flex items-baseline gap-1.5" title="Awaiting dispatch: intake, pending dispatch or recommendation ready">
          <dt class="text-muted">Awaiting</dt><dd class="text-base font-bold">{n(stats.awaiting)}</dd>
        </div>
        <div class="flex items-baseline gap-1.5" title="Open incidents whose category or priority a dispatcher has not confirmed">
          <dt class="text-muted">Unclassified</dt><dd class="text-base font-bold {stats.unclassified ? 'text-warn' : ''}">{n(stats.unclassified)}</dd>
        </div>
        <span class="h-4 w-px bg-line-strong" aria-hidden="true"></span>
        <div class="flex items-baseline gap-1.5"><dt class="text-muted">● Available</dt><dd class="text-base font-bold text-ok">{n(stats.available)}<span class="text-xs font-normal text-muted">/{units.length}</span></dd></div>
        <div class="flex items-baseline gap-1.5"><dt class="text-muted">▶ Assigned</dt><dd class="text-base font-bold text-accent">{n(stats.assigned)}</dd></div>
        <div class="flex items-baseline gap-1.5" title="Unavailable: out of service or off duty"><dt class="text-muted">✕ Out</dt><dd class="text-base font-bold">{n(stats.unavailable)}</dd></div>
      </dl>
      {#if stats.noPosition}<span class="text-faint" title="These units have no position and are not drawn on the map">({stats.noPosition} unit{stats.noPosition > 1 ? 's' : ''} without position)</span>{/if}
      </div>
      <button class="btn btn-ghost btn-sm max-lg:hidden" aria-expanded={rightOpen} aria-controls="details-panel" onclick={() => (rightOpen = !rightOpen)}>
        {rightOpen ? 'Details ▶' : '◀ Details'}
      </button>
    </div>
    <div class="relative min-h-0 flex-1">
      <IncidentMap
        incidents={visible}
        {units}
        {facilities}
        selectedId={selectedId}
        highlightUnitIds={eligible.map((c) => c.unitId)}
        routeUnitIds={reviewed ? [] : chosenIds}
        {focusToken}
        onSelect={pick}
      />
      {#if loading}
        <p class="absolute top-3 left-3 z-[1000] rounded-md border border-line-strong bg-panel px-3 py-1.5 text-xs text-muted" role="status">Loading incidents and units…</p>
      {:else if !loadError && queue.length === 0}
        <p class="absolute top-3 left-3 z-[1000] rounded-md border border-line-strong bg-panel px-3 py-1.5 text-xs text-muted">No incidents to show.</p>
      {/if}
      <div class="pointer-events-none absolute right-16 bottom-6 left-1/2 z-[1100] -translate-x-1/2 max-w-md" aria-live="polite">
        {#if toast}
          <p class="pointer-events-auto rounded-md border px-3 py-2 text-sm shadow-xl {toast.ok ? 'border-ok bg-ok-soft text-fg' : 'border-danger bg-danger-soft text-fg'}" role={toast.ok ? 'status' : 'alert'}>
            <span class={toast.ok ? 'text-ok' : 'text-danger'} aria-hidden="true">{toast.ok ? '✔' : '✖'}</span>
            {toast.text}
            <button class="ml-2 text-xs underline" onclick={() => (toast = null)}>Dismiss</button>
          </p>
        {/if}
      </div>
    </div>
  </section>

  <!-- Right: details -->
  <section
    id="details-panel"
    class="min-h-0 flex-col overflow-hidden border-l border-line bg-panel {view === 'details' ? 'flex' : 'max-lg:hidden lg:flex'}"
    aria-label="Incident details"
    inert={!rightOpen}
  >
    {#if !selected}
      <div class="p-5 text-sm text-muted">
        <p class="font-medium text-fg">No incident selected</p>
        <p class="mt-1">Select an incident from the queue or the map to review its requirements and unit recommendations.</p>
      </div>
    {:else}
      <div class="shrink-0 border-b border-line p-3">
        <div class="flex items-start gap-2">
          <div class="min-w-0 flex-1">
            <h2 class="flex flex-wrap items-center gap-x-3 gap-y-1 text-base font-semibold">
              <span class="font-mono">{selected.number}</span>
              <PriorityBadge priority={selected.priority} confirmed={selected.priorityConfirmed ?? false} size={20} />
            </h2>
            <p class="mt-0.5 text-xs text-muted">
              {label(selected.status)} · {waitMinutes(selected.createdAt, now)} min since created
            </p>
          </div>
          <button class="btn btn-ghost btn-sm" aria-label="Close details" onclick={() => (selectedId = undefined)}>✕</button>
        </div>
        <p class="mt-2 text-sm">{selected.narrative}</p>
        <p class="mt-1.5 text-xs text-muted">{label(selected.provenance.dataOrigin)} data</p>
      </div>

      <div class="min-h-0 flex-1 space-y-3 overflow-y-auto p-3">
        {#if detailError}
          <p class="rounded-md border border-danger/60 bg-danger-soft p-2 text-sm" role="alert"><strong class="text-danger">✖</strong> {detailError}</p>
        {/if}

        <section class="panel p-3" aria-labelledby="h-loc">
          <div class="flex items-center gap-2">
            <h3 id="h-loc" class="section-title">Location</h3>
            <button class="btn btn-sm ml-auto" onclick={() => (locationDialog = true)}>Correct location…</button>
          </div>
          <p class="mt-1.5 text-sm">{selected.location.address ?? 'No address on record'}</p>
          {#if selected.location.geocodeConfidence != null}
            {@const conf = describeConfidence(selected.location.geocodeConfidence)}
            <p class="mt-1 flex flex-wrap items-center gap-1.5 text-xs">
              <span class="badge {conf.key === 'matched' ? 'border-ok/60 text-ok' : 'border-warn/60 text-warn'}">{conf.key === 'matched' ? '✔' : '◐'} {conf.label}</span>
              {#if conf.key !== 'matched'}<span class="text-muted">Not an exact destination. Verify with the caller.</span>{/if}
            </p>
          {:else}
            <p class="mt-1 text-xs"><span class="badge border-warn/60 text-warn">◐ Source not recorded</span> <span class="text-muted">Placed by hand or entered elsewhere. Verify with the caller.</span></p>
          {/if}
        </section>

        <section class="panel p-3" aria-labelledby="h-class">
          <h3 id="h-class" class="section-title">Classification</h3>
          {#if selected.confirmedCategory}
            <p class="mt-1.5 text-sm">✔ Confirmed by dispatcher: <strong>{label(selected.confirmedCategory)}</strong></p>
          {:else}
            <p class="mt-1.5 text-sm text-warn">▲ Not yet confirmed. Requirements below may be provisional.</p>
          {/if}
          {#if suggestion}
            <div class="mt-2 rounded-md border border-dashed border-ai bg-ai-soft p-2.5 text-sm" role="status">
              <span class="badge border-ai text-ai">AI SUGGESTION · unverified</span>
              <p class="mt-1.5">{label(suggestion.category)}{#if suggestion.suggestedPriority} · {suggestion.suggestedPriority}{/if}{#if suggestion.confidence != null} · {pct(suggestion.confidence)} model confidence{/if}</p>
              {#if suggestion.limitations?.length}
                <ul class="mt-1 list-disc pl-5 text-xs text-muted">{#each suggestion.limitations as l}<li>{l}</li>{/each}</ul>
              {/if}
              <div class="mt-2 flex items-center gap-2">
                <button class="btn btn-primary btn-sm" onclick={confirmSuggestion} disabled={busy !== ''}>{busy === 'confirm' ? 'Confirming…' : 'Confirm as dispatcher'}</button>
                <button class="btn btn-ghost btn-sm" onclick={() => (suggestion = null)}>Dismiss</button>
              </div>
            </div>
          {:else if !selected.confirmedCategory}
            <button class="btn btn-sm mt-2" onclick={suggestClassification} disabled={busy !== ''}>
              {busy === 'classify' ? 'Working…' : 'Suggest classification (AI)'}
            </button>
          {/if}
        </section>

        <section class="panel p-3" aria-labelledby="h-req">
          <h3 id="h-req" class="section-title">Required capabilities</h3>
          {#if requirements}
            <ul class="mt-1.5 space-y-1 text-sm">
              {#each requirements.requirements as r}
                <li class="flex items-baseline gap-2">
                  <span class="badge {r.mandatory ? 'border-line-strong text-fg' : ''}">{r.mandatory ? 'MANDATORY' : 'optional'}</span>
                  <span>{r.label ?? r.capabilityId}{r.quantity && r.quantity > 1 ? ` ×${r.quantity}` : ''}</span>
                  {#if r.origin === 'ml_suggested'}<span class="badge ml-auto border-ai/60 text-ai">AI · unverified</span>
                  {:else}<span class="ml-auto text-xs text-muted">{label(r.origin)}</span>{/if}
                </li>
              {/each}
            </ul>
          {:else if detailLoading}
            <p class="mt-1.5 text-sm text-muted" role="status">Loading…</p>
          {:else}
            <p class="mt-1.5 text-sm text-muted">No requirements available for this incident.</p>
          {/if}
        </section>

        <section class="panel border-l-2 border-l-ai p-3" aria-labelledby="h-rec">
          <div class="flex flex-wrap items-center gap-x-2 gap-y-1">
            <h3 id="h-rec" class="section-title">Unit recommendation</h3>
            <span class="badge border-ai/60 text-ai">AI-ASSISTED · not verified</span>
            <button class="btn btn-primary btn-sm ml-auto" onclick={generateRecommendation} disabled={busy !== ''}>
              {busy === 'recommend' ? 'Working…' : recommendation ? 'Re-run' : 'Generate'}
            </button>
          </div>
          {#if recommendation}
            <p class="mt-1.5 text-xs text-muted">
              Generated {formatTime(recommendation.createdAt)} · policy {recommendation.policyVersionId.slice(0, 8)}
              {#if recommendation.confidence != null}· model confidence {pct(recommendation.confidence)}{/if}
            </p>
            {#if isStale}
              <div class="mt-2 rounded-md border-2 border-warn bg-warn-soft p-2 text-sm" role="alert">
                <strong class="text-warn">▲ OUTDATED</strong>: re-run before relying on this.
                {#if staleReasons.length}<ul class="mt-1 list-disc pl-5 text-xs">{#each staleReasons as r}<li>{r}</li>{/each}</ul>{/if}
              </div>
            {/if}
            {#if recommendation.limitations?.length}
              <ul class="mt-1.5 list-disc pl-5 text-xs text-muted">{#each recommendation.limitations as l}<li>{l}</li>{/each}</ul>
            {/if}
            {#if pkg}
              <div class="mt-3 rounded-md border p-2 text-xs {pkg.satisfiesAllMandatory ? 'border-ai/60 bg-ai-soft' : 'border-warn bg-warn-soft'}">
                <p class="font-semibold text-fg">
                  {pkg.satisfiesAllMandatory ? '★' : '▲'} Recommended package:
                  {candidates.filter((c) => recommendedIds.includes(c.unitId)).map((c) => c.callSign).join(' + ')}
                  {#if pkg.worstEtaSeconds != null}<span class="font-normal text-muted">· all on scene in {formatEta(pkg.worstEtaSeconds)}</span>{/if}
                </p>
                {#if pkg.explanation}<p class="mt-0.5 text-muted">{pkg.explanation}</p>{/if}
                <p class="mt-0.5 text-muted">Ranks order eligible units by ETA alone; the package is what covers the mandatory requirements.</p>
              </div>
            {/if}
            <h4 class="mt-3 mb-1.5 text-xs font-semibold text-muted">Eligible units ({eligible.length})</h4>
            {#if eligible.length}
              <ul class="space-y-1.5">{#each eligible as c (c.unitId)}{@render candidateRow(c)}{/each}</ul>
            {:else}
              <p class="rounded-md border border-warn/60 bg-warn-soft p-2 text-sm">▲ No eligible units. Review the ineligible reasons below.</p>
            {/if}
            {#if ineligible.length}
              <details class="mt-3">
                <summary class="cursor-pointer text-xs font-semibold text-muted hover:text-fg">Ineligible units ({ineligible.length})</summary>
                <ul class="mt-1.5 space-y-1.5">{#each ineligible as c (c.unitId)}{@render candidateRow(c)}{/each}</ul>
              </details>
            {/if}
          {:else if detailLoading}
            <p class="mt-1.5 text-sm text-muted" role="status">Loading…</p>
          {:else}
            <p class="mt-1.5 text-sm text-muted">No recommendation yet. Generate one to see eligible units ranked by ETA.</p>
          {/if}
        </section>

        {#if recommendation}
          <!-- Human decision: visually and structurally separate from the AI-assisted recommendation above. -->
          <section class="panel border-l-2 border-l-accent p-3" aria-labelledby="h-dec">
            <h3 id="h-dec" class="section-title">Dispatcher decision <span class="ml-1 font-normal normal-case text-accent">human</span></h3>
            <fieldset class="mt-2" disabled={reviewed}>
              <legend class="sr-only">Decision</legend>
              <div class="grid grid-cols-3 gap-1" role="radiogroup">
                {#each [['accept', 'Accept'], ['modify', 'Modify'], ['reject', 'Reject']] as [v, t]}
                  <label class="btn cursor-pointer justify-center {decision === v ? 'border-accent bg-accent-soft' : ''} has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent">
                    <input type="radio" class="sr-only" name="decision" value={v} bind:group={decision} onchange={() => chooseDecision(v as 'accept' | 'modify' | 'reject')} />{t}
                  </label>
                {/each}
              </div>
              <p class="mt-1.5 text-xs text-muted">
                {decision === 'accept' ? 'Assign the recommended package.' : decision === 'modify' ? 'Use the ticked units above after changing the selection.' : 'Record that no recommended units are used.'}
              </p>
              {#if acceptBlocker && !reviewed}<p class="mt-1 text-xs text-warn">▲ {acceptBlocker}</p>{/if}
              <div class="mt-3">
                <label class="label" for="exp">Explanation <span class="font-normal text-faint">(optional)</span></label>
                <textarea id="exp" class="field" rows="2" maxlength="2000" bind:value={explanation}></textarea>
              </div>
              {#if needsOverride && decision !== 'reject'}
                <div class="mt-2">
                  <label class="label" for="ovr">Override reason <span class="text-warn">(required)</span>: selection differs from the recommended package</label>
                  <textarea id="ovr" class="field" rows="2" maxlength="2000" required bind:value={overrideReason}></textarea>
                </div>
              {/if}
            </fieldset>
            {#if reviewResult}
              <p id="review-result" class="mt-3 rounded-md border p-2 text-sm {reviewResult.ok ? 'border-ok bg-ok-soft' : 'border-danger bg-danger-soft'}" role={reviewResult.ok ? 'status' : 'alert'}>
                <span class={reviewResult.ok ? 'text-ok' : 'text-danger'} aria-hidden="true">{reviewResult.ok ? '✔' : '✖'}</span>
                {reviewResult.text}
              </p>
            {/if}
          </section>
        {/if}
      </div>

      {#if recommendation}
        <div class="shrink-0 border-t border-line bg-app/60 p-3">
          <div class="flex items-center gap-3">
            <p class="min-w-0 flex-1 truncate text-xs text-muted">
              {#if reviewed}Decision recorded.{:else if decision === 'reject'}Reject: no units.{:else if chosenCandidates.length}Units: <strong class="text-fg">{chosenCandidates.map((c) => c.callSign).join(', ')}</strong>{:else}No units ticked.{/if}
              {#if isStale && !reviewed}<span class="text-warn"> · outdated</span>{/if}
            </p>
            <button class="btn btn-primary" disabled={!canSubmit} onclick={() => (reviewDialog = true)}>
              Record decision…
            </button>
          </div>
          {#if needsOverride && decision !== 'reject' && !overrideReason.trim() && !reviewed}
            <p class="mt-1.5 text-xs text-warn">Enter an override reason to continue.</p>
          {/if}
        </div>
      {/if}
    {/if}
  </section>
</div>
</div>

<ConfirmDialog bind:open={reviewDialog} title="Record this decision?" confirmText="Record decision" busy={busy === 'review'} onConfirm={submitReview}>
  <dl class="space-y-1.5">
    <div class="flex gap-2"><dt class="w-20 shrink-0 text-faint">Incident</dt><dd class="text-fg">{selected?.number}</dd></div>
    <div class="flex gap-2"><dt class="w-20 shrink-0 text-faint">Decision</dt><dd class="text-fg capitalize">{decision}</dd></div>
    <div class="flex gap-2"><dt class="w-20 shrink-0 text-faint">Units</dt>
      <dd class="text-fg">{decision === 'reject' ? 'None' : chosenCandidates.map((c) => `${c.callSign}${c.eligible ? '' : ' (ineligible)'}`).join(', ')}</dd></div>
    {#if needsOverride && decision !== 'reject'}<div class="flex gap-2"><dt class="w-20 shrink-0 text-faint">Override</dt><dd class="text-fg">{overrideReason}</dd></div>{/if}
  </dl>
  {#if isStale}<p class="mt-2 rounded border border-warn bg-warn-soft p-2 text-warn">▲ This recommendation is outdated; the server may reject it.</p>{/if}
  <p class="mt-3 text-xs">Simulated: this records your decision in the audit log. No real dispatch order is issued.</p>
</ConfirmDialog>

<ConfirmDialog bind:open={oosDialog} title="Mark {oosTarget?.callSign} out of service?" confirmText="Mark out of service" danger busy={busy.startsWith('oos-')} onConfirm={markOutOfService}>
  <p>This is a simulated status change for unit <strong class="text-fg">{oosTarget?.callSign}</strong>. It will make the unit unavailable and can invalidate recommendations that include it.</p>
</ConfirmDialog>

{#if selected}
  <LocationCorrectionDialog
    bind:open={locationDialog}
    incident={selected}
    onSaved={(updated) => {
      queue = queue.map((i) => (i.incidentId === updated.incidentId ? updated : i));
      if (recommendation) staleReasons = [...staleReasons, 'Incident location was corrected'];
      notify(true, `Location corrected for ${updated.number}.`);
      void loadAll();
    }}
  />
{/if}
