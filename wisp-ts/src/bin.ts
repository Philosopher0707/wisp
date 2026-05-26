/** CLI entry point for wisp-ts REPL */

import process from "node:process";
import { WispConfig } from "./config.js";
import { CompositionRoot } from "./composition.js";
import { CLITransport } from "./transport/cli.js";

async function main(): Promise<void> {
  const args = process.argv.slice(2);
  const config = new WispConfig();

  let mode = "repl";
  let prompt: string | undefined;
  let sessionId: string | undefined;

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "repl") {
      mode = "repl";
    } else if (a === "run") {
      mode = "run";
    } else if (a.startsWith("--model") && i + 1 < args.length) {
      config.model = args[++i];
    } else if (a.startsWith("--workspace") && i + 1 < args.length) {
      config.workspace = args[++i];
    } else if (a.startsWith("--session") && i + 1 < args.length) {
      sessionId = args[++i];
    } else if (a === "--auto-approve") {
      config.auto_approve = true;
    } else if (a === "--show-thinking") {
      config.show_thinking = true;
    } else if (a === "--help" || a === "-h") {
      printHelp();
      process.exit(0);
    } else if (!a.startsWith("-")) {
      prompt = a;
    }
  }

  const root = new CompositionRoot(config);
  root.start();

  const session = await root.runtime.getOrCreateSession(
    sessionId ?? crypto.randomUUID(),
    config.model,
    config.workspace ?? process.cwd()
  );

  const transport = new CLITransport(root.runtime, config);
  transport.start();

  try {
    if (mode === "run" && prompt) {
      const handler = config.auto_approve ? undefined : transport.approve.bind(transport);
      for await (const event of root.runtime.runTurn(session, prompt, handler)) {
        await transport.send(event as { type: string });
      }
    } else {
      await transport.runRepl(session);
    }
  } finally {
    transport.stop();
    root.shutdown();
  }
}

function printHelp(): void {
  process.stdout.write(`Usage: wisp-ts [options] [command] ['prompt']

Commands:
  repl                     Interactive REPL mode (default)
  run 'prompt'             Single-shot with a prompt

Options:
  --model <name>          Model to use
  --workspace <dir>        Working directory
  --session <id>           Continue an existing session
  --auto-approve            Auto-approve tool calls
  --show-thinking           Show reasoning trace inline
  --help, -h                Show this help

Examples:
  wisp-ts repl
  wisp-ts run 'refactor auth module'
  wisp-ts --model llama3 'write tests for utils.ts'
`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
