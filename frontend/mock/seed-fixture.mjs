// Turns the database seed rows (see seed-sql.mjs) into API-shaped data, so the UI can be exercised against the same
// scenarios the backend team seeds. This is a *translation for testing*, not part of the contract.
//
// The seed uses database vocabulary that the OpenAPI contract does not: statuses such as "assigned", free-form incident
// categories such as "commercial_smoke_report", recommendation status "current", ETA method "synthetic_estimate". Each
// such value is mapped onto the closest contract value below, and every translation is recorded in `report` so the
// mismatch stays visible. A seed value with no mapping falls back to a safe default and is recorded as `unmapped`.

const POLICY_ID = '00000000-0000-4000-8000-0000000f0001';
const DEFAULT_MINUTES_AGO = 5;

// seed value -> contract value
export const MAPS = {
  unitStatus: {
    available: 'available', assigned: 'dispatched_simulated', dispatched: 'dispatched_simulated', en_route: 'en_route_simulated',
    on_scene: 'on_scene_simulated', returning: 'returning', out_of_service: 'out_of_service', off_duty: 'off_duty'
  },
  category: {
    cardiac_arrest: 'medical_cardiac', trauma: 'medical_trauma', medical: 'medical_other', breathing_difficulty: 'medical_other',
    multi_patient_incident: 'medical_other', multiple_patients: 'medical_other', burglary_in_progress: 'burglary',
    shoplifting_report: 'other', vehicle_theft_report: 'other', traffic_collision: 'traffic_collision', motor_vehicle_collision: 'traffic_collision',
    welfare_check: 'welfare_check', disturbance_possible_weapon: 'domestic_disturbance', commercial_smoke_report: 'structure_fire',
    building_alarm_smoke: 'structure_fire', brush_fire: 'wildland_fire', vegetation_fire: 'wildland_fire', vessel_fire_report: 'other',
    collision_smoke_possible_entrapment: 'traffic_collision'
  },
  etaMethod: { synthetic_estimate: 'straight_line_fallback' },
  recommendationStatus: { current: 'pending_review' },
  requirementOrigin: { rules: 'protocol_rule', rule: 'protocol_rule', ml: 'ml_suggested' }
};

/** Discipline of an agency, from its code prefix (SFD fire, NVPD police, NVER emergency medical). */
const disciplineOf = (code = '') => (/^SFD|FIRE/i.test(code) ? 'fire' : /PD|POLICE/i.test(code) ? 'police' : 'ems');

const FACILITY_TYPE = (code = '') => (/FIRE/i.test(code) ? 'fire_station' : /POLICE/i.test(code) ? 'police_station' : 'ems_station');

/** Contract category -> discipline that responds, for incidents created in the UI during a test session. */
const DISCIPLINE_FOR_CATEGORY = {
  structure_fire: 'fire', vehicle_fire: 'fire', wildland_fire: 'fire', hazmat: 'fire', technical_rescue: 'fire', traffic_collision: 'fire',
  medical_cardiac: 'ems', medical_trauma: 'ems', medical_other: 'ems',
  assault: 'police', burglary: 'police', domestic_disturbance: 'police', welfare_check: 'police', other: 'police'
};
/** Capabilities (by seed code) a new incident of a category needs: [code, mandatory]. */
const REQUIRED_FOR_CATEGORY = {
  structure_fire: [['STRUCTURAL_FIRE', true], ['LADDER_RESCUE', false]], wildland_fire: [['WILDLAND_BRUSH', true]], hazmat: [['HAZMAT_INITIAL', true]],
  technical_rescue: [['TECHNICAL_RESCUE', true]], vehicle_fire: [['STRUCTURAL_FIRE', true]], traffic_collision: [['TECHNICAL_RESCUE', true], ['STRUCTURAL_FIRE', false]],
  medical_cardiac: [['ALS', true], ['CARDIAC', false]], medical_trauma: [['ALS', true], ['TRAUMA', false]], medical_other: [['BLS', true]],
  burglary: [['PATROL_RESPONSE', true]], assault: [['PATROL_RESPONSE', true]], domestic_disturbance: [['PATROL_RESPONSE', true], ['CRISIS_DEESCALATION', false]],
  welfare_check: [['PATROL_RESPONSE', true]], other: [['PATROL_RESPONSE', true]]
};

const km = (a, b) => {
  const r = (d) => (d * Math.PI) / 180;
  const h = Math.sin(r(b.lat - a.lat) / 2) ** 2 + Math.cos(r(a.lat)) * Math.cos(r(b.lat)) * Math.sin(r(b.lon - a.lon) / 2) ** 2;
  return 12742 * Math.asin(Math.sqrt(h));
};

export function buildFixture(tables, { now = Date.now() } = {}) {
  const report = { translated: new Map(), unmapped: new Map(), notes: [] };
  const note = (bucket, kind, from, to) => {
    const k = `${kind}: "${from}"${to ? ` -> "${to}"` : ''}`;
    report[bucket].set(k, (report[bucket].get(k) ?? 0) + 1);
  };
  const map = (kind, table, value, fallback) => {
    if (value == null) return value;
    if (value in table) { if (table[value] !== value) note('translated', kind, value, table[value]); return table[value]; }
    note('unmapped', kind, value, fallback);
    return fallback;
  };
  const prov = (extra = {}) => ({ dataOrigin: 'synthetic', sourceSystem: 'database/seed.py', sourceTimestamp: new Date(now).toISOString(), verificationStatus: 'verified', stale: false, ...extra });
  const rows = (t) => tables[t] ?? [];

  const agencies = new Map(rows('agency').map((a) => [a.id, a]));
  const caps = new Map(rows('capability').map((c) => [c.id, c]));
  const capByCode = new Map(rows('capability').map((c) => [c.code, c]));
  const disciplineOfAgency = (id) => disciplineOf(agencies.get(id)?.code);

  const facilities = rows('facility').map((f) => ({
    facilityId: f.id, agencyId: f.agency_id, name: f.name, type: FACILITY_TYPE(f.code), location: { lat: f.latitude, lon: f.longitude }, provenance: prov()
  }));

  const units = rows('unit').map((u) => ({
    unitId: u.id, agencyId: u.agency_id, callSign: u.unit_number, discipline: disciplineOfAgency(u.agency_id), unitType: u.unit_type,
    status: map('unit status', MAPS.unitStatus, u.status, 'off_duty'), stationId: u.facility_id, location: { lat: u.latitude, lon: u.longitude },
    version: u.version ?? 1, provenance: prov()
  }));
  const unitById = new Map(units.map((u) => [u.unitId, u]));

  /** unit id -> capability code -> state ("available" | "unavailable"), straight from unit_capability. */
  const unitCaps = new Map();
  for (const uc of rows('unit_capability')) {
    const code = caps.get(uc.capability_id)?.code;
    if (!code) continue;
    (unitCaps.get(uc.unit_id) ?? unitCaps.set(uc.unit_id, new Map()).get(uc.unit_id)).set(code, uc.state);
  }

  // When did the call come in? The earliest event for the incident; otherwise a few minutes ago.
  const firstEvent = new Map();
  for (const e of rows('incident_event')) {
    const t = Date.parse(e.created_at);
    if (!firstEvent.has(e.incident_id) || t < firstEvent.get(e.incident_id)) firstEvent.set(e.incident_id, t);
  }

  const agencyByIncident = new Map(); // incident id -> agency id (kept out of the API-shaped incident)
  const suggestions = new Map(); // incident id -> { category, confidence, modelVersion } for the "suggest classification" action
  const incidents = rows('incident').map((i, n) => {
    const confirmed = map('incident category', MAPS.category, i.category_confirmed, 'other');
    if (i.category_suggested) {
      suggestions.set(i.id, {
        category: map('suggested category', MAPS.category, i.category_suggested, 'other'),
        confidence: i.classification_confidence ?? undefined, modelVersion: i.classification_model_version ?? undefined, priority: i.priority
      });
    }
    agencyByIncident.set(i.id, i.agency_id);
    return {
      incidentId: i.id, number: i.incident_number, status: i.status, priority: i.priority ?? 'unassigned', priorityConfirmed: !!i.priority_confirmed,
      confirmedCategory: confirmed ?? null, narrative: i.narrative, version: i.version ?? 1,
      location: { address: i.address ?? undefined, point: { lat: i.latitude, lon: i.longitude } },
      createdAt: new Date(firstEvent.get(i.id) ?? now - (DEFAULT_MINUTES_AGO + n) * 60_000).toISOString(), updatedAt: new Date(now).toISOString(),
      provenance: prov()
    };
  });
  const incidentById = new Map(incidents.map((i) => [i.incidentId, i]));

  const requirementRows = new Map(); // incident id -> [{ code, name, mandatory, origin }]
  for (const r of rows('incident_capability_requirement')) {
    const c = caps.get(r.capability_id);
    if (!c) continue;
    (requirementRows.get(r.incident_id) ?? requirementRows.set(r.incident_id, []).get(r.incident_id)).push({
      code: c.code, name: c.name, mandatory: !!r.mandatory, origin: map('requirement source', MAPS.requirementOrigin, r.source, 'protocol_rule')
    });
  }

  const toRequirement = (r) => ({ capabilityId: r.code, label: r.name, kind: 'apparatus', mandatory: r.mandatory, origin: r.origin });

  /** Capability requirements for an incident: the seeded ones, or category rules for an incident made in the UI. */
  function requirementsFor(incident) {
    const seeded = requirementRows.get(incident.incidentId);
    if (seeded) return seeded.map(toRequirement);
    return (REQUIRED_FOR_CATEGORY[incident.confirmedCategory ?? 'other'] ?? [])
      .map(([code, mandatory]) => ({ code, name: capByCode.get(code)?.name ?? code, mandatory, origin: 'protocol_rule' }))
      .map(toRequirement);
  }

  const checksFor = (unit, reqs) => reqs.map((r) => {
    const state = unitCaps.get(unit.unitId)?.get(r.capabilityId);
    return { capabilityId: r.capabilityId, state: state === 'available' ? 'available' : state === 'unavailable' ? 'unavailable' : 'missing', mandatory: r.mandatory };
  });

  const toCandidate = (unit, incident, reqs, extra) => ({
    unitId: unit.unitId, callSign: unit.callSign, checks: checksFor(unit, reqs), ...extra,
    ...(extra.etaSeconds === undefined ? { etaSeconds: Math.round((km(unit.location, incident.location.point) / 40) * 3600) } : {})
  });

  const baseRecommendation = (incident, candidates, extra = {}) => ({
    recommendationId: extra.recommendationId ?? crypto.randomUUID(), incidentId: incident.incidentId, status: 'pending_review', stage: 'hard_eligibility', candidates,
    inputs: { unitSnapshotAt: new Date(now).toISOString(), requirementSetGeneratedAt: new Date(now).toISOString(), modelVersions: [] }, policyVersionId: POLICY_ID,
    createdAt: new Date().toISOString(), provenance: prov(), humanConfirmationRequired: true, ...extra
  });

  /** The seeded recommendations, as the backend would return them. */
  const seededRecommendations = new Map();
  for (const rec of rows('recommendation')) {
    const incident = incidentById.get(rec.incident_id);
    if (!incident) continue;
    const reqs = requirementsFor(incident);
    const candidates = rows('recommendation_candidate')
      .filter((c) => c.recommendation_id === rec.id && unitById.has(c.unit_id))
      .sort((a, b) => (a.rank ?? 99) - (b.rank ?? 99))
      .map((c) => toCandidate(unitById.get(c.unit_id), incident, reqs, {
        eligible: !!c.eligible, rank: c.eligible ? c.rank : null, etaSeconds: c.eta_seconds ?? null,
        etaMethod: map('eta method', MAPS.etaMethod, c.eta_method, 'straight_line_fallback'),
        ineligibleReasons: c.ineligibility_reasons ?? [], coverageImpact: c.coverage_impact == null ? undefined : String(c.coverage_impact),
        rankingFactors: [{ factor: 'Seed score', value: String(c.score) }, { factor: 'Distance', value: `${c.distance_km} km` }]
      }));
    // Keep what the seed says, but record where it contradicts itself, so a surprising map or ETA is explained.
    const seedRows = rows('recommendation_candidate').filter((c) => c.recommendation_id === rec.id);
    for (const c of candidates) {
      const u = unitById.get(c.unitId);
      if (c.eligible && u.status !== 'available') report.notes.push(`${incident.number}: ${u.callSign} is "${u.status}" but the seed lists it as eligible`);
      const seedKm = seedRows.find((r) => r.unit_id === c.unitId)?.distance_km;
      const realKm = km(u.location, incident.location.point);
      if (seedKm != null && Math.abs(seedKm - realKm) > Math.max(1, seedKm)) {
        report.notes.push(`${incident.number}: seed says ${u.callSign} is ${seedKm} km away (ETA ${Math.round((c.etaSeconds ?? 0) / 60)} min) but its position is ${realKm.toFixed(1)} km from the incident`);
      }
    }
    seededRecommendations.set(incident.incidentId, baseRecommendation(incident, candidates, {
      recommendationId: rec.id, createdAt: rec.generated_at, status: map('recommendation status', MAPS.recommendationStatus, rec.status, 'pending_review'),
      limitations: [rec.reasoning_summary, 'Seed data: ETAs and scores are synthetic.'].filter(Boolean)
    }));
  }

  /** Hard eligibility from capabilities and status, ranked by straight-line ETA: for incidents without a seeded recommendation. */
  function buildRecommendation(incident) {
    const agency = agencyByIncident.get(incident.incidentId);
    const discipline = agency ? disciplineOfAgency(agency) : DISCIPLINE_FOR_CATEGORY[incident.confirmedCategory ?? 'other'] ?? 'police';
    const reqs = requirementsFor(incident);
    const pool = units.filter((u) => u.discipline === discipline);
    const scored = pool.map((u) => {
      const reasons = [];
      if (u.status !== 'available') reasons.push(`Unit status is ${u.status.replaceAll('_', ' ')}`);
      for (const c of checksFor(u, reqs)) if (c.mandatory && c.state !== 'available') reasons.push(`Missing mandatory capability ${c.capabilityId}`);
      return { u, reasons, eta: Math.round((km(u.location, incident.location.point) / 40) * 3600) };
    });
    const eligible = scored.filter((s) => !s.reasons.length).sort((a, b) => a.eta - b.eta);
    const candidates = [
      ...eligible.map((s, i) => toCandidate(s.u, incident, reqs, { eligible: true, rank: i + 1, etaSeconds: s.eta, etaMethod: 'straight_line_fallback', ineligibleReasons: [], rankingFactors: [{ factor: 'Straight-line ETA', value: `${Math.round(s.eta / 60)} min` }] })),
      ...scored.filter((s) => s.reasons.length).map((s) => toCandidate(s.u, incident, reqs, { eligible: false, rank: null, etaSeconds: s.eta, etaMethod: 'straight_line_fallback', ineligibleReasons: s.reasons, rankingFactors: [] }))
    ];
    return baseRecommendation(incident, candidates, { limitations: ['Fixture data: ETA is a straight-line estimate at 40 km/h, not a routed drive time.'] });
  }

  /** What "suggest classification" returns for a seeded incident (the seed's own suggestion), if any. */
  const suggestionFor = (incidentId) => suggestions.get(incidentId);

  return {
    incidents, units, facilities, requirementsFor, buildRecommendation, seededRecommendations, suggestionFor,
    report: {
      translated: [...report.translated].map(([k, n]) => `${k} (x${n})`),
      unmapped: [...report.unmapped].map(([k, n]) => `${k} (x${n})`),
      notes: report.notes
    }
  };
}
