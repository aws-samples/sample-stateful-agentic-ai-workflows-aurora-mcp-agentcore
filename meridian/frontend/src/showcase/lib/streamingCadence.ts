export function typewriterCadence(textLength: number) {
  const stepMs = 24;
  const targetDurationMs = Math.min(3200, Math.max(720, textLength * 4));
  const totalSteps = Math.max(1, Math.ceil(targetDurationMs / stepMs));
  const charsPerStep = Math.max(3, Math.ceil(textLength / totalSteps));
  const naturalDurationMs = Math.ceil(textLength / charsPerStep) * stepMs;

  return { stepMs, charsPerStep, naturalDurationMs };
}
