/** Transport ABC — the interface all UIs implement.
 * Decouples the core from any specific transport (CLI, TUI, WebSocket, SSE).
 */

export interface TransportEvent {
  type: string;
  [key: string]: unknown;
}

export abstract class Transport {
  abstract send(event: TransportEvent): Promise<void>;
  abstract recv(): Promise<string | null>;
  abstract approve(toolCall: Record<string, unknown>): Promise<boolean>;
  abstract start(): void;
  abstract stop(): void;
}
