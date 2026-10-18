// Pure state logic for dispatcher location entry. No UI, no network: everything here is unit tested.
//
// Rules this module enforces:
//  - An ambiguous lookup never picks a result for the dispatcher: a candidate must be chosen explicitly.
//  - A dispatcher-confirmed or dispatcher-placed location is never replaced silently: location updates from
//    another source (for example a future automated caller-location feed) only appear as `incoming`
//    until the dispatcher accepts or dismisses them.
//  - Coordinates exist only when a geocoder result or the dispatcher's own map click supplied them. There is
//    no fallback position, and "unknown" never produces one.

export type Method = 'address' | 'landmark' | 'map' | 'unknown';
export type CandidateKind = 'address' | 'place' | 'street' | 'area';
export type Phase = 'idle' | 'resolving' | 'resolved' | 'ambiguous' | 'unresolved' | 'error';
export type Reliability = 'verified' | 'approximate' | 'ambiguous' | 'unresolved' | 'pending';

export interface Candidate {
  id: string;
  label: string;
  /** Neighborhood / city, to tell similar candidates apart. */
  detail?: string;
  lat: number;
  lon: number;
  kind: CandidateKind;
  /** 'geocoder' for lookup results; any other value names an external location feed. */
  source: string;
}

export interface ExternalUpdate {
  /** Human-readable name of the feed, for example "Wireless caller location". */
  source: string;
  label: string;
  lat: number;
  lon: number;
  receivedAt: number;
}

export interface LocationState {
  method: Method;
  /** What the dispatcher typed, kept verbatim: it is saved as the incident address. */
  query: string;
  region: string;
  /** Apartment, floor, suite and similar. */
  details: string;
  phase: Phase;
  error: string;
  candidates: Candidate[];
  chosen: Candidate | null;
  /** A marker the dispatcher placed or dragged. Takes precedence over `chosen`. */
  manual: { lat: number; lon: number } | null;
  /** True once the dispatcher chose, placed or adjusted the position themselves. */
  confirmed: boolean;
  incoming: ExternalUpdate | null;
}

export const DEFAULT_REGION = 'Seattle, WA';

export function initialState(): LocationState {
  return {
    method: 'address', query: '', region: DEFAULT_REGION, details: '', phase: 'idle', error: '',
    candidates: [], chosen: null, manual: null, confirmed: false, incoming: null
  };
}

const clearResult = (s: LocationState): LocationState => ({
  ...s, phase: 'idle', error: '', candidates: [], chosen: null, manual: null, confirmed: false
});

export function setMethod(s: LocationState, method: Method): LocationState {
  if (method === s.method) return s;
  return { ...clearResult(s), method };
}

/** The dispatcher edited the text: any earlier result no longer describes it. */
export function setQuery(s: LocationState, query: string): LocationState {
  if (query === s.query) return s;
  const typed = query.trim().length > 0;
  return { ...clearResult(s), query, phase: typed ? 'resolving' : 'idle' };
}

export function startResolving(s: LocationState): LocationState {
  return { ...s, phase: 'resolving', error: '' };
}

export function applyResults(s: LocationState, found: Candidate[]): LocationState {
  const seen = new Set<string>();
  const candidates = found.filter((c) => {
    const key = `${c.label}|${c.detail ?? ''}|${c.lat.toFixed(4)}|${c.lon.toFixed(4)}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (candidates.length === 0) return { ...s, phase: 'unresolved', error: '', candidates: [], chosen: null, manual: null, confirmed: false };
  if (candidates.length === 1) return { ...s, phase: 'resolved', error: '', candidates, chosen: candidates[0], manual: null, confirmed: false };
  return { ...s, phase: 'ambiguous', error: '', candidates, chosen: null, manual: null, confirmed: false };
}

/** A failed lookup keeps everything the dispatcher entered. */
export function applyError(s: LocationState, message: string): LocationState {
  return { ...s, phase: 'error', error: message, candidates: [], chosen: null, manual: null, confirmed: false };
}

export function choose(s: LocationState, id: string): LocationState {
  const c = s.candidates.find((x) => x.id === id);
  if (!c) return s;
  return { ...s, phase: 'resolved', chosen: c, manual: null, confirmed: true };
}

export function placeManual(s: LocationState, lat: number, lon: number): LocationState {
  return { ...s, phase: 'resolved', error: '', manual: { lat, lon }, confirmed: true };
}

/** Drop the current position (keeps the typed text). */
export function clearPoint(s: LocationState): LocationState {
  return { ...s, phase: 'idle', error: '', candidates: [], chosen: null, manual: null, confirmed: false };
}

export function receiveExternal(s: LocationState, update: ExternalUpdate): LocationState {
  return { ...s, incoming: update };
}

export function dismissIncoming(s: LocationState): LocationState {
  return { ...s, incoming: null };
}

export function acceptIncoming(s: LocationState): LocationState {
  const u = s.incoming;
  if (!u) return s;
  const chosen: Candidate = { id: `ext-${u.receivedAt}`, label: u.label, lat: u.lat, lon: u.lon, kind: 'place', source: u.source };
  return { ...s, phase: 'resolved', error: '', candidates: [chosen], chosen, manual: null, confirmed: true, incoming: null };
}

export type PointSource = 'dispatcher_map' | 'geocoder' | 'external';

export function currentPoint(s: LocationState): { lat: number; lon: number; source: PointSource } | null {
  if (s.method === 'unknown') return null;
  if (s.manual) return { ...s.manual, source: 'dispatcher_map' };
  if (s.chosen) return { lat: s.chosen.lat, lon: s.chosen.lon, source: s.chosen.source === 'geocoder' ? 'geocoder' : 'external' };
  return null;
}

export function reliability(s: LocationState): Reliability {
  if (s.method === 'unknown') return 'pending';
  const p = currentPoint(s);
  if (!p) return s.phase === 'ambiguous' ? 'ambiguous' : 'unresolved';
  if (p.source !== 'geocoder') return 'approximate';
  if (s.method === 'landmark') return 'approximate'; // a landmark description is never an exact destination
  return s.chosen!.kind === 'address' && (s.confirmed || s.candidates.length === 1) ? 'verified' : 'approximate';
}

export const RELIABILITY_TEXT: Record<Reliability, { label: string; hint: string }> = {
  verified: { label: 'Verified match', hint: 'An exact address was matched. Check it on the map.' },
  approximate: { label: 'Approximate', hint: 'Placed by hand or matched to a place or street only. Not an exact destination.' },
  ambiguous: { label: 'Ambiguous', hint: 'Several locations match. Choose one; none has been selected for you.' },
  unresolved: { label: 'Unresolved', hint: 'No location yet. Refine the description or place a marker on the map.' },
  pending: { label: 'Not yet determined', hint: 'Location is still being obtained.' }
};

/** Why the incident cannot be saved yet, or undefined when it can. */
export function saveBlocker(s: LocationState): string | undefined {
  if (s.method === 'unknown') {
    return 'The approved API requires a map position for every incident, so an incident with an unknown location cannot be saved yet. Your entries are kept in this tab.';
  }
  if (s.phase === 'resolving') return 'Location lookup in progress.';
  if (currentPoint(s)) return undefined;
  if (s.phase === 'ambiguous') return 'Choose one of the matching locations, or place a marker on the map.';
  if (s.phase === 'error') return 'Location lookup failed. Retry, or place a marker on the map.';
  return 'Enter a location and choose a match, or place a marker on the map.';
}

/** Heuristic precision tiers sent as `geocodeConfidence` (0..1). These are not provider scores. */
const TIER: Record<CandidateKind, number> = { address: 0.9, place: 0.7, street: 0.4, area: 0.2 };

export interface ApiLocation {
  address?: string;
  point: { lat: number; lon: number };
  geocodeConfidence?: number;
}

/** The only place coordinates leave this module: the approved `Location` shape, or null when there is no position. */
export function toApiLocation(s: LocationState): ApiLocation | null {
  const p = currentPoint(s);
  if (!p) return null;
  const q = s.query.trim();
  const region = s.region.trim();
  const base = q ? (region && s.method !== 'map' && !q.toLowerCase().includes(region.split(',')[0].toLowerCase()) ? `${q}, ${region}` : q) : '';
  const details = s.details.trim();
  let address = [base || (p.source === 'dispatcher_map' ? 'Map position set by dispatcher' : ''), details].filter(Boolean).join(', ');
  if (address.length > 300) address = address.slice(0, 300);
  const out: ApiLocation = { point: { lat: p.lat, lon: p.lon } };
  if (address) out.address = address;
  // Manual placement and external feeds carry no geocode score: omitting it is the only honest signal the contract allows.
  if (p.source === 'geocoder' && s.chosen) out.geocodeConfidence = s.method === 'landmark' ? Math.min(TIER[s.chosen.kind], TIER.place) : TIER[s.chosen.kind];
  return out;
}

const WORD: Record<string, string> = {
  st: 'street', ave: 'avenue', av: 'avenue', blvd: 'boulevard', rd: 'road', dr: 'drive', ln: 'lane', ct: 'court', pl: 'place',
  pkwy: 'parkway', hwy: 'highway', ter: 'terrace', n: 'north', s: 'south', e: 'east', w: 'west', ne: 'northeast', nw: 'northwest',
  se: 'southeast', sw: 'southwest'
};
const words = (t: string) => t.toLowerCase().replace(/[^a-z0-9\s]/g, ' ').split(/\s+/).filter(Boolean).map((w) => WORD[w] ?? w).sort().join(' ');

/**
 * When a geocoder matched a street address that does not read the same as what the caller reported (a direction added,
 * a different street type, another number), says so. Abbreviations ("St" / "Street") are not differences.
 */
export function differenceNote(s: LocationState): string | null {
  const c = s.chosen;
  const typed = s.query.split(',')[0].trim();
  if (s.method !== 'address' || s.manual || !c || c.source !== 'geocoder' || c.kind !== 'address' || !typed) return null;
  return words(typed) === words(c.label) ? null : `Matched "${c.label}" for the reported "${typed}". The caller's wording and the map match differ.`;
}

/** Describes a saved `geocodeConfidence` (the only provenance the contract stores for a position). */
export function describeConfidence(c: number | null | undefined): { key: 'unrecorded' | 'matched' | 'approximate' | 'low'; label: string } {
  if (c == null) return { key: 'unrecorded', label: 'Source not recorded' };
  if (c >= 0.8) return { key: 'matched', label: 'Address-level match' };
  if (c >= 0.5) return { key: 'approximate', label: 'Approximate: place' };
  return { key: 'low', label: 'Approximate: low confidence' };
}

/** Positions to draw as approximate on the dispatch map. */
export const isApproximate = (c: number | null | undefined): boolean => c != null && c < 0.8;
