import * as Dialog from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import { useRef } from 'react';
import type { ReactNode } from 'react';

interface ShowcaseSheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  subtitle: string;
  description: string;
  closeLabel: string;
  children: ReactNode;
}

export function ShowcaseSheet({
  open,
  onOpenChange,
  title,
  subtitle,
  description,
  closeLabel,
  children,
}: ShowcaseSheetProps) {
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const portalContainer =
    typeof document === 'undefined'
      ? undefined
      : document.querySelector<HTMLElement>('.mds-root') ?? undefined;

  const restoreFocus = () => {
    const target = returnFocusRef.current;
    if (!target) return;
    queueMicrotask(() => target.focus());
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(nextOpen) => {
        onOpenChange(nextOpen);
        if (!nextOpen) restoreFocus();
      }}
    >
      <Dialog.Portal container={portalContainer}>
        <Dialog.Overlay className="mds-drawer-backdrop" />
        <Dialog.Content
          className="mds-drawer"
          onOpenAutoFocus={() => {
            returnFocusRef.current = document.activeElement as HTMLElement;
          }}
          onCloseAutoFocus={(event) => {
            event.preventDefault();
            restoreFocus();
          }}
        >
          <header>
            <div>
              <Dialog.Title asChild>
                <span>{title}</span>
              </Dialog.Title>
              <strong>{subtitle}</strong>
              <Dialog.Description className="mds-sr-only">
                {description}
              </Dialog.Description>
            </div>
            <Dialog.Close asChild>
              <button type="button" aria-label={closeLabel}>
                <X size={17} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </header>
          {children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
