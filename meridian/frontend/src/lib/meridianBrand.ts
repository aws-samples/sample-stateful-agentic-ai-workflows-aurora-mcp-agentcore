/** Meridian aircraft brand tile, bundled and optimized for reliable loading. */
export const MERIDIAN_MARK_SRC = '/brand/meridian-mark.jpg';

/** Brand mark URL (local asset; size handled via CSS). */
export function meridianLogoUrl(_sizePx?: number): string {
  return MERIDIAN_MARK_SRC;
}

export const MERIDIAN_FAVICON_URL = MERIDIAN_MARK_SRC;
