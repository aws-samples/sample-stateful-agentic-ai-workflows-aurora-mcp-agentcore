import { useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ArrowRight, Check, Clock3, Compass, Heart, MapPin, RefreshCw,
} from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripVisualPhoto } from '../lib/tripVisualPhoto';
import { derivePersonalization } from '../lib/discoveryPersonalization';

const NO_TRIPS: Product[] = [];

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
}

function DestinationPhoto({ src, featured }: { src: string; featured: boolean }) {
  const ref = useRef<HTMLImageElement>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    const photo = ref.current;
    if (!photo) return;
    let active = true;
    const reveal = async () => {
      // onLoad can precede decoding. Keep the reserved image surface until
      // pixels are ready, including when the browser serves a cached photo.
      try { await photo.decode?.(); } catch { /* The fallback stays visible on failure. */ }
      if (active && photo.naturalWidth > 0) setLoaded(true);
    };
    photo.addEventListener('load', reveal);
    if (photo.complete && photo.naturalWidth > 0) void reveal();
    return () => { active = false; photo.removeEventListener('load', reveal); };
  }, [src]);
  return <img ref={ref} src={src} alt="" width="1600" height="900" decoding="async"
    loading={featured ? 'eager' : 'lazy'} className={`mc-destination-photo${loaded ? ' is-loaded' : ''}`}
    {...{ fetchpriority: featured ? 'high' : 'auto' }} />;
}

function TripCard({ product, state, featured = false }: {
  product: Product;
  state: MeridianShowcaseState;
  featured?: boolean;
}) {
  const photo = tripVisualPhoto(product).src;
  const saved = state.savedTripIds.has(product.product_id);
  const profile = state.travelerProfile ?? state.previewProfile;
  const facts = state.memoryFacts.length ? state.memoryFacts : state.previewFacts;
  const match = derivePersonalization(product, profile, facts).find(pill => pill.tone !== 'context');
  return (
    <article className={`mc-trip${featured ? ' is-featured' : ''}`} aria-label={product.name} data-theme={featured ? 'dark' : undefined}>
      <div className="mc-trip-image">
        {photo && <DestinationPhoto key={photo} src={photo} featured={featured} />}
        <span className="mc-trip-image-fallback" aria-hidden="true"><MapPin size={28} /></span>
        <button type="button" className="mc-save" disabled={state.isLoading} onClick={() => state.saveTrip(product)}
          aria-pressed={saved} aria-label={`${saved ? 'Unsave' : 'Save'} ${product.name}`}>
          <Heart size={18} fill={saved ? 'currentColor' : 'none'} aria-hidden="true" />
        </button>
      </div>
      <div className="mc-trip-copy">
        <span className="mc-trip-location">{product.destination ?? product.region ?? product.category}</span>
        <h2>{product.name}</h2>
        <p>{product.description}</p>
        <footer>
          <span className="mc-trip-facts"><span className="mc-trip-meta"><Clock3 size={14} aria-hidden="true" />{product.available_sizes?.[0] ?? 'Flexible duration'}</span>
          <span className="mc-price"><small>From </small><strong>{money(product.price)}</strong><small> / traveler</small></span>
          </span>
          <button type="button" className="mc-trip-open" disabled={state.isLoading} onClick={() => state.openTripDetails(product)} aria-label={`${featured ? 'Explore this trip' : 'Details'}: ${product.name}`}>
            {featured ? 'Explore this trip' : 'Details'}<ArrowRight size={16} aria-hidden="true" />
          </button>
        </footer>
      </div>
      {match && (featured || match.tone === 'caution') && <div className={`mc-trip-match is-${match.tone}`}>
        {match.tone === 'caution' ? <AlertTriangle size={13} aria-hidden="true" /> : <Check size={13} aria-hidden="true" />}
        <span>{match.label}</span>
      </div>}
    </article>
  );
}

// The opening screen has never had invented inventory: the pool below is
// always either the live recommendations from a chat turn, or the live
// Aurora catalog. When that catalog hasn't resolved yet, failed, or came
// back empty, the three blocks after the collection section say so instead
// of standing in a fabricated card.
function CatalogSkeleton() {
  return (
    <section className="mc-collection" aria-label="Travel inspiration">
      <div className="mc-section-heading"><h2>A little inspiration for your next chapter</h2></div>
      <div className="mc-trip-skeleton is-featured" role="status" aria-live="polite"
        aria-label="Loading the live catalog" />
      <div className="mc-supporting-trips" aria-hidden="true">
        <div className="mc-trip-skeleton" />
        <div className="mc-trip-skeleton" />
      </div>
    </section>
  );
}

export function DiscoveryWorkspace({ state, onClear, onDiscover }: {
  state: MeridianShowcaseState;
  onClear: () => void;
  greeting?: string;
  onDiscover?: () => void;
}) {
  const hasTurn = state.messages.length > 0;
  const latestMessage = state.messages[state.messages.length - 1];
  const replyStarted = latestMessage?.role === 'bot' && Boolean(latestMessage.text.trim());
  // Pre-turn, the pool is exactly the live Aurora catalog - never a bundled
  // preview. A zero-result search (post-turn) also stays empty on purpose.
  const pool = hasTurn ? (state.isLoading
    ? (replyStarted ? state.streamingRecommendations ?? NO_TRIPS : NO_TRIPS)
    : state.recommendations) : state.catalog;
  const catalogLoading = !hasTurn && state.catalog.length === 0
    && (state.backendStatus === 'checking' || state.connectionRefreshing);
  const catalogFailed = !hasTurn && !catalogLoading && state.catalog.length === 0
    && state.backendStatus === 'offline';
  const catalogEmpty = !hasTurn && !catalogLoading && !catalogFailed && state.catalog.length === 0;
  const options = useMemo(() => {
    if (hasTurn) return pool.slice(0, 3);
    const profile = state.travelerProfile ?? state.previewProfile;
    const facts = state.memoryFacts.length ? state.memoryFacts : state.previewFacts;
    const score = (product: Product) => derivePersonalization(product, profile, facts, 7)
      .reduce((sum, pill) => sum + (pill.id === 'goal' ? 4 : pill.tone === 'match' ? 1 : pill.tone === 'caution' ? -2 : 0), 0);
    return [...pool].sort((a, b) => score(b) - score(a)).slice(0, 3);
  }, [pool, hasTurn, state.travelerProfile, state.previewProfile, state.memoryFacts, state.previewFacts]);

  return (
    <section className="mc-workspace" aria-label="Meridian concierge">
      <header className="mc-welcome">
        <div><h1>{hasTurn ? 'Let’s make it your kind of trip.' : 'Your next chapter, Jordan.'}</h1>
        <span>A trip that feels like you.</span></div>
        <button type="button" className="mc-text-button mc-walkthrough" onClick={onClear} aria-label="How it works: start the capability ladder at Phase 1">How it works<ArrowRight size={15} aria-hidden="true" /></button>
      </header>

      <button type="button" className="mc-studio-chat-jump" onClick={() => {
        document.querySelector<HTMLTextAreaElement>('#concierge-compose textarea')?.focus();
      }}>Ask your concierge<ArrowRight size={16} aria-hidden="true" /></button>
      {hasTurn && !state.error && <p className="mc-results-status" role="status">
        {state.recoveryRequest ? 'Recovery review' : state.isLoading ? (options.length ? 'Matches found. Checking the final details…' : 'Finding trips for you…')
          : options.length ? `${options.length} ${options.length === 1 ? 'trip' : 'trips'} to explore` : 'No matching trips'}
      </p>}
      {hasTurn && state.isLoading && options.length === 0 && <div className="mc-trip-skeleton is-featured" role="status" aria-label="Updating your trip recommendations" />}
      {hasTurn && state.error && !state.isLoading && <div className="mc-empty-results" role="status">
        <AlertTriangle size={24} aria-hidden="true" /><div><strong>We couldn’t update your trip options.</strong>
          <p>Your concierge has the request details. You can adjust your search or start a new chat.</p>
          <button type="button" className="mc-text-button" onClick={state.clearChat}>Start a new chat<ArrowRight size={16} aria-hidden="true" /></button>
        </div></div>}

      {!hasTurn && catalogLoading && <CatalogSkeleton />}
      {!hasTurn && catalogFailed && <div className="mc-error" role="alert">
        <AlertTriangle size={19} aria-hidden="true" />
        <div><strong>The live catalog is unavailable.</strong>
          <p>{state.connectionIssue ?? 'Meridian could not load the live catalog.'}</p></div>
        <button type="button" onClick={() => void state.refreshConnection()} disabled={state.connectionRefreshing}>
          <RefreshCw size={15} aria-hidden="true" />Check again
        </button>
      </div>}
      {!hasTurn && catalogEmpty && <div className="mc-empty-results"><Compass size={24} aria-hidden="true" />
        <div><strong>Nothing in the collection right now.</strong>
          <p>Aurora returned no trips. Check back soon, or ask your concierge.</p></div></div>}

      {!state.error && !catalogLoading && !catalogFailed && !catalogEmpty
        && options.length > 0 && <section className="mc-collection" aria-label={hasTurn ? 'Your trip recommendations' : 'Travel inspiration'}>
        <TripCard key={options[0].product_id} product={options[0]} state={state} featured />
        <div className="mc-supporting-trips">{options.slice(1).map(product => <TripCard key={product.product_id} product={product} state={state} />)}</div>
        {onDiscover && !state.isLoading && <button type="button" className="mc-text-button" onClick={onDiscover}>Explore more<ArrowRight size={15} aria-hidden="true" /></button>}
        <p className="mc-catalog-note">Meridian collection<span>·</span>USD per traveler. Dates and availability confirmed when you plan.</p>
      </section>}
      {hasTurn && !state.isLoading && !state.error && options.length === 0 && <div className="mc-empty-results"><Compass size={24} aria-hidden="true" /><div>
        {state.recoveryRequest ? <><strong>Review your recovery options</strong><p>Open the recovery plan to find alternatives. You’ll review the shortlist before requesting a hold.</p></>
          : <><strong>A different direction?</strong><p>Try another destination or a wider budget to find more options.</p></>}
      </div></div>}
    </section>
  );
}
