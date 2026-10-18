<script lang="ts">
  import { onDestroy } from 'svelte';
  import LocationMap from './LocationMap.svelte';
  import { searchPlaces } from '$lib/location/geocode';
  import {
    RELIABILITY_TEXT, acceptIncoming, applyError, applyResults, choose, clearPoint, currentPoint, differenceNote, dismissIncoming,
    placeManual, reliability, saveBlocker, setMethod, setQuery, startResolving,
    type CandidateKind, type LocationState, type Method
  } from '$lib/location/state';

  /** `value` is the whole location-entry state; the page reads the result through `toApiLocation(value)`. */
  let { value = $bindable(), idPrefix = 'loc', reference = null }: { value: LocationState; idPrefix?: string; reference?: { lat: number; lon: number } | null } = $props();

  const METHODS: { id: Method; label: string }[] = [
    { id: 'address', label: 'Street address' },
    { id: 'landmark', label: 'Landmark / area' },
    { id: 'map', label: 'Pick on map' },
    { id: 'unknown', label: 'Not yet known' }
  ];
  const KIND_TEXT: Record<CandidateKind, string> = { address: 'Address', place: 'Place', street: 'Street only', area: 'Area only' };
  const MIN_CHARS = 4;
  const DEBOUNCE_MS = 600;

  let map: LocationMap | undefined;
  let ctl: AbortController | undefined;
  let timer: ReturnType<typeof setTimeout> | undefined;
  let seq = 0; // only the newest lookup may write its result

  const isLive = (m: Method) => m === 'address' || m === 'landmark'; // matches appear as the dispatcher types
  const point = $derived(currentPoint(value));
  const rel = $derived(reliability(value));
  const blocker = $derived(saveBlocker(value));
  const showList = $derived(value.candidates.length > 1);
  const adjusted = $derived(value.manual && value.chosen);
  const difference = $derived(differenceNote(value));

  async function run() {
    clearTimeout(timer);
    ctl?.abort();
    const mine = ++seq;
    const c = (ctl = new AbortController());
    const { query, region } = value;
    if (query.trim().length < 2) return;
    value = startResolving(value);
    try {
      const found = await searchPlaces(query, region, c.signal);
      if (mine !== seq) return; // superseded by newer typing
      value = applyResults(value, found);
    } catch (e) {
      if (mine !== seq || c.signal.aborted) return;
      value = applyError(value, e instanceof Error ? e.message : 'Location lookup failed');
    }
  }

  function schedule() {
    clearTimeout(timer);
    ctl?.abort();
    seq++;
    if (isLive(value.method) && value.query.trim().length >= MIN_CHARS) timer = setTimeout(run, DEBOUNCE_MS);
  }

  function onQuery(text: string) {
    let s = setQuery(value, text);
    // Short text is not searched, so it must not read as a pending lookup.
    if (!isLive(s.method) || text.trim().length < MIN_CHARS) s = { ...s, phase: 'idle' };
    value = s;
    schedule();
  }

  function onMethod(m: Method) {
    value = setMethod(value, m);
    clearTimeout(timer);
    ctl?.abort();
    seq++;
    schedule();
  }

  function onRegion(text: string) {
    value = { ...value, region: text };
    if (value.candidates.length || value.phase !== 'idle') schedule();
  }

  function onEnter(e: KeyboardEvent) {
    if (e.key !== 'Enter') return;
    e.preventDefault(); // Enter must not submit the surrounding incident form
    void run();
  }

  function placeAtCenter() {
    const c = map?.center();
    if (c) value = placeManual(value, c.lat, c.lon);
  }

  onDestroy(() => {
    clearTimeout(timer);
    ctl?.abort();
  });

  const mapPoint = $derived(point ? { ...point, approximate: rel !== 'verified' } : null);
  const relStyle: Record<string, string> = {
    verified: 'border-ok/60 bg-ok-soft text-ok',
    approximate: 'border-warn/60 bg-warn-soft text-warn',
    ambiguous: 'border-warn/60 bg-warn-soft text-warn',
    unresolved: 'border-line-strong bg-raised text-muted',
    pending: 'border-warn/60 bg-warn-soft text-warn'
  };
  const relGlyph: Record<string, string> = { verified: '✔', approximate: '◐', ambiguous: '?', unresolved: '✖', pending: '▲' };
</script>

<div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(17rem,24rem)]">
  <div class="min-w-0 space-y-3">
    <fieldset>
      <legend class="label">How was the location given?</legend>
      <div class="flex flex-wrap gap-1" role="radiogroup">
        {#each METHODS as m}
          <label class="chip cursor-pointer has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent" data-on={value.method === m.id}>
            <input type="radio" class="sr-only" name="{idPrefix}-method" value={m.id} checked={value.method === m.id} onchange={() => onMethod(m.id)} />
            {m.label}
          </label>
        {/each}
      </div>
    </fieldset>

    {#if value.method === 'address'}
      <div>
        <label class="label" for="{idPrefix}-q">Street address</label>
        <input id="{idPrefix}-q" class="field" autocomplete="off" placeholder="412 Pine St" value={value.query} oninput={(e) => onQuery(e.currentTarget.value)} onkeydown={onEnter} aria-describedby="{idPrefix}-status" />
        <p class="mt-1 text-xs text-muted">A partial address is fine. Matches appear as you type.</p>
      </div>
      <div>
        <label class="label" for="{idPrefix}-d">Apartment, floor, suite, entrance <span class="font-normal text-faint">(optional)</span></label>
        <input id="{idPrefix}-d" class="field" autocomplete="off" placeholder="Apt 4B, 2nd floor" bind:value={value.details} maxlength="80" />
      </div>
    {:else if value.method === 'landmark'}
      <div>
        <label class="label" for="{idPrefix}-q">Landmark, business, park, highway or area</label>
        <input id="{idPrefix}-q" class="field" autocomplete="off" placeholder="Pike Place Market, Green Lake Park, I-5 near Mercer St" value={value.query} oninput={(e) => onQuery(e.currentTarget.value)} onkeydown={onEnter} aria-describedby="{idPrefix}-status" />
        <p class="mt-1 text-xs text-muted">Your wording is saved as the address. Matches to a place or area are marked approximate.</p>
      </div>
    {:else if value.method === 'map'}
      <div>
        <label class="label" for="{idPrefix}-q">Description <span class="font-normal text-faint">(optional)</span></label>
        <input id="{idPrefix}-q" class="field" autocomplete="off" placeholder="North shelter at Green Lake, caller sees smoke" value={value.query} oninput={(e) => onQuery(e.currentTarget.value)} />
      </div>
      <div class="flex flex-wrap items-center gap-2">
        <button type="button" class="btn" onclick={placeAtCenter}>Place marker at map center</button>
        <span class="text-xs text-muted">Click the map, or pan with the arrow keys and use this button. Drag the marker to adjust.</span>
      </div>
    {:else}
      <div class="rounded-md border border-warn/60 bg-warn-soft p-3 text-sm" role="note">
        <p class="font-semibold text-warn">▲ Location not yet determined</p>
        <p class="mt-1">{blocker}</p>
        <p class="mt-2 text-xs text-muted">This option is a UI placeholder. It is not connected to the API and nothing is saved. Pick another method as soon as the location is known.</p>
      </div>
    {/if}

    {#if value.method === 'address' || value.method === 'landmark'}
      <details class="text-xs">
        <summary class="cursor-pointer text-muted hover:text-fg">Search area: <span class="text-fg">{value.region.trim() || 'anywhere'}</span></summary>
        <label class="label mt-2" for="{idPrefix}-r">City or region used to narrow the search</label>
        <input id="{idPrefix}-r" class="field" autocomplete="off" value={value.region} oninput={(e) => onRegion(e.currentTarget.value)} />
      </details>
    {/if}

    <!-- Resolution status: always says how much to trust the position -->
    <div id="{idPrefix}-status" aria-live="polite" class="space-y-2">
      {#if value.phase === 'resolving'}
        <p class="text-sm text-muted" role="status"><span aria-hidden="true">◌</span> Looking up location…</p>
      {:else if value.phase === 'error'}
        <div class="rounded-md border border-danger/60 bg-danger-soft p-2 text-sm" role="alert">
          <span class="text-danger" aria-hidden="true">✖</span> {value.error}. What you typed is kept.
          <button type="button" class="btn btn-sm ml-1" onclick={run}>Retry</button>
        </div>
      {/if}

      {#if value.method !== 'unknown' && (value.query.trim() || point)}
        <div class="rounded-md border p-2.5 text-sm {relStyle[rel]}">
          <p class="font-semibold"><span aria-hidden="true">{relGlyph[rel]}</span> {RELIABILITY_TEXT[rel].label}</p>
          <p class="mt-0.5 text-xs text-fg">{RELIABILITY_TEXT[rel].hint}</p>
        </div>
        {#if value.query.trim()}
          <dl class="grid grid-cols-[6.5rem_minmax(0,1fr)] gap-x-2 gap-y-1 text-xs">
            <dt class="text-faint">Caller reported</dt><dd class="text-fg">{value.query.trim()}{value.details.trim() ? `, ${value.details.trim()}` : ''}</dd>
            {#if point}
              <dt class="text-faint">Map position</dt>
              <dd class="text-fg">
                {#if value.manual}Placed by dispatcher{adjusted ? `, adjusted from "${value.chosen?.label}"` : ''}
                {:else if value.chosen}{value.chosen.label}{value.chosen.detail ? `, ${value.chosen.detail}` : ''}{value.chosen.source !== 'geocoder' ? ` (${value.chosen.source})` : ''}
                {/if}
              </dd>
            {/if}
          </dl>
          {#if difference}
            <p class="rounded-md border border-warn/60 bg-warn-soft p-2 text-xs text-fg" role="status"><span class="text-warn" aria-hidden="true">▲</span> {difference} Confirm the address with the caller.</p>
          {/if}
        {/if}
      {:else if value.phase === 'unresolved'}
        <p class="text-sm text-muted">No location found.</p>
      {/if}

      {#if value.phase === 'unresolved' && value.query.trim()}
        <div class="rounded-md border border-line-strong bg-raised p-2.5 text-sm">
          <p>No match for "{value.query.trim()}". Your description is kept. Refine it, try another method, or place a marker on the map.</p>
          <button type="button" class="btn btn-sm mt-2" onclick={() => onMethod('map')}>Pick on map instead</button>
        </div>
      {/if}
    </div>

    {#if showList}
      <section aria-labelledby="{idPrefix}-cands">
        <h3 id="{idPrefix}-cands" class="section-title mb-1.5">{value.phase === 'ambiguous' ? 'Several matches. Choose one' : 'Other matches'}</h3>
        <ol class="space-y-1">
          {#each value.candidates as c, i (c.id)}
            <li>
              <button
                type="button"
                class="flex w-full items-start gap-2 rounded-md border p-2 text-left hover:bg-raised {value.chosen?.id === c.id && !value.manual ? 'border-accent bg-accent-soft' : 'border-line-strong'}"
                aria-pressed={value.chosen?.id === c.id && !value.manual}
                onclick={() => (value = choose(value, c.id))}
              >
                <span class="grid size-6 shrink-0 place-items-center rounded-full border-2 border-accent text-xs font-bold" aria-hidden="true">{i + 1}</span>
                <span class="min-w-0 flex-1">
                  <span class="block text-sm font-medium">{c.label}</span>
                  {#if c.detail}<span class="block text-xs text-muted">{c.detail}</span>{/if}
                </span>
                <span class="badge shrink-0">{KIND_TEXT[c.kind]}</span>
              </button>
            </li>
          {/each}
        </ol>
      </section>
    {/if}

    {#if value.incoming}
      <div class="rounded-md border-2 border-accent bg-accent-soft p-2.5 text-sm" role="status">
        <p class="font-semibold">New location from {value.incoming.source}</p>
        <p class="mt-0.5 text-xs">{value.incoming.label}. Your current location has not been changed.</p>
        <div class="mt-2 flex gap-2">
          <button type="button" class="btn btn-primary btn-sm" onclick={() => (value = acceptIncoming(value))}>Use this location</button>
          <button type="button" class="btn btn-sm" onclick={() => (value = dismissIncoming(value))}>Keep current</button>
        </div>
      </div>
    {/if}

    {#if point}
      <button type="button" class="btn btn-ghost btn-sm" onclick={() => (value = clearPoint(value))}>Clear location</button>
    {/if}

    {#if value.method === 'address' || value.method === 'landmark'}
      <!-- Required credit for the OpenStreetMap data behind the address search. -->
      <p class="text-[0.7rem] text-faint">
        Address search by <a class="underline" href="https://photon.komoot.io" target="_blank" rel="noopener noreferrer">Photon</a>,
        data &copy; <a class="underline" href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener noreferrer">OpenStreetMap</a> contributors.
      </p>
    {/if}
  </div>

  <div class="h-72 lg:h-auto lg:min-h-80">
    <LocationMap
      bind:this={map}
      point={mapPoint}
      {reference}
      candidates={value.candidates}
      placeOnClick={value.method === 'map'}
      readonly={value.method === 'unknown'}
      onPlace={(lat, lon) => (value = placeManual(value, lat, lon))}
      onChoose={(id) => (value = choose(value, id))}
    />
  </div>
</div>
