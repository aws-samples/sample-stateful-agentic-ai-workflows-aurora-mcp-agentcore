export const RENAME = {
  '--mc-ink': '--mds-label', '--mds-on-surface': '--mds-label', '--mds-ink': '--mds-label',
  '--proof-ink': '--mds-label', '--app-fg': '--mds-label', '--mds-chrome-nav-hover': '--mds-label',
  '--mds-chrome-nav-active': '--mds-label', '--mds-chrome-brand': '--mds-label',
  '--mds-chrome-account': '--mds-label', '--mds-tooltip-fg': '--mds-label',
  '--mc-muted': '--mds-label-2', '--mds-on-surface-soft': '--mds-label-2',
  '--mds-muted': '--mds-label-2',
  '--mds-chrome-nav': '--mds-label-2', '--mds-chrome-control': '--mds-label-2',
  '--mds-chrome-status': '--mds-label-2', '--proof-dim': '--mds-label-2',
  '--mds-on-surface-faint': '--mds-label-3', '--mds-dim': '--mds-label-3',
  '--mds-chrome-account-muted': '--mds-label-3',
  '--mc-ground': '--mds-ground', '--mds-bg': '--mds-ground', '--mds-app-surface': '--mds-ground',
  '--app-bg': '--mds-ground', '--mds-shell': '--mds-ground',
  '--mds-chrome-sidebar-background': '--mds-ground',
  '--mds-chrome-right-background': '--mds-ground', '--mds-chrome-main-background': '--mds-ground',
  '--mc-surface': '--mds-surface', '--mds-card': '--mds-surface', '--mds-panel': '--mds-surface',
  '--mds-experience-panel': '--mds-surface', '--mds-side-panel-background': '--mds-surface',
  '--proof-panel': '--mds-surface', '--mds-chrome-control-background': '--mds-surface',
  '--mc-soft': '--mds-surface-2', '--mds-shell-elevated': '--mds-surface-2',
  '--mds-panel-2': '--mds-surface-2',
  '--mds-experience-panel-raised': '--mds-surface-2', '--mds-fill-1': '--mds-surface-2',
  '--mds-fill-2': '--mds-surface-2', '--mds-fill-3': '--mds-surface-2',
  '--mds-chrome-nav-hover-background': '--mds-surface-2',
  '--mds-chrome-nav-active-background': '--mds-surface-2',
  '--mds-tooltip-bg': '--mds-surface-2',
  '--app-skeleton': '--mds-skeleton', '--app-skeleton-shimmer': '--mds-skeleton-shimmer',
  '--mc-accent': '--mds-tint', '--mds-recovery-blue': '--mds-blue', '--mc-status': '--mds-blue',
  '--mds-blue-soft': '--mds-blue-fill', '--mds-blue-line': '--mds-blue',
  '--mc-action': '--mds-action', '--mc-action-hover': '--mds-action-hover',
  '--mc-action-edge': '--mds-action',
  '--mc-on-action': '--mds-on-action',
  '--mds-recovery-green': '--mds-green', '--proof-good': '--mds-green',
  '--mds-recovery-yellow': '--mds-yellow', '--mds-memory': '--mds-yellow',
  '--mc-caution': '--mds-yellow',
  '--proof-stop': '--mds-yellow', '--mds-trip-dot': '--mds-yellow',
  '--mds-memory-line': '--mds-yellow',
  '--mds-memory-soft': '--mds-yellow-fill', '--mc-caution-bg': '--mds-yellow-fill',
  '--mds-danger': '--mds-red',
};

export const LINE_VARS = new Set([
  '--mds-line', '--mds-line-strong', '--mc-line', '--mc-divider', '--proof-line',
  '--mds-chrome-sidebar-border', '--mds-chrome-right-border', '--mds-chrome-account-border',
  '--mds-side-panel-border', '--mds-experience-border', '--mds-chrome-nav-hover-border',
  '--mds-chrome-nav-active-border', '--mds-chrome-control-border', '--mds-tooltip-border',
  '--mds-chrome-brand-border', '--mds-chrome-avatar-border',
]);

export const CHANNEL_HUE = {
  '--mds-blue-rgb': 'blue', '--mds-green-rgb': 'green', '--mds-recovery-green-rgb': 'green',
  '--mds-recovery-yellow-rgb': 'yellow', '--mds-teal-rgb': 'cyan',
};

export const DEFINITION_RENAME = { '--mc-controls-height': '--mds-controls-height' };

export const DELETE_DEFINITIONS = new Set([
  ...Object.keys(RENAME), ...LINE_VARS, ...Object.keys(CHANNEL_HUE),
  '--mds-blue', '--mds-green', '--mds-yellow', '--mds-scrim', '--mds-recovery-rail', '--mds-bg-2',
  '--mds-recovery-yellow-strong', '--mds-recovery-red', '--mds-chrome-breadcrumb',
  '--mds-prose-measure',
]);
