// Browser-side location lookup used until the backend offers location resolution.
//
// This is a temporary adapter, not part of the API contract:
//  - Addresses, businesses, parks, highways and other places: Photon (OpenStreetMap data), a typeahead-friendly
//    public geocoder. It sends what the dispatcher types to a third-party service. That is acceptable for synthetic demo data only;
// real incidents need backend-side (or self-hosted) resolution. Everything returns `Candidate[]`, so the
// backend can replace this module without touching the UI.
import { DEFAULT_CENTER } from '$lib/map/basemaps';
import type { Candidate, CandidateKind } from './state';

const PHOTON = 'https://photon.komoot.io';
const TIMEOUT_MS = 12_000;

async function getJson(url: string, init: RequestInit | undefined, signal?: AbortSignal): Promise<unknown> {
  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(new DOMException('Lookup timed out', 'TimeoutError')), TIMEOUT_MS);
  const onAbort = () => ctl.abort(signal?.reason);
  signal?.addEventListener('abort', onAbort);
  try {
    const res = await fetch(url, { ...init, signal: ctl.signal });
    if (!res.ok) throw new Error(`Location service returned HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    if (signal?.aborted) throw e; // superseded by a newer lookup: the caller ignores this
    if (ctl.signal.aborted) throw new Error('Location lookup timed out');
    if (e instanceof TypeError) throw new Error('Location service is unreachable');
    throw e;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener('abort', onAbort);
  }
}

interface PhotonFeature {
  geometry: { coordinates: [number, number] };
  properties: {
    name?: string; housenumber?: string; street?: string; district?: string; locality?: string; city?: string;
    state?: string; type?: string; osm_key?: string; osm_value?: string; osm_id?: number; osm_type?: string;
    extent?: [number, number, number, number];
  };
}

const AREA_TYPES = new Set(['city', 'district', 'locality', 'county', 'state', 'country', 'other', 'suburb', 'neighbourhood']);

export function photonKind(p: PhotonFeature['properties']): CandidateKind {
  // A bare house (OSM place=house) or an unnamed numbered feature is an address. A named business, park or
  // building that happens to carry a house number is a place: its point is not a street address.
  if (p.housenumber && (p.osm_key === 'place' || !p.name)) return 'address';
  if (p.housenumber) return 'place';
  if (p.type === 'street' || (p.osm_key === 'highway' && p.name)) return 'street';
  if (p.type && AREA_TYPES.has(p.type)) return 'area';
  return 'place';
}

export function photonToCandidate(f: PhotonFeature): Candidate | null {
  const p = f.properties;
  const [lon, lat] = f.geometry.coordinates;
  if (typeof lat !== 'number' || typeof lon !== 'number') return null;
  const kind = photonKind(p);
  const label = kind === 'address' ? [p.housenumber, p.street].filter(Boolean).join(' ') || p.name || '' : p.name || p.street || '';
  if (!label) return null;
  const street = kind === 'place' && p.housenumber ? [p.housenumber, p.street].filter(Boolean).join(' ') : undefined;
  const detail = [street, p.district ?? p.locality, p.city, p.state].filter((x, i, a) => x && a.indexOf(x) === i).join(', ');
  return { id: `${p.osm_type ?? 'x'}${p.osm_id ?? `${lat},${lon}`}`, label, detail: detail || undefined, lat, lon, kind, source: 'geocoder' };
}

const mentionsRegion = (q: string, region: string) => q.toLowerCase().includes(region.split(',')[0].trim().toLowerCase());

/** Addresses, businesses, parks, highways and other named places. */
export async function searchPlaces(query: string, region: string, signal?: AbortSignal): Promise<Candidate[]> {
  const q = query.trim();
  const r = region.trim();
  const full = r && !mentionsRegion(q, r) ? `${q}, ${r}` : q;
  const url = `${PHOTON}/api/?limit=6&lang=en&lat=${DEFAULT_CENTER[0]}&lon=${DEFAULT_CENTER[1]}&q=${encodeURIComponent(full)}`;
  const data = (await getJson(url, undefined, signal)) as { features?: PhotonFeature[] };
  return (data.features ?? []).map(photonToCandidate).filter((c): c is Candidate => !!c);
}
