import type { WsClientMessage, WsServerMessage } from '@/core/types';

export interface MissionSocketHandlers {
  onMessage(msg: WsServerMessage): void;
  onOpen(): void;
  onClose(): void;
}

export class MissionSocket {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;

  constructor(
    private readonly url: string,
    private readonly handlers: MissionSocketHandlers,
  ) {}

  connect(): void {
    if (this.closed) return;
    const ws = new WebSocket(this.url);
    this.ws = ws;
    ws.onopen = () => {
      this.attempt = 0;
      this.handlers.onOpen();
    };
    ws.onmessage = (ev) => {
      try {
        this.handlers.onMessage(JSON.parse(String(ev.data)) as WsServerMessage);
      } catch {
        // ignore malformed frames
      }
    };
    ws.onclose = () => {
      this.ws = null;
      this.handlers.onClose();
      this.scheduleReconnect();
    };
    ws.onerror = () => ws.close();
  }

  send(msg: WsClientMessage): boolean {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
  }

  private scheduleReconnect(): void {
    if (this.closed) return;
    const delay = Math.min(8000, 500 * 2 ** this.attempt);
    this.attempt += 1;
    this.timer = setTimeout(() => this.connect(), delay);
  }
}

export function socketUrl(): string {
  const { protocol, host } = window.location;
  return `${protocol === 'https:' ? 'wss' : 'ws'}://${host}/ws`;
}
