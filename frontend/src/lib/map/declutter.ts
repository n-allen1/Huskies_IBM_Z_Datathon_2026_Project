// Screen-space collision resolution for map markers.
//
// Markers whose boxes overlap are pushed apart horizontally: for each overlapping pair the left one moves left and
// the right one moves right by half of what is needed (plus a small gap), so both are fully visible and neither
// moves further than it has to. Pairs that are already clear, or separated vertically, are not touched.
//
// Pure and deterministic (same input, same output), so the map can re-run it on every redraw and zoom.

export interface Box {
  id: string;
  /** Center in screen pixels at the marker's true position. */
  cx: number;
  cy: number;
  w: number;
  h: number;
}

export interface DeclutterOptions {
  /** Empty pixels to leave between two separated markers. */
  gap?: number;
  /** No marker is moved further than this from its true position. */
  maxShift?: number;
  iterations?: number;
}

/** Horizontal offset in pixels for each marker id (0 when it needs no move). */
export function declutter(boxes: Box[], { gap = 4, maxShift = 160, iterations = 24 }: DeclutterOptions = {}): Map<string, number> {
  const dx = new Map<string, number>(boxes.map((b) => [b.id, 0]));
  const x = (b: Box) => b.cx + (dx.get(b.id) ?? 0);

  for (let pass = 0; pass < iterations; pass++) {
    let moved = false;
    // Left-to-right with a stable tie-break on id: the same input always resolves the same way.
    const order = [...boxes].sort((a, b) => x(a) - x(b) || (a.id < b.id ? -1 : 1));
    for (let i = 0; i < order.length; i++) {
      for (let j = i + 1; j < order.length; j++) {
        const a = order[i];
        const b = order[j];
        const need = (a.w + b.w) / 2 + gap - (x(b) - x(a)); // > 0 means the boxes overlap horizontally
        if (need <= 0) {
          if (x(b) - x(a) > (a.w + b.w) / 2 + gap + maxShift * 2) break; // everything further right is clear of `a`
          continue;
        }
        if (Math.abs(a.cy - b.cy) >= (a.h + b.h) / 2 + gap) continue; // separated vertically: no collision
        // Share the move; if one side has hit its limit the other takes the remainder.
        let da = need / 2;
        let db = need / 2;
        const roomA = maxShift + (dx.get(a.id) ?? 0); // how far `a` can still move left
        const roomB = maxShift - (dx.get(b.id) ?? 0); // how far `b` can still move right
        if (da > roomA) { db += da - roomA; da = Math.max(0, roomA); }
        if (db > roomB) { da = Math.min(roomA, da + (db - roomB)); db = Math.max(0, roomB); }
        if (da <= 0 && db <= 0) continue;
        dx.set(a.id, (dx.get(a.id) ?? 0) - da);
        dx.set(b.id, (dx.get(b.id) ?? 0) + db);
        moved = true;
      }
    }
    if (!moved) break;
  }
  // Whole pixels keep icons crisp; round away from zero so a rounded shift can never re-create an overlap.
  for (const [id, v] of dx) dx.set(id, v === 0 ? 0 : v < 0 ? Math.floor(v) : Math.ceil(v));
  return dx;
}
