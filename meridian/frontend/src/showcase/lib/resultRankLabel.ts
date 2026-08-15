export function resultRankLabel(
  phase: number,
  index: number,
  options: { preRerank?: boolean; saved?: boolean } = {},
): string {
  if (options.saved) return 'Saved';

  if (phase === 3) {
    if (options.preRerank) return `Hybrid #${index + 1}`;
    return index === 0 ? 'Best fit' : `Ranked #${index + 1}`;
  }
  if (phase === 4) {
    return index === 0 ? 'Personalized' : `Ranked #${index + 1}`;
  }
  if (phase === 5) {
    return index === 0 ? 'Recovery pick' : `Plan option #${index + 1}`;
  }
  return 'Live catalog';
}
