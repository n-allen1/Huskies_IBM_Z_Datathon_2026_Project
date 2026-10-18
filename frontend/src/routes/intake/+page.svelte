<script lang="ts">
  import { onMount } from 'svelte';
  import { api, errorText, newIdempotencyKey } from '$lib/api/client';
  import type { Classification, Incident, IncidentCategory } from '$lib/api/types';
  import { label } from '$lib/format';
  import LocationPicker from '$lib/components/LocationPicker.svelte';
  import {
    describeConfidence, initialState, saveBlocker, toApiLocation, type LocationState
  } from '$lib/location/state';

  const categories: IncidentCategory[] = [
    'structure_fire', 'vehicle_fire', 'wildland_fire', 'medical_cardiac', 'medical_trauma', 'medical_other',
    'traffic_collision', 'technical_rescue', 'hazmat', 'assault', 'burglary', 'domestic_disturbance',
    'welfare_check', 'other'
  ];

  let narrative = $state('');
  let callback = $state('');
  let reportedCategory = $state<IncidentCategory | ''>('');
  let priority = $state<'P1' | 'P2' | 'P3' | 'P4' | ''>('');
  let loc = $state<LocationState>(initialState());

  // Reuse the key across retries of the same submission so a double-click cannot create two incidents.
  let submitKey = newIdempotencyKey();
  let saving = $state(false);
  let error = $state('');
  let incident = $state<Incident | null>(null);

  let suggestion = $state<Classification | null>(null);
  let classifying = $state(false);
  let classifyError = $state('');
  let confirmCategory = $state<IncidentCategory>('other');
  let confirmPriority = $state<'P1' | 'P2' | 'P3' | 'P4'>('P2');
  let confirmed = $state<Classification | null>(null);

  const apiLocation = $derived(toApiLocation(loc));
  const blocker = $derived(narrative.trim() ? saveBlocker(loc) : 'Describe the incident first.');

  // ---------- draft: nothing typed is lost to a reload, a failed lookup or a failed save ----------
  const DRAFT_KEY = 'rm.intake.draft.v1';
  let restored = false;
  onMount(() => {
    try {
      const d = JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? 'null');
      if (d) {
        narrative = d.narrative ?? '';
        callback = d.callback ?? '';
        reportedCategory = d.reportedCategory ?? '';
        priority = d.priority ?? '';
        if (d.loc?.method) loc = { ...initialState(), ...d.loc, phase: d.loc.phase === 'resolving' ? 'idle' : d.loc.phase, incoming: null };
      }
    } catch {
      /* unreadable draft: start empty */
    }
    restored = true;
  });
  $effect(() => {
    const snapshot = JSON.stringify({ narrative, callback, reportedCategory, priority, loc });
    if (!restored || incident) return;
    try {
      sessionStorage.setItem(DRAFT_KEY, snapshot);
    } catch {
      /* storage blocked: the form still works, the draft just is not kept */
    }
  });
  const clearDraft = () => {
    try {
      sessionStorage.removeItem(DRAFT_KEY);
    } catch {
      /* nothing to clear */
    }
  };

  async function save(ev: SubmitEvent) {
    ev.preventDefault();
    const location = toApiLocation(loc);
    if (!location || saving) return; // no position: nothing is sent and nothing is invented
    saving = true;
    error = '';
    const { data, error: err } = await api.POST('/api/v1/incidents', {
      params: { header: { 'Idempotency-Key': submitKey } },
      body: {
        narrative,
        location,
        callbackNumber: callback || undefined,
        reportedCategory: reportedCategory || undefined,
        priority: priority || undefined,
        isSynthetic: true
      }
    }).catch((e) => ({ data: undefined, error: e }));
    saving = false;
    if (data) {
      incident = data;
      clearDraft();
    } else error = `${errorText(err)}. Your entries are kept; you can retry.`;
  }

  async function suggest() {
    if (!incident) return;
    classifying = true;
    classifyError = '';
    const { data, error: err } = await api
      .POST('/api/v1/incidents/{incidentId}/classifications', {
        params: { path: { incidentId: incident.incidentId } },
        body: { mode: 'infer' }
      })
      .catch((e) => ({ data: undefined, error: e }));
    classifying = false;
    if (data) {
      suggestion = data;
      confirmCategory = data.category;
      confirmPriority = data.suggestedPriority ?? 'P2';
    } else {
      classifyError = `${errorText(err)} – choose the category manually below.`;
    }
  }

  async function confirm() {
    if (!incident) return;
    classifying = true;
    classifyError = '';
    const { data, error: err } = await api
      .POST('/api/v1/incidents/{incidentId}/classifications', {
        params: { path: { incidentId: incident.incidentId } },
        body: {
          mode: 'confirm',
          category: confirmCategory,
          priority: confirmPriority,
          inferredClassificationId: suggestion?.classificationId
        }
      })
      .catch((e) => ({ data: undefined, error: e }));
    classifying = false;
    if (data) confirmed = data;
    else classifyError = errorText(err);
  }

  function reset() {
    narrative = callback = '';
    reportedCategory = priority = '';
    loc = initialState();
    incident = suggestion = confirmed = null;
    submitKey = newIdempotencyKey();
    clearDraft();
  }
</script>

<div class="mx-auto h-full max-w-5xl overflow-y-auto p-6">
  <h1 class="mb-4 text-lg font-semibold">Emergency call intake</h1>

  {#if !incident}
    <form class="space-y-4" onsubmit={save}>
      <section class="panel p-4" aria-labelledby="h-inc">
        <h2 id="h-inc" class="section-title mb-3">Incident</h2>
        <div class="grid gap-3 md:grid-cols-3">
          <div class="md:col-span-3">
            <label class="label" for="narrative">What is happening?</label>
            <textarea id="narrative" class="field" rows="3" required maxlength="8000" bind:value={narrative}></textarea>
          </div>
          <div>
            <label class="label" for="rc">Caller-reported category</label>
            <select id="rc" class="field" bind:value={reportedCategory}>
              <option value="">Not specified</option>
              {#each categories as c}<option value={c}>{label(c)}</option>{/each}
            </select>
          </div>
          <div>
            <label class="label" for="pr">Priority (dispatcher confirms later)</label>
            <select id="pr" class="field" bind:value={priority}>
              <option value="">Unassigned</option>
              {#each ['P1', 'P2', 'P3', 'P4'] as p}<option value={p}>{p}</option>{/each}
            </select>
          </div>
          <div>
            <label class="label" for="cb">Callback number (synthetic)</label>
            <input id="cb" class="field" maxlength="32" bind:value={callback} />
          </div>
        </div>
      </section>

      <section class="panel p-4" aria-labelledby="h-loc">
        <h2 id="h-loc" class="section-title mb-3">Location</h2>
        <LocationPicker bind:value={loc} />
      </section>

      <div class="flex flex-wrap items-center gap-3">
        <button class="btn btn-primary" disabled={saving || !!blocker || !apiLocation} aria-describedby="save-note">{saving ? 'Saving…' : 'Save incident'}</button>
        <p id="save-note" class="text-xs {blocker ? 'text-warn' : 'text-muted'}">
          {#if blocker}{blocker}{:else}All incidents are marked <strong>synthetic</strong>.{/if}
        </p>
        {#if error}<p class="w-full text-sm font-medium text-danger" role="alert">✖ {error}</p>{/if}
      </div>
    </form>
  {:else}
    <section class="panel mb-4 p-4" aria-labelledby="saved">
      <h2 id="saved" class="text-base font-semibold">Incident saved: {incident.number}</h2>
      <p class="mt-1 text-sm text-muted">Status: {label(incident.status)} · Priority: {incident.priority}
        {incident.priorityConfirmed ? '(confirmed)' : '(not yet confirmed)'}</p>
      <p class="mt-1 text-sm">{incident.narrative}</p>
      <p class="mt-2 text-sm">
        <span class="text-muted">Location:</span> {incident.location.address ?? 'No address on record'}
        <span class="badge ml-1">{describeConfidence(incident.location.geocodeConfidence).label}</span>
      </p>
      <p class="mt-1 text-xs text-muted">The location can be corrected from the incident's detail panel on the Dispatch screen.</p>
    </section>

    <section class="panel mb-4 p-4" aria-labelledby="cls">
      <h2 id="cls" class="section-title">Classification</h2>
      {#if confirmed}
        <p class="mt-2" role="status"><span class="text-ok">✔</span> Confirmed by human: <strong>{label(confirmed.category)}</strong>
          {#if confirmed.suggestedPriority}· {confirmed.suggestedPriority}{/if}</p>
        <div class="mt-3 flex gap-2">
          <a class="btn btn-primary" href="/dispatch">Hand off to dispatch</a>
          <button class="btn" onclick={reset}>New call</button>
        </div>
      {:else}
        <div class="mt-2 flex items-center gap-3">
          <button class="btn" onclick={suggest} disabled={classifying}>
            {classifying ? 'Working…' : 'Suggest classification (AI)'}
          </button>
        </div>
        {#if classifyError}<p class="mt-2 text-sm font-medium text-danger" role="alert">✖ {classifyError}</p>{/if}
        {#if suggestion}
          <div class="mt-3 rounded-md border border-dashed border-ai bg-ai-soft p-2.5 text-sm" role="status">
            <span class="badge border-ai text-ai">AI SUGGESTION – unverified</span>
            <p class="mt-1">Suggested: <strong>{label(suggestion.category)}</strong>
              {#if suggestion.confidence != null}· confidence {(suggestion.confidence * 100).toFixed(0)}%{/if}
              {#if suggestion.modelVersion}· model {suggestion.modelVersion}{/if}</p>
            {#if suggestion.limitations?.length}
              <ul class="mt-1 list-disc pl-5 text-muted">
                {#each suggestion.limitations as l}<li>{l}</li>{/each}
              </ul>
            {/if}
          </div>
        {/if}
        <div class="mt-3 grid gap-3 sm:grid-cols-3">
          <div>
            <label class="label" for="cc">Confirmed category</label>
            <select id="cc" class="field" bind:value={confirmCategory}>
              {#each categories as c}<option value={c}>{label(c)}</option>{/each}
            </select>
          </div>
          <div>
            <label class="label" for="cp">Confirmed priority</label>
            <select id="cp" class="field" bind:value={confirmPriority}>
              {#each ['P1', 'P2', 'P3', 'P4'] as p}<option value={p}>{p}</option>{/each}
            </select>
          </div>
          <div class="self-end">
            <button class="btn btn-primary" onclick={confirm} disabled={classifying}>Confirm classification</button>
          </div>
        </div>
      {/if}
    </section>
  {/if}
</div>
