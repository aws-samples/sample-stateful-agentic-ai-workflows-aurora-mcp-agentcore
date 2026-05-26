/**
 * Meridian brand mark — Unsplash travel photography (bundled for reliable loading).
 * Source: https://unsplash.com/photos/snow-covered-mountain-1506905925346
 */
export const MERIDIAN_MARK_SRC = '/brand/meridian-mark.jpg';

/** Brand mark URL (local asset; size handled via CSS). */
export function meridianLogoUrl(_sizePx?: number): string {
  return MERIDIAN_MARK_SRC;
}

export const MERIDIAN_FAVICON_URL = MERIDIAN_MARK_SRC;
