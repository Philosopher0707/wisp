/** Async queue for non-blocking stdin reads. */

export class StdinQueue {
  private _buffer: string[] = [];
  private _resolvers: Array<(value: string | null) => void> = [];
  private _closed = false;

  push(line: string): void {
    if (this._closed) return;
    if (this._resolvers.length > 0) {
      const resolve = this._resolvers.shift()!;
      resolve(line);
    } else {
      this._buffer.push(line);
    }
  }

  close(): void {
    this._closed = true;
    while (this._resolvers.length > 0) {
      const resolve = this._resolvers.shift()!;
      resolve(null);
    }
  }

  async next(): Promise<string | null> {
    if (this._closed) return null;
    if (this._buffer.length > 0) {
      return this._buffer.shift()!;
    }
    return new Promise((resolve) => {
      this._resolvers.push(resolve);
    });
  }
}
