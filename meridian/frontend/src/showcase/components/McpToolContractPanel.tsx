import { Cable, CheckCircle2, Clock3 } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { deriveMcpContracts } from '../lib/showcaseProof';

export function McpToolContractPanel({ state }: { state: MeridianShowcaseState }) {
  const contracts = deriveMcpContracts(state.traceSpans).slice(0, 3);
  const observedCount = contracts.filter((c) => c.observed).length;

  return (
    <section className="mds-contract-panel" aria-label="MCP tool contract">
      <div className="mds-contract-head">
        <span className="mds-contract-icon" aria-hidden="true">
          <Cable size={16} strokeWidth={2.2} />
        </span>
        <div>
          <strong>MCP tool contract</strong>
          <small>{observedCount ? `${observedCount} observed from trace` : 'ready before the next MCP run'}</small>
        </div>
      </div>
      <div className="mds-contract-list">
        {contracts.map((contract) => (
          <div
            className={`mds-contract-row${contract.observed ? ' is-observed' : ''}`}
            key={`${contract.server}-${contract.tool}`}
          >
            <div className="mds-contract-row-head">
              <span aria-hidden="true">
                {contract.observed ? <CheckCircle2 size={14} /> : <Clock3 size={14} />}
              </span>
              <b>{contract.tool}</b>
              <code>{contract.server}</code>
            </div>
            <dl>
              <div>
                <dt>Request</dt>
                <dd>{contract.request}</dd>
              </div>
              <div>
                <dt>Aurora</dt>
                <dd>{contract.auroraOperation}</dd>
              </div>
              <div>
                <dt>Result</dt>
                <dd>{contract.result}</dd>
              </div>
            </dl>
          </div>
        ))}
      </div>
    </section>
  );
}
