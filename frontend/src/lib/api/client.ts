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

export const newIdempotencyKey = (): string => crypto.randomUUID();

/** Human-readable text for any openapi-fetch error value or thrown error. */
export function errorText(error: unknown): string {
  if (error && typeof error === 'object' && 'code' in error && 'message' in error) {
    const e = error as ApiError;
    return `${e.code}: ${e.message}`;
  }
  if (error instanceof Error) return error.message;
  return 'Request failed';
}
