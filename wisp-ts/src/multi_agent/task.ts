/** Unified task and result types for all multi-agent systems in Wisp TS. */

export function _newId(): string {
  return `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;
}

export function _nowTs(): number {
  return performance.now();
}

// ── Contract ─────────────────────────────────────────────────────────────

export class SubagentContract {
  name = "subagent";
  role = "generalist";
  task = "";
  systemPrompt: string | null = null;
  tools: string[] = ["all"];
  allowedSkills: string[] = [];
  maxIterations = 15;
  timeoutSeconds = 120;
  maxTokens: number | null = null;
  maxInputTokens: number | null = null;
  maxOutputTokens: number | null = null;
  maxOutputChars = 8000;
  outputFormat = "text";
  outputSchema: Record<string, unknown> | null = null;
  autoRetryParse = true;
  model: string | null = null;
  workspace: string | null = null;
  worktreeIsolated = true;
  autoApprove = false;
  progressCallback: ((event: OrchestratorEvent) => void | Promise<void>) | null = null;
  systemPromptExtra = "";
  prompt = "";
  contextFiles: string[] = [];
  subagentDepth = 0;
  subagentBranchCount = 0;
  retryCount = 0;
  maxRetries = 0;
  maxMemoryMb = 2048;
  cacheContext = "";
  metadata: Record<string, unknown> = {};

  constructor(init?: Partial<SubagentContract>) {
    if (init) Object.assign(this, init);
    if (this.prompt && !this.task) this.task = this.prompt;
  }
}

export type SubagentTask = SubagentContract;

// ── Result ─────────────────────────────────────────────────────────────────

export class SubagentResult {
  taskId = "";
  success = false;
  output = "";
  error: string | null = null;
  filesChanged: string[] = [];
  elapsedSeconds = 0;
  iterationsUsed = 0;
  retryCount = 0;
  timedOut = false;
  hitIterationLimit = false;
  worktreePatch: string | null = null;
  patchApplied = false;
  messages: Record<string, unknown>[] = [];
  toolCalls: Record<string, unknown>[] = [];
  tokensUsed = 0;
  inputTokens = 0;
  outputTokens = 0;
  modelUsed = "";
  validatedOutput: unknown = null;
  spec: unknown = null;
  durationSeconds = 0;
  sessionId = "";

  constructor(init?: Partial<SubagentResult>) {
    if (init) Object.assign(this, init);
    if (this.durationSeconds && !this.elapsedSeconds) this.elapsedSeconds = this.durationSeconds;
  }
}

// ── Orchestrator Event ───────────────────────────────────────────────────

export class EventKind {
  static PLANNING = "planning";
  static TASK_STARTED = "task_started";
  static TASK_PROGRESS = "task_progress";
  static TASK_COMPLETED = "task_completed";
  static TASK_FAILED = "task_failed";
  static TASK_RETRY = "task_retry";
  static DONE = "done";
}

export class OrchestratorEvent {
  taskId = "";
  eventType = EventKind.TASK_STARTED;
  payload: Record<string, unknown> = {};

  constructor(init?: Partial<OrchestratorEvent>) {
    if (init) Object.assign(this, init);
  }

  toWsMessage(): Record<string, unknown> | null {
    const kind = this.eventType;
    const p = this.payload;
    switch (kind) {
      case EventKind.TASK_STARTED:
        return {
          type: "subagent_start",
          subagent_id: this.taskId,
          name: (p.role as string) ?? (p.name as string) ?? "",
          description: (p.description as string) ?? "",
        };
      case EventKind.TASK_PROGRESS:
        return {
          type: "subagent_progress",
          subagent_id: this.taskId,
          progress: (p.progress as string) ?? "",
        };
      case EventKind.TASK_COMPLETED:
        return {
          type: "subagent_complete",
          subagent_id: this.taskId,
          files_changed: (p.files_changed as string[]) ?? [],
          duration_ms: Math.round(((p.elapsed as number) ?? 0) * 1000),
        };
      case EventKind.TASK_FAILED:
        return {
          type: "subagent_fail",
          subagent_id: this.taskId,
          error: (p.error as string) ?? "",
        };
      default:
        return null;
    }
  }
}
