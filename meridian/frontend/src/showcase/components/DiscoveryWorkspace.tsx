import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle,
  ArrowRight,
  Check,
  Clock3,
  Database,
  Eraser,
  Heart,
  Lock,
  MapPin,
  Pause,
  Play,
  Sparkles,
} from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripVisualPhoto } from '../lib/tripVisualPhoto';
import { derivePersonalization } from '../lib/discoveryPersonalization';
import { prefersReducedMotion } from '../lib/prefersReducedMotion';

/** How long each trip holds the hero before the next one takes over. */
const ROTATE_MS = 7000;

const CATALOG_PREVIEW: Product[] = [
  {
    product_id: 'WEL-005',
    name: 'Tuscany Wine & Wellness',
    brand: 'Trafalgar',
    price: 3699,
    description:
      'Villa stay, vineyard tours, cooking class, and an optional truffle-season add-on.',
    image_url: '/travel/catalog/WEL-005.jpg',
    category: 'Wellness & Luxury',
    destination: 'Chianti',
    region: 'Europe',
    available_sizes: ['6 nights', '8 nights'],
    availability: { '6 nights': 5, '8 nights': 3 },
    highlights: ['vineyard tours', 'cooking class'],
  },
  {
    product_id: 'TKY-003',
    name: 'Tokyo Executive Stopover',
    brand: 'JAL Premium',
    price: 1949,
    description:
      'A Marunouchi stay with Haneda lounge access, car service, late checkout, and a quiet floor.',
    image_url: '/travel/catalog/TKY-003.jpg',
    category: 'Business Travel',
    destination: 'Tokyo',
    region: 'Asia-Pacific',
    available_sizes: ['2 nights', '3 nights', '4 nights'],
    availability: { '2 nights': 14, '3 nights': 11, '4 nights': 8 },
    highlights: ['lounge access', 'car service', 'quiet floor'],
  },
  {
    product_id: 'CTY-002',
    name: 'Tokyo Culture & Cuisine',
    brand: 'ANA Holidays',
    price: 2499,
    description:
      'A Shibuya base with a Tsukiji breakfast tour, teamLab, Hakone, and a rail pass.',
    image_url: '/travel/catalog/CTY-002.jpg',
    category: 'City Breaks',
    destination: 'Tokyo',
    region: 'Asia-Pacific',
    available_sizes: ['5 nights', '7 nights'],
    availability: { '5 nights': 10, '7 nights': 7 },
    highlights: ['rail pass', 'kaiseki dinner'],
  },
];

function money(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 0,
  }).format(value);
}

function availabilityLabel(product: Product): string {
  const inventory = Object.values(product.availability ?? {}).filter(
    (value): value is number => Number.isFinite(value),
  );
  if (inventory.length > 0) {
    const total = inventory.reduce((sum, value) => sum + value, 0);
    return `${total} places across ${inventory.length} stays`;
  }
  const durations = product.available_sizes?.length ?? 0;
  return durations > 0
    ? `${durations} stay lengths`
    : 'Availability checked during search';
}

function discoverySignals(product: Product): string[] {
  const highlights = (product.highlights ?? []).slice(0, 2);
  const duration = product.available_sizes?.[0];
  return [...highlights, duration].filter(
    (value): value is string => Boolean(value),
  );
}

function travelerContext(state: MeridianShowcaseState): string {
  const profile = state.travelerProfile;
  if (!profile) return 'Traveler context loads from Aurora';

  const facts = [
    profile.home_airport ? `${profile.home_airport} home airport` : null,
    profile.party_size
      ? `${profile.party_size} ${profile.party_size === 1 ? 'traveler' : 'travelers'}`
      : null,
    Object.keys(profile.loyalty_programs ?? {}).length > 0
      ? `${Object.keys(profile.loyalty_programs ?? {}).length} loyalty profiles`
      : null,
  ].filter(Boolean);

  return facts.join(', ');
}

function DiscoveryTrip({
  product,
  featured,
  onView,
  onSave,
}: {
  product: Product;
  featured: boolean;
  onView: () => void;
  onSave: () => void;
}) {
  const photo = tripVisualPhoto(product).src;

  return (
    <article
      className={`mds-discovery-trip${featured ? ' is-featured' : ''}`}
      aria-label={product.name}
    >
      {photo && (
        <img
          src={photo}
          alt=""
          width="1600"
          height="900"
          loading={featured ? 'eager' : 'lazy'}
        />
      )}
      <span className="mds-discovery-scrim" aria-hidden="true" />
      <button
        type="button"
        className="mds-discovery-save"
        onClick={onSave}
        aria-label={`Save ${product.name}`}
      >
        <Heart size={17} aria-hidden="true" />
      </button>
      <div className="mds-discovery-trip-copy">
        <span className="mds-discovery-location">
          <MapPin size={14} aria-hidden="true" />
          {product.destination ?? product.region ?? product.category}
        </span>
        <h2>{product.name}</h2>
        {featured && <p>{product.description}</p>}
        <div className="mds-discovery-signals">
          {discoverySignals(product).map((signal) => (
            <span key={signal}>
              <Check size={12} aria-hidden="true" />
              {signal}
            </span>
          ))}
        </div>
        <footer>
          <span>
            <small>From</small>
            <strong>{money(product.price)}</strong>
            <em>per traveler</em>
          </span>
          <span className="mds-discovery-availability">
            <Clock3 size={14} aria-hidden="true" />
            {availabilityLabel(product)}
          </span>
          <button type="button" onClick={onView}>
            View trip
            <ArrowRight size={16} aria-hidden="true" />
          </button>
        </footer>
      </div>
    </article>
  );
}

function PersonalizationPills({
  state,
  product,
}: {
  state: MeridianShowcaseState;
  product: Product | undefined;
}) {
  const pills = useMemo(
    () => derivePersonalization(product, state.travelerProfile, state.memoryFacts),
    [product, state.travelerProfile, state.memoryFacts],
  );

  if (pills.length === 0) {
    // The showcase starts with traveler context disconnected on purpose -
    // Production is where it gets authorized. Say that, rather than implying
    // a load is in flight or inventing personalization we have not earned.
    return (
      <p className="mds-discovery-pills-empty" role="status">
        <Lock size={13} aria-hidden="true" />
        {state.memoryLoading
          ? 'Reading traveler context from Aurora…'
          : 'Traveler context is disconnected. Phase 4 authorizes the workload for Alex, and these become live preference matches.'}
      </p>
    );
  }

  return (
    <ul
      className="mds-discovery-pills"
      aria-label={`Why Meridian is showing ${product?.name ?? 'this trip'}`}
    >
      {pills.map((pill) => (
        <li key={pill.id} className={`mds-discovery-pill is-${pill.tone}`}>
          <span className="mds-discovery-pill-icon" aria-hidden="true">
            {pill.tone === 'caution' ? (
              <AlertTriangle size={12} />
            ) : pill.tone === 'match' ? (
              <Check size={12} />
            ) : (
              <Database size={12} />
            )}
          </span>
          <span className="mds-discovery-pill-label">{pill.label}</span>
          <span className="mds-discovery-pill-source">{pill.source}</span>
        </li>
      ))}
    </ul>
  );
}

export function DiscoveryWorkspace({
  state,
  onClear,
  greeting,
}: {
  state: MeridianShowcaseState;
  onClear: () => void;
  greeting: string;
}) {
  // Prefer the live recommendation set, then the live catalog, and only fall
  // back to the bundled preview when Aurora has not answered yet.
  const pool =
    state.recommendations.length > 0
      ? state.recommendations
      : state.catalog.length > 0
        ? state.catalog
        : CATALOG_PREVIEW;

  const [rotationIndex, setRotationIndex] = useState(0);
  const [paused, setPaused] = useState(prefersReducedMotion);
  const rotationRef = useRef<number | null>(null);

  // Keep the index in range when the pool changes underneath the rotation.
  useEffect(() => {
    setRotationIndex((index) => (index < pool.length ? index : 0));
  }, [pool.length]);

  useEffect(() => {
    if (paused || pool.length < 2) return;
    rotationRef.current = window.setInterval(() => {
      setRotationIndex((index) => (index + 1) % pool.length);
    }, ROTATE_MS);
    return () => {
      if (rotationRef.current !== null) window.clearInterval(rotationRef.current);
    };
  }, [paused, pool.length]);

  const featured = pool[rotationIndex % pool.length];
  const supporting = pool
    .filter((_, index) => index !== rotationIndex % pool.length)
    .slice(0, 2);
  const sourceLabel =
    state.recommendations.length > 0
      ? 'Current recommendation set'
      : state.catalog.length > 0
        ? 'Live Aurora catalog'
        : 'Meridian catalog preview';

  return (
    <section className="mds-discovery-workspace" aria-label="Meridian discovery">
      <header className="mds-discovery-heading">
        <div>
          <h1>{`Good ${greeting}, Alex.`}</h1>
          <p>Travel ideas shaped around the way you already travel.</p>
        </div>
        <div className="mds-discovery-heading-actions">
          <span className="mds-discovery-context">
            <Sparkles size={16} aria-hidden="true" />
            <span>
              <strong>{sourceLabel}</strong>
              {travelerContext(state)}
            </span>
          </span>
          <button
            type="button"
            className="mds-discovery-clear"
            onClick={onClear}
            aria-label="Clear discovery and start the capability ladder"
          >
            <Eraser size={16} aria-hidden="true" />
            Clear
          </button>
        </div>
      </header>

      <PersonalizationPills state={state} product={featured} />

      <div className="mds-discovery-grid">
        <div className="mds-discovery-featured" key={featured?.product_id}>
          <DiscoveryTrip
            product={featured}
            featured
            onView={() => state.openTripDetails(featured)}
            onSave={() => state.saveTrip(featured)}
          />
          {pool.length > 1 && (
            <div className="mds-discovery-rotation">
              <button
                type="button"
                className="mds-discovery-rotation-toggle"
                onClick={() => setPaused((value) => !value)}
                aria-label={paused ? 'Resume catalog rotation' : 'Pause catalog rotation'}
              >
                {paused ? <Play size={13} /> : <Pause size={13} />}
              </button>
              <ol aria-label="Catalog rotation">
                {pool.slice(0, 8).map((item, index) => (
                  <li key={item.product_id}>
                    <button
                      type="button"
                      className={index === rotationIndex % pool.length ? 'is-active' : ''}
                      aria-current={index === rotationIndex % pool.length ? 'true' : undefined}
                      aria-label={item.name}
                      onClick={() => {
                        setPaused(true);
                        setRotationIndex(index);
                      }}
                    />
                  </li>
                ))}
              </ol>
            </div>
          )}
        </div>
        {supporting.map((product) => (
          <DiscoveryTrip
            key={product.product_id}
            product={product}
            featured={false}
            onView={() => state.openTripDetails(product)}
            onSave={() => state.saveTrip(product)}
          />
        ))}
      </div>
    </section>
  );
}
