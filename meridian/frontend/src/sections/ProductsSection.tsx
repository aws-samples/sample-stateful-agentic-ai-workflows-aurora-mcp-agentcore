/**
 * ProductsSection — Meridian Pro trip catalog (browse, not ranked search).
 *
 * Cards load from GET /api/packages?featured=true: up to two packages per trip type,
 * ordered by price within each type. Hybrid match % and "match because" copy appear
 * only after a Phase 3+ concierge search — not on this grid.
 */
import { useEffect, useMemo, useState } from 'react';
import { FadeIn } from '../components/FadeIn';
import { useAgentBridge } from '../context/AgentBridge';
import { DEMO_PRODUCTS, DEMO_PROMPT } from '../lib/proDemoData';
import { fetchProducts } from '../api/client';
import type { Product } from '../types';

const GRID_COLS = 3;
/** Full rows only — 9 cards = 3×3 with no orphan column. */
const CATALOG_LIMIT = 9;

/** Static highlights for browse cards (not traveler-specific). */
const TAGS_BY_CATEGORY: Record<string, string[]> = {
  'City Breaks': ['Walkable', 'Refundable'],
  'Beach & Resort': ['Coastal', 'Adults only'],
  'Adventure & Outdoors': ['Hiking', 'Refundable'],
  'Wellness & Luxury': ['Spa', 'Slow pace'],
  'Business travel': ['Lounge access', '1-stop'],
};

function moneyFormat(price: number): string {
  return `$${price.toFixed(0)}`;
}

type SortMode = 'catalog' | 'price-asc' | 'price-desc';

export function ProductsSection() {
  const { openConcierge } = useAgentBridge();
  const [products, setProducts] = useState<Product[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [category, setCategory] = useState<string>('all');
  const [sort, setSort] = useState<SortMode>('catalog');
  const [saved, setSaved] = useState<Set<string>>(new Set());

  useEffect(() => {
    setLoading(true);
    fetchProducts(undefined, CATALOG_LIMIT, true)
      .then((items) => {
        setProducts(items);
        setError(null);
      })
      .catch(() => {
        setProducts(DEMO_PRODUCTS.slice(0, CATALOG_LIMIT));
        setError('Backend offline — showing fixture trips until FastAPI is available.');
      })
      .finally(() => setLoading(false));
  }, []);

  const categories = useMemo(() => {
    const cats = new Set(products.map((p) => p.category));
    return ['all', ...Array.from(cats).sort()];
  }, [products]);

  const visible = useMemo(() => {
    let list = category === 'all' ? products : products.filter((p) => p.category === category);
    if (sort === 'price-asc') list = [...list].sort((a, b) => a.price - b.price);
    if (sort === 'price-desc') list = [...list].sort((a, b) => b.price - a.price);
    if (sort === 'catalog') {
      list = [...list].sort((a, b) => {
        const cat = a.category.localeCompare(b.category);
        if (cat !== 0) return cat;
        return b.price - a.price;
      });
    }
    if (list.length > GRID_COLS) {
      const fullRows = Math.floor(list.length / GRID_COLS) * GRID_COLS;
      list = list.slice(0, fullRows);
    }
    return list;
  }, [products, category, sort]);

  const askAboutTrip = (p: Product) => {
    openConcierge({
      phase: 3,
      prompt: `Tell me about ${p.name} — dates, pricing, and how it compares to similar trips in your catalog.`,
      send: true,
    });
  };

  const searchCatalog = () => {
    openConcierge({
      phase: 3,
      prompt: DEMO_PROMPT,
      send: true,
    });
  };

  return (
    <section id="products" className="mp-section">
      <FadeIn>
        <div className="mp-section-h-row">
          <div className="mp-section-h">
            <div className="mp-label-row">Phase 3 · semantic retrieval</div>
            <h2>Trip catalog in Aurora.</h2>
            <p>
              This grid is a <strong>browse view</strong> — not a ranked search. We show up to two
              packages per trip type (highest price in each category) from the seeded catalog. Every
              package is indexed with Cohere Embed&nbsp;v4 + tsvector for hybrid search. Ask the
              concierge in Phase&nbsp;3+ to see <em>match %</em> and a <em>match because</em> line
              per result — those need a live query, not a persona on this page.
            </p>
          </div>
          <div className="actions">
            <button type="button" className="mp-btn primary sm" onClick={searchCatalog}>
              Run hybrid search ↗
            </button>
            <select
              className="mp-btn ghost sm mp-select"
              value={category}
              onChange={(e) => setCategory(e.target.value)}
              aria-label="Filter by category"
            >
              {categories.map((c) => (
                <option key={c} value={c}>
                  {c === 'all' ? 'All categories' : c}
                </option>
              ))}
            </select>
            <select
              className="mp-btn ghost sm mp-select"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortMode)}
              aria-label="Sort trips"
            >
              <option value="catalog">Sort: catalog picks</option>
              <option value="price-asc">Price: low → high</option>
              <option value="price-desc">Price: high → low</option>
            </select>
          </div>
        </div>
      </FadeIn>

      {error && (
        <div
          style={{
            margin: '0 0 16px',
            padding: '10px 14px',
            background: 'rgba(255,91,31,0.06)',
            border: '1px solid rgba(255,91,31,0.25)',
            borderRadius: 12,
            color: 'var(--mp-accent-2)',
            fontSize: 14,
          }}
        >
          {error}
        </div>
      )}

      {!loading && products.length > 0 && (
        <p className="mp-catalog-note" role="status">
          Showing {visible.length} catalog picks · sorted by{' '}
          {sort === 'catalog' ? 'trip type, then price within type' : sort === 'price-asc' ? 'price ↑' : 'price ↓'}
          {!products.some((p) => p.similarity != null) && ' · no semantic scores until you search in the concierge'}
        </p>
      )}

      <div className="mp-pkg-grid">
        {loading
          ? Array.from({ length: CATALOG_LIMIT }).map((_, i) => (
              <div key={i} className="mp-pkg" style={{ opacity: 0.4 }}>
                <div className="mp-pkg-hero" />
                <div className="mp-pkg-body">
                  <div style={{ height: 14, background: 'var(--mp-rail)', borderRadius: 4 }} />
                  <div
                    style={{
                      height: 10,
                      background: 'var(--mp-rail)',
                      borderRadius: 4,
                      width: '60%',
                      marginTop: 8,
                    }}
                  />
                </div>
              </div>
            ))
          : visible.map((p, i) => {
              const tags = TAGS_BY_CATEGORY[p.category] ?? ['In catalog'];
              const matchPct =
                p.similarity != null ? `${(p.similarity * 100).toFixed(0)}% match` : null;
              return (
                <FadeIn key={p.product_id} delay={i * 0.04}>
                  <article className="mp-pkg">
                    <div className="mp-pkg-hero" data-cat={p.category}>
                      {p.image_url && <img src={p.image_url} alt={p.name} />}
                      <span className="mp-pkg-cat">{p.category}</span>
                      {matchPct ? (
                        <span className="mp-pkg-match">{matchPct}</span>
                      ) : (
                        <span className="mp-pkg-badge">Catalog</span>
                      )}
                      <button
                        type="button"
                        className={`mp-pkg-save${saved.has(p.product_id) ? ' saved' : ''}`}
                        aria-label={saved.has(p.product_id) ? 'Saved' : 'Save'}
                        onClick={(e) => {
                          e.stopPropagation();
                          setSaved((prev) => {
                            const next = new Set(prev);
                            if (next.has(p.product_id)) next.delete(p.product_id);
                            else next.add(p.product_id);
                            return next;
                          });
                        }}
                      >
                        <span style={{ position: 'absolute', left: -9999 }}>
                          {saved.has(p.product_id) ? 'Saved' : 'Save'}
                        </span>
                      </button>
                    </div>
                    <div className="mp-pkg-body">
                      <div className="mp-pkg-name">{p.name}</div>
                      <div className="mp-pkg-where">
                        {p.brand}
                        {p.description
                          ? ` · ${p.description.slice(0, 38)}${p.description.length > 38 ? '…' : ''}`
                          : ''}
                      </div>
                      <div className="mp-pkg-tags">
                        {tags.map((t) => (
                          <span key={t}>{t}</span>
                        ))}
                      </div>
                      <div className="mp-pkg-foot">
                        <div className="mp-pkg-price">
                          <small style={{ marginRight: 4 }}>from</small>
                          {moneyFormat(p.price)}
                        </div>
                        <button
                          type="button"
                          className="mp-pkg-add"
                          aria-label="Ask concierge about this trip"
                          onClick={() => askAboutTrip(p)}
                        >
                          +
                        </button>
                      </div>
                    </div>
                  </article>
                </FadeIn>
              );
            })}
      </div>
    </section>
  );
}
