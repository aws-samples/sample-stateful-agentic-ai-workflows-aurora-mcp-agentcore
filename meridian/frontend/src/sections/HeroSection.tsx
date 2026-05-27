/**
 * HeroSection — Meridian Pro editorial hero with live featured trip card.
 */
import { useEffect, useMemo, useState } from 'react';
import { useAgentBridge } from '../context/AgentBridge';
import { fetchProducts } from '../api/client';
import { DEMO_PRODUCTS, DEMO_PROMPT } from '../lib/proDemoData';
import type { Product } from '../types';

interface HeroSectionProps {
  scrollY: number;
}

const FEATURE_IDS = ['CTY-001', 'BCH-001', 'ADV-001', 'WEL-001', 'CTY-002', 'BCH-004'];
const ROTATE_MS = 6000;

/** Request a wider Unsplash crop so the hero banner stays sharp at 16:10. */
function heroImageSrc(url: string): string {
  return url
    .replace(/w=\d+/i, 'w=1600')
    .replace(/h=\d+/i, 'h=1000');
}

const matchLines: Record<string, string> = {
  'City Breaks':
    'Walkable old town · refundable rate · vegetarian dinners reservable 4 of 6 nights.',
  'Beach & Resort':
    'Adults-only stretch · 1-stop transfer · matches "slow + warm" memory facts.',
  'Adventure & Outdoors':
    'Mountain refuges with refundable holds · matches "refundable + active" preference.',
  'Wellness & Luxury':
    'Spa with daily yoga · slow pace · matches dietary + boutique preferences.',
  'Business travel':
    'Lounge access on layover · early arrival · matches aisle preference.',
};

export function HeroSection({ scrollY: _scrollY }: HeroSectionProps) {
  const { openConcierge } = useAgentBridge();
  const [items, setItems] = useState<Product[]>([]);
  const [index, setIndex] = useState(0);

  useEffect(() => {
    fetchProducts(undefined, 30, false)
      .then((all) => {
        const picks = FEATURE_IDS.map((id) => all.find((p) => p.product_id === id)).filter(
          (p): p is Product => Boolean(p),
        );
        setItems(picks.length > 0 ? picks : all.slice(0, 6));
      })
      .catch(() => {
        const fallbackPicks = FEATURE_IDS.map((id) => DEMO_PRODUCTS.find((p) => p.product_id === id)).filter(
          (p): p is Product => Boolean(p),
        );
        setItems(fallbackPicks.length > 0 ? fallbackPicks : DEMO_PRODUCTS.slice(0, 6));
      });
  }, []);

  useEffect(() => {
    if (items.length < 2) return;
    const t = setInterval(() => setIndex((i) => (i + 1) % items.length), ROTATE_MS);
    return () => clearInterval(t);
  }, [items.length]);

  const current = items[index];
  const matchBecause = useMemo(() => {
    if (!current) {
      return 'Match because: matches "slow + wine country", refundable, veg dinners reservable 4 of 6 nights.';
    }
    const tail = matchLines[current.category] ?? 'matches your stored memory facts.';
    return `Match because: ${tail}`;
  }, [current]);

  const tripId = current ? current.product_id.toLowerCase() : 'trip_2614';

  return (
    <section className="mp-hero">
      <div>
        <div className="mp-label-row">Meridian – agentic travel concierge</div>
        <h1>
          Plan. <em className="serif">Fly.</em> Land.
        </h1>
        <p className="lede">
          An agentic travel concierge that understands intent – not keywords. Ask{' '}
          &ldquo;{DEMO_PROMPT}&rdquo; and a Strands supervisor routes through AgentCore Memory,
          MCP Gateway tools, and Cohere Embed&nbsp;v4 hybrid search on Aurora PostgreSQL – so you
          get the right hotel, flight, and neighborhood. Every tool span traced. Every traveler
          fact remembered.
        </p>
        <div className="mp-hero-cta">
          <button
            type="button"
            className="mp-btn primary"
            onClick={() => openConcierge({ phase: 4, focus: true, prompt: DEMO_PROMPT })}
          >
            Talk to concierge
          </button>
          <button
            type="button"
            className="mp-btn ghost"
            onClick={() => document.getElementById('products')?.scrollIntoView({ behavior: 'smooth' })}
          >
            Browse trips
          </button>
        </div>
        <div className="mp-hero-scale mp-fancy-panel">
          <p className="mp-hero-scale-eyebrow">Same two founders. Same vibe.</p>
          <div className="mp-hero-scale-metric" aria-label="Scale from 50 to 500,000 trips per day">
            <div className="mp-hero-scale-end">
              <span className="mp-hero-scale-num">50</span>
            </div>
            <div className="mp-hero-scale-bridge" aria-hidden="true">
              <span className="mp-hero-scale-bridge-line" />
              <span className="mp-hero-scale-arrow">→</span>
            </div>
            <div className="mp-hero-scale-end is-to">
              <span className="mp-hero-scale-num">500,000</span>
              <span className="mp-hero-scale-unit">trips/day</span>
            </div>
          </div>
          <p className="mp-hero-scale-tail">
            10,000× the trip volume – every recommendation still feels hand-picked
          </p>
        </div>
        <div className="mp-hero-stats">
          <div className="mp-stat"><b>30</b>curated packages</div>
          <div className="mp-stat"><b>5</b>orchestration modes</div>
          <div className="mp-stat"><b>1024d</b>Cohere v4</div>
          <div className="mp-stat"><b>~340ms</b>p50 latency</div>
          <div className="mp-stat"><b>99.8%</b>MCP tool uptime</div>
        </div>
      </div>

      <div
        className="mp-feature mp-feature-clickable"
        role="button"
        tabIndex={0}
        onClick={() =>
          current &&
          openConcierge({
            phase: 4,
            prompt: `We're planning ${current.name}. What should we know before booking?`,
            send: true,
          })
        }
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            if (current) {
              openConcierge({
                phase: 4,
                prompt: `We're planning ${current.name}. What should we know before booking?`,
                send: true,
              });
            }
          }
        }}
      >
        <div className="mp-feature-top">
          <div className="id">
            Currently planning <b>· {tripId}</b>
          </div>
          <div className="badge">held · 12h</div>
        </div>
        <div className="mp-feature-scene">
          {current?.image_url ? (
            <img src={heroImageSrc(current.image_url)} alt={current.name} />
          ) : null}
          <div className="mp-feature-ribbon">
            <div>
              <strong>{current?.name ?? 'Tuscan Vineyards · 7 nights'}</strong>
              <span>
                {current
                  ? `${current.brand} · ${current.category}`
                  : 'Florence + Chianti · May 14–21 · two travelers'}
              </span>
            </div>
            <div className="price">${(current?.price ?? 2840).toFixed(0)}</div>
          </div>
        </div>
        <div className="mp-feature-meta">
          <div className="cell">
            From<b>BOS</b>
          </div>
          <div className="cell">
            Hotel<b>{current?.brand ?? 'Borgo San Felice'}</b>
          </div>
          <div className="cell">
            Refundable<b>Until May 11</b>
          </div>
        </div>
        <div className="mp-feature-why">
          <div className="quote">
            <em>{matchBecause.split(':')[0]}:</em>
            {matchBecause.includes(':') ? matchBecause.slice(matchBecause.indexOf(':') + 1) : ''}
            <div className="src">grounded in your memory · 8 traveler facts · 2 prior trips</div>
          </div>
        </div>
      </div>
    </section>
  );
}
