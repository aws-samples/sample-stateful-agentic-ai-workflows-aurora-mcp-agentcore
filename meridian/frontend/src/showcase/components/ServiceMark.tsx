/**
 * Official AWS service marks.
 *
 * A generic cylinder standing in for Amazon Aurora is the kind of detail an
 * AWS audience reads instantly, so the surfaces that name a service use its
 * real icon. Everything that signals a *state* rather than a product - an RLS
 * padlock, a check, a chevron - stays on the shared icon set, because a
 * service logo there would say something untrue.
 *
 * Aurora uses the supplied AWS SVG unchanged. AgentCore switches to its
 * small artwork below 17px so its glyph stays legible.
 */

const MARKS = {
  aurora: {
    src: '/brand/aurora.svg',
    smallSrc: '/brand/aurora.svg',
    label: 'Amazon Aurora',
  },
  agentcore: {
    src: '/brand/agentcore.svg',
    smallSrc: '/brand/agentcore-sm.svg',
    label: 'Amazon Bedrock AgentCore',
  },
} as const;

export type ServiceMarkName = keyof typeof MARKS;

/** Below this the tile's detailed glyph stops resolving. */
const SMALL_BREAKPOINT = 17;

export function ServiceMark({
  name,
  size = 17,
  title,
  className,
}: {
  name: ServiceMarkName;
  size?: number;
  /** Set only when the mark is the sole label for something. */
  title?: string;
  className?: string;
}) {
  const mark = MARKS[name];
  const src = size < SMALL_BREAKPOINT ? mark.smallSrc : mark.src;

  return (
    <img
      className={`mds-service-mark${className ? ` ${className}` : ''}`}
      src={src}
      width={size}
      height={size}
      alt={title ?? ''}
      aria-hidden={title ? undefined : true}
      title={title}
      loading="eager"
      decoding="async"
    />
  );
}

/** Aurora tile used wherever the interface previously used a database cylinder. */
export function AuroraIcon({ size = 20, className }: { size?: number; className?: string }) {
  return <ServiceMark name="aurora" size={Math.max(size, 20)} className={className} />;
}
