import { useEffect, useMemo, useRef } from 'react';
import { AlertTriangle, ArrowRight, Check, Clock3, Compass, Heart, MapPin, RotateCcw } from 'lucide-react';
import { ShowcaseMarkdown } from './ChatTranscript';
import { ConciergeBell } from '../icons/TravelIcons';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripVisualPhoto } from '../lib/tripVisualPhoto';
import { derivePersonalization } from '../lib/discoveryPersonalization';

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

export function DiscoveryWorkspace({ state, onClear, greeting, onDiscover }: {
  state: MeridianShowcaseState;
  onClear: () => void;
  greeting: string;
  onDiscover?: () => void;
}) {
  const hasTurn = state.messages.length > 0;
  // A zero-result search stays empty. Bundled inspiration is only for the opening.
  const pool = hasTurn ? state.recommendations : state.catalog.length ? state.catalog : CATALOG_PREVIEW;
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
          <span className="mc-message-author">{message.role === 'user' ? 'You' : <><ConciergeBell size={16} />Meridian</>}</span>
          {message.role === 'user' ? <p>{message.text}</p> : <ShowcaseMarkdown source={message.text} />}
        </li>)}
      </ol>
      {state.isLoading && <div className="mc-loading" role="status"><ConciergeBell size={18} /><span>Finding the right options for you…</span><span className="mc-loading-dots" aria-hidden="true">•••</span></div>}
      {state.error && <div className="mc-error" role="alert"><AlertTriangle size={19} aria-hidden="true" /><div><strong>We couldn’t complete that request.</strong><p>Your conversation is still here. Please try again.</p></div>
        <button type="button" onClick={() => { state.clearError(); void state.replayLastPrompt(); }} disabled={state.isLoading}><RotateCcw size={15} />Try again</button></div>}

      {!state.isLoading && !state.error && options.length > 0 && <section className="mc-collection" aria-label={hasTurn ? 'Your trip recommendations' : 'Travel inspiration'}>
        <div className="mc-section-heading"><h2>{hasTurn ? 'Worth a closer look' : 'A little inspiration for your next chapter'}</h2>
          {onDiscover && <button type="button" className="mc-text-button" onClick={onDiscover}>Explore more<ArrowRight size={15} aria-hidden="true" /></button>}
        </div>
        <TripCard product={options[0]} state={state} featured />
        <div className="mc-supporting-trips">{options.slice(1).map(product => <TripCard key={product.product_id} product={product} state={state} />)}</div>
        <p className="mc-catalog-note">{hasTurn || state.catalog.length ? 'Meridian collection' : 'A preview of the Meridian collection'}<span>·</span>USD per traveler. Dates and availability confirmed when you plan.</p>
      </section>}
      {hasTurn && !state.isLoading && !state.error && options.length === 0 && <div className="mc-empty-results"><Compass size={24} aria-hidden="true" /><div><strong>A different direction?</strong><p>Try another destination or a wider budget to find more options.</p></div></div>}
      <div ref={endRef} />
    </section>
  );
}
