# Git Hooks

This directory contains git hooks for the flacfetch project.

## Installation

To install the hooks, run:

```bash
./.githooks/install.sh
```

Or manually:

```bash
cp .githooks/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## Available Hooks

### pre-commit

Runs before each commit to ensure code quality:

- **Linting**: Runs `ruff check --fix` on all Python files being committed
- **Auto-fix**: Automatically fixes issues when possible (e.g., formatting, imports)
- **Prevention**: Prevents commit if there are unfixable linting errors

#### Behavior

1. When you commit, the hook automatically runs ruff on changed Python files
2. If ruff can auto-fix issues (like whitespace, import sorting), it will:
   - Fix the issues
   - Stage the fixed files
   - Continue with the commit
3. If ruff finds issues it cannot auto-fix, it will:
   - Show the errors
   - Prevent the commit
   - Ask you to fix the issues manually

#### Bypassing the Hook

If you absolutely need to commit without running the hook (not recommended):

```bash
git commit --no-verify -m "your message"
```

## Maintenance

When updating hooks:

1. Edit the hook file in `.githooks/`
2. Run `./.githooks/install.sh` to update your local copy
3. Commit the changes so other developers get the updated hooks

