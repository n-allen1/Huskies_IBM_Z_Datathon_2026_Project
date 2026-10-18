import { describe, expect, it } from 'vitest';
import { declutter, type Box } from './declutter';

const box = (id: string, cx: number, cy: number, w = 40, h = 22): Box => ({ id, cx, cy, w, h });
const clear = (boxes: Box[], dx: Map<string, number>, gap = 0) => {
  for (let i = 0; i < boxes.length; i++) {
    for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      const ox = (a.w + b.w) / 2 - Math.abs(a.cx + (dx.get(a.id) ?? 0) - (b.cx + (dx.get(b.id) ?? 0)));
      const oy = (a.h + b.h) / 2 - Math.abs(a.cy - b.cy);
      if (ox > -gap && oy > -gap) return false; // overlapping (or closer than the gap)
    }
  }
  return true;
};

describe('declutter', () => {
  it('leaves markers that do not overlap exactly where they are', () => {
    const dx = declutter([box('a', 0, 0), box('b', 100, 0), box('c', 0, 100)]);
    expect([...dx.values()]).toEqual([0, 0, 0]);
  });

  it('does not move markers that are separated vertically', () => {
    const dx = declutter([box('a', 0, 0), box('b', 5, 40)]);
    expect(dx.get('a')).toBe(0);
    expect(dx.get('b')).toBe(0);
  });

  it('moves the left one left and the right one right by the same amount', () => {
    // 40px wide boxes 10px apart overlap by 30px; with a 4px gap they need 34px, so 17px each.
    const dx = declutter([box('a', 100, 0), box('b', 110, 0)]);
    expect(dx.get('a')).toBeLessThan(0);
    expect(dx.get('b')).toBeGreaterThan(0);
    expect(Math.abs(dx.get('a')!)).toBeCloseTo(dx.get('b')!, 0);
    expect(Math.abs(dx.get('a')!)).toBeLessThanOrEqual(18);
  });

  it('moves them just enough: they end up a gap apart, not further', () => {
    const boxes = [box('a', 100, 0), box('b', 110, 0)];
    const dx = declutter(boxes, { gap: 4 });
    const distance = boxes[1].cx + dx.get('b')! - (boxes[0].cx + dx.get('a')!);
    expect(distance).toBeGreaterThanOrEqual(44);
    expect(distance).toBeLessThanOrEqual(46); // 40 wide + 4 gap, plus at most rounding
  });

  it('separates markers that sit at the identical position, deterministically', () => {
    const boxes = [box('b', 50, 0), box('a', 50, 0)];
    const one = declutter(boxes);
    const two = declutter([...boxes].reverse());
    expect(one.get('a')).toBeLessThan(0); // tie broken by id: "a" goes left
    expect(one.get('b')).toBeGreaterThan(0);
    expect([...one.entries()].sort()).toEqual([...two.entries()].sort());
  });

  it('resolves a crowd so that nothing overlaps', () => {
    const boxes = [box('a', 100, 0), box('b', 105, 0), box('c', 110, 0), box('d', 112, 4), box('e', 300, 0)];
    const dx = declutter(boxes);
    expect(clear(boxes, dx)).toBe(true);
    expect(dx.get('e')).toBe(0); // the far marker is untouched
  });

  it('handles different sizes and a small gap', () => {
    const boxes = [box('inc', 100, 0, 24, 24), box('unit', 104, 0, 52, 22)];
    const dx = declutter(boxes, { gap: 2 });
    expect(clear(boxes, dx, 2)).toBe(true);
  });

  it('never moves a marker further than maxShift', () => {
    const boxes = Array.from({ length: 12 }, (_, i) => box(`m${String(i).padStart(2, '0')}`, 100, 0));
    const dx = declutter(boxes, { maxShift: 60 });
    for (const v of dx.values()) expect(Math.abs(v)).toBeLessThanOrEqual(60);
  });

  it('is stable: running it on its own result changes nothing further', () => {
    const boxes = [box('a', 100, 0), box('b', 110, 0), box('c', 118, 0)];
    const first = declutter(boxes);
    const settled = boxes.map((b) => ({ ...b, cx: b.cx + (first.get(b.id) ?? 0) }));
    const again = declutter(settled);
    expect([...again.values()].every((v) => v === 0)).toBe(true);
  });

  it('copes with no markers', () => {
    expect(declutter([]).size).toBe(0);
  });
});
