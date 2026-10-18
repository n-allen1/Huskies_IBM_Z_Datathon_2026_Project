import type { EventEnvelope } from './api/types';

export interface SseFrame {
  id?: string;
  event?: string;
  data: string;
}

/** Split a text buffer into complete SSE frames; returns the unconsumed remainder. */
export function parseSse(buffer: string): { frames: SseFrame[]; rest: string } {
  const normalized = buffer.replaceAll('\r\n', '\n');
  const parts = normalized.split('\n\n');
  const rest = parts.pop() ?? '';
  const frames: SseFrame[] = [];
  for (const part of parts) {
    const frame: SseFrame = { data: '' };
    const data: string[] = [];
    for (const line of part.split('\n')) {
      if (line.startsWith(':')) continue; // comment / keep-alive
      const i = line.indexOf(':');
      const field = i === -1 ? line : line.slice(0, i);
      const value = i === -1 ? '' : line.slice(i + 1).replace(/^ /, '');
      if (field === 'id') frame.id = value;
      else if (field === 'event') frame.event = value;
      else if (field === 'data') data.push(value);
    }
    frame.data = data.join('\n');
    if (frame.data || frame.event) frames.push(frame);
  }
  return { frames, rest };
}

export function frameToEnvelope(frame: SseFrame): EventEnvelope | null {
  try {
    return JSON.parse(frame.data) as EventEnvelope;
  } catch {
    return null;
  }
}
