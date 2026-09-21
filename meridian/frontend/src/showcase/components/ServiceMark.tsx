/**
 * Official AWS service marks.
 *
 * A generic cylinder standing in for Amazon Aurora is the kind of detail an
 * AWS audience reads instantly, so the surfaces that name a service use its
 * real icon. Everything that signals a *state* rather than a product - an RLS
 * padlock, a check, a chevron - stays on the shared icon set, because a
 * service logo there would say something untrue.
 *
 * Aurora, AgentCore and Bedrock use the official AWS architecture icons
 * unchanged, shown plain and never inside a second tile. AgentCore switches to
 * its small artwork below 17px so its glyph stays legible.
 */

const MARKS = {
  'app-runner': {
    src: '/brand/aws-2026-07-31/app-runner.svg',
    smallSrc: '/brand/aws-2026-07-31/app-runner.svg',
    label: 'AWS App Runner',
  },
  lambda: {
    src: '/brand/aws-2026-07-31/lambda.svg',
    smallSrc: '/brand/aws-2026-07-31/lambda.svg',
    label: 'AWS Lambda',
  },
  cloudfront: {
    src: '/brand/aws-2026-07-31/cloudfront.svg',
    smallSrc: '/brand/aws-2026-07-31/cloudfront.svg',
    label: 'Amazon CloudFront',
  },
  s3: {
    src: '/brand/aws-2026-07-31/s3.svg',
    smallSrc: '/brand/aws-2026-07-31/s3.svg',
    label: 'Amazon S3',
  },
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
  bedrock: {
    src: '/brand/bedrock.svg',
    smallSrc: '/brand/bedrock.svg',
    label: 'Amazon Bedrock',
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
