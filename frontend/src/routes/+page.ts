import { redirect } from '@sveltejs/kit';

// Keep the query string, so /?demo=1 still turns the demo controls on.
export function load({ url }) {
  redirect(307, '/dispatch' + url.search);
}
