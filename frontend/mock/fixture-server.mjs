// Stateful fixture server for UI work: fixed, contract-shaped synthetic data (fictional Seattle-area county)
// plus a working /events/stream. Unlike Prism it returns the same data on every request, so the dispatch
// screen is predictable. Start it with `npm run mock:fixtures`, then run the UI with
// VITE_API_TARGET=http://localhost:4011.
//
// It serves only operations that exist in contracts/openapi.json and does not define any new API surface.
import http from 'node:http';
import crypto from 'node:crypto';

const PORT = Number(process.env.PORT ?? 4011);
const uuid = () => crypto.randomUUID();
const fid = (n) => `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`;
const iso = (msAgo = 0) => new Date(Date.now() - msAgo).toISOString();
const MIN = 60_000;

const provenance = () => ({
  dataOrigin: 'synthetic', sourceSystem: 'frontend-fixture-server', sourceTimestamp: iso(), retrievedAt: iso(),
  verificationStatus: 'verified', stale: false
});

// ---------- seed ----------
const mkIncident = (n, priority, status, category, narrative, address, lat, lon, mins, confirmed = true) => ({
  incidentId: fid(n), number: `INC-2026-${String(100 + n).padStart(6, '0')}`, status, priority,
  priorityConfirmed: confirmed, confirmedCategory: category, narrative, version: 1,
  location: { address, point: { lat, lon } }, createdAt: iso(mins * MIN), updatedAt: iso(), provenance: provenance()
});

const incidents = [
  mkIncident(1, 'P1', 'pending_dispatch', 'structure_fire', 'Reported fire on 3rd floor, residents evacuating, smoke visible from street.', '412 Pine St', 47.6135, -122.3335, 9),
  mkIncident(2, 'P1', 'recommendation_ready', 'medical_cardiac', 'Adult male collapsed, not breathing, bystander CPR in progress.', '1200 4th Ave', 47.6089, -122.3358, 4),
  mkIncident(3, 'P2', 'pending_dispatch', 'traffic_collision', 'Two-vehicle collision, one driver trapped, fuel leaking.', 'I-5 NB at Mercer', 47.6247, -122.3302, 14, false),
  mkIncident(4, 'P2', 'intake', null, 'Caller reports strong gas odor in apartment hallway.', '88 Denny Way', 47.6183, -122.3409, 2, false),
  mkIncident(5, 'P3', 'dispatched_simulated', 'medical_other', 'Elderly female fall, conscious, hip pain.', '305 Broadway', 47.6065, -122.3213, 26),
  mkIncident(6, 'P3', 'pending_dispatch', 'burglary', 'Burglary alarm, no occupants expected.', '900 Madison St', 47.6098, -122.3262, 31),
  mkIncident(7, 'P4', 'pending_dispatch', 'welfare_check', 'Welfare check requested by relative.', '77 Yesler Way', 47.602, -122.3345, 48),
  mkIncident(8, 'P2', 'on_scene_simulated', 'hazmat', 'Unknown liquid spill, mild odor, area cordoned.', 'Pier 56', 47.605, -122.3405, 52),
  mkIncident(9, 'P3', 'pending_dispatch', 'medical_trauma', 'Cyclist struck, minor injuries.', 'Westlake Ave & 7th', 47.619, -122.3375, 6),
  mkIncident(10, 'P1', 'pending_dispatch', 'vehicle_fire', 'Vehicle fire under overpass.', 'SR-99 at Battery St', 47.617, -122.347, 1),
  mkIncident(11, 'P3', 'resolved', 'other', 'Nuisance call, resolved on scene.', 'Pike Pl', 47.6097, -122.3425, 120)
];

const mkUnit = (n, callSign, discipline, unitType, status, lat, lon) => ({
  unitId: fid(1000 + n), agencyId: fid(9000 + (discipline === 'fire' ? 1 : discipline === 'ems' ? 2 : 3)),
  callSign, discipline, unitType, status, location: { lat, lon }, minimumCrew: 2, version: 1, provenance: provenance()
});

const units = [
  mkUnit(1, 'E1', 'fire', 'Engine Type 1', 'available', 47.6076, -122.335),
  mkUnit(2, 'E2', 'fire', 'Engine Type 1', 'available', 47.62, -122.35),
  mkUnit(3, 'E3', 'fire', 'Engine Type 1', 'en_route_simulated', 47.614, -122.33),
  mkUnit(4, 'L1', 'fire', 'Ladder', 'available', 47.602, -122.333),
  mkUnit(5, 'M1', 'ems', 'ALS Ambulance', 'available', 47.611, -122.329),
  mkUnit(6, 'M2', 'ems', 'ALS Ambulance', 'on_scene_simulated', 47.6065, -122.3215),
  mkUnit(7, 'M3', 'ems', 'BLS Ambulance', 'out_of_service', 47.615, -122.34),
  mkUnit(8, 'HZ1', 'fire', 'Hazmat', 'on_scene_simulated', 47.6052, -122.3402),
  mkUnit(9, 'M4', 'ems', 'BLS Ambulance', 'returning', 47.623, -122.32),
  mkUnit(10, 'P7', 'police', 'Patrol', 'available', 47.6101, -122.334),
  mkUnit(11, 'P8', 'police', 'Patrol', 'off_duty', 47.6001, -122.33)
];

const facilities = [
  { facilityId: fid(2001), agencyId: fid(9001), name: 'Station 1', type: 'fire_station', location: { lat: 47.604, lon: -122.333 }, provenance: provenance() },
  { facilityId: fid(2002), agencyId: fid(9002), name: 'Harborview Medical', type: 'hospital', location: { lat: 47.604, lon: -122.324 }, provenance: provenance() },
  { facilityId: fid(2003), agencyId: fid(9003), name: 'Central Precinct', type: 'police_station', location: { lat: 47.599, lon: -122.329 }, provenance: provenance() }
];

const POLICY_ID = fid(3001);
const DISCIPLINE_FOR = {
  structure_fire: 'fire', vehicle_fire: 'fire', wildland_fire: 'fire', hazmat: 'fire', technical_rescue: 'fire', traffic_collision: 'fire',
  medical_cardiac: 'ems', medical_trauma: 'ems', medical_other: 'ems',
  assault: 'police', burglary: 'police', domestic_disturbance: 'police', welfare_check: 'police', other: 'police'
};
const REQUIREMENTS = {
  fire: [
    { capabilityId: 'pump_engine', label: 'Engine company', kind: 'apparatus', quantity: 2, mandatory: true, origin: 'rule_based' },
    { capabilityId: 'ladder', label: 'Ladder truck', kind: 'apparatus', quantity: 1, mandatory: true, origin: 'rule_based' },
    { capabilityId: 'als', label: 'ALS ambulance', kind: 'apparatus', mandatory: false, origin: 'ml_suggested' }
  ],
  ems: [{ capabilityId: 'als', label: 'ALS ambulance', kind: 'apparatus', quantity: 1, mandatory: true, origin: 'rule_based' }],
  police: [{ capabilityId: 'patrol', label: 'Patrol unit', kind: 'apparatus', quantity: 1, mandatory: true, origin: 'rule_based' }]
};

const haversineKm = (a, b) => {
  const r = (d) => (d * Math.PI) / 180;
  const h = Math.sin(r(b.lat - a.lat) / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(r(b.lon - a.lon) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
};

function buildRecommendation(incident) {
  const need = DISCIPLINE_FOR[incident.confirmedCategory ?? 'other'] ?? 'police';
  const scored = units.map((u) => {
    const eta = u.location ? Math.round((haversineKm(u.location, incident.location.point) / 40) * 3600) : null; // 40 km/h straight-line
    const reasons = [];
    if (u.status !== 'available') reasons.push(`Unit status is ${u.status.replaceAll('_', ' ')}`);
    if (u.discipline !== need) reasons.push(`Discipline ${u.discipline} does not match the ${need} requirement`);
    return { u, eta, reasons };
  });
  const eligible = scored.filter((s) => !s.reasons.length).sort((a, b) => (a.eta ?? 1e9) - (b.eta ?? 1e9));
  const candidates = [
    ...eligible.map((s, i) => candidate(s, i + 1, true)),
    ...scored.filter((s) => s.reasons.length && s.u.discipline === need).map((s) => candidate(s, null, false))
  ];
  return {
    recommendationId: uuid(), incidentId: incident.incidentId, status: 'pending_review', stage: 'hard_eligibility', candidates,
    inputs: { unitSnapshotAt: iso(), requirementSetGeneratedAt: iso(), modelVersions: [] }, policyVersionId: POLICY_ID,
    limitations: ['Fixture data: ETA is a straight-line estimate at 40 km/h, not a routed drive time.'],
    createdAt: iso(), provenance: provenance(), humanConfirmationRequired: true
  };
}

function candidate({ u, eta, reasons }, rank, eligible) {
  return {
    unitId: u.unitId, callSign: u.callSign, eligible, rank, etaSeconds: eta, etaMethod: 'straight_line_fallback',
    checks: [{ capabilityId: 'apparatus_available', state: u.status === 'available' ? 'available' : 'unavailable', mandatory: true }],
    ineligibleReasons: reasons,
    rankingFactors: eligible ? [{ factor: 'Straight-line ETA', value: eta == null ? 'n/a' : `${Math.round(eta / 60)} min` }] : []
  };
}

const recommendations = new Map(); // incidentId -> latest
recommendations.set(incidents[1].incidentId, buildRecommendation(incidents[1]));

// ---------- events ----------
const clients = new Set();
let seq = 0;
const nextId = () => String(++seq).padStart(12, '0');
function emit(type, entityType, entityId, payload = {}) {
  const id = nextId();
  const env = { id, type, timestamp: iso(), entity: { type: entityType, id: entityId }, version: 1, schemaVersion: '1', payload };
  for (const res of clients) res.write(`id: ${id}\nevent: ${type}\ndata: ${JSON.stringify(env)}\n\n`);
}
setInterval(() => emit('stream.heartbeat', 'none', undefined), 15_000);

// ---------- http ----------
const send = (res, status, body) => {
  res.writeHead(status, { 'content-type': 'application/json' });
  res.end(JSON.stringify(body));
};
const fail = (res, status, code, message) => send(res, status, { code, message, requestId: uuid(), timestamp: iso() });
const validPoint = (p) => p && typeof p.lat === 'number' && typeof p.lon === 'number' && Math.abs(p.lat) <= 90 && Math.abs(p.lon) <= 180;
const list = (data) => ({ data, page: { nextCursor: null, limit: 100, totalEstimate: data.length } });

http
  .createServer((req, res) => {
    const chunks = [];
    req.on('data', (c) => chunks.push(c));
    req.on('end', () => {
      const path = new URL(req.url, 'http://x').pathname.replace(/^\/api\/v1/, '');
      let body = {};
      try { body = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : {}; } catch { return fail(res, 400, 'invalid_json', 'Body is not valid JSON'); }
      const m = (re) => path.match(re);
      let r;

      if (path === '/events/stream') {
        res.writeHead(200, { 'content-type': 'text/event-stream', 'cache-control': 'no-cache', connection: 'keep-alive' });
        res.write(': connected\n\n');
        clients.add(res);
        res.on('close', () => clients.delete(res)); // res, not req: req 'close' fires once the request body is read
        return;
      }
      if (path === '/incidents' && req.method === 'POST') {
        // Mirrors the contract: location.point is required, so an address alone is rejected.
        const pt = body.location?.point;
        if (!body.narrative || body.isSynthetic !== true) return fail(res, 400, 'validation_error', 'narrative and isSynthetic=true are required');
        if (!validPoint(pt)) return fail(res, 400, 'validation_error', 'location.point (lat, lon) is required');
        const n = incidents.length + 1;
        const inc = {
          incidentId: uuid(), number: `INC-2026-${String(100 + n).padStart(6, '0')}`, status: 'intake', priority: body.priority ?? 'unassigned',
          priorityConfirmed: false, confirmedCategory: null, narrative: body.narrative, version: 1, callbackNumber: body.callbackNumber,
          location: body.location, createdAt: iso(), updatedAt: iso(), provenance: { ...provenance(), dataOrigin: 'user_entered' }
        };
        incidents.push(inc);
        emit('incident.created', 'incident', inc.incidentId);
        return send(res, 201, inc);
      }
      if ((r = m(/^\/incidents\/([^/]+)$/)) && req.method === 'PATCH') {
        const inc = incidents.find((i) => i.incidentId === r[1]);
        if (!inc) return fail(res, 404, 'not_found', 'Incident not found');
        if (body.version !== inc.version) return fail(res, 409, 'conflict', 'Incident was changed by someone else; reload and retry');
        if (body.location) {
          if (!validPoint(body.location.point)) return fail(res, 400, 'validation_error', 'location.point (lat, lon) is required');
          inc.location = body.location;
        }
        for (const k of ['status', 'priority', 'priorityConfirmed', 'confirmedCategory', 'narrative', 'callbackNumber']) if (k in body) inc[k] = body[k];
        inc.version++;
        inc.updatedAt = iso();
        emit('incident.updated', 'incident', inc.incidentId);
        return send(res, 200, inc);
      }
      if (path === '/incidents' && req.method === 'GET') return send(res, 200, list(incidents));
      if (path === '/resources/units') return send(res, 200, list(units));
      if (path === '/resources/facilities') return send(res, 200, list(facilities));

      if ((r = m(/^\/incidents\/([^/]+)\/requirements$/))) {
        const inc = incidents.find((i) => i.incidentId === r[1]);
        if (!inc) return fail(res, 404, 'not_found', 'Incident not found');
        const need = DISCIPLINE_FOR[inc.confirmedCategory ?? 'other'] ?? 'police';
        return send(res, 200, { incidentId: inc.incidentId, policyVersionId: POLICY_ID, generatedAt: iso(), requirements: REQUIREMENTS[need] });
      }
      if ((r = m(/^\/incidents\/([^/]+)\/recommendations$/))) {
        const inc = incidents.find((i) => i.incidentId === r[1]);
        if (!inc) return fail(res, 404, 'not_found', 'Incident not found');
        if (req.method === 'POST') {
          const rec = buildRecommendation(inc);
          recommendations.set(inc.incidentId, rec);
          emit('recommendation.created', 'recommendation', rec.recommendationId);
          return send(res, 201, rec);
        }
        const rec = recommendations.get(inc.incidentId);
        return send(res, 200, list(rec ? [rec] : []));
      }
      if ((r = m(/^\/incidents\/([^/]+)\/classifications$/)) && req.method === 'POST') {
        const inc = incidents.find((i) => i.incidentId === r[1]);
        if (!inc) return fail(res, 404, 'not_found', 'Incident not found');
        const base = { classificationId: uuid(), incidentId: inc.incidentId, createdAt: iso(), policyVersionId: POLICY_ID };
        if (body.mode === 'infer') {
          return send(res, 201, { ...base, source: 'ml_inferred', category: 'structure_fire', suggestedPriority: 'P1', confidence: 0.91, modelVersion: 'fixture-1', status: 'suggested', limitations: ['Fixture suggestion, not a model output.'] });
        }
        inc.confirmedCategory = body.category; inc.priority = body.priority ?? inc.priority; inc.priorityConfirmed = true;
        inc.version++; inc.updatedAt = iso();
        emit('incident.updated', 'incident', inc.incidentId);
        return send(res, 201, { ...base, source: 'dispatcher_confirmed', category: body.category, suggestedPriority: body.priority, status: 'confirmed' });
      }
      if ((r = m(/^\/recommendations\/([^/]+)\/reviews$/)) && req.method === 'POST') {
        return send(res, 201, {
          reviewId: uuid(), recommendationId: r[1], reviewerId: fid(4001), decision: body.decision, selectedUnitIds: body.selectedUnitIds ?? [],
          explanation: body.explanation, overrideReason: body.overrideReason, isOverride: !!body.overrideReason, simulatedAssignment: true, createdAt: iso()
        });
      }
      if ((r = m(/^\/resources\/units\/([^/]+)\/status-events$/)) && req.method === 'POST') {
        const u = units.find((x) => x.unitId === r[1]);
        if (!u) return fail(res, 404, 'not_found', 'Unit not found');
        const previousStatus = u.status;
        u.status = body.status; u.version++;
        const invalidated = [];
        for (const rec of recommendations.values()) {
          if (rec.candidates.some((c) => c.unitId === u.unitId)) { rec.status = 'invalidated'; invalidated.push(rec.recommendationId); }
        }
        emit('unit.status_changed', 'unit', u.unitId, { status: u.status, previousStatus });
        for (const id of invalidated) emit('recommendation.invalidated', 'recommendation', id);
        return send(res, 201, { statusEventId: uuid(), unitId: u.unitId, status: u.status, previousStatus, reason: body.reason, recordedAt: iso(), invalidatedRecommendationIds: invalidated });
      }
      fail(res, 404, 'not_found', `${req.method} ${path} is not served by the fixture server`);
    });
  })
  .listen(PORT, () => console.log(`Fixture server on http://localhost:${PORT} (set VITE_API_TARGET to this URL)`));
