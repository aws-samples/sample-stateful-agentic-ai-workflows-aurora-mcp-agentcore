/** Meridian monogram, shared by the app shell and favicon. */
// The query string is a cache key for the mark: bump it when the artwork changes.
export const MERIDIAN_MARK_SRC = '/brand/meridian-mark.svg?v=3';

/** Brand mark URL (local asset; size handled via CSS). */
export function meridianLogoUrl(_sizePx?: number): string {
  return MERIDIAN_MARK_SRC;
}

export const MERIDIAN_FAVICON_URL = MERIDIAN_MARK_SRC;
