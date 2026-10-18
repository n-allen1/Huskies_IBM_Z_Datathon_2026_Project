import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { mergeTables, parseInserts, readSeedSql } from './seed-sql.mjs';
import { buildFixture } from './seed-fixture.mjs';

const contract = JSON.parse(readFileSync(new URL('../../contracts/openapi.json', import.meta.url), 'utf8'));
const schemas = contract.components.schemas;
const SEED_FILE = new URL('../../database/seed.py', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1');
const NOW = Date.parse('2026-10-18T12:00:00Z');

const fixture = buildFixture(parseInserts(readSeedSql(SEED_FILE, { now: NOW })), { now: NOW });

describe('SQL value parsing', () => {
  const parse = (sql) => parseInserts({ sql, now: NOW });

  it('handles commas, parentheses and doubled quotes inside strings', () => {
    const t = parse("INSERT INTO t (a, b, c) VALUES ('Near the Locks, Seattle (demo; not real)', 'it''s fine', 3.5) ON CONFLICT DO NOTHING;");
    expect(t.t).toEqual([{ a: 'Near the Locks, Seattle (demo; not real)', b: "it's fine", c: 3.5 }]);
  });

  it('reads several rows, booleans, nulls, jsonb and NOW() intervals', () => {
    const t = parse(`INSERT INTO t (id, ok, n, j, at) VALUES
      ('1', TRUE, NULL, '["a, b"]'::jsonb, NOW() - INTERVAL '9 minutes'),
      ('2', FALSE, -4, '{"k":1}'::jsonb, NOW());`);
    expect(t.t.map((r) => r.id)).toEqual(['1', '2']);
    expect(t.t[0]).toMatchObject({ ok: true, n: null, j: ['a, b'] });
    expect(t.t[1].j).toEqual({ k: 1 });
    expect(Date.parse(t.t[0].at)).toBe(NOW - 9 * 60_000);
    expect(Date.parse(t.t[1].at)).toBe(NOW);
  });

  it('reports a column/value count mismatch instead of misreading the row', () => {
    expect(() => parse("INSERT INTO t (a, b) VALUES ('only one');")).toThrow(/values for 2 columns/);
  });

  it('keeps the first row for an id when seeds are merged (ON CONFLICT DO NOTHING)', () => {
    const merged = mergeTables([{ t: [{ id: 'x', v: 1 }] }, { t: [{ id: 'x', v: 2 }, { id: 'y', v: 3 }] }]);
    expect(merged.t).toEqual([{ id: 'x', v: 1 }, { id: 'y', v: 3 }]);
  });
});

describe('database seed translated to the API contract', () => {
  const enumOf = (schema, prop) => schemas[schema].properties[prop].enum;
  const requireKeys = (schema, obj, label) => {
    for (const key of schemas[schema].required ?? []) expect(obj[key], `${label}.${key}`).not.toBeUndefined();
  };

  it('loads something', () => {
    expect(fixture.incidents.length).toBeGreaterThan(0);
    expect(fixture.units.length).toBeGreaterThan(0);
  });

  it('maps every seed value; none fell back to a guess (extend MAPS in seed-fixture.mjs if this fails)', () => {
    expect(fixture.report.unmapped).toEqual([]);
  });

  it('incidents use contract enums and carry every required field', () => {
    for (const i of fixture.incidents) {
      requireKeys('Incident', i, i.number);
      expect(enumOf('Incident', 'status')).toContain(i.status);
      expect(enumOf('Incident', 'priority')).toContain(i.priority);
      if (i.confirmedCategory) expect(schemas.IncidentCategory.enum).toContain(i.confirmedCategory);
      expect(Number.isFinite(i.location.point.lat) && Math.abs(i.location.point.lat) <= 90).toBe(true);
      expect(Number.isFinite(i.location.point.lon) && Math.abs(i.location.point.lon) <= 180).toBe(true);
      expect(Object.keys(i).filter((k) => k.startsWith('_'))).toEqual([]); // nothing non-contract leaks out
    }
  });

  it('units and facilities use contract enums and carry every required field', () => {
    for (const u of fixture.units) {
      requireKeys('Unit', u, u.callSign);
      expect(enumOf('Unit', 'status')).toContain(u.status);
      expect(enumOf('Unit', 'discipline')).toContain(u.discipline);
    }
    for (const f of fixture.facilities) {
      requireKeys('Facility', f, f.name);
      expect(enumOf('Facility', 'type')).toContain(f.type);
    }
  });

  it('seeded recommendations are valid and refer to real incidents and units', () => {
    const unitIds = new Set(fixture.units.map((u) => u.unitId));
    const incidentIds = new Set(fixture.incidents.map((i) => i.incidentId));
    for (const [incidentId, rec] of fixture.seededRecommendations) {
      expect(incidentIds.has(incidentId)).toBe(true);
      requireKeys('Recommendation', rec, rec.recommendationId);
      expect(enumOf('Recommendation', 'status')).toContain(rec.status);
      for (const c of rec.candidates) {
        requireKeys('Candidate', c, c.callSign);
        expect(unitIds.has(c.unitId)).toBe(true);
        expect(enumOf('Candidate', 'etaMethod')).toContain(c.etaMethod);
        if (c.eligible) expect(c.rank).toBeGreaterThan(0);
        else expect(c.rank).toBeNull();
        for (const check of c.checks) requireKeys('CapabilityCheck', check, 'check');
      }
    }
  });

  it('requirements are valid for every incident, seeded or not', () => {
    for (const i of fixture.incidents) {
      for (const r of fixture.requirementsFor(i)) {
        requireKeys('CapabilityRequirement', r, r.capabilityId);
        expect(schemas.CapabilityRequirement.properties.kind.enum).toContain(r.kind);
        expect(schemas.CapabilityRequirement.properties.origin.enum).toContain(r.origin);
      }
    }
  });

  it('ids are unique', () => {
    const dup = (xs) => xs.length !== new Set(xs).size;
    expect(dup(fixture.incidents.map((i) => i.incidentId))).toBe(false);
    expect(dup(fixture.units.map((u) => u.unitId))).toBe(false);
    expect(dup(fixture.facilities.map((f) => f.facilityId))).toBe(false);
  });
});

describe('recommendations generated from the seed', () => {
  it('never rank an ineligible unit as eligible, and order eligible units by ETA', () => {
    for (const incident of fixture.incidents) {
      const rec = fixture.buildRecommendation(incident);
      const reqs = fixture.requirementsFor(incident);
      const mandatory = reqs.filter((r) => r.mandatory).map((r) => r.capabilityId);
      const eligible = rec.candidates.filter((c) => c.eligible);
      for (const c of rec.candidates) {
        const lacking = c.checks.filter((k) => mandatory.includes(k.capabilityId) && k.state !== 'available');
        const unit = fixture.units.find((u) => u.unitId === c.unitId);
        if (c.eligible) {
          expect(lacking, `${c.callSign} eligible without a mandatory capability`).toEqual([]);
          expect(unit.status).toBe('available');
        } else expect(c.ineligibleReasons.length).toBeGreaterThan(0);
      }
      const etas = eligible.map((c) => c.etaSeconds);
      expect(etas).toEqual([...etas].sort((a, b) => a - b));
      expect(eligible.map((c) => c.rank)).toEqual(eligible.map((_, k) => k + 1));
    }
  });
});
