export function RouteSkeleton() {
  return (
    <div className="route-skeleton" role="status" aria-live="polite">
      <span className="sr-only">Loading Meridian</span>
      <aside className="route-skeleton-sidebar" aria-hidden="true">
        <i className="route-skeleton-brand" />
        <i />
        <i />
        <i />
      </aside>
      <main className="route-skeleton-main" aria-hidden="true">
        <div className="route-skeleton-toolbar" />
        <div className="route-skeleton-copy">
          <i />
          <i />
        </div>
        <div className="route-skeleton-grid">
          <i className="is-featured" />
          <i />
          <i />
        </div>
      </main>
    </div>
  );
}
