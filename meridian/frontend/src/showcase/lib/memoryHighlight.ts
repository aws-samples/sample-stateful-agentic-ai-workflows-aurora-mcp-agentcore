/**
 * memoryHighlight — mark recalled-from-memory phrases inside Production+ replies.
 *
 * A rehype plugin that walks the markdown-derived hast tree and wraps phrases
 * the Production concierge recalls from Aurora-backed memory (rather than from
 * the prompt) in `<mark class="mds-memory-highlight">` element nodes. Running
 * on hast (post-markdown) means it survives ReactMarkdown's `skipHtml` and
 * re-applies cleanly to each streamed prefix, so a phrase highlights the
 * instant the typewriter fully reveals it.
 */

// Phrases surfaced from traveler_preferences / profile for the demo persona.
// Specific alternatives come first so e.g. "shellfish allergy" wins over a
// bare "shellfish", and "boutique-over-chain" over "boutique".
const MEMORY_PHRASE_SOURCE = [
  'shellfish(?:\\s+allerg\\w+)?',
  'party of (?:two|2)',
  '(?:two|2)\\s+travelers',
  'home airport',
  '\\bJFK\\b',
  'no[-\\s]red[-\\s]?eyes?',
  'red[-\\s]?eyes?',
  'boutique(?:[-\\s](?:over|>)[-\\s]?chain)?',
  '(?:Marriott\\s+)?Bonvoy(?:\\s+Platinum(?:\\s+Elite)?)?',
  'Platinum Elite',
  'vegetarian(?:[-\\s]friendly)?',
  'Oct(?:ober)?\\.?\\s*12\\s*[–-]\\s*19',
].join('|');

// Minimal hast shape — enough to walk and rewrite text nodes without pulling
// in @types/hast just for this transform.
export interface HastNode {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
}

/**
 * Split one text node's value into text + `<mark>` element nodes on each memory
 * phrase match.
 *
 * @param value Raw text content of a hast text node.
 * @returns The rewritten node list, or null when nothing matched so callers
 *   can skip the swap and keep the original node.
 */
export function splitMemoryPhrases(value: string): HastNode[] | null {
  const re = new RegExp(MEMORY_PHRASE_SOURCE, 'gi');
  const out: HastNode[] = [];
  let last = 0;
  let match: RegExpExecArray | null;
  while ((match = re.exec(value)) !== null) {
    if (match.index > last) out.push({ type: 'text', value: value.slice(last, match.index) });
    out.push({
      type: 'element',
      tagName: 'mark',
      properties: { className: ['mds-memory-highlight'] },
      children: [{ type: 'text', value: match[0] }],
    });
    last = match.index + match[0].length;
    if (match.index === re.lastIndex) re.lastIndex += 1; // guard against zero-width loops
  }
  if (!out.length) return null;
  if (last < value.length) out.push({ type: 'text', value: value.slice(last) });
  return out;
}

function highlightMemoryInTree(node: HastNode): void {
  if (!node.children) return;
  // Never rewrite inside code — recalled facts there are literal SQL/JSON.
  if (node.tagName === 'code' || node.tagName === 'pre') return;
  const rewritten: HastNode[] = [];
  let changed = false;
  for (const child of node.children) {
    if (child.type === 'text' && typeof child.value === 'string') {
      const split = splitMemoryPhrases(child.value);
      if (split) {
        rewritten.push(...split);
        changed = true;
        continue;
      }
    } else {
      highlightMemoryInTree(child);
    }
    rewritten.push(child);
  }
  if (changed) node.children = rewritten;
}

/** rehype plugin: wrap memory-sourced phrases in `<mark>` element nodes. */
export function rehypeMemoryHighlight() {
  return (tree: HastNode) => highlightMemoryInTree(tree);
}
