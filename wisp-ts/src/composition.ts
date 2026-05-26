/** CompositionRoot — the wiring layer. */

import path from "node:path";
import { WispConfig } from "./config.js";
import { UnifiedStore } from "./infra/store.js";
import { SecurityPolicy } from "./infra/security.js";
import { PermissionMode } from "./config.js";
import { AuditTrail } from "./infra/audit.js";
import { TokenCounter } from "./infra/token_counter.js";
import { WispAgentCore } from "./core/engine.js";
import { AgentRuntime } from "./core/runtime.js";
import { ProviderFactory } from "./providers/factory.js";
import { ToolRegistry } from "./tools/registry.js";
import { SubagentOrchestrator } from "./multi_agent/orchestrator.js";

export class CompositionRoot {
  config: WispConfig;
  store: UnifiedStore;
  security: SecurityPolicy;
  auditTrail: AuditTrail;
  tokenCounter: TokenCounter;
  toolRegistry: ToolRegistry;
  orchestrator: SubagentOrchestrator;
  runtime: AgentRuntime;

  constructor(config: WispConfig) {
    this.config = config;
    const workspace = config.workspace ?? path.resolve(".");
    const dbPath = path.join(workspace, ".wisp", "wisp.db");

    this.store = new UnifiedStore(dbPath);
    this.auditTrail = new AuditTrail(path.join(workspace, ".wisp", "audit.jsonl"));
    this.security = new SecurityPolicy(
      (config.permission_mode as PermissionMode) ?? PermissionMode.AUTO_EDIT
    );
    this.tokenCounter = new TokenCounter(config.chars_per_token);
    this.toolRegistry = new ToolRegistry();
    this.orchestrator = new SubagentOrchestrator(config, workspace);

    const coreFactory = () => {
      const factory = new ProviderFactory();
      const provider = factory.fromConfig({
        provider: config.provider,
        ollama_url: config.ollama_url,
        model: config.model,
      });
      return new WispAgentCore(config, provider, this.security, this.toolRegistry, this.tokenCounter);
    };

    this.runtime = new AgentRuntime(this.store, coreFactory, this.orchestrator, this.tokenCounter, this.auditTrail);
  }

  start(): void {
    // lifecycle hooks
  }

  shutdown(): void {
    // cleanup
  }
}
