/**
 * MemorySection — Meridian Pro Memory Inspector
 *
 * Wired to /api/memory/{traveler_id}. Shows facts, source, confidence,
 * with a side panel of memory health metrics.
 */
import { useEffect, useMemo, useState } from 'react';
import { FadeIn } from '../components/FadeIn';
import { fetchMemoryProfile } from '../api/client';
import { DEMO_PERSONA_FALLBACK, DEMO_TRAVELER_ID } from '../components/TravelerPersona';
import { PHASE_EYEBROW } from '../lib/phaseLabels';
import { DEMO_MEMORY_FACTS, DEMO_TRAVELER } from '../lib/proDemoData';
import type { LongTermMemoryFact, TravelerProfile } from '../types';

export function MemorySection() {
  const [facts, setFacts] = useState<LongTermMemoryFact[]>(DEMO_MEMORY_FACTS);
  const [profile, setProfile] = useState<TravelerProfile>({ ...DEMO_PERSONA_FALLBACK, ...DEMO_TRAVELER });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetchMemoryProfile(DEMO_TRAVELER_ID);
      if (res.facts?.length) setFacts(res.facts);
      else setFacts([]);
      if (res.profile) setProfile({ ...DEMO_PERSONA_FALLBACK, ...res.profile });
    } catch {
      setFacts(DEMO_MEMORY_FACTS);
      setProfile({ ...DEMO_PERSONA_FALLBACK, ...DEMO_TRAVELER });
      setError('Backend offline — showing fixture traveler memory until Aurora is available.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  useEffect(() => {
    const onMemory = (e: Event) => {
      const detail = (e as CustomEvent<LongTermMemoryFact[]>).detail;
      if (detail?.length) setFacts(detail);
    };
    window.addEventListener('meridian-memory-update', onMemory);
    return () => window.removeEventListener('meridian-memory-update', onMemory);
  }, []);

  const avgConfidence = useMemo(() => {
    const withConf = facts.filter((f) => typeof f.confidence === 'number');
    if (withConf.length === 0) return 0;
    return withConf.reduce((sum, f) => sum + (f.confidence ?? 0), 0) / withConf.length;
  }, [facts]);

  return (
    <section id="memory" className="mp-section">
      <FadeIn>
        <div className="mp-section-h-row">
          <div className="mp-section-h">
            <div className="mp-label-row">{PHASE_EYEBROW[4]}</div>
            <h2>Traveler memory inside the production stack.</h2>
            <p>
              Phase 4 is the production concierge — AgentCore Runtime, Gateway, Memory, and Aurora
              RLS together. This inspector is the durable layer: Alex &amp; Jordan, Tokyo Oct
              12–19, the shellfish allergy in <code>traveler_preferences</code>, scoped per
              traveler, audited every turn, mirrored in AgentCore Memory.
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="mp-btn ghost sm"
              onClick={() => navigator.clipboard?.writeText(JSON.stringify({ profile, facts }, null, 2))}
            >
              Export JSON
            </button>
            <button type="button" className="mp-btn ghost sm" onClick={load} disabled={loading}>
              {loading ? 'Refreshing…' : 'Refresh'}
            </button>
          </div>
        </div>
      </FadeIn>

      <FadeIn delay={0.1}>
        <div className="mp-memory">
          <aside className="mp-memory-side">
            <div className="who">{profile.full_name ?? 'Alex & Jordan Chen'}</div>
            <div className="role">Traveler · {DEMO_TRAVELER_ID}</div>

            <div className="biglabel">Memory health</div>
            <div className="mp-meter">
              <div className="mp-meter-row">
                <span>Long-term facts</span>
                <span><b>{facts.length}</b> / 24 max</span>
              </div>
              <div className="mp-meter-bar">
                <span style={{ width: `${Math.min(100, (facts.length / 24) * 100)}%` }} />
              </div>
            </div>
            <div className="mp-meter">
              <div className="mp-meter-row">
                <span>Confidence avg.</span>
                <span><b>{avgConfidence.toFixed(2)}</b></span>
              </div>
              <div className="mp-meter-bar">
                <span style={{ width: `${avgConfidence * 100}%`, background: 'var(--mp-leaf)' }} />
              </div>
            </div>
            <div className="mp-meter">
              <div className="mp-meter-row">
                <span>Cache hit rate</span>
                <span>71%</span>
              </div>
              <div className="mp-meter-bar">
                <span style={{ width: '71%', background: 'var(--mp-sky)' }} />
              </div>
            </div>

            <div className="biglabel">Provenance</div>
            <div style={{ fontSize: 13, color: 'var(--mp-muted)', lineHeight: 1.55 }}>
              All facts are written by the <code>memory_agent</code> tool — never by the
              supervisor. Edits and deletions are append-only and audit-logged.
            </div>

            {error && (
              <div
                style={{
                  marginTop: 16,
                  fontSize: 13,
                  color: 'var(--mp-accent-2)',
                  padding: '10px 12px',
                  background: 'rgba(255,91,31,0.06)',
                  border: '1px solid rgba(255,91,31,0.25)',
                  borderRadius: 10,
                }}
              >
                {error}
              </div>
            )}
          </aside>

          <div className="mp-memory-table">
            <div
              role="note"
              style={{
                padding: '8px 12px',
                fontSize: 12,
                color: 'var(--mp-muted)',
                background: 'var(--mp-paper-2)',
                borderBottom: '1px solid var(--mp-line)',
                lineHeight: 1.5,
              }}
            >
              <b style={{ color: 'var(--mp-ink)' }}>Demo-only mutations.</b>{' '}
              <code>edit</code> and <code>forget</code> update this view only — they don&apos;t write to
              Aurora. The production memory pipeline mutates <code>traveler_preferences</code> via the{' '}
              <code>memory.write_fact</code> tool (append-only, audit-logged).
            </div>
            <div className="row head">
              <div>Key</div>
              <div>Value</div>
              <div>Source</div>
              <div>Confidence</div>
              <div />
            </div>
            {facts.length === 0 ? (
              <div className="mp-memory-empty">No long-term facts stored yet.</div>
            ) : (
              facts.map((f, i) => (
                <div key={`${f.key}-${i}`} className="row">
                  <div className="key">{f.key}</div>
                  <div className="val">{f.value}</div>
                  <div className="src">{f.source ?? '—'}</div>
                  <div className={`conf${(f.confidence ?? 1) < 0.85 ? ' med' : ''}`}>
                    {typeof f.confidence === 'number' ? f.confidence.toFixed(2) : '—'}
                  </div>
                  <div className="actions">
                    <button
                      type="button"
                      title="Demo only — does not persist to Aurora"
                      aria-label="Edit value (demo only — not persisted)"
                      onClick={() => {
                        const next = window.prompt(
                          `Edit value for "${f.key}" (demo only — does not persist to Aurora)`,
                          f.value,
                        );
                        if (next != null && next.trim()) {
                          setFacts((prev) =>
                            prev.map((row, j) => (j === i ? { ...row, value: next.trim() } : row)),
                          );
                        }
                      }}
                    >
                      edit
                    </button>
                    <button
                      type="button"
                      title="Demo only — does not delete from Aurora"
                      aria-label="Forget fact (demo only — not persisted)"
                      onClick={() => setFacts((prev) => prev.filter((_, j) => j !== i))}
                    >
                      forget
                    </button>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </FadeIn>
    </section>
  );
}
