/** Tool schemas — OpenAI-compatible function schemas for the TypeScript port. */

export interface ToolSchema {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: {
      type: "object";
      properties: Record<string, { type: string; description: string; default?: unknown }>;
      required?: string[];
    };
  };
}

export const TOOL_SCHEMAS: ToolSchema[] = [
  {
    type: "function",
    function: {
      name: "read_file",
      description: "Read the contents of a file. Returns the ENTIRE file by default. Max file size: 50 MB.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Path to the file (relative to workspace or absolute)" },
          offset: { type: "number", description: "Starting line number (0-indexed). Default 0.", default: 0 },
          limit: { type: "number", description: "Max lines to read." },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "write_file",
      description: "Write content to a file. Creates parent directories if needed. WARNING: Overwrites existing files.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Path to write to" },
          content: { type: "string", description: "Full content to write" },
        },
        required: ["path", "content"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "edit_file",
      description: "Replace exact text in a file. The old_text must match exactly and be unique.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Path to the file" },
          old_text: { type: "string", description: "Exact text to replace (must be unique)" },
          new_text: { type: "string", description: "Replacement text" },
        },
        required: ["path", "old_text", "new_text"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "edit_file_multi",
      description: "Make multiple precise edits to a single file in one call.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Path to the file to edit" },
          edits: {
            type: "array",
            description: "One or more targeted replacements.",
            default: [],
          },
        },
        required: ["path", "edits"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_bash",
      description: "Run a bash command. Max command length: 4096 chars. Timeout: configurable (default 60s).",
      parameters: {
        type: "object",
        properties: {
          command: { type: "string", description: "Shell command to execute" },
          timeout: { type: "number", description: "Timeout in seconds", default: 60 },
        },
        required: ["command"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "list_files",
      description: "List files in a directory. Max 500 entries.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "Directory path", default: "." },
          pattern: { type: "string", description: "Glob pattern", default: "*" },
        },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "web_fetch",
      description: "Fetch content from a URL. Respects robots.txt and has 30s timeout.",
      parameters: {
        type: "object",
        properties: {
          url: { type: "string", description: "URL to fetch" },
          max_chars: { type: "number", description: "Maximum characters to return", default: 10000 },
        },
        required: ["url"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "search_symbols",
      description: "Search the code index for symbols matching a query.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Search term" },
          max_results: { type: "number", description: "Maximum results to return", default: 20 },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "remember",
      description: "Store a fact in cross-session memory.",
      parameters: {
        type: "object",
        properties: {
          fact: { type: "string", description: "The fact to remember." },
        },
        required: ["fact"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "recall",
      description: "Search cross-session memory for relevant facts.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "What to search for" },
          limit: { type: "number", description: "Max results (1-50)", default: 10 },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "git_status",
      description: "Show git status for the workspace.",
      parameters: { type: "object", properties: {}, required: [] },
    },
  },
  {
    type: "function",
    function: {
      name: "git_diff",
      description: "Show git diff for a file or the entire workspace.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "File to diff (omit for entire workspace)", default: "" },
          staged: { type: "boolean", description: "Show staged changes", default: false },
        },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "git_branch",
      description: "List branches, create a new branch, or switch.",
      parameters: {
        type: "object",
        properties: {
          action: { type: "string", description: "list, create, or switch" },
          name: { type: "string", description: "Branch name" },
        },
        required: ["action"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "git_commit",
      description: "Stage files and commit with a message.",
      parameters: {
        type: "object",
        properties: {
          message: { type: "string", description: "Commit message" },
          files: { type: "string", description: "Comma-separated file paths to stage", default: "" },
        },
        required: ["message"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "git_push",
      description: "Push current branch to remote.",
      parameters: {
        type: "object",
        properties: {
          set_upstream: { type: "boolean", description: "Set upstream tracking", default: false },
        },
        required: [],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "diagnose",
      description: "Diagnose an error from test output, traceback, or command output.",
      parameters: {
        type: "object",
        properties: {
          error_output: { type: "string", description: "The error output to analyze" },
        },
        required: ["error_output"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "plan_task",
      description: "Create a structured plan with subtasks.",
      parameters: {
        type: "object",
        properties: {
          goal: { type: "string", description: "High-level goal for the plan" },
          tasks: { type: "string", description: "Newline-separated task list" },
        },
        required: ["goal", "tasks"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "mark_step_done",
      description: "Mark a plan task as completed.",
      parameters: {
        type: "object",
        properties: {
          task_id: { type: "string", description: "Task ID to mark done" },
          notes: { type: "string", description: "Optional completion notes", default: "" },
        },
        required: ["task_id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "update_plan",
      description: "Update a plan task's status.",
      parameters: {
        type: "object",
        properties: {
          task_id: { type: "string", description: "Task ID to update" },
          status: { type: "string", description: "New status: pending, in_progress, done, skipped, blocked" },
          notes: { type: "string", description: "Optional notes", default: "" },
        },
        required: ["task_id", "status"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "lsp_diagnostics",
      description: "Run language server diagnostics on a file.",
      parameters: {
        type: "object",
        properties: {
          path: { type: "string", description: "File path to check" },
        },
        required: ["path"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "web_search",
      description: "Search the web for information, docs, error messages, or latest news.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Search query" },
          num_results: { type: "number", description: "Number of results", default: 5 },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "search_codebase",
      description: "Semantic search over the codebase using embeddings.",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "Natural language query about the codebase" },
          top_k: { type: "number", description: "Number of results", default: 5 },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "run_tests",
      description: "Run tests for the given files, or all tests if no files specified.",
      parameters: {
        type: "object",
        properties: {
          files: { type: "array", description: "List of changed source files", default: [] },
          workspace: { type: "string", description: "Workspace directory", default: "." },
          timeout: { type: "number", description: "Maximum seconds to wait", default: 120 },
        },
        required: [],
      },
    },
  },
];
