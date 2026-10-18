import { redirect } from '@sveltejs/kit';

// A short address to remember: /demo opens the dispatch screen with the demo controls (the "DEMO · Reset data" button) turned on.
// The tab remembers the setting, so it stays on while moving between Dispatch and Intake.
export function load() {
  redirect(307, '/dispatch?demo=1');
}
