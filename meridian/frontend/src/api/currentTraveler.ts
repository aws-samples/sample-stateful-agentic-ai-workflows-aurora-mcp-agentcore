/**
 * What the browser sends when it means "the traveler I am signed in as".
 *
 * The API resolves it from the verified credential, so the page never has to know, send or
 * choose a traveler id. A request that names any other id is refused unless it is the caller's own.
 */
export const CURRENT_TRAVELER = 'me';
