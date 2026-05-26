/** LSP tool stubs */

export function toolLspDiagnostics(_path: string): string {
  return JSON.stringify({ status: "ok", data: { message: "LSP not yet integrated" } });
}

export function toolLspDefinition(_path: string, _line: number, _char: number): string {
  return JSON.stringify({ status: "ok", data: { message: "LSP not yet integrated" } });
}

export function toolLspReferences(_path: string, _line: number, _char: number): string {
  return JSON.stringify({ status: "ok", data: { message: "LSP not yet integrated" } });
}

export function toolLspHover(_path: string, _line: number, _char: number): string {
  return JSON.stringify({ status: "ok", data: { message: "LSP not yet integrated" } });
}

export function toolLspSymbols(_path: string): string {
  return JSON.stringify({ status: "ok", data: { message: "LSP not yet integrated" } });
}
