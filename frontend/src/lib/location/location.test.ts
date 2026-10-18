import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  acceptIncoming, applyError, applyResults, choose, clearPoint, currentPoint, describeConfidence, differenceNote, dismissIncoming,
  initialState, placeManual, receiveExternal, reliability, saveBlocker, setMethod, setQuery, toApiLocation, type Candidate
} from './state';
import { photonKind, photonToCandidate, searchPlaces } from './geocode';

const cand = (over: Partial<Candidate> = {}): Candidate => ({
  id: 'a', label: '412 Pine St', detail: 'Seattle', lat: 47.6113, lon: -122.3374, kind: 'address', source: 'geocoder', ...over
});
const typed = (q: string, method: 'address' | 'landmark' = 'address') => setQuery(setMethod(initialState(), method), q);

describe('address resolved to one result', () => {
  it('is usable and verified, and keeps the dispatcher text as the address', () => {
    const s = applyResults(typed('412 Pine St'), [cand()]);
    expect(s.phase).toBe('resolved');
    expect(reliability(s)).toBe('verified');
    expect(saveBlocker(s)).toBeUndefined();
    expect(toApiLocation(s)).toEqual({ address: '412 Pine St, Seattle, WA', point: { lat: 47.6113, lon: -122.3374 }, geocodeConfidence: 0.9 });
  });

  it('adds apartment/floor details to the saved address', () => {
    let s = applyResults(typed('412 Pine St, Seattle'), [cand()]);
    s = { ...s, details: 'Apt 4B, floor 2' };
    expect(toApiLocation(s)?.address).toBe('412 Pine St, Seattle, Apt 4B, floor 2');
  });
});

describe('ambiguous results', () => {
  const two = [cand({ id: 'a' }), cand({ id: 'b', detail: 'Tacoma', lat: 47.25, lon: -122.44 })];

  it('never picks one for the dispatcher and blocks saving', () => {
    const s = applyResults(typed('100 Main St'), two);
    expect(s.phase).toBe('ambiguous');
    expect(s.chosen).toBeNull();
    expect(currentPoint(s)).toBeNull();
    expect(reliability(s)).toBe('ambiguous');
    expect(toApiLocation(s)).toBeNull();
    expect(saveBlocker(s)).toMatch(/Choose one/);
  });

  it('becomes usable and verified once the dispatcher chooses', () => {
    const s = choose(applyResults(typed('100 Main St'), two), 'b');
    expect(currentPoint(s)).toMatchObject({ lat: 47.25, source: 'geocoder' });
    expect(reliability(s)).toBe('verified');
    expect(saveBlocker(s)).toBeUndefined();
  });

  it('drops duplicate results', () => {
    expect(applyResults(typed('x'), [cand(), cand()]).phase).toBe('resolved');
  });
});

describe('unresolved and failed lookups', () => {
  it('no results: unresolved, nothing to save, typed text kept', () => {
    const s = applyResults(typed('asdf qwer'), []);
    expect(s.phase).toBe('unresolved');
    expect(s.query).toBe('asdf qwer');
    expect(reliability(s)).toBe('unresolved');
    expect(toApiLocation(s)).toBeNull();
  });

  it('lookup error keeps the entered text and offers the map as a way forward', () => {
    const s = applyError(typed('412 Pine St'), 'Location service is unreachable');
    expect(s.query).toBe('412 Pine St');
    expect(saveBlocker(s)).toMatch(/lookup failed/i);
    expect(toApiLocation(placeManual(s, 47.6, -122.3))).not.toBeNull();
  });

  it('typing invalidates an earlier result and blocks saving until the new lookup finishes', () => {
    const s = setQuery(applyResults(typed('412 Pine'), [cand()]), '412 Pine St');
    expect(s.chosen).toBeNull();
    expect(s.phase).toBe('resolving');
    expect(saveBlocker(s)).toMatch(/in progress/);
  });
});

describe('manual map placement', () => {
  it('is approximate, carries no geocode score, and gets a clear address when nothing was typed', () => {
    const s = placeManual(setMethod(initialState(), 'map'), 47.61, -122.33);
    expect(reliability(s)).toBe('approximate');
    expect(toApiLocation(s)).toEqual({ address: 'Map position set by dispatcher', point: { lat: 47.61, lon: -122.33 } });
  });

  it('dragging a geocoded result turns it into a dispatcher placement', () => {
    const s = placeManual(applyResults(typed('412 Pine St'), [cand()]), 47.62, -122.34);
    expect(currentPoint(s)).toMatchObject({ lat: 47.62, source: 'dispatcher_map' });
    expect(toApiLocation(s)?.geocodeConfidence).toBeUndefined();
    expect(reliability(s)).toBe('approximate');
  });

  it('a landmark entry stays approximate and carries a low confidence even if the match is address-level', () => {
    const s = applyResults(typed('Pike Place Market', 'landmark'), [cand({ kind: 'address' })]);
    expect(reliability(s)).toBe('approximate');
    expect(toApiLocation(s)?.geocodeConfidence).toBeLessThan(0.8);
  });

  it('a landmark or street match is approximate, not verified', () => {
    expect(reliability(applyResults(typed('Pike Place Market', 'landmark'), [cand({ kind: 'place' })]))).toBe('approximate');
    expect(reliability(applyResults(typed('Pine St'), [cand({ kind: 'street' })]))).toBe('approximate');
  });
});

describe('unknown location', () => {
  it('has no position and cannot be saved with the current contract', () => {
    const s = setMethod(initialState(), 'unknown');
    expect(currentPoint(s)).toBeNull();
    expect(toApiLocation(s)).toBeNull();
    expect(reliability(s)).toBe('pending');
    expect(saveBlocker(s)).toMatch(/requires a map position/);
  });

  it('never invents coordinates even if a position existed before switching', () => {
    const s = setMethod(applyResults(typed('412 Pine St'), [cand()]), 'unknown');
    expect(toApiLocation(s)).toBeNull();
  });

  it('switching method clears results of the previous method', () => {
    const s = setMethod(applyResults(typed('412 Pine St'), [cand()]), 'map');
    expect(s.chosen).toBeNull();
    expect(clearPoint(s).manual).toBeNull();
  });
});

describe('location updates from another source', () => {
  const update = { source: 'Wireless caller location', label: 'Near 5th & Pine', lat: 47.612, lon: -122.335, receivedAt: 1 };

  it('does not replace a confirmed location: it waits as incoming', () => {
    const confirmed = choose(applyResults(typed('100 Main'), [cand({ id: 'a' }), cand({ id: 'b', lat: 1, lon: 2 })]), 'a');
    const s = receiveExternal(confirmed, update);
    expect(currentPoint(s)).toMatchObject({ lat: 47.6113, source: 'geocoder' });
    expect(s.incoming?.source).toBe('Wireless caller location');
  });

  it('accepting applies it and labels it as an external, approximate position', () => {
    const s = acceptIncoming(receiveExternal(applyResults(typed('412 Pine St'), [cand()]), update));
    expect(currentPoint(s)).toMatchObject({ lat: 47.612, source: 'external' });
    expect(reliability(s)).toBe('approximate');
    expect(s.incoming).toBeNull();
    expect(toApiLocation(s)?.geocodeConfidence).toBeUndefined();
  });

  it('dismissing keeps the dispatcher location untouched', () => {
    const s = dismissIncoming(receiveExternal(placeManual(initialState(), 47.6, -122.3), update));
    expect(currentPoint(s)).toMatchObject({ lat: 47.6 });
    expect(s.incoming).toBeNull();
  });
});

describe('caller wording versus the matched address', () => {
  it('abbreviations and punctuation are not differences', () => {
    expect(differenceNote(applyResults(typed('412 Pine St'), [cand({ label: '412 Pine Street' })]))).toBeNull();
    expect(differenceNote(applyResults(typed('412 pine st., Seattle'), [cand({ label: '412 Pine Street' })]))).toBeNull();
  });
  it('flags an added direction, a different number or another street type', () => {
    expect(differenceNote(applyResults(typed('412 Pine St'), [cand({ label: '412 East Pine Street' })]))).toMatch(/East Pine Street/);
    expect(differenceNote(applyResults(typed('412 Pine St'), [cand({ label: '421 Pine Street' })]))).not.toBeNull();
    expect(differenceNote(applyResults(typed('412 Pine St'), [cand({ label: '412 Pine Avenue' })]))).not.toBeNull();
  });
  it('says nothing for landmarks, manual placement, or when nothing was typed', () => {
    expect(differenceNote(applyResults(typed('Pike Place Market', 'landmark'), [cand({ label: 'Pike Place Market', kind: 'place' })]))).toBeNull();
    expect(differenceNote(placeManual(applyResults(typed('412 Pine St'), [cand({ label: '412 East Pine Street' })]), 1, 2))).toBeNull();
    expect(differenceNote(applyResults(typed(''), [cand()]))).toBeNull();
  });
});

describe('describeConfidence', () => {
  it('reports unrecorded sources honestly', () => {
    expect(describeConfidence(undefined).key).toBe('unrecorded');
    expect(describeConfidence(0.9).key).toBe('matched');
    expect(describeConfidence(0.7).key).toBe('approximate');
    expect(describeConfidence(0.2).key).toBe('low');
  });
});

describe('geocoder adapter', () => {
  afterEach(() => vi.unstubAllGlobals());
  const feature = (props: Record<string, unknown>, coords: [number, number] = [-122.3374, 47.6113]) => ({ geometry: { coordinates: coords }, properties: props });

  it('classifies Photon results', () => {
    expect(photonKind({ housenumber: '412', street: 'Pine Street' })).toBe('address');
    expect(photonKind({ type: 'street', name: 'Pine Street' })).toBe('street');
    expect(photonKind({ type: 'city', name: 'Seattle' })).toBe('area');
    expect(photonKind({ type: 'house', name: 'Pike Place Market', osm_key: 'amenity' })).toBe('place');
    // a named business with a house number is a place, not an exact address
    expect(photonKind({ housenumber: '85', street: 'Pike Place', name: 'Pike Place Market', osm_key: 'amenity' })).toBe('place');
    expect(photonKind({ housenumber: '412', street: 'Pine Street', name: '', osm_key: 'place', osm_value: 'house' })).toBe('address');
    expect(photonToCandidate(feature({ housenumber: '412', street: 'Pine Street', city: 'Seattle', state: 'Washington', osm_id: 1, osm_type: 'N' }))).toMatchObject({
      label: '412 Pine Street', detail: 'Seattle, Washington', kind: 'address', lat: 47.6113, lon: -122.3374
    });
    expect(photonToCandidate(feature({}))).toBeNull(); // nothing to show the dispatcher
  });

  it('searchPlaces returns candidates and adds the search area to the query', async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ features: [feature({ housenumber: '412', street: 'Pine Street', city: 'Seattle' })] }) });
    vi.stubGlobal('fetch', fetchMock);
    const out = await searchPlaces('412 Pine St', 'Seattle, WA');
    expect(out).toHaveLength(1);
    expect(decodeURIComponent(fetchMock.mock.calls[0][0] as string)).toContain('q=412 Pine St, Seattle, WA');
  });

  it('turns network and HTTP failures into readable errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));
    await expect(searchPlaces('x', '')).rejects.toThrow('unreachable');
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 429, json: async () => ({}) }));
    await expect(searchPlaces('x', '')).rejects.toThrow('HTTP 429');
  });
});
