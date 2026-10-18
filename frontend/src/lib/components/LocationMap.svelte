<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import 'leaflet/dist/leaflet.css';
  import type * as Leaflet from 'leaflet';
  import { BASEMAPS, DEFAULT_CENTER, WORLD } from '$lib/map/basemaps';
  import { esc } from '$lib/ops';
  import type { Candidate } from '$lib/location/state';

  let {
    point,
    candidates = [],
    reference = null,
    placeOnClick = false,
    readonly = false,
    onPlace,
    onChoose
  }: {
    /** The position currently in use. `approximate` draws a dashed ring so it is not read as an exact destination. */
    point: { lat: number; lon: number; source: 'geocoder' | 'dispatcher_map' | 'external'; approximate: boolean } | null;
    /** Shown as numbered markers while a lookup is ambiguous. */
    candidates?: Candidate[];
    /** The position currently saved on the incident, drawn faintly while it is being corrected. */
    reference?: { lat: number; lon: number } | null;
    placeOnClick?: boolean;
    readonly?: boolean;
    onPlace?: (lat: number, lon: number) => void;
    onChoose?: (id: string) => void;
  } = $props();

  let el: HTMLDivElement;
  let L: typeof Leaflet;
  let map: Leaflet.Map | undefined;
  let layer: Leaflet.LayerGroup | undefined;
  let ready = $state(false);

  /** Center of the map: lets a keyboard user place a marker without a mouse (pan with the arrow keys first). */
  export function center(): { lat: number; lon: number } | undefined {
    const c = map?.getCenter();
    return c ? { lat: c.lat, lon: c.lng } : undefined;
  }

  onMount(() => {
    let disposed = false;
    let ro: ResizeObserver | undefined;
    void import('leaflet').then((mod) => {
      if (disposed) return;
      L = mod.default ?? mod;
      map = L.map(el, { maxBounds: WORLD, maxBoundsViscosity: 1, zoomSnap: 0.5, minZoom: 3, attributionControl: true }).setView(DEFAULT_CENTER, 12);
      map.attributionControl.setPrefix(false);
      map.createPane('rmLabels').style.zIndex = '350';
      map.getPane('rmLabels')!.classList.add('rm-labels-pane');
      const b = BASEMAPS.night;
      const common = { noWrap: true, bounds: WORLD, maxZoom: 19, maxNativeZoom: b.maxNative, crossOrigin: true } as const;
      L.tileLayer(b.base, { ...common, attribution: b.attr }).addTo(map);
      L.tileLayer(b.labels, { ...common, pane: 'rmLabels' }).addTo(map);
      layer = L.layerGroup().addTo(map);
      map.on('click', (e: Leaflet.LeafletMouseEvent) => {
        if (placeOnClick && !readonly) onPlace?.(e.latlng.lat, e.latlng.lng);
      });
      ro = new ResizeObserver(() => map?.invalidateSize());
      ro.observe(el);
      ready = true;
    });
    return () => {
      disposed = true;
      ro?.disconnect();
      map?.remove();
    };
  });

  const pinHtml = (p: NonNullable<typeof point>) =>
    `<div class="rm-marker rm-pin ${p.source === 'geocoder' ? 'geo' : 'hand'}${p.approximate ? ' approx' : ''}"><span class="rm-pin-dot"></span></div>`;

  function draw() {
    if (!map || !layer) return;
    layer.clearLayers();
    const pts: [number, number][] = [];
    if (reference) {
      pts.push([reference.lat, reference.lon]);
      L.circleMarker([reference.lat, reference.lon], { radius: 9, color: '#9baec2', weight: 2, dashArray: '3 4', fillOpacity: 0, interactive: true })
        .bindTooltip('Current saved position', { className: 'rm-tip', direction: 'top' })
        .addTo(layer);
    }
    candidates.forEach((c, i) => {
      if (point) return; // once one is in use, the others are no longer candidates
      pts.push([c.lat, c.lon]);
      L.marker([c.lat, c.lon], {
        icon: L.divIcon({ className: 'rm-icon', html: `<div class="rm-marker rm-cand">${i + 1}</div>`, iconSize: [0, 0] }),
        title: `${i + 1}. ${c.label}${c.detail ? ', ' + c.detail : ''}`, alt: `Candidate ${i + 1}: ${c.label}`, keyboard: true
      })
        .bindTooltip(`<b>${i + 1}. ${esc(c.label)}</b>${c.detail ? `<br><span class="sub">${esc(c.detail)}</span>` : ''}<br><span class="sub">Click to use this location</span>`, { className: 'rm-tip', direction: 'top', offset: [0, -14] })
        .on('click', () => onChoose?.(c.id))
        .addTo(layer!);
    });
    if (point) {
      pts.push([point.lat, point.lon]);
      const label = point.source === 'dispatcher_map' ? 'Location placed by dispatcher' : point.source === 'external' ? 'Location from external source' : 'Matched location';
      L.marker([point.lat, point.lon], {
        icon: L.divIcon({ className: 'rm-icon', html: pinHtml(point), iconSize: [0, 0] }),
        draggable: !readonly, title: label + (readonly ? '' : '. Drag to adjust.'), alt: label, keyboard: !readonly, zIndexOffset: 1000
      })
        .on('dragend', (e) => {
          const ll = (e.target as Leaflet.Marker).getLatLng();
          onPlace?.(ll.lat, ll.lng);
        })
        .addTo(layer);
    }
    return pts;
  }

  // Refit only when what is shown changes (a new result, a chosen candidate, a placed marker), never on every redraw.
  let lastKey = '';
  $effect(() => {
    void [point, candidates, reference, ready];
    untrack(() => {
      const pts = draw();
      if (!pts || !map) return;
      const key = JSON.stringify([point?.lat, point?.lon, reference?.lat, reference?.lon, candidates.map((c) => c.id)]);
      if (key === lastKey) return;
      lastKey = key;
      if (pts.length === 1) map.setView(pts[0], Math.max(map.getZoom(), 16), { animate: true });
      else if (pts.length > 1) map.fitBounds(L.latLngBounds(pts), { padding: [40, 40], maxZoom: 16 });
    });
  });
</script>

<div class="relative h-full min-h-56 w-full overflow-hidden rounded-md border border-line-strong">
  <div bind:this={el} class="rm-map h-full w-full" role="application" aria-label="Location map. Use the controls beside it to enter or confirm the location; the map can also be used to place or adjust a marker."></div>
  {#if placeOnClick && !readonly && !point}
    <p class="pointer-events-none absolute top-2 left-1/2 z-[1000] -translate-x-1/2 rounded border border-line-strong bg-panel/95 px-2 py-1 text-xs text-muted">Click the map to place the location</p>
  {/if}
</div>
