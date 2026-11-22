import { ServiceMark } from '../components/ServiceMark';
import type { Hop, StepPathData } from './briefingSteps';
import { laneStyle, useStepper } from './stepPathState';
import './briefingSteps.css';

/**
 * Back, Next step and Start over. Ends use aria-disabled rather than disabled so the focused
 * button keeps focus when the path reaches its first or last step.
 */
export function StepControls({ index, count, label, onBack, onNext, onReset }: {
  index: number; count: number; label: string;
  onBack: () => void; onNext: () => void; onReset: () => void;
}) {
  const first = index === 0;
  const last = index === count - 1;
  const unless = (blocked: boolean, action: () => void) => () => { if (!blocked) action(); };
  return <div className="mds-step-controls" role="group" aria-label={`${label} controls`}>
    <button type="button" className="mds-step-button" aria-disabled={first} onClick={unless(first, onBack)}>Back</button>
    <button type="button" className="mds-step-button is-primary" aria-disabled={last} onClick={unless(last, onNext)}>Next step</button>
    <button type="button" className="mds-step-button" aria-disabled={first} onClick={unless(first, onReset)}>Start over</button>
    <span className="mds-step-count" aria-hidden="true">Step {index + 1} of {count}</span>
  </div>;
}

export function HopMark({ hop }: { hop: Hop }) {
  if (hop.service) return <ServiceMark name={hop.service} size={40} />;
  return hop.glyph ? <span className="mds-step-glyph" aria-hidden="true">{hop.glyph}</span> : null;
}

export function HopBody({ hop, label }: { hop: Hop; label?: string }) {
  return <>
    {label && <span className="mds-step-hop-label">{label}</span>}
    <HopMark hop={hop} />
    <div className="mds-step-hop-text"><strong>{hop.title}</strong><span>{hop.detail}</span></div>
    {hop.branches && <dl className="mds-step-branches">{hop.branches.map(([name, value]) => (
      <div key={name}><dt>{name}</dt><dd>{value}</dd></div>
    ))}</dl>}
    {hop.image && <img className="mds-step-image" src={hop.image.src} alt={hop.image.alt} width={320} height={180} loading="lazy" />}
  </>;
}

/** A linear path of hops: one idea per click, captioned in a polite live region. */
export function StepPath({ data, note }: { data: StepPathData; note?: string }) {
  const { index, back, next, reset } = useStepper(data.steps.length);
  const step = data.steps[index];
  const finished = index === data.steps.length - 1;
  const state = (hop: number) => {
    if (hop < step.hop || (finished && hop === step.hop)) return 'is-done';
    return hop === step.hop ? 'is-current' : 'is-ahead';
  };
  return <div className="mds-step-path">
    <div className="mds-step-track" style={laneStyle(data.hops.length, step.hop)}>
      <span className="mds-step-chip" aria-hidden="true">{step.chip}</span>
      <ol className="mds-step-lane" aria-label={data.label}>
        {data.hops.map((hop, position) => (
          <li key={hop.title} className={`mds-step-hop ${state(position)}`} aria-current={position === step.hop ? 'step' : undefined}>
            <HopBody hop={hop} />
            {position === step.hop && <span className="mds-step-chip-inline" aria-hidden="true">{step.chip}</span>}
          </li>
        ))}
      </ol>
    </div>
    <p className="mds-step-caption" aria-live="polite"><span className="mds-sr-only">Step {index + 1} of {data.steps.length}. </span>{step.caption}</p>
    <StepControls index={index} count={data.steps.length} label={data.label} onBack={back} onNext={next} onReset={reset} />
    {note && <p className="mds-step-note">{note}</p>}
  </div>;
}
