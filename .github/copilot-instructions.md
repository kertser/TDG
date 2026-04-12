Priority instruction: all terminal output must be PowerShell-compatible on Windows.

CRITICAL: This repository runs on Windows and uses PowerShell as the default shell environment.

All shell commands, scripts, and terminal instructions must be valid PowerShell syntax.
Never generate Bash, sh, zsh, or Unix-shell syntax unless the user explicitly requests it.

Assume:
- OS: Windows
- Shell: PowerShell
- Script type: `.ps1`
- Paths: Windows style paths
- Command chaining, piping, environment variables, and file operations must be PowerShell-native

Hard rules:
- Do not use Bash or Unix syntax such as:
  - `| head`
  - `grep`
  - `sed`
  - `awk`
  - `cut`
  - `xargs`
  - `export`
  - `env VAR=value cmd`
  - `&&`
  - `||`
  - `ls -la`
  - `source`
  - `./script.sh`
  - `chmod +x`
  - forward-slash-only path assumptions
- Do not rely on Unix compatibility tools unless the user explicitly asks for Git Bash, WSL, Cygwin, or similar.
- Do not suggest `.sh` scripts unless explicitly requested.
- Prefer `.ps1` scripts over `.bat` files unless the repository already requires `.bat`.

Use PowerShell equivalents:
- `| head` -> `| Select-Object -First N`
- `grep pattern` -> `Select-String pattern`
- `ls` / `ls -la` -> `Get-ChildItem -Force`
- `cat file` -> `Get-Content file`
- `pwd` -> `Get-Location`
- `cd` -> `Set-Location`
- `cp` -> `Copy-Item`
- `mv` -> `Move-Item`
- `rm` -> `Remove-Item`
- `mkdir` -> `New-Item -ItemType Directory`
- `export NAME=value` -> `$env:NAME = "value"`
- `unset NAME` -> `Remove-Item Env:NAME`
- `which cmd` -> `Get-Command cmd`
- `touch file` -> `New-Item file -ItemType File -Force`
- `./script.sh` -> `.\script.ps1`
- `source file` -> `. .\file.ps1`

PowerShell conventions:
- Use `.\script.ps1` for local scripts
- Use `$env:NAME = "value"` for environment variables
- Use `Join-Path` when composing paths in scripts
- Use `Test-Path` before assuming a file or folder exists
- Use `-ErrorAction Stop` when failure should stop execution
- Prefer full cmdlet names in scripts when clarity matters
- Prefer idiomatic PowerShell pipelines, not text-parsing pipelines modeled after Bash
- When formatting structured output, prefer objects over plain text where practical

Command composition:
- If multiple commands must run in sequence, write them as separate lines or use PowerShell-appropriate control flow
- Do not use Bash chaining operators like `&&` or `||`
- When conditional execution is needed, use PowerShell syntax such as `if (...) { ... }`

Scripts:
- When generating automation or setup scripts, default to PowerShell `.ps1`
- Include parameter blocks when useful
- Use clear comments and basic error handling
- Prefer reusable functions over long one-liners when the task is non-trivial

If a user asks for a shell command and does not specify a shell, always answer in PowerShell.
If translating a Unix command, convert it into idiomatic PowerShell rather than giving a Unix-compatible workaround.