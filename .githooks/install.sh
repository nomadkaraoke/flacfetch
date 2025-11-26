#!/bin/bash

# Script to install git hooks from .githooks directory

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GIT_DIR="$(git rev-parse --git-dir)"

echo "Installing git hooks..."

# Copy all executable files from .githooks to .git/hooks
for hook in "$SCRIPT_DIR"/*; do
    # Skip this install script
    if [ "$(basename "$hook")" = "install.sh" ] || [ "$(basename "$hook")" = "README.md" ]; then
        continue
    fi
    
    # Get the hook name
    hook_name="$(basename "$hook")"
    
    # Copy and make executable
    cp "$hook" "$GIT_DIR/hooks/$hook_name"
    chmod +x "$GIT_DIR/hooks/$hook_name"
    
    echo "✓ Installed $hook_name"
done

echo ""
echo "✅ Git hooks installed successfully!"
echo ""
echo "The following hooks are now active:"
echo "  - pre-commit: Runs ruff linter with auto-fix"

