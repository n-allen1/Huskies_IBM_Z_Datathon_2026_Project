import createClient from 'openapi-fetch';
import type { paths } from './schema';
import type { ApiError } from './types';

// Mock/demo only. Real deployments swap in the backend's authentication adapter.
export const DEMO_TOKEN: string = import.meta.env.VITE_DEMO_TOKEN ?? 'demo-dispatcher';

// baseUrl is same-origin: Vite proxies /api in dev, NGINX in deployment.
export const api = createClient<paths>({
  baseUrl: '',
  headers: { Authorization: `Bearer ${DEMO_TOKEN}` }
});

// Demo only. POST /api/v1/admin/demo/reset is allowed for the system_admin role, which the dispatcher token above lacks.
const DEMO_ADMIN_TOKEN: string = import.meta.env.VITE_DEMO_ADMIN_TOKEN ?? 'demo-admin';

/** Restore the server's deterministic seed data (needs DEMO_MODE=true on the server). Never throws. */
export async function resetDemoData(): Promise<{ ok: boolean; text: string }> {
  try {
    const { response, error } = await api.POST('/api/v1/admin/demo/reset', {
      headers: { Authorization: `Bearer ${DEMO_ADMIN_TOKEN}` }
    });
    if (response.ok) return { ok: true, text: 'Demo data restored to the seed.' };
    if (response.status === 403) return { ok: false, text: 'Reset is disabled on this server (not in demo mode).' };
    return { ok: false, text: errorText(error) };
  } catch (e) {
    return { ok: false, text: errorText(e) };
  }
}

/**
 * A random UUID v4. `crypto.randomUUID` exists only in secure contexts (HTTPS or localhost), and the demo is served over plain
 * HTTP from the VM's address, where it is undefined. The pages create their first key while initializing, so calling it directly
 * left them blank. `getRandomValues` is available everywhere.
 */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === 'function') return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  b[6] = (b[6] & 0x0f) | 0x40; // version 4
  b[8] = (b[8] & 0x3f) | 0x80; // RFC 4122 variant
  const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

/** Human-readable text for any openapi-fetch error value or thrown error. */
export function errorText(error: unknown): string {
  if (error && typeof error === 'object' && 'code' in error && 'message' in error) {
    const e = error as ApiError;
    return `${e.code}: ${e.message}`;
  }
  if (error instanceof Error) return error.message;
  return 'Request failed';
}
