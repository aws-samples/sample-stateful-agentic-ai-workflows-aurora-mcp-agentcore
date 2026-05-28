// Showcase config (no fallback data — everything renders from live Aurora).
//
// The /showcase route is a live demo surface. We deliberately do NOT ship
// synthetic recommendations, traveler facts, or trace fixtures: when the
// backend is offline the UI stays empty so it's obvious to the presenter
// that Aurora isn't responding, rather than masking the failure with mocks.

export const SHOWCASE_TRAVELER_ID = 'trv_meridian_demo';

// Empty initial prompt — the chat composer starts as a clean slate so the
// presenter can type their own travel intent on stage.
export const SHOWCASE_INITIAL_PROMPT = '';
