<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import 'leaflet/dist/leaflet.css';
  import type { Facility, Incident, Unit } from '$lib/api/types';
  import type * as Leaflet from 'leaflet';
  import { label, priorityRank, waitMinutes } from '$lib/format';
  import { BASEMAPS, WORLD, type BasemapKey } from '$lib/map/basemaps';
  import { describeConfidence, isApproximate } from '$lib/location/state';
  import {
    AWAITING_DISPATCH, PRIORITY, UNIT_GROUP_META, esc, prioritySvg, unitGroup, type Priority
  } from '$lib/ops';

  let {
    incidents,
    units,
    facilities,
    selectedId,
    highlightUnitIds = [],
    routeUnitIds = [],
    focusToken = 0,
    onSelect
  }: {
    incidents: Incident[];
    units: Unit[];
    facilities: Facility[];
    selectedId?: string;
    /** Eligible candidates for the selected incident: ringed on the map. */
    highlightUnitIds?: string[];
    /** Units the dispatcher currently has ticked: joined to the incident by a dashed line. */
    routeUnitIds?: string[];
    /** Increment to ask the map to center on the selected incident. */
    focusToken?: number;
    onSelect?: (incidentId: string) => void;
  } = $props();

  // ---------- basemaps ----------
  // Basemaps live in $lib/map/basemaps (shared with the intake location map).

  const MIN_ZOOM_FLOOR = 3;
  const PREFS_KEY = 'rm.map.v4';
  type Prefs = { basemap: BasemapKey; units: boolean; facilities: boolean; lines: boolean; labels: boolean };
  const DEFAULT_PREFS: Prefs = { basemap: 'night', units: true, facilities: true, lines: true, labels: true };
  function loadPrefs(): Prefs {
    try {
      const p = JSON.parse(localStorage.getItem(PREFS_KEY) ?? '{}');
      return { ...DEFAULT_PREFS, ...p, basemap: p.basemap in BASEMAPS ? p.basemap : DEFAULT_PREFS.basemap };
    } catch {
      return { ...DEFAULT_PREFS };
    }
  }
  let prefs = $state<Prefs>(loadPrefs());
  $effect(() => {
    const snapshot = JSON.stringify(prefs);
    try {
      localStorage.setItem(PREFS_KEY, snapshot);
    } catch {
      /* private mode or blocked storage: preferences simply don't persist */
    }
  });

  // ---------- map state ----------
  let el: HTMLDivElement;
  let L: typeof Leaflet;
  let map: Leaflet.Map | undefined;
  let layer: Leaflet.LayerGroup | undefined;
  let baseLayer: Leaflet.TileLayer | undefined;
  let labelLayer: Leaflet.TileLayer | undefined;
  let resizeObserver: ResizeObserver | undefined;
  let ready = $state(false);
  let zoom = $state(12);
  let tileErrors = $state(0);
  let tileOk = $state(0);
  let didFit = false;
  let menuOpen = $state(false);
  let legendOpen = $state(false);

  const tilesFailing = $derived(tileErrors >= 4 && tileOk === 0);

  function applyBasemap() {
    if (!map) return;
    const b = BASEMAPS[prefs.basemap];
    baseLayer?.remove();
    labelLayer?.remove();
    tileErrors = tileOk = 0;
    const common = { noWrap: true, bounds: WORLD, maxZoom: 19, maxNativeZoom: b.maxNative, attribution: b.attr, crossOrigin: true } as const;
    baseLayer = L.tileLayer(b.base, common).addTo(map);
    const count = (ok: boolean) => () => (ok ? tileOk++ : tileErrors++);
    baseLayer.on('tileload', count(true)).on('tileerror', count(false));
    if (b.labels && prefs.labels) {
      labelLayer = L.tileLayer(b.labels, { ...common, pane: 'rmLabels', attribution: '' }).addTo(map);
    } else labelLayer = undefined;
  }

  onMount(() => {
    let disposed = false;
    void import('leaflet').then((mod) => {
      if (disposed) return;
      L = mod.default ?? mod;
      map = L.map(el, {
        maxBounds: WORLD,
        maxBoundsViscosity: 1,
        zoomControl: false, // replaced by our own controls so they sit with the other map tools
        zoomSnap: 0.5,
        minZoom: MIN_ZOOM_FLOOR
      }).setView([47.6062, -122.3321], 12);
      map.createPane('rmLabels').style.zIndex = '350';
      map.getPane('rmLabels')!.classList.add('rm-labels-pane');
      map.attributionControl.setPrefix(false);
      // Never zoom out past the point where one world copy fills the map in both directions; beyond that the
      // edges would show tiles that do not exist ("Map data not yet available"). Re-evaluated on every resize,
      // for example when a side panel collapses and the map gets wider.
      const fitMinZoom = () => {
        if (!map) return;
        map.invalidateSize();
        const needed = Math.log2(Math.max(el.clientWidth, el.clientHeight) / 256);
        map.setMinZoom(Math.max(MIN_ZOOM_FLOOR, Math.ceil(needed * 2) / 2)); // zoomSnap is 0.5
      };
      fitMinZoom();
      resizeObserver = new ResizeObserver(fitMinZoom);
      resizeObserver.observe(el);
      map.on('zoomend', () => (zoom = map!.getZoom()));
      layer = L.layerGroup().addTo(map);
      applyBasemap();
      ready = true;
    });
    return () => {
      disposed = true;
      resizeObserver?.disconnect();
      map?.remove();
    };
  });

  // Basemap or label toggles
  $effect(() => {
    void [prefs.basemap, prefs.labels, ready];
    untrack(() => applyBasemap());
  });

  // ---------- drawing ----------
  const CLUSTER_BELOW_ZOOM = 15;
  const FIT_MAX_ZOOM = 13;

  /** Greedy pixel-grid clustering: stable under panning because it works in layer coordinates. */
  function cluster<T extends { lat: number; lon: number }>(items: T[], cell: number, keep: (t: T) => boolean) {
    const buckets = new Map<string, T[]>();
    const singles: T[] = [];
    for (const it of items) {
      if (keep(it) || zoom >= CLUSTER_BELOW_ZOOM) {
        singles.push(it);
        continue;
      }
      const p = map!.latLngToLayerPoint([it.lat, it.lon]);
      const k = `${Math.floor(p.x / cell)}:${Math.floor(p.y / cell)}`;
      (buckets.get(k) ?? buckets.set(k, []).get(k)!).push(it);
    }
    const groups: T[][] = [];
    for (const b of buckets.values()) {
      if (b.length === 1) singles.push(b[0]);
      else groups.push(b);
    }
    return { singles, groups };
  }

  const divIcon = (html: string, title: string) => L.divIcon({ className: 'rm-icon', html, iconSize: [0, 0] });

  const wait = (i: Incident) => `${waitMinutes(i.createdAt)} min since created`;

  function incidentTip(i: Incident): string {
    return `<b>${esc(i.number)}</b> · ${esc(i.priority === 'unassigned' ? 'no priority' : i.priority)}${i.priorityConfirmed ? '' : ' (unconfirmed)'}<br>` +
      `<span class="sub">${esc(label(i.status))} · ${esc(i.confirmedCategory ? label(i.confirmedCategory) : 'category unconfirmed')}</span><br>` +
      `${esc(i.location.address ?? 'No address')}<br><span class="sub">Location: ${esc(describeConfidence(i.location.geocodeConfidence).label)}</span><br><span class="sub">${esc(wait(i))}</span>`;
  }

  function draw() {
    if (!map || !layer) return;
    layer.clearLayers();
    const add = (m: Leaflet.Layer) => m.addTo(layer!);
    const hi = new Set(highlightUnitIds);

    if (prefs.facilities) {
      for (const f of facilities) {
        const g = f.type === 'hospital' ? 'H' : f.type === 'eoc' ? 'E' : 'S';
        const name = `${f.name} (${label(f.type)})`;
        add(
          L.marker([f.location.lat, f.location.lon], {
            icon: divIcon(`<div class="rm-marker rm-facility" aria-hidden="true">${g}</div>`, name),
            zIndexOffset: -500, title: name, alt: name, keyboard: false
          }).bindTooltip(`<b>${esc(f.name)}</b><br><span class="sub">${esc(label(f.type))}</span>`, { className: 'rm-tip', direction: 'top', offset: [0, -8] })
        );
      }
    }

    if (prefs.units) {
      const pts = units.filter((u) => u.location).map((u) => ({ u, lat: u.location!.lat, lon: u.location!.lon }));
      const { singles, groups } = cluster(pts, 52, (p) => hi.has(p.u.unitId) || routeUnitIds.includes(p.u.unitId));
      for (const { u, lat, lon } of singles) {
        const grp = unitGroup(u.status);
        const cand = hi.has(u.unitId);
        const text = `${u.callSign} – ${UNIT_GROUP_META[grp].text}, ${label(u.status)}`;
        add(
          L.marker([lat, lon], {
            icon: divIcon(`<div class="rm-marker rm-unit ${grp}${cand ? ' candidate' : ''}"><span class="g" aria-hidden="true">${UNIT_GROUP_META[grp].glyph}</span>${esc(u.callSign)}</div>`, text),
            zIndexOffset: cand ? 600 : 0, title: text, alt: text, keyboard: false
          }).bindTooltip(
            `<b>${esc(u.callSign)}</b> · ${esc(u.unitType ?? u.discipline)}<br><span class="sub">${esc(UNIT_GROUP_META[grp].text)} (${esc(label(u.status))})${cand ? ' · eligible for selected incident' : ''}<br>Simulated position</span>`,
            { className: 'rm-tip', direction: 'top', offset: [0, -10] }
          )
        );
      }
      for (const g of groups) {
        const c = L.latLng(g.reduce((s, p) => s + p.lat, 0) / g.length, g.reduce((s, p) => s + p.lon, 0) / g.length);
        const names = g.map((p) => p.u.callSign).join(', ');
        add(
          L.marker(c, {
            icon: divIcon(`<div class="rm-marker rm-cluster units">${g.length}u</div>`, `${g.length} units`),
            zIndexOffset: -100, title: `${g.length} units: ${names}`, alt: `${g.length} units`, keyboard: false
          })
            .bindTooltip(`<b>${g.length} units</b><br><span class="sub">${esc(names)}</span><br><span class="sub">Zoom in to separate</span>`, { className: 'rm-tip', direction: 'top', offset: [0, -10] })
            .on('click', () => zoomTo(g.map((p) => [p.lat, p.lon] as [number, number])))
        );
      }
    }

    // Dispatch lines: straight lines only. The contract carries no road routes, so they are labeled as such in the legend.
    const sel = incidents.find((i) => i.incidentId === selectedId);
    if (sel && prefs.lines) {
      const from: [number, number] = [sel.location.point.lat, sel.location.point.lon];
      for (const uid of routeUnitIds) {
        const u = units.find((x) => x.unitId === uid);
        if (!u?.location) continue;
        add(L.polyline([from, [u.location.lat, u.location.lon]], { color: '#4da8d8', weight: 2.5, dashArray: '2 7', lineCap: 'round', opacity: 0.95, interactive: false }));
      }
    }

    const pts = incidents.map((i) => ({ i, lat: i.location.point.lat, lon: i.location.point.lon }));
    const { singles, groups } = cluster(pts, 44, (p) => p.i.incidentId === selectedId);
    for (const { i, lat, lon } of singles) {
      const isSel = i.incidentId === selectedId;
      const open = AWAITING_DISPATCH.has(i.status);
      const size = isSel ? 30 : open ? 24 : 18;
      const text = `${i.number}, ${PRIORITY[i.priority].name}, ${label(i.status)}`;
      const html = `<div class="rm-marker rm-inc${open || isSel ? '' : ' handled'}">${isSel ? '<span class="rm-ring"></span>' : ''}${isApproximate(i.location.geocodeConfidence) ? '<span class="rm-approx-ring"></span>' : ''}${prioritySvg(i.priority, size)}${isSel ? `<span class="rm-inc-tag">${esc(i.number)}</span>` : ''}</div>`;
      add(
        L.marker([lat, lon], {
          icon: divIcon(html, text),
          zIndexOffset: isSel ? 5000 : (5 - (priorityRank[i.priority] ?? 5)) * 100 + (open ? 50 : 0),
          title: text, alt: text, keyboard: true
        })
          .bindTooltip(incidentTip(i), { className: 'rm-tip', direction: 'top', offset: [0, -size / 2 - 2] })
          .on('click', () => onSelect?.(i.incidentId))
      );
    }
    for (const g of groups) {
      const c = L.latLng(g.reduce((s, p) => s + p.lat, 0) / g.length, g.reduce((s, p) => s + p.lon, 0) / g.length);
      const top = g.reduce<Priority>((m, p) => ((priorityRank[p.i.priority] ?? 9) < (priorityRank[m] ?? 9) ? p.i.priority : m), 'unassigned');
      const mix = (['P1', 'P2', 'P3', 'P4', 'unassigned'] as Priority[])
        .map((p) => [p, g.filter((x) => x.i.priority === p).length] as const)
        .filter(([, n]) => n)
        .map(([p, n]) => `${n}× ${p === 'unassigned' ? 'no priority' : p}`)
        .join(', ');
      add(
        L.marker(c, {
          icon: divIcon(`<div class="rm-marker rm-cluster" style="border-color:${PRIORITY[top].color}">${g.length}<span class="rm-cluster-top">${prioritySvg(top, 15)}</span></div>`, mix),
          zIndexOffset: (5 - (priorityRank[top] ?? 5)) * 100 + 20,
          title: `${g.length} incidents: ${mix}`, alt: `${g.length} incidents`, keyboard: true
        })
          .bindTooltip(`<b>${g.length} incidents</b><br><span class="sub">${esc(mix)}<br>Click to zoom in</span>`, { className: 'rm-tip', direction: 'top', offset: [0, -16] })
          .on('click', () => zoomTo(g.map((p) => [p.lat, p.lon] as [number, number])))
      );
    }

    // First data frame: fit to everything once, then never move the view on redraws.
    if (!didFit && (incidents.length || units.length)) {
      didFit = true;
      fitAll();
    }
  }

  function zoomTo(points: [number, number][]) {
    if (!map) return;
    const b = L.latLngBounds(points);
    if (b.getNorthEast().equals(b.getSouthWest())) map.flyTo(b.getCenter(), map.getZoom() + 2, { duration: 0.4 });
    else map.flyToBounds(b, { padding: [70, 70], maxZoom: 17, duration: 0.4 });
  }

  function fitAll() {
    if (!map) return;
    const pts: [number, number][] = [
      ...incidents.map((i) => [i.location.point.lat, i.location.point.lon] as [number, number]),
      ...units.filter((u) => u.location).map((u) => [u.location!.lat, u.location!.lon] as [number, number])
    ];
    // Cap the zoom so one lone incident (or a tight cluster) still shows neighborhood context, and so we never
    // land on a zoom the basemap has no tiles for. Zooming further in is the dispatcher's call.
    if (pts.length) map.fitBounds(L.latLngBounds(pts), { padding: [80, 80], maxZoom: FIT_MAX_ZOOM, animate: false });
  }

  const selectedIncident = $derived(incidents.find((i) => i.incidentId === selectedId));

  function recenter() {
    const p = selectedIncident?.location.point;
    if (map && p) map.flyTo([p.lat, p.lon], Math.max(map.getZoom(), 14), { duration: 0.6 });
  }

  // Centers only on request (focusToken bump from a queue or marker click); redraws never move the view.
  let lastToken = 0;
  $effect(() => {
    const token = focusToken;
    untrack(() => {
      if (token === lastToken) return;
      lastToken = token;
      recenter();
    });
  });

  $effect(() => {
    // Re-draw whenever inputs change.
    void [incidents, units, facilities, selectedId, highlightUnitIds, routeUnitIds, prefs.units, prefs.facilities, prefs.lines, zoom, ready];
    draw();
  });

  const priorityLegend: Priority[] = ['P1', 'P2', 'P3', 'P4', 'unassigned'];
  const toggles: { key: 'units' | 'facilities' | 'lines' | 'labels'; text: string }[] = [
    { key: 'units', text: 'Units' },
    { key: 'facilities', text: 'Stations & hospitals' },
    { key: 'lines', text: 'Dispatch lines' },
    { key: 'labels', text: 'Place labels' }
  ];
</script>

<div class="rm-map relative h-full w-full">
  <div
    bind:this={el}
    class="h-full w-full"
    role="application"
    aria-label="Incident and unit map. The incident queue lists the same incidents and is the keyboard-friendly way to select one."
  ></div>

  {#if tilesFailing}
    <p class="absolute top-3 left-1/2 z-[1000] -translate-x-1/2 rounded-md border border-warn bg-warn-soft px-3 py-1.5 text-xs font-medium text-warn" role="status">
      ▲ Basemap tiles unavailable. Incidents and units are still shown but without geographic context.
    </p>
  {/if}

  <!-- Controls -->
  <div class="absolute top-3 right-3 z-[1000] flex flex-col items-end gap-2">
    <div class="flex flex-col overflow-hidden rounded-md border border-line-strong bg-panel/95 shadow-lg" role="group" aria-label="Map zoom">
      <button class="grid size-8 place-items-center text-lg text-fg hover:bg-raised" aria-label="Zoom in" onclick={() => map?.zoomIn()}>+</button>
      <button class="grid size-8 place-items-center border-t border-line text-lg text-fg hover:bg-raised" aria-label="Zoom out" onclick={() => map?.zoomOut()}>−</button>
    </div>
    <div class="flex flex-col overflow-hidden rounded-md border border-line-strong bg-panel/95 shadow-lg" role="group" aria-label="Map view">
      <button class="grid h-8 place-items-center px-2 text-xs font-medium text-fg hover:bg-raised" onclick={fitAll}>Fit all</button>
      <button class="grid h-8 place-items-center border-t border-line px-2 text-xs font-medium text-fg hover:bg-raised disabled:cursor-not-allowed disabled:text-faint disabled:hover:bg-transparent" disabled={!selectedIncident} onclick={recenter}>Selected</button>
    </div>
    <div class="relative">
      <button
        class="btn bg-panel/95 shadow-lg"
        aria-expanded={menuOpen}
        aria-controls="map-layers"
        onclick={() => (menuOpen = !menuOpen)}
      >
        <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 1 15 5 8 9 1 5zM1 8l7 4 7-4M1 11l7 4 7-4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>
        Layers
      </button>
      {#if menuOpen}
        <div id="map-layers" class="absolute top-full right-0 mt-1 w-52 rounded-md border border-line-strong bg-panel p-3 shadow-xl">
          <fieldset>
            <legend class="section-title mb-1.5">Basemap</legend>
            {#each Object.entries(BASEMAPS) as [key, b]}
              <label class="flex items-center gap-2 py-0.5 text-sm">
                <input type="radio" name="basemap" value={key} bind:group={prefs.basemap} class="accent-[#4da8d8]" />
                {b.name}
              </label>
            {/each}
          </fieldset>
          <fieldset class="mt-3 border-t border-line pt-2">
            <legend class="section-title mb-1.5">Show</legend>
            {#each toggles as t}
              <label class="flex items-center gap-2 py-0.5 text-sm">
                <input type="checkbox" bind:checked={prefs[t.key]} class="accent-[#4da8d8]" />
                {t.text}
              </label>
            {/each}
          </fieldset>
        </div>
      {/if}
    </div>
  </div>

  <!-- Legend -->
  <div class="absolute bottom-6 left-3 z-[1000]">
    <button class="btn btn-sm bg-panel/95 shadow-lg" aria-expanded={legendOpen} aria-controls="map-legend" onclick={() => (legendOpen = !legendOpen)}>
      {legendOpen ? 'Hide legend' : 'Legend'}
    </button>
    {#if legendOpen}
      <div id="map-legend" class="mt-1 w-60 rounded-md border border-line-strong bg-panel p-3 text-xs shadow-xl">
        <p class="section-title mb-1.5">Incident priority</p>
        <ul class="grid grid-cols-2 gap-x-2 gap-y-1">
          {#each priorityLegend as p}
            <li class="flex items-center gap-1.5">{@html prioritySvg(p, 16)}{p === 'unassigned' ? 'None' : PRIORITY[p].name.replace('Priority ', 'P')}</li>
          {/each}
        </ul>
        <p class="mt-2 text-muted">Faded = already dispatched or on scene.</p>
        <p class="mt-1 flex items-center gap-1.5 text-muted"><span class="inline-block size-3.5 rounded-full border-2 border-dashed border-muted"></span>Dashed ring = approximate location.</p>
        <p class="section-title mt-3 mb-1.5">Units</p>
        <ul class="space-y-1">
          <li class="flex items-center gap-2"><span class="rm-unit [transform:none] available inline-flex rounded border px-1 text-[10px]"><span class="g">●</span>E1</span>Available</li>
          <li class="flex items-center gap-2"><span class="rm-unit [transform:none] assigned inline-flex rounded border px-1 text-[10px]"><span class="g">▶</span>E2</span>Assigned (dispatched, en route, on scene)</li>
          <li class="flex items-center gap-2"><span class="rm-unit [transform:none] returning inline-flex rounded border px-1 text-[10px]"><span class="g">↩</span>E3</span>Returning</li>
          <li class="flex items-center gap-2"><span class="rm-unit [transform:none] unavailable inline-flex rounded border px-1 text-[10px]"><span class="g">✕</span>E4</span>Out of service / off duty</li>
          <li class="flex items-center gap-2"><span class="rm-unit [transform:none] available candidate inline-flex rounded border px-1 text-[10px]"><span class="g">●</span>E5</span>Eligible for selected incident</li>
        </ul>
        <p class="mt-3 text-muted">Dashed lines join the selected incident to ticked units. They are straight lines, not road routes. Unit positions are simulated.</p>
      </div>
    {/if}
  </div>
</div>
