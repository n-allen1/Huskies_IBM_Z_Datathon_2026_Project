import { describe, expect, it } from 'vitest';
import { demoControlsFrom } from './demo';

describe('demoControlsFrom', () => {
  it('is off by default', () => {
    expect(demoControlsFrom('', null)).toBe(false);
    expect(demoControlsFrom('?x=1', null)).toBe(false);
  });

  it('is turned on by ?demo=1 and remembered', () => {
    expect(demoControlsFrom('?demo=1', null)).toBe(true);
    expect(demoControlsFrom('', '1')).toBe(true);
  });

  it('is turned off again by ?demo=0, even if remembered', () => {
    expect(demoControlsFrom('?demo=0', '1')).toBe(false);
  });

  it('ignores any other value', () => {
    expect(demoControlsFrom('?demo=yes', null)).toBe(false);
    expect(demoControlsFrom('?demo=yes', '1')).toBe(true);
  });
});
