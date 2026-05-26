/**
 * MeridianMark — square brand image from Unsplash (nav, stage, favicon).
 */
import { meridianLogoUrl } from '../lib/meridianBrand';

export type MeridianMarkVariant = 'nav' | 'stage';

const SIZES: Record<MeridianMarkVariant, number> = {
  nav: 28,
  stage: 36,
};

interface MeridianMarkProps {
  variant?: MeridianMarkVariant;
  className?: string;
}

export function MeridianMark({ variant = 'nav', className = '' }: MeridianMarkProps) {
  const px = SIZES[variant];
  const classes = ['mp-brand-mark', variant === 'stage' ? 'ds-mark-img' : '', className]
    .filter(Boolean)
    .join(' ');

  return (
    <img
      src={meridianLogoUrl(px)}
      alt=""
      className={classes}
      width={px}
      height={px}
      loading="eager"
      decoding="async"
    />
  );
}
