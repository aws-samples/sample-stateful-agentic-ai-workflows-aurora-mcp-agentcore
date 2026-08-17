import * as Tooltip from '@radix-ui/react-tooltip';
import type { ReactElement } from 'react';

export function IconTooltip({
  label,
  children,
}: {
  label: string;
  children: ReactElement;
}) {
  const portalContainer =
    typeof document === 'undefined'
      ? undefined
      : document.querySelector<HTMLElement>('.mds-root') ?? undefined;

  return (
    <Tooltip.Provider delayDuration={300}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
        <Tooltip.Portal container={portalContainer}>
          <Tooltip.Content
            className="mds-tooltip"
            side="bottom"
            sideOffset={7}
            collisionPadding={10}
          >
            {label}
            <Tooltip.Arrow className="mds-tooltip-arrow" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}
