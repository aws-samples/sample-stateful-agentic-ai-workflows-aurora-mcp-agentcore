/**
 * Official AWS service marks.
 *
 * A generic cylinder standing in for AWS Aurora is the kind of detail an
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
    src: '/brand/aws-2026-07-31/aurora.svg',
    smallSrc: '/brand/aws-2026-07-31/aurora.svg',
    label: 'AWS Aurora',
  },
  agentcore: {
    src: '/brand/agentcore-purple/agentcore.svg',
    smallSrc: '/brand/agentcore-purple/agentcore.svg',
    label: 'Amazon Bedrock AgentCore',
  },
  'agentcore-runtime': {
    src: '/brand/agentcore-purple/runtime.svg',
    smallSrc: '/brand/agentcore-purple/runtime.svg',
    label: 'AgentCore Runtime',
  },
  'agentcore-gateway': {
    src: '/brand/agentcore-purple/gateway.svg',
    smallSrc: '/brand/agentcore-purple/gateway.svg',
    label: 'AgentCore Gateway',
  },
  'agentcore-memory': {
    src: '/brand/agentcore-purple/memory.svg',
    smallSrc: '/brand/agentcore-purple/memory.svg',
    label: 'AgentCore Memory',
  },
  'agentcore-policy': {
    src: '/brand/agentcore-purple/policy.svg',
    smallSrc: '/brand/agentcore-purple/policy.svg',
    label: 'AgentCore Policy',
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
      className={`mds-service-mark mds-service-mark-${name}${className ? ` ${className}` : ''}`}
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
