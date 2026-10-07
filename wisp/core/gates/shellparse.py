"""A deterministic shell-command parser. Pure: stdlib only, no I/O, no clock, no randomness.

The command gate must never decide on a heuristic it cannot explain. The old check split a string on `;&|` and looked for
`rm` ("a surface-level heuristic; it cannot catch obfuscated commands"). This module builds the structure instead:

    Seq(Stmt(AndOr(Pipeline(Simple | Group)))) with Words that carry the command substitutions they contain.

**Fail closed.** Anything the parser does not fully understand returns `Parse(ok=False, reasons=...)`; the caller denies. A
construct is either parsed completely or refused, never guessed at. `parse` never raises.

Understood: words with single/double/ANSI-C quoting and backslash escapes; `$(…)`, backticks and process substitution
(parsed recursively, so what runs inside is visible); `$((…))`, `${…}` and `$var` (marked *dynamic*); `&&`, `||`, `|`, `|&`,
`;`, `&`, newlines; `( … )` and `{ … }` groups; redirections with fds; here-docs and here-strings; leading `NAME=value`
assignments; comments; and the reserved words of compound commands (they separate statements, so every command inside a
`for`/`if`/`while` body is still parsed and checked). Refused: `case … esac` (its `pat)` syntax), `;;`, unbalanced quotes or
brackets, nesting deeper than `MAX_DEPTH`, and input longer than `MAX_LENGTH`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MAX_LENGTH = 16_384
MAX_DEPTH = 8
MAX_TOKENS = 4_000


# ── Tree ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Word:
    """One shell word after quote removal.

    `text` is the literal text with quotes removed and every expansion left verbatim (`"$HOME/x"` → `$HOME/x`).
    `dynamic` is True when the word contains an expansion whose value is unknown until run time (`$var`, `${…}`, `$(…)`,
    backticks, `$((…))`, process substitution). `glob` is True when it has an unquoted `*`, `?` or `[`.
    `subs` holds the parsed command substitutions inside it, so a walker sees every command that would run.
    """

    text: str
    dynamic: bool = False
    glob: bool = False
    quoted: bool = False
    subs: tuple["Seq", ...] = ()


@dataclass(frozen=True)
class Redirect:
    op: str  # one of > >> >| < << <<- <<< >& <& &> &>> <>
    fd: str  # leading fd or "", e.g. "2" in `2>file`
    target: Word | None
    heredoc: str | None = None  # the body, for << and <<-


@dataclass(frozen=True)
class Simple:
    assignments: tuple[tuple[str, Word], ...]
    words: tuple[Word, ...]
    redirects: tuple[Redirect, ...]


@dataclass(frozen=True)
class Group:
    kind: str  # "subshell" (…) or "brace" {…}
    body: "Seq"
    redirects: tuple[Redirect, ...] = ()


Node = Simple | Group


@dataclass(frozen=True)
class Pipeline:
    stages: tuple[Node, ...]
    negated: bool = False
    stderr_piped: tuple[bool, ...] = ()  # `|&` after stage i


@dataclass(frozen=True)
class AndOr:
    first: Pipeline
    rest: tuple[tuple[str, Pipeline], ...] = ()  # (op, pipeline) with op in {"&&", "||"}


@dataclass(frozen=True)
class Stmt:
    andor: AndOr
    background: bool = False


@dataclass(frozen=True)
class Seq:
    stmts: tuple[Stmt, ...]


@dataclass(frozen=True)
class Parse:
    ok: bool
    tree: Seq | None
    reasons: tuple[str, ...] = field(default_factory=tuple)


# ── Lexer ─────────────────────────────────────────────────────────────────

_OPERATOR_CHARS = frozenset(";&|()<>")
_BLANK = frozenset(" \t")
# Words that, in command position, only structure a compound command. They separate statements; they are never commands.
_RESERVED_PREFIX = frozenset({"if", "then", "else", "elif", "do", "while", "until", "time", "coproc"})
_RESERVED_SKIP_STATEMENT = frozenset({"fi", "done", "}", "esac", "in"})
_LOOP_HEADERS = frozenset({"for", "select"})


# Internal: unsupported or malformed input, converted to Parse(ok=False) at the boundary. A builtin alias, not a new exception class:
# every exception class under wisp/ needs a row in register.md. Catching ValueError at the boundary only widens what fails closed.
_Refuse = ValueError


@dataclass
class _Tok:
    kind: str  # WORD | OP | REDIR | NEWLINE
    word: Word | None = None
    op: str = ""
    fd: str = ""
    heredoc: str | None = None


def _decode_ansi_c(body: str) -> str:
    """Decode the body of $'…'. Anything not understood refuses rather than guessing."""
    out: list[str] = []
    i = 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "a": "\a", "b": "\b", "f": "\f", "v": "\v", "\\": "\\", "'": "'", '"': '"', "e": "\x1b"}
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        i += 1
        if i >= len(body):
            raise _Refuse("dangling escape in $'…'")
        c = body[i]
        if c in simple:
            out.append(simple[c])
            i += 1
        elif c == "x":
            hexd = body[i + 1:i + 3]
            if not hexd or any(h not in "0123456789abcdefABCDEF" for h in hexd):
                raise _Refuse("bad \\x escape in $'…'")
            out.append(chr(int(hexd, 16)))
            i += 1 + len(hexd)
        elif c in "01234567":
            j = i
            while j < len(body) and j < i + 3 and body[j] in "01234567":
                j += 1
            out.append(chr(int(body[i:j], 8)))
            i = j
        else:
            raise _Refuse(f"unsupported escape \\{c} in $'…'")
    return "".join(out)


class _Lexer:
    def __init__(self, src: str, depth: int) -> None:
        self.s = src
        self.n = len(src)
        self.i = 0
        self.depth = depth
        self.toks: list[_Tok] = []
        self._pending_heredocs: list[tuple[int, str, bool, bool]] = []  # (token index, delimiter, quoted, strip_tabs)

    # -- helpers --
    def _peek(self, k: int = 0) -> str:
        j = self.i + k
        return self.s[j] if j < self.n else ""

    def _emit(self, tok: _Tok) -> None:
        if len(self.toks) >= MAX_TOKENS:
            raise _Refuse("too many tokens")
        self.toks.append(tok)

    # -- main loop --
    def run(self) -> list[_Tok]:
        while self.i < self.n:
            c = self.s[self.i]
            if c in _BLANK:
                self.i += 1
            elif c == "\\" and self._peek(1) == "\n":
                self.i += 2
            elif c == "\n":
                self._emit(_Tok("NEWLINE"))
                self.i += 1
                self._read_heredoc_bodies()
            elif c == "#" and self._at_word_start():
                while self.i < self.n and self.s[self.i] != "\n":
                    self.i += 1
            elif c in _OPERATOR_CHARS and not (c in "<>" and self._peek(1) == "("):
                self._operator_or_redirect()
            elif c.isdigit() and self._digits_then_redirect():
                self._operator_or_redirect()
            else:
                self._emit(_Tok("WORD", word=self._word()))
        if self._pending_heredocs:
            raise _Refuse("here-document body missing its terminator")
        return self.toks

    def _at_word_start(self) -> bool:
        return self.i == 0 or self.s[self.i - 1] in _BLANK or self.s[self.i - 1] in "\n;&|()"

    def _digits_then_redirect(self) -> bool:
        j = self.i
        while j < self.n and self.s[j].isdigit():
            j += 1
        return j < self.n and self.s[j] in "<>" and (self.i == 0 or self.s[self.i - 1] in _BLANK or self.s[self.i - 1] in "\n;&|()")

    def _operator_or_redirect(self) -> None:
        fd = ""
        while self.i < self.n and self.s[self.i].isdigit():
            fd += self.s[self.i]
            self.i += 1
        c = self.s[self.i]
        two = self.s[self.i:self.i + 2]
        three = self.s[self.i:self.i + 3]
        if fd == "" and c == "&" and two == "&&":
            self._emit(_Tok("OP", op="&&"))
            self.i += 2
        elif fd == "" and c == "|" and two == "||":
            self._emit(_Tok("OP", op="||"))
            self.i += 2
        elif fd == "" and c == "|" and two == "|&":
            self._emit(_Tok("OP", op="|&"))
            self.i += 2
        elif fd == "" and c == ";" and two == ";;":
            raise _Refuse("`;;` (case) is not supported")
        elif fd == "" and c in ";&|()":
            if c == "&" and self._peek(1) == ">":
                op = "&>>" if three == "&>>" else "&>"
                self.i += len(op)
                self._emit_redirect(op, "")
            else:
                self._emit(_Tok("OP", op=c))
                self.i += 1
        elif c == "<" or c == ">":
            op = c
            if three == "<<<":
                op = "<<<"
            elif three == "<<-":
                op = "<<-"
            elif two in ("<<", ">>", ">|", ">&", "<&", "<>"):
                op = two
            self.i += len(op)
            self._emit_redirect(op, fd)
        else:
            raise _Refuse(f"unexpected character {c!r}")

    def _emit_redirect(self, op: str, fd: str) -> None:
        while self.i < self.n and self.s[self.i] in _BLANK:
            self.i += 1
        if self.i >= self.n or self.s[self.i] in "\n;&|()":
            raise _Refuse(f"redirection {op!r} has no target")
        target = self._word()
        tok = _Tok("REDIR", word=target, op=op, fd=fd)
        self._emit(tok)
        if op in ("<<", "<<-"):
            delim = target.text
            if target.dynamic and not target.quoted:
                raise _Refuse("here-document delimiter must be a plain word")
            self._pending_heredocs.append((len(self.toks) - 1, delim, target.quoted, op == "<<-"))

    def _read_heredoc_bodies(self) -> None:
        while self._pending_heredocs:
            idx, delim, quoted, strip = self._pending_heredocs.pop(0)
            lines: list[str] = []
            while True:
                if self.i >= self.n:
                    raise _Refuse(f"here-document {delim!r} is not terminated")
                end = self.s.find("\n", self.i)
                line = self.s[self.i:] if end == -1 else self.s[self.i:end]
                self.i = self.n if end == -1 else end + 1
                cmp_line = line.lstrip("\t") if strip else line
                if cmp_line == delim:
                    break
                lines.append(cmp_line if strip else line)
            body = "\n".join(lines)
            tok = self.toks[idx]
            subs: tuple[Seq, ...] = ()
            if not quoted:
                subs = tuple(_substitutions_in(body, self.depth))
            tok.heredoc = body
            tok.word = Word(tok.word.text if tok.word else delim, dynamic=bool(subs), quoted=quoted, subs=subs)

    # -- words --
    def _word(self) -> Word:
        buf: list[str] = []
        dynamic = False
        glob = False
        quoted = False
        subs: list[Seq] = []
        while self.i < self.n:
            c = self.s[self.i]
            if c in _BLANK or c == "\n":
                break
            if c in _OPERATOR_CHARS:
                if c in "<>" and self._peek(1) == "(" :
                    text, seq = self._process_substitution()
                    buf.append(text)
                    subs.append(seq)
                    dynamic = True
                    continue
                break
            if c == "\\":
                if self._peek(1) == "\n":
                    self.i += 2
                    continue
                if self.i + 1 >= self.n:
                    raise _Refuse("dangling backslash")
                buf.append(self.s[self.i + 1])
                quoted = True
                self.i += 2
            elif c == "'":
                end = self.s.find("'", self.i + 1)
                if end == -1:
                    raise _Refuse("unterminated single quote")
                buf.append(self.s[self.i + 1:end])
                quoted = True
                self.i = end + 1
            elif c == '"':
                text, d, ss = self._double_quoted()
                buf.append(text)
                dynamic = dynamic or d
                subs.extend(ss)
                quoted = True
            elif c == "$":
                text, d, ss, glob_unused = self._dollar()
                buf.append(text)
                dynamic = dynamic or d
                subs.extend(ss)
            elif c == "`":
                text, seq = self._backtick()
                buf.append(text)
                subs.append(seq)
                dynamic = True
            else:
                if c in "*?[":
                    glob = True
                buf.append(c)
                self.i += 1
        return Word("".join(buf), dynamic=dynamic, glob=glob, quoted=quoted, subs=tuple(subs))

    def _double_quoted(self) -> tuple[str, bool, list[Seq]]:
        self.i += 1  # opening quote
        buf: list[str] = []
        dynamic = False
        subs: list[Seq] = []
        while True:
            if self.i >= self.n:
                raise _Refuse("unterminated double quote")
            c = self.s[self.i]
            if c == '"':
                self.i += 1
                return "".join(buf), dynamic, subs
            if c == "\\":
                nxt = self._peek(1)
                if nxt in ('"', "\\", "$", "`"):
                    buf.append(nxt)
                    self.i += 2
                elif nxt == "\n":
                    self.i += 2
                else:
                    buf.append("\\")
                    self.i += 1
            elif c == "$":
                text, d, ss, _g = self._dollar()
                buf.append(text)
                dynamic = dynamic or d
                subs.extend(ss)
            elif c == "`":
                text, seq = self._backtick()
                buf.append(text)
                subs.append(seq)
                dynamic = True
            else:
                buf.append(c)
                self.i += 1

    def _dollar(self) -> tuple[str, bool, list[Seq], bool]:
        nxt = self._peek(1)
        if nxt == "(":
            if self._peek(2) == "(":
                inner = self._balanced("$((", "))")
                return f"$(({inner}))", True, [], False
            inner = self._balanced("$(", ")")
            seq = _parse_nested(inner, self.depth + 1)
            return f"$({inner})", True, [seq], False
        if nxt == "{":
            inner = self._balanced("${", "}")
            return "${" + inner + "}", True, [], False
        if nxt == "'":
            end = self._find_ansi_c_end(self.i + 2)
            decoded = _decode_ansi_c(self.s[self.i + 2:end])
            self.i = end + 1
            return decoded, False, [], False
        j = self.i + 1
        if j < self.n and (self.s[j].isalnum() or self.s[j] in "_@*#?$!-"):
            j += 1
            if self.s[j - 1].isalpha() or self.s[j - 1] == "_":
                while j < self.n and (self.s[j].isalnum() or self.s[j] == "_"):
                    j += 1
            text = self.s[self.i:j]
            self.i = j
            return text, True, [], False
        self.i += 1
        return "$", False, [], False

    def _find_ansi_c_end(self, start: int) -> int:
        j = start
        while j < self.n:
            if self.s[j] == "\\":
                j += 2
                continue
            if self.s[j] == "'":
                return j
            j += 1
        raise _Refuse("unterminated $'…'")

    def _balanced(self, opener: str, closer: str) -> str:
        """Text between `opener` at self.i and its matching `closer`; advances self.i past it. Quote-aware.

        `opener` is `$(`, `<(`, `>(`, `${` or `$((` (arithmetic, closed by `))`).
        """
        open_ch = "{" if opener.endswith("{") else "("
        close_ch = "}" if open_ch == "{" else ")"
        arithmetic = opener == "$(("
        start = self.i + len(opener)
        depth = 2 if arithmetic else 1
        j = start
        while j < self.n:
            c = self.s[j]
            if c == "\\":
                j += 2
                continue
            if c == "'":
                end = self.s.find("'", j + 1)
                if end == -1:
                    raise _Refuse(f"unterminated quote inside {opener}")
                j = end + 1
                continue
            if c == '"':
                j += 1
                while j < self.n and self.s[j] != '"':
                    j += 2 if self.s[j] == "\\" else 1
                if j >= self.n:
                    raise _Refuse(f"unterminated quote inside {opener}")
                j += 1
                continue
            if c == open_ch:
                depth += 1
            elif c == close_ch:
                depth -= 1
                if depth == 0:
                    self.i = j + 1
                    return self.s[start:j - 1] if arithmetic else self.s[start:j]
            j += 1
        raise _Refuse(f"unterminated {opener}")

    def _backtick(self) -> tuple[str, Seq]:
        j = self.i + 1
        buf: list[str] = []
        while j < self.n:
            c = self.s[j]
            if c == "\\" and j + 1 < self.n and self.s[j + 1] in "`\\$":
                buf.append(self.s[j + 1])
                j += 2
                continue
            if c == "`":
                self.i = j + 1
                inner = "".join(buf)
                return f"`{inner}`", _parse_nested(inner, self.depth + 1)
            buf.append(c)
            j += 1
        raise _Refuse("unterminated backtick")

    def _process_substitution(self) -> tuple[str, Seq]:
        marker = self.s[self.i:self.i + 2]
        inner = self._balanced(marker, ")")
        return f"{marker}{inner})", _parse_nested(inner, self.depth + 1)


def _substitutions_in(text: str, depth: int) -> list[Seq]:
    """The command substitutions inside an unquoted here-document body (expansions are live there)."""
    found: list[Seq] = []
    lx = _Lexer(text, depth)
    i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c == "$" and text[i + 1:i + 2] == "(" and text[i + 1:i + 3] != "((":
            lx.i = i
            inner = lx._balanced("$(", ")")
            found.append(_parse_nested(inner, depth + 1))
            i = lx.i
            continue
        if c == "`":
            lx.i = i
            _t, seq = lx._backtick()
            found.append(seq)
            i = lx.i
            continue
        i += 1
    return found


# ── Parser ────────────────────────────────────────────────────────────────


class _Parser:
    def __init__(self, toks: list[_Tok], depth: int) -> None:
        self.t = toks
        self.p = 0
        self.depth = depth
        self.steps = 0

    def _peek(self) -> _Tok | None:
        return self.t[self.p] if self.p < len(self.t) else None

    def _is_op(self, *ops: str) -> bool:
        tok = self._peek()
        return tok is not None and tok.kind == "OP" and tok.op in ops

    def _skip_newlines(self) -> None:
        while (tok := self._peek()) is not None and tok.kind == "NEWLINE":
            self.p += 1

    def parse_seq(self, closer: str | None) -> Seq:
        stmts: list[Stmt] = []
        while True:
            self.steps += 1
            if self.steps > 20 * MAX_TOKENS:
                raise _Refuse("parse budget exceeded")  # a safety net: every loop above must consume a token
            self._skip_newlines()
            while self._is_op(";"):
                self.p += 1
                self._skip_newlines()
            tok = self._peek()
            if tok is None:
                if closer is not None:
                    raise _Refuse(f"missing closing {closer!r}")
                break
            if closer == ")" and tok.kind == "OP" and tok.op == ")":
                break
            if closer == "}" and tok.kind == "WORD" and tok.word is not None and tok.word.text == "}" and not tok.word.quoted:
                break
            if tok.kind == "OP" and tok.op == ")":
                raise _Refuse("unbalanced `)`")
            before = self.p
            stmt = self._statement()
            if stmt is not None:
                stmts.append(stmt)
            elif self.p == before:
                # An operator with nothing before it (`&& a`, `| b`): consuming no token would loop forever.
                raise _Refuse(f"unexpected {self._describe(tok)}")
        return Seq(tuple(stmts))

    @staticmethod
    def _describe(tok: _Tok) -> str:
        return f"operator {tok.op!r}" if tok.kind == "OP" else f"{tok.kind.lower()} token"

    def _statement(self) -> Stmt | None:
        first = self._pipeline()
        if first is None:
            return None
        rest: list[tuple[str, Pipeline]] = []
        while self._is_op("&&", "||"):
            op = self.t[self.p].op
            self.p += 1
            self._skip_newlines()
            nxt = self._pipeline()
            if nxt is None:
                raise _Refuse(f"`{op}` has no right-hand command")
            rest.append((op, nxt))
        background = False
        if self._is_op("&"):
            self.p += 1
            background = True
        return Stmt(AndOr(first, tuple(rest)), background)

    def _pipeline(self) -> Pipeline | None:
        negated = False
        tok = self._peek()
        if tok is not None and tok.kind == "WORD" and tok.word is not None and tok.word.text == "!" and not tok.word.quoted:
            negated = True
            self.p += 1
        stages: list[Node] = []
        stderr: list[bool] = []
        node = self._command()
        if node is None:
            if negated:
                raise _Refuse("`!` has no command")
            return None
        stages.append(node)
        while self._is_op("|", "|&"):
            op = self.t[self.p].op
            self.p += 1
            self._skip_newlines()
            nxt = self._command()
            if nxt is None:
                raise _Refuse(f"`{op}` has no right-hand command")
            stderr.append(op == "|&")
            stages.append(nxt)
        return Pipeline(tuple(stages), negated, tuple(stderr))

    def _command(self) -> Node | None:
        tok = self._peek()
        if tok is None or tok.kind == "NEWLINE":
            return None
        if tok.kind == "OP":
            if tok.op == "(":
                self.p += 1
                if self.depth >= MAX_DEPTH:
                    raise _Refuse("nesting too deep")
                self.depth += 1
                body = self.parse_seq(")")
                self.depth -= 1
                self.p += 1  # consume ")"
                return Group("subshell", body, self._trailing_redirects())
            if tok.op in (";", "&", "&&", "||", "|", "|&", ")"):
                return None
            raise _Refuse(f"unexpected operator {tok.op!r}")
        assigns: list[tuple[str, Word]] = []
        words: list[Word] = []
        redirects: list[Redirect] = []
        while True:
            tok = self._peek()
            if tok is None or tok.kind == "NEWLINE" or (tok.kind == "OP" and tok.op != "("):
                break
            if tok.kind == "REDIR":
                redirects.append(self._redirect(tok))
                self.p += 1
                continue
            if tok.kind == "OP":  # "(" after words: a function definition `name ( )`
                if words and self._function_def_follows():
                    self.p += 2
                    words = []
                    assigns = []
                    continue
                raise _Refuse("unsupported `(` after a command word")
            word = tok.word
            assert word is not None
            if not words and not word.quoted:
                if word.text == "{":
                    self.p += 1
                    if self.depth >= MAX_DEPTH:
                        raise _Refuse("nesting too deep")
                    self.depth += 1
                    body = self.parse_seq("}")
                    self.depth -= 1
                    self.p += 1
                    return Group("brace", body, self._trailing_redirects())
                if word.text in _RESERVED_PREFIX:
                    self.p += 1
                    continue
                if word.text in _RESERVED_SKIP_STATEMENT:
                    self.p += 1
                    while (nxt := self._peek()) is not None and nxt.kind != "NEWLINE" and nxt.kind != "OP":
                        self.p += 1
                    return Simple((), (), ())
                if word.text == "case":
                    raise _Refuse("`case` is not supported")
                if word.text in _LOOP_HEADERS:
                    return self._loop_header()
                if word.text == "function":
                    self.p += 1
                    continue
                name, eq, value = word.text.partition("=")
                if eq and name and (name[0].isalpha() or name[0] == "_") and all(ch.isalnum() or ch == "_" for ch in name):
                    assigns.append((name, Word(value, dynamic=word.dynamic, glob=word.glob, quoted=word.quoted, subs=word.subs)))
                    self.p += 1
                    continue
            words.append(word)
            self.p += 1
        return Simple(tuple(assigns), tuple(words), tuple(redirects))

    def _function_def_follows(self) -> bool:
        a = self.t[self.p] if self.p < len(self.t) else None
        b = self.t[self.p + 1] if self.p + 1 < len(self.t) else None
        return bool(a and b and a.kind == "OP" and a.op == "(" and b.kind == "OP" and b.op == ")")

    def _loop_header(self) -> Simple:
        """`for x in a b c` runs no command, but its words may contain substitutions, so they are kept under a name no rule matches."""
        words: list[Word] = [Word("::loop-header::")]
        while (tok := self._peek()) is not None and tok.kind != "NEWLINE" and tok.kind != "OP":
            if tok.kind == "WORD" and tok.word is not None:
                words.append(tok.word)
            self.p += 1
        return Simple((), tuple(words), ())

    def _redirect(self, tok: _Tok) -> Redirect:
        return Redirect(tok.op, tok.fd, tok.word, tok.heredoc)

    def _trailing_redirects(self) -> tuple[Redirect, ...]:
        out: list[Redirect] = []
        while (tok := self._peek()) is not None and tok.kind == "REDIR":
            out.append(self._redirect(tok))
            self.p += 1
        return tuple(out)


def _parse_nested(src: str, depth: int) -> Seq:
    if depth > MAX_DEPTH:
        raise _Refuse("command substitution nested too deeply")
    toks = _Lexer(src, depth).run()
    return _Parser(toks, depth).parse_seq(None)


def parse(command: str) -> Parse:
    """Parse `command`. Never raises: any refusal comes back as `Parse(ok=False, reasons=(...))`."""
    if not isinstance(command, str):
        return Parse(False, None, ("command must be text",))
    if "\x00" in command:
        return Parse(False, None, ("NUL byte in command",))
    if len(command) > MAX_LENGTH:
        return Parse(False, None, (f"command longer than {MAX_LENGTH} characters",))
    try:
        return Parse(True, _parse_nested(command, 0))
    except _Refuse as exc:
        return Parse(False, None, (str(exc),))
    except RecursionError:
        return Parse(False, None, ("nesting too deep",))
