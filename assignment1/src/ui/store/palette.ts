import { create } from 'zustand';

type PaletteRequest = { kind: 'import' } | null;
interface PaletteState {
  open: boolean;
  pending: PaletteRequest;
  setOpen(o: boolean): void;
  request(r: PaletteRequest): void;
}

export const usePalette = create<PaletteState>((set) => ({
  open: false,
  pending: null,
  setOpen: (open) => set({ open }),
  request: (pending) => set({ pending, open: false }),
}));
