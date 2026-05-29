/**
 * traceAdapter — convert a backend `ChatResponse` into the Demo Stage span model.
 *
 * Requires a live API response with activities and/or products — no fixture
 * trace synthesis.
 */
import type { ChatResponse, Product } from '../../types';
import type { StageRecommendation, StageScenario } from '../types';
import {
  activityToStageSpan,
  sumSpanLatency as sumLatency,
} from '../../lib/activityToStageSpan';

export { sumLatency };

function heroForProduct(p: Product, idx: number): StageRecommendation['hero'] {
  const cat = (p.category ?? '').toLowerCase();
  if (cat.includes('wine')) return 'wine';
  if (cat.includes('beach') || cat.includes('resort')) return 'beach';
  if (cat.includes('river')) return 'river';
  if (cat.includes('mountain') || cat.includes('alp')) return 'mountain';
  if (cat.includes('city') || cat.includes('business')) return 'city';
  const palette: StageRecommendation['hero'][] = ['wine', 'beach', 'mountain', 'river', 'city'];
  return palette[idx % palette.length];
}

function adaptProducts(products: Product[] | undefined): StageRecommendation[] {
  if (!products?.length) return [];
  return products.slice(0, 3).map((p, idx) => ({
    id: p.product_id,
    title: p.name,
    region: p.brand,
    nights: 7,
    matchPct: Math.round((p.similarity ?? 0.9) * 100),
    priceUsd: Math.round(p.price),
    hero: heroForProduct(p, idx),
    primary: idx === 0,
    rationale: (p.description ?? '').split(/[.,]/).slice(0, 3).map((s) => s.trim()).filter(Boolean),
    // Carry the live Product so the deck can render the showcase TripVisual.
    product: p,
  }));
}

// Turn a snake_case preference key into a human tag word: "no_red_eye" →
// "no red eye", "shellfish allergy" stays as-is. Kept short so the fact
// chips read cleanly in the traveler card.
function humanizeFactKey(key: string): string {
  return key.replace(/_/g, ' ').trim();
}

// Map the live Aurora memory facts (traveler_preferences) into compact
// "key · value" tag strings for the traveler card. Falls back to the
// scenario's hardcoded facts only when the response carries none.
function factsFromResponse(response: ChatResponse, fallback: string[]): string[] {
  const facts = response.memory_facts ?? [];
  if (!facts.length) return fallback;
  return facts
    .map((f) => {
      const k = humanizeFactKey(f.key ?? '');
      const v = (f.value ?? '').trim();
      if (k && v) return `${k} · ${v}`;
      return v || k;
    })
    .filter(Boolean);
}

/**
 * Build a stage scenario from a live chat response and scenario metadata.
 * Returns null when the response lacks both activities and products.
 */
export function adaptChatResponseToScenario(
  response: ChatResponse | null | undefined,
  baseScenario: StageScenario,
): StageScenario | null {
  if (!response) return null;
  const rawActivities = response.activities ?? [];
  if (!rawActivities.length && !response.products?.length) return null;

  const spans = rawActivities.map(activityToStageSpan);
  const recommendations = adaptProducts(response.products);
  const assistantReply = response.message?.trim() ?? '';
  const facts = factsFromResponse(response, baseScenario.traveler.facts);

  return {
    ...baseScenario,
    traveler: { ...baseScenario.traveler, facts },
    traceId: response.conversation_id ?? baseScenario.traceId,
    spans,
    recommendations,
    assistantReply,
    reasoning: spans.map((s) => s.name).join(' → '),
  };
}
