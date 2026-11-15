import type { Phase } from '../../types';
import type { PromptOptions } from '../hooks/useMeridianShowcase';
import { SHOWCASE_FINALE_PROMPT } from './showcaseAdapters';

/**
 * The options of a ladder prompt that launches a recovery: the canceled-flight story prompt sent to
 * Phase 5. Any other prompt, including typed free text, is a read and declares no intent.
 */
export function recoveryLaunch(prompt: string, phase: Phase | undefined): PromptOptions | undefined {
  return phase === 5 && prompt.trim() === SHOWCASE_FINALE_PROMPT ? { intent: 'recovery' } : undefined;
}
