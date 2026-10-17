export function waitMinutes(iso: string, now: number = Date.now()): number {
  return Math.max(0, Math.floor((now - Date.parse(iso)) / 60000));
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null) return 'n/a';
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}m ${s.toString().padStart(2, '0')}s`;
}

export function formatTime(iso: string | undefined): string {
  return iso
    ? new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    : '-';
}

export const priorityRank: Record<string, number> = { P1: 1, P2: 2, P3: 3, P4: 4, unassigned: 5 };

export const label = (v: string): string => v.replaceAll('_', ' ');
