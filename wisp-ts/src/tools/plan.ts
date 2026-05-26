import fs from "node:fs";
import path from "node:path";

export interface PlanTask {
  id: string;
  description: string;
  complexity: "low" | "medium" | "high";
  status: "pending" | "in_progress" | "done" | "skipped" | "blocked";
  dependencies: string[];
  filesToTouch: string[];
  notes: string;
}

export interface Plan {
  id: string;
  goal: string;
  workspace: string;
  tasks: PlanTask[];
  updatedAt: number;
}

const _plans = new Map<string, Plan>();

function _planKey(workspace: string): string {
  return path.resolve(workspace);
}

function _parseTasks(text: string): PlanTask[] {
  const tasks: PlanTask[] = [];
  const lines = text.split("\n");
  for (const raw of lines) {
    const line = raw.trim();
    if (!line) continue;
    // Match: 1. [low] Description — files: a.py, b.py
    const m = line.match(/^(\d+)\.\s*\[(low|medium|high)\]\s*(.+)$/i);
    if (!m) continue;
    let description = m[3].trim();
    const complexity = m[2] as "low" | "medium" | "high";
    const depsMatch = description.match(/deps:\s*([\d,\s]+)/i);
    const dependencies: string[] = depsMatch
      ? depsMatch[1].split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    if (depsMatch) description = description.replace(/deps:\s*[\d,\s]+/i, "").trim();
    const filesMatch = description.match(/files:\s*([^,\n]+)/i);
    const filesToTouch: string[] = filesMatch
      ? filesMatch[1].split(",").map((s) => s.trim()).filter(Boolean)
      : [];
    if (filesMatch) description = description.replace(/files:\s*[^,\n]+/i, "").trim();
    tasks.push({
      id: m[1],
      description: description.replace(/[\u2014\u2013\u002d]+$/, "").trim(),
      complexity,
      status: "pending",
      dependencies,
      filesToTouch,
      notes: "",
    });
  }
  return tasks;
}

export function toolPlanTask(goal: string, tasks: string, workspace = "."): string {
  const parsed = _parseTasks(tasks);
  if (parsed.length === 0) return "⚠ No tasks parsed. Use format: '1. [low] Description — files: a.py'";
  const plan: Plan = {
    id: `plan-${Date.now()}`,
    goal,
    workspace: path.resolve(workspace),
    tasks: parsed,
    updatedAt: Date.now(),
  };
  _plans.set(_planKey(workspace), plan);

  // Also persist to disk
  try {
    const dir = path.join(workspace, ".wisp");
    if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(path.join(dir, "plans.json"), JSON.stringify(plan, null, 2), "utf-8");
  } catch { /* ignore */ }

  const lines = ["✓ Created plan", `Goal: ${goal}`, `Tasks: ${parsed.length}`, ""];
  for (const t of parsed) {
    const deps = t.dependencies.length ? ` (deps: ${t.dependencies.join(", ")})` : "";
    const files = t.filesToTouch.length ? ` [files: ${t.filesToTouch.join(", ")}]` : "";
    lines.push(`  ${t.id}. [${t.complexity}] ${t.description}${deps}${files}`);
  }
  return lines.join("\n");
}

export function toolMarkStepDone(taskId: string, notes = "", workspace = "."): string {
  const plan = _plans.get(_planKey(workspace));
  if (!plan) return "⚠ No active plan for this workspace.";
  const task = plan.tasks.find((t) => t.id === taskId);
  if (!task) return `⚠ Task ${taskId} not found.`;
  task.status = "done";
  if (notes) task.notes = notes;
  plan.updatedAt = Date.now();
  const done = plan.tasks.filter((t) => t.status === "done").length;
  return `✓ Marked task ${taskId} as done. Progress: ${done}/${plan.tasks.length}`;
}

export function toolUpdatePlan(taskId: string, status: string, notes = "", workspace = "."): string {
  const plan = _plans.get(_planKey(workspace));
  if (!plan) return "⚠ No active plan for this workspace.";
  const task = plan.tasks.find((t) => t.id === taskId);
  if (!task) return `⚠ Task ${taskId} not found.`;
  if (["pending", "in_progress", "done", "skipped", "blocked"].includes(status)) {
    task.status = status as PlanTask["status"];
  } else {
    return `⚠ Invalid status '${status}'. Use pending|in_progress|done|skipped|blocked.`;
  }
  if (notes) task.notes = notes;
  plan.updatedAt = Date.now();
  const done = plan.tasks.filter((t) => t.status === "done").length;
  return `✓ Updated task ${taskId} to '${status}'. Progress: ${done}/${plan.tasks.length}`;
}

export function loadPlanFromDisk(workspace: string): Plan | null {
  try {
    const file = path.join(workspace, ".wisp", "plans.json");
    if (!fs.existsSync(file)) return null;
    const raw = fs.readFileSync(file, "utf-8");
    const plan = JSON.parse(raw) as Plan;
    _plans.set(_planKey(workspace), plan);
    return plan;
  } catch {
    return null;
  }
}
