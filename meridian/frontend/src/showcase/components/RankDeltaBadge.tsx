/**
 * RankDeltaBadge — shows how far Cohere Rerank 3.5 moved a candidate from its
 * pre-rerank (hybrid pgvector + tsvector) position. Positive delta = promoted
 * (moved up toward the top), negative = demoted. Used on Phase 3 trip cards
 * once the reorder animation has played, so the audience sees the reranker's
 * verdict per result. Token-driven so it reads in both light and dark themes.
 */
interface RankDeltaBadgeProps {
  delta: number;
}

export function RankDeltaBadge({ delta }: RankDeltaBadgeProps) {
  const dir = delta > 0 ? 'up' : delta < 0 ? 'down' : 'flat';
  const glyph = delta > 0 ? '▲' : delta < 0 ? '▼' : '•';
  const label =
    delta > 0
      ? `Reranker promoted this ${delta} ${delta === 1 ? 'place' : 'places'}`
      : delta < 0
        ? `Reranker moved this down ${Math.abs(delta)} ${Math.abs(delta) === 1 ? 'place' : 'places'}`
        : 'Reranker kept this in place';

  return (
    <span className={`mds-rerank-badge is-${dir}`} title={label} aria-label={label}>
      <span aria-hidden="true">{glyph}</span>
      {delta !== 0 && <span aria-hidden="true">{Math.abs(delta)}</span>}
    </span>
  );
}
