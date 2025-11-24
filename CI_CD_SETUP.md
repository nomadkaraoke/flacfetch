# CI/CD Setup Summary

This document describes the complete CI/CD setup for flacfetch, following patterns from your other projects (karaoke-gen, python-audio-separator).

## What Was Added

### 1. Modern Python Packaging (`pyproject.toml`)
- ✅ Modern build system using setuptools
- ✅ Project metadata and dependencies
- ✅ Development dependencies (pytest, ruff, mypy, coverage)
- ✅ Console script entry point (`flacfetch`)
- ✅ Tool configurations (pytest, ruff, mypy, coverage)
- ✅ Python 3.8+ support
- ✅ SPDX license format (MIT)

### 2. GitHub Actions Workflows

#### Test Workflow (`.github/workflows/test.yml`)
- Runs on: push to main/develop, pull requests
- Matrix testing:
  - Python versions: 3.8, 3.9, 3.10, 3.11, 3.12
  - Operating systems: Ubuntu, macOS, Windows
- Steps:
  - Lint with ruff
  - Type check with mypy
  - Run tests with coverage
  - Upload coverage to Codecov

#### Release Workflow (`.github/workflows/release.yml`)
- Triggers on: git tags matching `v*`
- Creates GitHub Release with:
  - Generated release notes from commits
  - Built distribution files (.tar.gz and .whl)

#### Publish Workflow (`.github/workflows/publish.yml`)
- Triggers on: GitHub Release published
- Uses PyPI Trusted Publishing (no API token needed!)
- Builds and uploads to PyPI automatically

#### CodeQL Workflow (`.github/workflows/codeql.yml`)
- Security scanning
- Runs on: push to main, pull requests, weekly schedule

### 3. Documentation

#### CONTRIBUTING.md
- Development setup instructions
- Testing guidelines
- Release process documentation
- Code quality tools usage

#### CHANGELOG.md
- Version 0.2.0 with all recent improvements
- Follows Keep a Changelog format
- Semantic versioning

#### Updated README.md
- Added badges (PyPI, Python version, Tests, Coverage, License)
- Installation from PyPI
- Development installation instructions
- Contributing section

### 4. GitHub Templates
- Bug report template
- Feature request template
- Pull request template

### 5. Release Helper Script
- `scripts/release.sh` - Automates version bumping and tag creation

### 6. Updated Files
- `flacfetch/__init__.py` - Added version info
- `MANIFEST.in` - Updated to include all necessary files
- `.gitignore` - Already comprehensive

## How to Use

### Running Tests Locally

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run with coverage
pytest --cov=flacfetch --cov-report=term-missing

# Lint
ruff check flacfetch/

# Type check
mypy flacfetch/
```

### Creating a Release

#### Option 1: Using the Helper Script

```bash
./scripts/release.sh 0.3.0
git push origin main
git push origin v0.3.0
```

#### Option 2: Manual Process

1. Update version in `flacfetch/__init__.py` and `pyproject.toml`
2. Update `CHANGELOG.md` with changes
3. Commit: `git commit -m "Bump version to X.Y.Z"`
4. Tag: `git tag -a vX.Y.Z -m "Release version X.Y.Z"`
5. Push: `git push origin main && git push origin vX.Y.Z`

### What Happens Automatically

When you push a tag:
1. ✅ **Release workflow** creates a GitHub Release with notes
2. ✅ **Publish workflow** builds and uploads to PyPI

## PyPI Trusted Publisher Setup (One-Time)

To enable automatic PyPI publishing:

1. Go to [PyPI](https://pypi.org/) (create account if needed)
2. Navigate to: Account Settings → Publishing → Add a new pending publisher
3. Fill in:
   - **PyPI Project Name**: `flacfetch`
   - **Owner**: `nomadkaraoke`
   - **Repository name**: `flacfetch`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`

After the first release, all future releases will publish automatically!

## Codecov Setup (Optional)

1. Go to [codecov.io](https://codecov.io/)
2. Sign in with GitHub
3. Add the `nomadkaraoke/flacfetch` repository
4. Add `CODECOV_TOKEN` to GitHub Secrets (if needed)

## Current Status

✅ All tests passing (13/13)
✅ Package builds successfully
✅ Workflows configured
✅ Documentation complete
✅ Ready for first release!

## Next Steps

1. **Review and commit all changes**
2. **Push to GitHub**: `git push origin main`
3. **Set up PyPI Trusted Publisher** (see above)
4. **Create first release**: `./scripts/release.sh 0.2.0`
5. **Push tag**: `git push origin v0.2.0`
6. **Verify** package appears on PyPI
7. **Test installation**: `pip install flacfetch`

## Version History

- **0.2.0** - CI/CD setup, improved CLI, better UX
- **0.1.0** - Initial release

## Files Added/Modified

### New Files
```
pyproject.toml
CONTRIBUTING.md
CHANGELOG.md
CI_CD_SETUP.md
.github/workflows/test.yml
.github/workflows/publish.yml
.github/workflows/release.yml
.github/workflows/codeql.yml
.github/ISSUE_TEMPLATE/bug_report.md
.github/ISSUE_TEMPLATE/feature_request.md
.github/pull_request_template.md
scripts/release.sh
```

### Modified Files
```
flacfetch/__init__.py (added version info)
README.md (added badges, improved installation)
MANIFEST.in (updated includes)
```

## Maintenance

### Updating Dependencies
```bash
pip install -e ".[dev]"
pip list --outdated
```

### Security Updates
- CodeQL scans run automatically weekly
- GitHub Dependabot can be enabled for automated dependency PRs

---

**Questions?** See CONTRIBUTING.md or open an issue!

