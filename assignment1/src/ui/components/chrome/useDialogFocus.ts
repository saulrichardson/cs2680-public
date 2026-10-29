'use client';
import { type KeyboardEvent, type RefObject, useCallback, useEffect, useRef } from 'react';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function focusable(container: HTMLElement | null): HTMLElement[] {
  if (!container) return [];
  // `offsetParent === null` catches the display:none halves of the dialogs (a listing that is not
  // open yet), which must not be Tab stops; a fixed-position element would report null too, but no
  // dialog here has one.
  return [...container.querySelectorAll<HTMLElement>(FOCUSABLE)].filter(
    (el) => el.offsetParent !== null || el === document.activeElement,
  );
}

/**
 * Modal focus behaviour for the three dialogs: the first field takes focus when the dialog opens,
 * Tab cycles inside it instead of walking the board behind the scrim, and closing hands focus back
 * to whatever opened it. Spread the returned props onto the dialog element.
 */
export function useDialogFocus<T extends HTMLElement>(): {
  ref: RefObject<T | null>;
  onKeyDown: (e: KeyboardEvent<T>) => void;
} {
  const ref = useRef<T>(null);
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    focusable(ref.current).at(0)?.focus();
    return () => {
      // The trigger is often gone by now — a palette row unmounts with the palette that opened this
      // dialog — in which case the browser's own "back to <body>" is the best available answer.
      if (trigger?.isConnected) trigger.focus();
    };
  }, []);
  const onKeyDown = useCallback((e: KeyboardEvent<T>) => {
    if (e.key !== 'Tab') return;
    const items = focusable(ref.current);
    const first = items.at(0);
    const last = items.at(-1);
    if (!first || !last) return;
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }, []);
  return { ref, onKeyDown };
}
