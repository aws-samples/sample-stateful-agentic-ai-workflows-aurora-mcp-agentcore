import { Cable, CheckCircle2, Clock3 } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { deriveMcpContracts } from '../lib/showcaseProof';

/**
 * Condensed MCP tool contract for the Phase 2 rail.
 *
 * The teaching point is which governed tool ran, on which server, and what it
 * did in Aurora. That fits one line per tool; the previous request/Aurora/
 * result definition list spent five lines each and pushed the trace spans
 * below the fold. The full request and result stay available on hover.
 */
export function McpToolContractPanel({ state }: { state: MeridianShowcaseState }) {
  const contracts = deriveMcpContracts(state.traceSpans).slice(0, 3);
  const observedCount = contracts.filter((c) => c.observed).length;

  return (
    <section className="mds-contract-panel is-condensed" aria-label="MCP tool contract">
      <div className="mds-contract-head">
        <span className="mds-contract-icon" aria-hidden="true">
          <Cable size={16} strokeWidth={2.2} />
        </span>
        <div>
          <strong>MCP tool contract</strong>
          <small>
            {observedCount
              ? `${observedCount} observed from trace`
              : 'ready before the next MCP run'}
          </small>
        </div>
      </div>
      <ul className="mds-contract-list">
        {contracts.map((contract) => (
          <li
            className={`mds-contract-row${contract.observed ? ' is-observed' : ''}`}
            key={`${contract.server}-${contract.tool}`}
            title={`${contract.request} → ${contract.auroraOperation} → ${contract.result}`}
          >
            <span className="mds-contract-status" aria-hidden="true">
              {contract.observed ? <CheckCircle2 size={13} /> : <Clock3 size={13} />}
            </span>
            <span className="mds-contract-names">
              <b>{contract.tool}</b>
              <code>{contract.server}</code>
            </span>
            <span className="mds-contract-aurora">{contract.auroraOperation}</span>
            <span className="sr-only">
              {contract.observed ? 'Observed. ' : 'Not yet observed. '}
              Request {contract.request}. Result {contract.result}.
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
