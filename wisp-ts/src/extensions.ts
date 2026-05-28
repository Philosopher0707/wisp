/** ExtensionHost — unified extension system with lifecycle management. */

export interface Extension {
  name: string;
  start(): void;
  stop(): void;
  tools(): Array<Record<string, unknown>>;
  intercept(event: Record<string, unknown>): { action: "allow" | "block"; reason?: string };
}

export class ExtensionHost {
  private _extensions: Extension[] = [];

  register(ext: Extension): void {
    try {
      ext.start();
      this._extensions.push(ext);
    } catch (exc) {
      console.warn(`Extension ${ext.name} failed to start: ${exc}`);
    }
  }

  tools(): Array<Record<string, unknown>> {
    const tools: Array<Record<string, unknown>> = [];
    for (const ext of this._extensions) {
      try {
        const extTools = ext.tools();
        if (extTools?.length) tools.push(...extTools);
      } catch (exc) {
        console.warn(`Extension ${ext.name} tools() failed: ${exc}`);
      }
    }
    return tools;
  }

  intercept(event: Record<string, unknown>): { action: "allow" | "block"; reason?: string } {
    for (const ext of this._extensions) {
      try {
        const result = ext.intercept(event);
        if (result.action === "block") return result;
      } catch (exc) {
        console.warn(`Extension ${ext.name} intercept() failed: ${exc} — denying by default`);
        return { action: "block", reason: `Extension error: ${exc}` };
      }
    }
    return { action: "allow" };
  }

  start(): void {
    // Extensions already started during register()
  }

  stop(): void {
    for (const ext of [...this._extensions].reverse()) {
      try {
        ext.stop();
      } catch (exc) {
        console.warn(`Extension ${ext.name} stop() failed: ${exc}`);
      }
    }
    this._extensions.length = 0;
  }
}

/** PluginExtension — wraps a simple tool provider as an extension. */
export class PluginExtension implements Extension {
  name: string;
  private _tools: Array<Record<string, unknown>>;

  constructor(name: string, tools: Array<Record<string, unknown>>) {
    this.name = name;
    this._tools = tools;
  }

  start(): void {}
  stop(): void {}

  tools(): Array<Record<string, unknown>> {
    return this._tools;
  }

  intercept(_event: Record<string, unknown>): { action: "allow" | "block" } {
    return { action: "allow" };
  }
}

/** SkillExtension — exposes workspace skills as prefixed tools. */
export class SkillExtension implements Extension {
  name = "skills";
  private _workspace: string;

  constructor(workspace: string) {
    this._workspace = workspace;
  }

  start(): void {}
  stop(): void {}

  tools(): Array<Record<string, unknown>> {
    // In a real implementation, would discover skills from workspace
    return [];
  }

  intercept(_event: Record<string, unknown>): { action: "allow" | "block" } {
    return { action: "allow" };
  }
}
