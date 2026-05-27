/** Backward-compatible WispAgent — thin wrapper around CompositionRoot + CLITransport. */

import { WispConfig } from "./config.js";
import { Session } from "./core/session.js";
import { CLITransport } from "./transport/cli.js";
import { CompositionRoot } from "./composition.js";

export class WispAgent {
  config: WispConfig;
  session: Session | null;
  agentId: string | null;
  role: string | null;
  private _activeSkill: string | null = null;
  messages: Array<{ role: string; content: string }> = [];
  private _root: CompositionRoot;

  constructor(config?: WispConfig, session?: Session, agentId?: string, role?: string) {
    this.config = config ?? new WispConfig();
    this._root = new CompositionRoot(this.config);
    this.session = session ?? null;
    this.agentId = agentId ?? null;
    this.role = role ?? null;
  }

  get activeSkill(): string | null {
    return this._activeSkill;
  }

  set activeSkill(v: string | null) {
    this._activeSkill = v;
  }

  /** Single-shot mode */
  run(prompt: string, skillName?: string, sessionId?: string): void {
    const sid = sessionId ?? this.session?.sessionId ?? `sess-${Date.now()}`;
    const model = this.config.model;
    const ws = this.config.workspace || ".";
    const root = this._root;
    root.start();
    const session = new Session(sid, model, ws);
    this.session = session;
    if (skillName) this._activeSkill = skillName;
    const transport = new CLITransport(root.runtime, this.config);
    transport.start();
    const runTurn = async () => {
      for await (const event of root.runtime.runTurn(session, prompt)) {
        await transport.send(event as { type: string; data?: Record<string, unknown> });
      }
    };
    runTurn().catch(() => { /* ignore */ }).finally(() => {
      transport.stop();
      root.shutdown();
    });
  }

  /** Interactive REPL */
  repl(skillName?: string, sessionId?: string): void {
    const sid = sessionId ?? `sess-${Date.now()}`;
    const model = this.config.model;
    const ws = this.config.workspace || ".";
    const root = this._root;
    root.start();
    const session = new Session(sid, model, ws);
    this.session = session;
    if (skillName) this._activeSkill = skillName;
    const transport = new CLITransport(root.runtime, this.config);
    transport.start();
    transport.runRepl(session).finally(() => {
      transport.stop();
      root.shutdown();
    });
  }
}
