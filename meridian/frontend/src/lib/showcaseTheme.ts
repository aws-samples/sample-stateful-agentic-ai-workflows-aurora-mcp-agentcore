export type ShowcaseTheme = 'dark' | 'light';

const THEME_STORAGE_KEY = 'meridian.theme';

/** Resolve once for the loading screen and again when the showcase mounts. */
export function initialShowcaseTheme(): ShowcaseTheme {
  if (typeof window === 'undefined') return 'light';
  const params = new URLSearchParams(window.location.search);
  const requested = params.get('theme');
  if (requested === 'dark' || requested === 'light') return requested;
  // A saved laptop preference must not change the bookmarked room preset.
  if (params.get('present') === '1') return 'light';
  try {
    const remembered = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (remembered === 'dark' || remembered === 'light') return remembered;
  } catch {
    // Blocked browser storage must not prevent the presentation from opening.
  }
  return 'light';
}

export function rememberShowcaseTheme(theme: ShowcaseTheme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Remembering the toggle is a convenience, never a requirement.
  }
}
