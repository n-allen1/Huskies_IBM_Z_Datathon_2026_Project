import { afterEach, describe, expect, it, vi } from 'vitest';
import { newIdempotencyKey } from './client';

const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

describe('newIdempotencyKey', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('returns a UUID v4', () => {
    expect(newIdempotencyKey()).toMatch(UUID_V4);
  });

  it('still works where crypto.randomUUID is missing (plain HTTP, non-localhost pages)', () => {
    // Browsers expose randomUUID only in secure contexts; getRandomValues is always there.
    vi.stubGlobal('crypto', { getRandomValues: globalThis.crypto.getRandomValues.bind(globalThis.crypto) });
    const keys = new Set(Array.from({ length: 50 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(50);
    for (const k of keys) {
      expect(k).toMatch(UUID_V4);
      // The contract asks for 8 to 128 characters.
      expect(k.length).toBeGreaterThanOrEqual(8);
      expect(k.length).toBeLessThanOrEqual(128);
    }
  });
});
