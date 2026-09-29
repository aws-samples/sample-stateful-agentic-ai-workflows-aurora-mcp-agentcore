function mapPart(part) {
  if (part === '0' || part === '50%') return part;
  const px = part.match(/^([\d.]+)px$/);
  if (!px) return null;
  const n = parseFloat(px[1]);
  if (n === 0) return '0';
  if (n >= 100) return 'var(--mds-radius-full)';
  if (n <= 7) return 'var(--mds-radius-s)';
  if (n <= 11) return 'var(--mds-radius-m)';
  return 'var(--mds-radius-l)';
}

export function mapRadius(value) {
  const parts = value.trim().split(/\s+/).map(mapPart);
  return parts.includes(null) ? null : parts.join(' ');
}
