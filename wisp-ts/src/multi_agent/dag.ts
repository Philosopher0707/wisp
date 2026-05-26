/** Task DAG — directed acyclic graph of subagent tasks with topological scheduling. */

export class TaskNode {
  name: string;
  task: unknown;
  dependencies: string[];
  metadata: Record<string, unknown>;

  constructor(name: string, task: unknown, dependencies: string[] = [], metadata: Record<string, unknown> = {}) {
    this.name = name;
    this.task = task;
    this.dependencies = dependencies;
    this.metadata = metadata;
  }
}

export class TaskDAG {
  nodes = new Map<string, TaskNode>();
  private _edges = new Map<string, Set<string>>();
  private _reverseEdges = new Map<string, Set<string>>();

  addNode(node: TaskNode): void {
    if (this.nodes.has(node.name)) throw new Error(`Duplicate node name: ${node.name}`);
    this.nodes.set(node.name, node);
    if (!this._edges.has(node.name)) this._edges.set(node.name, new Set());
    if (!this._reverseEdges.has(node.name)) this._reverseEdges.set(node.name, new Set());
    for (const dep of node.dependencies) {
      this.addEdge(dep, node.name);
    }
  }

  addEdge(fromNode: string, toNode: string): void {
    if (!this._edges.has(fromNode)) this._edges.set(fromNode, new Set());
    if (!this._reverseEdges.has(toNode)) this._reverseEdges.set(toNode, new Set());
    this._edges.get(fromNode)!.add(toNode);
    this._reverseEdges.get(toNode)!.add(fromNode);
  }

  dependenciesOf(name: string): Set<string> {
    return new Set(this._reverseEdges.get(name) ?? []);
  }

  dependentsOf(name: string): Set<string> {
    return new Set(this._edges.get(name) ?? []);
  }

  roots(): string[] {
    return Array.from(this.nodes.keys()).filter((name) => this.dependenciesOf(name).size === 0);
  }

  validate(): string[] {
    const errors: string[] = [];
    for (const node of this.nodes.values()) {
      for (const dep of node.dependencies) {
        if (!this.nodes.has(dep)) errors.push(`Node '${node.name}' depends on unknown '${dep}'`);
      }
    }

    // Kahn's algorithm
    const inDegree = new Map<string, number>();
    for (const name of this.nodes.keys()) {
      inDegree.set(name, this.dependenciesOf(name).size);
    }

    const q = Array.from(inDegree.entries()).filter(([, deg]) => deg === 0).map(([name]) => name);
    let visited = 0;
    let head = 0;

    while (head < q.length) {
      const current = q[head++];
      visited++;
      for (const dependent of this.dependentsOf(current)) {
        const newDeg = (inDegree.get(dependent) ?? 0) - 1;
        inDegree.set(dependent, newDeg);
        if (newDeg === 0) q.push(dependent);
      }
    }

    if (visited !== this.nodes.size) {
      const remaining = Array.from(inDegree.entries()).filter(([, deg]) => deg > 0).map(([name]) => name);
      errors.push(`Cycle detected involving: ${remaining.join(", ")}`);
    }

    return errors;
  }

  topologicalLevels(): string[][] {
    const remaining = new Map<string, number>();
    for (const name of this.nodes.keys()) {
      remaining.set(name, this.dependenciesOf(name).size);
    }

    const levels: string[][] = [];
    while (remaining.size > 0) {
      const currentLevel = Array.from(remaining.entries())
        .filter(([, deg]) => deg === 0)
        .map(([name]) => name)
        .sort();
      if (currentLevel.length === 0) break;
      levels.push(currentLevel);
      for (const name of currentLevel) {
        remaining.delete(name);
        for (const dependent of this.dependentsOf(name)) {
          if (remaining.has(dependent)) {
            remaining.set(dependent, (remaining.get(dependent) ?? 0) - 1);
          }
        }
      }
    }
    return levels;
  }
}

export class DAGResult {
  nodeResults = new Map<string, unknown>();
  levelOrder: string[][] = [];
  totalElapsed = 0;
  success = true;
  errors: string[] = [];

  constructor(init?: Partial<DAGResult>) {
    if (init) Object.assign(this, init);
  }
}

export class DAGScheduler {
  maxParallelism: number;
  timeoutPerNode: number;

  constructor(maxParallelism = 4, timeoutPerNode = 300) {
    this.maxParallelism = maxParallelism;
    this.timeoutPerNode = timeoutPerNode;
  }

  async execute(dag: TaskDAG, executor: (node: TaskNode) => Promise<unknown>): Promise<DAGResult> {
    const errors = dag.validate();
    if (errors.length > 0) {
      return new DAGResult({ success: false, errors });
    }

    const levels = dag.topologicalLevels();
    const allResults = new Map<string, unknown>();
    const start = performance.now();

    const semaphore = this._makeSemaphore(this.maxParallelism);

    const _runNode = async (node: TaskNode): Promise<void> => {
      const release = await semaphore.acquire();
      try {
        const result = await this._withTimeout(executor(node), this.timeoutPerNode * 1000);
        allResults.set(node.name, result);
      } catch (exc) {
        const errorMsg = exc instanceof Error ? exc.message : String(exc);
        allResults.set(node.name, { success: false, error: errorMsg, output: `Error: ${errorMsg}` });
      } finally {
        release();
      }
    };

    for (const level of levels) {
      const tasks = level.map((name) => _runNode(dag.nodes.get(name)!));
      await Promise.all(tasks);
    }

    const elapsed = (performance.now() - start) / 1000;
    const success = Array.from(allResults.values()).every((r) => {
      if (r && typeof r === "object") return (r as Record<string, unknown>).success !== false;
      return true;
    });

    return new DAGResult({ nodeResults: allResults, levelOrder: levels, totalElapsed: elapsed, success });
  }

  private _makeSemaphore(n: number): { acquire(): Promise<() => void> } {
    let count = n;
    const queue: Array<{ resolve: (release: () => void) => void }> = [];
    return {
      acquire(): Promise<() => void> {
        if (count > 0) {
          count--;
          return Promise.resolve(() => {
            count++;
            const next = queue.shift();
            if (next) { count--; next.resolve(() => { count++; }); }
          });
        }
        return new Promise((resolve) => queue.push({ resolve }));
      },
    };
  }

  private async _withTimeout<T>(promise: Promise<T>, ms: number): Promise<T> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("timeout")), ms);
      promise.then(
        (v) => { clearTimeout(timer); resolve(v); },
        (e) => { clearTimeout(timer); reject(e); }
      );
    });
  }
}
