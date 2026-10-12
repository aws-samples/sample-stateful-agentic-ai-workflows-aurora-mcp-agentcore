const LABELS: Record<string, string> = {
  no_red_eye: 'Avoid overnight flights',
  avoid_connections: 'Connections to avoid',
  vegetarian_friendly: 'Vegetarian dining',
  shellfish_allergy: 'Shellfish allergy',
  lodging_style: 'Stay preference',
  seat_pref: 'Seat preference',
  tokyo_culture: 'Tokyo plan',
  budget_cap: 'Budget per traveler',
};

export function preferenceLabel(key: string) {
  return LABELS[key] ?? key.replace(/_/g, ' ');
}

export function preferenceValue(key: string, value: string) {
  if (/^(true|false)$/i.test(value)) {
    return `${preferenceLabel(key)}: ${value.toLowerCase() === 'true' ? 'Yes' : 'No'}`;
  }
  if (key === 'lodging_style' && /^boutique\s*>\s*chain$/i.test(value)) return 'Boutique hotels over chains';
  if (key === 'avoid_connections') return `Avoid connections through ${value}`;
  return value;
}
