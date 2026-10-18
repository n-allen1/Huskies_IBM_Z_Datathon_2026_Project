import { describe, expect, it } from 'vitest';
import { frameToEnvelope, parseSse } from './sse';

describe('parseSse', () => {
  it('parses complete frames and keeps the partial remainder', () => {
    const { frames, rest } = parseSse(
      'id: 1\nevent: incident.created\ndata: {"a":1}\n\n: hb\n\nid: 2\ndata: par'
    );
    expect(frames).toHaveLength(1);
    expect(frames[0]).toMatchObject({ id: '1', event: 'incident.created', data: '{"a":1}' });
    expect(rest).toBe('id: 2\ndata: par');
  });

  it('handles CRLF and multi-line data', () => {
    const { frames } = parseSse('id: 5\r\ndata: a\r\ndata: b\r\n\r\n');
    expect(frames[0].data).toBe('a\nb');
  });

  it('returns null for invalid envelope JSON', () => {
    expect(frameToEnvelope({ data: 'nope' })).toBeNull();
  });
});
