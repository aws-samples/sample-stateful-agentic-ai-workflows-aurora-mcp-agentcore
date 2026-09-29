import { useEffect, useMemo, useRef } from 'react';
import {
  AlertTriangle, ArrowRight, Check, Clock3, Compass, Heart, MapPin, RefreshCw, X,
} from 'lucide-react';
import { ShowcaseMarkdown } from './ChatTranscript';
import { ConciergeBell } from '../icons/TravelIcons';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripVisualPhoto } from '../lib/tripVisualPhoto';
import { derivePersonalization } from '../lib/discoveryPersonalization';

function money(value: number): string {
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 }).format(value);
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
    <article className={`mc-trip${featured ? ' is-featured' : ''}`} aria-label={product.name}>
      <div className="mc-trip-image">
        {photo ? <img src={photo} alt="" width="1600" height="900" loading={featured ? 'eager' : 'lazy'}
          {...{ fetchpriority: featured ? 'high' : 'auto' }} onError={event => { event.currentTarget.style.visibility = 'hidden'; }} /> : null}
        <span className="mc-trip-image-fallback" aria-hidden="true"><MapPin size={28} /></span>
        <button type="button" className="mc-save" onClick={() => state.saveTrip(product)}
          aria-pressed={saved} aria-label={`${saved ? 'Unsave' : 'Save'} ${product.name}`}>
          <Heart size={18} fill={saved ? 'currentColor' : 'none'} aria-hidden="true" />
        </button>
        {featured && <span className="mc-destination"><MapPin size={14} aria-hidden="true" />{product.destination ?? product.region}</span>}
      </div>
      <div className="mc-trip-copy">
        {!featured && <span className="mc-trip-location">{product.destination ?? product.region ?? product.category}</span>}
        <h3>{product.name}</h3>
        <p>{product.description}</p>
        <div className="mc-trip-meta"><Clock3 size={14} aria-hidden="true" />{product.available_sizes?.[0] ?? 'Flexible duration'}<span>·</span>{product.brand}</div>
        <footer>
          <span className="mc-price"><small>From </small><strong>{money(product.price)}</strong><small> / traveler</small></span>
          <button type="button" className="mc-trip-open" onClick={() => state.openTripDetails(product)} aria-label={`${featured ? 'Explore this trip' : 'Details'}: ${product.name}`}>
            {featured ? 'Explore this trip' : 'Details'}<ArrowRight size={16} aria-hidden="true" />
          </button>
        </footer>
      </div>
      {match && <div className={`mc-trip-match is-${match.tone}`}>
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

export function DiscoveryWorkspace({ state, onClear, greeting, onDiscover }: {
  state: MeridianShowcaseState;
  onClear: () => void;
  greeting: string;
  onDiscover?: () => void;
}) {
  const hasTurn = state.messages.length > 0;
  // Pre-turn, the pool is exactly the live Aurora catalog - never a bundled
  // preview. A zero-result search (post-turn) also stays empty on purpose.
  const pool = hasTurn ? state.recommendations : state.catalog;
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
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (hasTurn) endRef.current?.scrollIntoView?.({ block: 'nearest', behavior: 'instant' });
  }, [state.messages.length, state.isLoading, hasTurn]);

  return (
    <section className="mc-workspace" aria-label="Meridian concierge">
      <header className="mc-welcome">
        <div><p>Good {greeting}, Alex.</p><h1>{hasTurn ? 'Let’s make it your kind of trip.' : 'Where would you like to go?'}</h1>
        <span>{hasTurn ? 'A little planning. More to look forward to.' : 'Somewhere new. Something familiar. A trip that’s yours.'}</span></div>
        <button type="button" className="mc-text-button mc-walkthrough" onClick={onClear} aria-label="How it works: start the capability ladder at Phase 1">How it works<ArrowRight size={15} aria-hidden="true" /></button>
      </header>

      <ol className="mc-conversation" aria-label="Conversation with Meridian">
        {state.messages.map((message, index) => <li key={`${index}-${message.role}`} className={`mc-message is-${message.role}`}>
          <span className="mc-message-author">{message.role === 'user' ? 'You' : <>
            <ConciergeBell size={16} />Meridian
            {message.modelLabel && <span className="mc-message-model">· {message.modelLabel}</span>}
          </>}</span>
          {message.role === 'user' ? <p>{message.text}</p> : <ShowcaseMarkdown source={message.text} />}
        </li>)}
      </ol>
      {state.isLoading && <div className="mc-loading" role="status"><ConciergeBell size={18} /><span>Finding the right options for you…</span><span className="mc-loading-dots" aria-hidden="true">•••</span></div>}
      {state.error && <div className="mc-error" role="alert"><AlertTriangle size={19} aria-hidden="true" /><div><strong>A request needs attention.</strong><p>{state.error}</p></div>
        <button type="button" onClick={state.clearError} disabled={state.isLoading}><X size={15} />Dismiss</button></div>}

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
          <p>Aurora returned no trips. Check back soon, or start a search below.</p></div></div>}

      {!state.isLoading && !state.error && !catalogLoading && !catalogFailed && !catalogEmpty
        && options.length > 0 && <section className="mc-collection" aria-label={hasTurn ? 'Your trip recommendations' : 'Travel inspiration'}>
        <div className="mc-section-heading"><h2>{hasTurn ? 'Worth a closer look' : 'A little inspiration for your next chapter'}</h2>
          {onDiscover && <button type="button" className="mc-text-button" onClick={onDiscover}>Explore more<ArrowRight size={15} aria-hidden="true" /></button>}
        </div>
        <TripCard product={options[0]} state={state} featured />
        <div className="mc-supporting-trips">{options.slice(1).map(product => <TripCard key={product.product_id} product={product} state={state} />)}</div>
        <p className="mc-catalog-note">Meridian collection<span>·</span>USD per traveler. Dates and availability confirmed when you plan.</p>
      </section>}
      {hasTurn && !state.isLoading && !state.error && options.length === 0 && <div className="mc-empty-results"><Compass size={24} aria-hidden="true" /><div><strong>A different direction?</strong><p>Try another destination or a wider budget to find more options.</p></div></div>}
      <div ref={endRef} />
    </section>
  );
}
