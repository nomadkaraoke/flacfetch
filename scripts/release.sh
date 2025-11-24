#!/bin/bash
# Release helper script
# Usage: ./scripts/release.sh <version>
# Example: ./scripts/release.sh 0.2.0

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <version>"
    echo "Example: $0 0.2.0"
    exit 1
fi

VERSION=$1
CURRENT_BRANCH=$(git branch --show-current)

if [ "$CURRENT_BRANCH" != "main" ]; then
    echo "Error: Must be on main branch to create a release"
    echo "Current branch: $CURRENT_BRANCH"
    exit 1
fi

echo "Creating release v$VERSION"
echo "================================"

# Check for uncommitted changes
if ! git diff-index --quiet HEAD --; then
    echo "Error: You have uncommitted changes. Please commit or stash them first."
    exit 1
fi

# Update version in files
echo "Updating version numbers..."
sed -i.bak "s/__version__ = \".*\"/__version__ = \"$VERSION\"/" flacfetch/__init__.py
sed -i.bak "s/version = \".*\"/version = \"$VERSION\"/" pyproject.toml
rm flacfetch/__init__.py.bak pyproject.toml.bak 2>/dev/null || true

# Show changes
echo ""
echo "Version updated in:"
echo "  - flacfetch/__init__.py"
echo "  - pyproject.toml"
echo ""

# Commit version bump
git add flacfetch/__init__.py pyproject.toml
git commit -m "Bump version to $VERSION"

# Create and push tag
echo "Creating git tag v$VERSION..."
git tag -a "v$VERSION" -m "Release version $VERSION"

echo ""
echo "Ready to push! Run the following commands:"
echo ""
echo "  git push origin main"
echo "  git push origin v$VERSION"
echo ""
echo "This will trigger:"
echo "  1. GitHub Release creation"
echo "  2. PyPI package publication"
echo ""

