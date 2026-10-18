// Reads the demo database seed (database/seed.py) so the UI can be tested against the same scenarios the backend team seeds.
//
// The seed is a Python file holding one SQL string (`SEED_SQL = '...'`) made of INSERT statements. This module extracts that
// string and parses the INSERTs into plain row objects keyed by column name. It understands only what the seed uses:
// quoted strings, numbers, TRUE/FALSE/NULL, `'...'::jsonb`, and `NOW()` with an optional `- INTERVAL 'n unit'`.
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';

const PY_ESCAPES = { n: '\n', t: '\t', r: '\r', "'": "'", '"': '"', '\\': '\\' };

/** The SQL text of a seed file: a path, or `git:<rev>[:path]` for an earlier revision of database/seed.py. */
export function readSeedSql(source, { repoRoot, now = Date.now() } = {}) {
  let py;
  if (source.startsWith('git:')) {
    const [, rev, path = 'database/seed.py'] = source.split(':');
    py = execFileSync('git', ['show', `${rev}:${path}`], { cwd: repoRoot, encoding: 'utf8' });
  } else {
    py = readFileSync(source, 'utf8');
  }
  const m = py.match(/^[A-Z_]+\s*=\s*'((?:[^'\\]|\\.)*)'/m);
  if (!m) throw new Error(`${source}: no SQL string constant found`);
  return { sql: m[1].replace(/\\(.)/gs, (_, c) => PY_ESCAPES[c] ?? c), now };
}

/** Split on commas that are not inside quotes or parentheses. */
function splitTop(text) {
  const parts = [];
  let depth = 0, quote = false, start = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (quote) {
      if (c === "'") { if (text[i + 1] === "'") i++; else quote = false; }
    } else if (c === "'") quote = true;
    else if (c === '(') depth++;
    else if (c === ')') depth--;
    else if (c === ',' && depth === 0) { parts.push(text.slice(start, i)); start = i + 1; }
  }
  parts.push(text.slice(start));
  return parts.map((p) => p.trim());
}

const UNIT_MS = { second: 1000, minute: 60_000, hour: 3_600_000, day: 86_400_000 };

function parseValue(raw, now) {
  const v = raw.trim();
  if (/^null$/i.test(v)) return null;
  if (/^true$/i.test(v)) return true;
  if (/^false$/i.test(v)) return false;
  if (/^-?\d+(\.\d+)?$/.test(v)) return Number(v);
  let m = v.match(/^'((?:[^']|'')*)'(?:::(\w+))?$/s);
  if (m) {
    const text = m[1].replaceAll("''", "'");
    return m[2] === 'jsonb' || m[2] === 'json' ? JSON.parse(text) : text;
  }
  m = v.match(/^NOW\(\)(?:\s*([-+])\s*INTERVAL\s*'(\d+)\s+(second|minute|hour|day)s?')?$/i);
  if (m) {
    const delta = m[1] ? Number(m[2]) * UNIT_MS[m[3].toLowerCase()] * (m[1] === '-' ? -1 : 1) : 0;
    return new Date(now + delta).toISOString();
  }
  throw new Error(`Unsupported SQL value: ${v.slice(0, 80)}`);
}

/** Every `INSERT INTO table (cols) VALUES (...),(...)` in the SQL, as `{ table: [ {col: value} ] }`. */
export function parseInserts({ sql, now }) {
  const tables = {};
  const head = /INSERT INTO\s+(\w+)\s*\(([^)]*)\)\s*VALUES\s*/gi;
  let h;
  while ((h = head.exec(sql))) {
    const cols = h[1] && h[2].split(',').map((c) => c.trim());
    let i = head.lastIndex;
    const rows = (tables[h[1]] ??= []);
    for (;;) {
      while (/\s/.test(sql[i])) i++;
      if (sql[i] !== '(') throw new Error(`Expected a row at ${sql.slice(i, i + 40)}`);
      // find the matching close paren, honoring quotes
      let depth = 0, quote = false, j = i;
      for (; j < sql.length; j++) {
        const c = sql[j];
        if (quote) { if (c === "'") { if (sql[j + 1] === "'") j++; else quote = false; } }
        else if (c === "'") quote = true;
        else if (c === '(') depth++;
        else if (c === ')' && --depth === 0) break;
      }
      const cells = splitTop(sql.slice(i + 1, j));
      if (cells.length !== cols.length) throw new Error(`${h[1]}: ${cells.length} values for ${cols.length} columns`);
      rows.push(Object.fromEntries(cols.map((c, k) => [c, parseValue(cells[k], now)])));
      i = j + 1;
      while (/\s/.test(sql[i])) i++;
      if (sql[i] === ',') { i++; continue; }
      break;
    }
    head.lastIndex = i;
  }
  return tables;
}

/** Merge several seeds (later ones add rows; the first row seen for an id wins, like ON CONFLICT DO NOTHING). */
export function mergeTables(list) {
  const out = {};
  for (const t of list) for (const [name, rows] of Object.entries(t)) {
    const have = (out[name] ??= []);
    const seen = new Set(have.map((r) => r.id).filter(Boolean));
    for (const r of rows) if (!r.id || !seen.has(r.id)) have.push(r);
  }
  return out;
}
