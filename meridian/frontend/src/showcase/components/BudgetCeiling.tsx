/** The saved budget, stated the way the gateway policy uses it.
 *
 * The traveler saves a per-traveler cap; Cedar judges a hold or a booking against
 * that cap times the party. Showing only one of the two invites the wrong
 * subtraction, so every surface shows both, in the same words.
 */
export function BudgetCeiling({ perTravelerCents, travelers }: {
  perTravelerCents?: number | null;
  travelers: number;
}) {
  if (perTravelerCents == null || !Number.isFinite(perTravelerCents)) return <>Not set</>;
  const perTraveler = perTravelerCents / 100;
  const party = Math.max(1, travelers);
  const money = (value: number) => `$${value.toLocaleString('en-US')}`;
  return <>
    {money(perTraveler)} per traveler
    {party > 1 && <small className="mc-budget-basis">{money(perTraveler * party)} for {party} travelers</small>}
  </>;
}
