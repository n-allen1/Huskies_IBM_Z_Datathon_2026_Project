import type { Incident, Unit } from './api/types';

export type Priority = Incident['priority'];

/** Priority meta. Shape and numeral carry the meaning; color only reinforces it. */
export const PRIORITY: Record<Priority, { n: string; name: string; color: string; shape: 'diamond' | 'triangle' | 'circle' | 'square' | 'dashed' }> = {
  P1: { n: '1', name: 'Priority 1, immediate', color: 'var(--color-p1)', shape: 'diamond' },
  P2: { n: '2', name: 'Priority 2, urgent', color: 'var(--color-p2)', shape: 'triangle' },
  P3: { n: '3', name: 'Priority 3, routine', color: 'var(--color-p3)', shape: 'circle' },
  P4: { n: '4', name: 'Priority 4, low', color: 'var(--color-p4)', shape: 'square' },
  unassigned: { n: '?', name: 'Priority not assigned', color: 'var(--color-muted)', shape: 'dashed' }
};

/** SVG path/shape for a priority glyph in a 20x20 box, filled with its color. */
export function prioritySvg(p: Priority, size = 20): string {
  const m = PRIORITY[p];
  const txt = (y: number) =>
    `<text x="10" y="${y}" text-anchor="middle" font-size="11" font-weight="800" fill="#0b111b" font-family="Segoe UI, system-ui, sans-serif">${m.n}</text>`;
  const stroke = 'stroke="#0b111b" stroke-width="1.5" stroke-linejoin="round"';
  let body: string;
  switch (m.shape) {
    case 'diamond':
      body = `<path d="M10 1 19 10 10 19 1 10Z" fill="${m.color}" ${stroke}/>${txt(14)}`;
      break;
    case 'triangle':
      body = `<path d="M10 1.5 19 18H1Z" fill="${m.color}" ${stroke}/>${txt(16)}`;
      break;
    case 'square':
      body = `<rect x="2" y="2" width="16" height="16" rx="2" fill="${m.color}" ${stroke}/>${txt(14)}`;
      break;
    case 'dashed':
      body = `<circle cx="10" cy="10" r="8" fill="#0b111b" stroke="${m.color}" stroke-width="1.8" stroke-dasharray="3 2"/><text x="10" y="14" text-anchor="middle" font-size="11" font-weight="800" fill="${m.color}" font-family="Segoe UI, system-ui, sans-serif">?</text>`;
      break;
    default:
      body = `<circle cx="10" cy="10" r="8.5" fill="${m.color}" ${stroke}/>${txt(14)}`;
  }
  return `<svg width="${size}" height="${size}" viewBox="0 0 20 20" aria-hidden="true" focusable="false">${body}</svg>`;
}

export type UnitGroup = 'available' | 'assigned' | 'returning' | 'unavailable';

export const unitGroup = (s: Unit['status']): UnitGroup =>
  s === 'available'
    ? 'available'
    : s === 'dispatched_simulated' || s === 'en_route_simulated' || s === 'on_scene_simulated'
      ? 'assigned'
      : s === 'returning'
        ? 'returning'
        : 'unavailable';

export const UNIT_GROUP_META: Record<UnitGroup, { glyph: string; text: string }> = {
  available: { glyph: '●', text: 'Available' },
  assigned: { glyph: '▶', text: 'Assigned' },
  returning: { glyph: '↩', text: 'Returning' },
  unavailable: { glyph: '✕', text: 'Unavailable' }
};

/** Statuses that are finished: hidden from the active queue by default. */
export const CLOSED_STATUSES = new Set<Incident['status']>(['resolved', 'cancelled']);

/** Statuses where the dispatcher still owes the incident a decision. */
export const AWAITING_DISPATCH = new Set<Incident['status']>(['intake', 'pending_dispatch', 'recommendation_ready']);

export const needsClassification = (i: Incident): boolean => !i.confirmedCategory || !i.priorityConfirmed;

export const esc = (s: string): string => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** "12s", "4m", "2h 5m": coarse age for freshness indicators. */
export function ageText(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}
