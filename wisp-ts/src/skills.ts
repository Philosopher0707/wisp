/** Skill discovery and matching */

import fs from "node:fs";
import path from "node:path";

const SKILL_DIR_NAMES = [
  ".agents/skills",
  ".warp/skills",
  ".claude/skills",
  ".codex/skills",
  ".cursor/skills",
  ".gemini/skills",
  ".opencode/skills",
  ".github/skills",
  ".copilot/skills",
  ".factory/skills",
];

export interface Skill {
  name: string;
  description: string;
  instructions: string;
  triggers: string[];
  filePath: string;
}

function parseSkill(filePath: string): Skill | null {
  try {
    const content = fs.readFileSync(filePath, "utf-8");
    if (!content.startsWith("---")) return null;
    const parts = content.split("---", 3);
    if (parts.length < 3) return null;
    const frontmatter = parts[1].trim();
    const instructions = parts[2].trim();

    const meta: Record<string, unknown> = {};
    for (const line of frontmatter.split("\n")) {
      const m = line.match(/^(.+?):\s*(.*)$/);
      if (m) {
        const key = m[1].trim();
        const val = m[2].trim();
        meta[key] = val;
      }
    }

    const name = meta.name as string | undefined;
    if (!name) return null;
    let triggers: string[] = [];
    if (meta.triggers) {
      triggers = (meta.triggers as string).split(",").map((t) => t.trim()).filter(Boolean);
    }

    return {
      name,
      description: (meta.description as string) ?? "",
      instructions,
      triggers,
      filePath,
    };
  } catch {
    return null;
  }
}

function scanSkillDir(dir: string, result: Skill[], seen: Set<string>): void {
  try {
    const entries = fs.readdirSync(dir, { withFileTypes: true });
    for (const entry of entries) {
      if (entry.isDirectory()) {
        const skillFile = path.join(dir, entry.name, "SKILL.md");
        if (fs.existsSync(skillFile)) {
          const skill = parseSkill(skillFile);
          if (skill && !seen.has(skill.name)) {
            seen.add(skill.name);
            result.push(skill);
          }
        }
      }
    }
  } catch {
    // ignore permission errors
  }
}

export function discoverSkills(workspace: string): Skill[] {
  const discovered: Skill[] = [];
  const seen = new Set<string>();
  const wsPath = path.resolve(workspace);
  for (const dirName of SKILL_DIR_NAMES) {
    scanSkillDir(path.join(wsPath, dirName), discovered, seen);
  }
  const homeDir = process.env.HOME ?? process.env.USERPROFILE ?? "/";
  for (const dirName of SKILL_DIR_NAMES) {
    scanSkillDir(path.join(homeDir, dirName), discovered, seen);
  }
  return discovered;
}

export function findSkill(name: string, workspace: string): Skill | null {
  const skills = discoverSkills(workspace);
  return skills.find((s) => s.name === name) ?? null;
}

export function matchSkills(query: string, workspace: string, minScore = 0): Array<[Skill, number]> {
  const skills = discoverSkills(workspace);
  const qLower = query.toLowerCase();
  const qWords = new Set(
    [...qLower.matchAll(/[a-z0-9]{2,}/g)].map((m) => m[0])
  );
  const results: Array<[Skill, number]> = [];
  for (const skill of skills) {
    let score = 0;
    const nameLower = skill.name.toLowerCase();
    if (nameLower === qLower) score += 3;
    else if (qLower.includes(nameLower)) score += 2;
    for (const trigger of skill.triggers) {
      if (qLower.includes(trigger.toLowerCase())) score += 2;
    }
    const descWords = new Set(
      [...skill.description.toLowerCase().matchAll(/[a-z0-9]{2,}/g)].map((m) => m[0])
    );
    let overlap = 0;
    for (const w of qWords) if (descWords.has(w)) overlap += 0.5;
    score += overlap;
    if (score > minScore) results.push([skill, score]);
  }
  return results.sort((a, b) => b[1] - a[1]);
}
