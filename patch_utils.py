import re

with open('wisp/tools/_utils.py', 'r') as f:
    content = f.read()

# Find the end of check_dangerous_command (the "return None" before _resolve_path)
old_block = """    # fork bomb heuristic
    if re.search(r'\\(\\)\\s*\\{[^}]*\\|[^}]*&[^}]*\\}', cmd_lower):
        return "fork bomb detected"

    return None


def _resolve_path"""

new_block = """    # fork bomb heuristic
    if re.search(r'\\(\\)\\s*\\{[^}]*\\|[^}]*&[^}]*\\}', cmd_lower):
        return "fork bomb detected"

    # -- Obfuscation / indirect execution vectors --

    # eval of any kind (eval "$(...)", eval `...`, eval 'string')
    if re.search(r'\\beval\\b', cmd_lower):
        return "dynamic code execution (eval)"

    # source / dot command with process substitution or remote fetch
    if re.search(r'\\b(source|\\.)\\s+.*\\b(curl|wget)\\b', cmd_lower):
        return "remote code execution (source from curl/wget)"
    if re.search(r'\\b(source|\\.)\\s*\\s*<\\s*\\(', cmd_lower):
        return "dynamic code execution (source with process substitution)"

    # bash -c with dangerous subcommands
    if re.search(r'\\bbash\\s+-c\\b', cmd_lower):
        return "dynamic code execution (bash -c)"

    # python/perl/ruby/node with -c or -e containing system/exec/os.system/child_process
    if re.search(r'\\bpython3?\\s+-[ce]\\b', cmd_lower) and re.search(r'\\b(os\\.system|subprocess\\.|exec\\(|system\\()', cmd_lower):
        return "dynamic code execution (python interpreter)"
    if re.search(r'\\bperl\\s+-[ce]\\b', cmd_lower) and re.search(r'\\b(system|exec|qx\\()', cmd_lower):
        return "dynamic code execution (perl interpreter)"
    if re.search(r'\\bruby\\s+-[ce]\\b', cmd_lower) and re.search(r'\\b(system|exec|eval|backtick|`[^`]*`)', cmd_lower):
        return "dynamic code execution (ruby interpreter)"
    if re.search(r'\\bnode\\s+-[ce]\\b', cmd_lower) and re.search(r'\\b(child_process|exec|spawn|eval)', cmd_lower):
        return "dynamic code execution (node interpreter)"

    # find with -exec rm / -ok rm / -execdir rm
    if re.search(r'\\bfind\\b', cmd_lower) and re.search(r'-exec(dir)?\\s+\\b(rm|mv|cp|chmod|chown|dd)\\b', cmd_lower):
        return "dangerous find -exec"

    # awk with system() or exec
    if re.search(r'\\bawk\\b', cmd_lower) and re.search(r'\\b(system|exec)\\s*\\(', cmd_lower):
        return "dynamic code execution (awk system/exec)"

    # xargs with dangerous commands
    if re.search(r'\\bxargs\\b', cmd_lower) and re.search(r'\\b(rm|mv|cp|chmod|chown|dd|sh|bash)\\b', cmd_lower):
        return "dangerous xargs command"

    # command substitution $(...) or backticks in combination with bash/sh/eval
    if re.search(r'\\b(bash|sh|zsh|eval)\\b', cmd_lower) and re.search(r'[\\$`]\\s*\\(|`[^`]*`', cmd_lower):
        return "dynamic code execution (command substitution)"

    # process substitution <(...) -- often used to hide curl | bash
    if re.search(r'\\b(bash|sh|zsh|source|\\.)\\b.*<\\s*\\(', cmd_lower):
        return "dynamic code execution (process substitution)"

    # base64 / hex / rot13 decoded and executed
    if re.search(r'\\b(base64|xxd|openssl)\\b.*\\|\\s*(bash|sh|zsh|eval)', cmd_lower):
        return "encoded payload execution"

    return None


def _resolve_path"""

if old_block not in content:
    print("ERROR: old block not found")
    exit(1)

content = content.replace(old_block, new_block)

with open('wisp/tools/_utils.py', 'w') as f:
    f.write(content)

print("Done")
