import type { ReactNode } from 'react';
import {
  ArrowRight,
  BadgeCheck,
  BedDouble,
  Bookmark,
  BusFront,
  CheckCircle2,
  Database,
  GitCompareArrows,
  HeartHandshake,
  MapPin,
  ShieldCheck,
  Sparkles,
  Users,
} from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripCardPresentation } from '../lib/tripCardPresentation';
import { TripVisual } from './TripVisual';

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

type SignalTone = 'blue' | 'green' | 'yellow' | 'violet';

interface TripSignal {
  label: string;
  tone: SignalTone;
  icon: typeof Sparkles;
}

function includesAny(source: string, patterns: RegExp[]): boolean {
  return patterns.some((pattern) => pattern.test(source));
}

function tripSignals(
  product: Product,
  state: MeridianShowcaseState,
  featured: boolean,
): TripSignal[] {
  const source = [
    product.name,
    product.category,
    product.description,
    ...(product.highlights ?? []),
  ]
    .join(' ')
    .toLowerCase();
  const signals: TripSignal[] = [];
  const travelerContextObserved =
    state.selectedPhase >= 4 &&
    state.memoryEnabled &&
    (state.memoryFacts.length > 0 || Boolean(state.travelerProfile));
  const checkpointObserved =
    state.selectedPhase === 5 &&
    (state.workflowStatus === 'paused' ||
      state.workflowStatus === 'resumed' ||
      state.traceSpans.some((span) =>
        /checkpoint|persist|postgres.?saver|aurora state/i.test(
          `${span.name} ${span.details ?? ''}`,
        ),
      ));

  if (checkpointObserved) {
    signals.push({ label: 'Checkpointed', tone: 'blue', icon: Database });
  }
  if (travelerContextObserved) {
    signals.push({ label: 'Memory match', tone: 'violet', icon: Sparkles });
  }
  if (travelerContextObserved && state.travelerProfile?.party_size) {
    const travelers = state.travelerProfile.party_size;
    signals.push({
      label: `${travelers} ${travelers === 1 ? 'traveler' : 'travelers'}`,
      tone: 'blue',
      icon: Users,
    });
  }
  if (
    travelerContextObserved &&
    includesAny(source, [/hotel/, /boutique/, /ryokan/, /villa/, /quiet floor/])
  ) {
    signals.push({ label: 'Preferred stay', tone: 'violet', icon: BedDouble });
  }
  if (includesAny(source, [/lounge/, /late check-?out/, /fast wi-?fi/])) {
    signals.push({ label: 'Lounge access', tone: 'green', icon: BadgeCheck });
  }
  if (includesAny(source, [/airport transfer/, /car service/, /seaplane transfer/])) {
    signals.push({ label: 'Airport transfer', tone: 'yellow', icon: BusFront });
  }
  if (
    signals.length < (featured ? 4 : 3) &&
    includesAny(source, [/food/, /kaiseki/, /dinner/, /cuisine/, /wine/, /cooking/])
  ) {
    signals.push({ label: 'Dining match', tone: 'green', icon: HeartHandshake });
  }
  if (
    travelerContextObserved &&
    signals.length < (featured ? 4 : 3) &&
    state.travelerProfile?.loyalty_programs
  ) {
    signals.push({ label: 'Elite status recognized', tone: 'blue', icon: ShieldCheck });
  }

  return signals.slice(0, featured ? 4 : 3);
}

export function TripResultCardContent({
  product,
  state,
  matchPct,
  matchLabel,
  matchExtra,
  compact = false,
  featured = false,
}: {
  product: Product;
  state: MeridianShowcaseState;
  matchPct: number | null;
  matchLabel?: string | null;
  matchExtra?: ReactNode;
  compact?: boolean;
  featured?: boolean;
}) {
  const facts = tripCardPresentation(product);
  const saved = state.savedTripIds.has(product.product_id);
  const signals = tripSignals(product, state, featured);
  const rankLabel =
    matchLabel ?? (matchPct != null ? `${matchPct}% match` : 'Live catalog');

  return (
    <>
      <div className="mds-trip-result-media">
        <TripVisual product={product} compact />
        <span className="mds-trip-result-media-shade" />
        <span className="mds-trip-result-destination">
          <MapPin size={12} />
          {facts.destination}
        </span>
        <span className="mds-trip-result-match">
          <i aria-hidden="true" />
          {rankLabel}
          {matchExtra}
        </span>
      </div>

      <div className="mds-trip-result-body">
        <div className="mds-trip-result-summary">
          <div className="mds-trip-result-heading">
            <span>{facts.region}</span>
            <strong>{product.name}</strong>
            <small>{product.brand}</small>
          </div>
          {product.description && (
            <p className="mds-trip-result-description">{product.description}</p>
          )}
          {signals.length > 0 && (
            <div className="mds-trip-result-signals" aria-label="Why this trip matches">
              {signals.map((signal) => {
                const Icon = signal.icon;
                return (
                  <span key={signal.label} className={`is-${signal.tone}`}>
                    <Icon size={featured ? 13 : 12} aria-hidden="true" />
                    {signal.label}
                  </span>
                );
              })}
            </div>
          )}
        </div>

        <div className="mds-trip-result-commerce">
          <span className="mds-trip-result-fact">
            <b>Trip length</b>
            <em>{facts.duration}</em>
          </span>
          <div className="mds-trip-result-price">
            <span>From</span>
            <b>{money(product.price)}</b>
            <small>per traveler</small>
          </div>
          <div
            className={`mds-trip-result-fact is-availability${
              facts.availabilityLow ? ' is-low' : ''
            }`}
          >
            <b>Availability</b>
            <em>
              <CheckCircle2 size={13} aria-hidden="true" />
              {facts.availability}
            </em>
            {!compact && (
              <div
                className="mds-trip-result-actions"
                onClick={(event) => event.stopPropagation()}
              >
                <button
                  type="button"
                  className="is-details"
                  onClick={() => state.openTripDetails(product)}
                >
                  View details
                  <ArrowRight size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="is-icon"
                  onClick={() => state.compareTrip(product)}
                  aria-label={`Compare ${product.name}`}
                  title="Add to comparison"
                >
                  <GitCompareArrows size={14} aria-hidden="true" />
                </button>
                <button
                  type="button"
                  className="is-icon"
                  onClick={() => state.saveTrip(product)}
                  aria-label={saved ? `Remove ${product.name} from saved trips` : `Save ${product.name}`}
                  aria-pressed={saved}
                  title={saved ? 'Saved' : 'Save trip'}
                >
                  <Bookmark size={15} fill={saved ? 'currentColor' : 'none'} aria-hidden="true" />
                </button>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
