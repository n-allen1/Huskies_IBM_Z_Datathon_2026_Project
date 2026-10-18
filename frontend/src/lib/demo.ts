/** Where a tab remembers that the demo controls are on. Per tab, so a normal visitor never sees them. */
export const DEMO_CONTROLS_KEY = 'dispatchz.demoControls';

/** Carries "Demo data restored" across the page reload that follows a reset. */
export const DEMO_RESET_NOTE_KEY = 'dispatchz.demoResetNote';

/** `?demo=1` turns the demo controls on, `?demo=0` turns them off; otherwise whatever this tab last chose. */
export function demoControlsFrom(search: string, remembered: string | null): boolean {
  const v = new URLSearchParams(search).get('demo');
  if (v === '1' || v === 'true') return true;
  if (v === '0' || v === 'false') return false;
  return remembered === '1';
}
