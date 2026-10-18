<script lang="ts">
  import { api, errorText } from '$lib/api/client';
  import type { Incident } from '$lib/api/types';
  import LocationPicker from './LocationPicker.svelte';
  import { describeConfidence, initialState, saveBlocker, toApiLocation, type LocationState } from '$lib/location/state';

  let {
    open = $bindable(false),
    incident,
    onSaved
  }: { open: boolean; incident: Incident; onSaved: (updated: Incident) => void } = $props();

  let dlg: HTMLDialogElement;
  let loc = $state<LocationState>(initialState());
  let saving = $state(false);
  let error = $state('');
  let openedFor = '';

  // Start from what the caller originally said, so correcting a typo is a quick edit, not a retype.
  $effect(() => {
    if (open) {
      if (!dlg.open) dlg.showModal();
      if (openedFor !== incident.incidentId) {
        openedFor = incident.incidentId;
        loc = { ...initialState(), query: incident.location.address ?? '' };
        error = '';
      }
    } else {
      if (dlg.open) dlg.close();
      openedFor = '';
    }
  });

  const next = $derived(toApiLocation(loc));
  const blocker = $derived(saveBlocker(loc));

  async function save() {
    const location = toApiLocation(loc);
    if (!location || saving) return;
    saving = true;
    error = '';
    const { data, error: err } = await api
      .PATCH('/api/v1/incidents/{incidentId}', {
        params: { path: { incidentId: incident.incidentId } },
        body: { version: incident.version, location }
      })
      .catch((e) => ({ data: undefined, error: e }));
    saving = false;
    if (data) {
      open = false;
      onSaved(data);
    } else {
      const conflict = err && typeof err === 'object' && 'code' in err && err.code === 'conflict';
      error = conflict
        ? 'This incident was changed by someone else while you were editing. Close this window, review the update, and correct the location again.'
        : `${errorText(err)}. The location was not changed and your entries are kept; you can retry.`;
    }
  }
</script>

<dialog
  bind:this={dlg}
  onclose={() => (open = false)}
  aria-labelledby="lc-title"
  class="m-auto w-[min(62rem,calc(100vw-2rem))] max-h-[calc(100vh-2rem)] overflow-y-auto rounded-lg border border-line-strong bg-panel p-0 text-fg backdrop:bg-black/60"
>
  <div class="p-4">
    <h2 id="lc-title" class="text-base font-semibold">Correct location · {incident.number}</h2>
    <p class="mt-1 text-sm text-muted">
      Current: <span class="text-fg">{incident.location.address ?? 'No address on record'}</span>
      · {describeConfidence(incident.location.geocodeConfidence).label}.
      The saved location stays in effect until you save a new one.
    </p>
    {#if open}
      <div class="mt-3">
        <LocationPicker bind:value={loc} idPrefix="lc" reference={incident.location.point} />
      </div>
    {/if}
    {#if error}<p class="mt-3 rounded-md border border-danger/60 bg-danger-soft p-2 text-sm" role="alert"><span class="text-danger">✖</span> {error}</p>{/if}
  </div>
  <div class="sticky bottom-0 flex items-center justify-end gap-3 border-t border-line bg-panel p-3">
    {#if blocker}<p class="mr-auto text-xs text-warn">{blocker}</p>{/if}
    <button class="btn" type="button" onclick={() => (open = false)}>Cancel</button>
    <button class="btn btn-primary" type="button" disabled={!next || saving} onclick={save}>{saving ? 'Saving…' : 'Save corrected location'}</button>
  </div>
</dialog>
