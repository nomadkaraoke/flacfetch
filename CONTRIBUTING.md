# Contributing to flacfetch

Thank you for your interest in contributing to flacfetch! This document provides guidelines and instructions for contributing.

## Development Setup

1. Clone the repository:
```bash
git clone https://github.com/nomadkaraoke/flacfetch.git
cd flacfetch
```

2. Create a virtual environment and install development dependencies:
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -e ".[dev]"
```

3. Run tests to verify your setup:
```bash
pytest
```

## Development Workflow

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=flacfetch --cov-report=term-missing

# Run only unit tests (fast)
pytest -m "not integration"

# Run specific test file
pytest tests/test_matching.py
```

### Code Quality

We use several tools to maintain code quality:

```bash
# Lint code
ruff check flacfetch/

# Auto-fix linting issues
ruff check --fix flacfetch/

# Type checking
mypy flacfetch/
```

### Making Changes

1. Create a new branch for your feature or bugfix:
```bash
git checkout -b feature/your-feature-name
```

2. Make your changes and add tests

3. Run the test suite:
```bash
pytest
```

4. Commit your changes with clear commit messages:
```bash
git commit -m "Add feature: description of your changes"
```

5. Push to your fork and create a pull request

## Release Process

Releases are automated through GitHub Actions. To create a new release:

1. **Update the version number** in two places:
   - `flacfetch/__init__.py` - update `__version__`
   - `pyproject.toml` - update `version`

2. **Commit the version bump**:
```bash
git add flacfetch/__init__.py pyproject.toml
git commit -m "Bump version to X.Y.Z"
git push origin main
```

3. **Create and push a git tag**:
```bash
git tag -a vX.Y.Z -m "Release version X.Y.Z"
git push origin vX.Y.Z
```

4. **GitHub Actions will automatically**:
   - Create a GitHub Release with release notes
   - Build the package
   - Publish to PyPI (requires PyPI trusted publisher configured)

### Version Numbering

We follow [Semantic Versioning](https://semver.org/):
- **MAJOR** version for incompatible API changes
- **MINOR** version for new functionality in a backwards compatible manner
- **PATCH** version for backwards compatible bug fixes

### First-Time PyPI Setup

To enable automatic PyPI publishing:

1. Go to [PyPI](https://pypi.org/) and create an account
2. Go to your PyPI account settings → Publishing → Add a new pending publisher
3. Configure:
   - **PyPI Project Name**: `flacfetch`
   - **Owner**: `nomadkaraoke`
   - **Repository name**: `flacfetch`
   - **Workflow name**: `publish.yml`
   - **Environment name**: `pypi`

After the first manual release, subsequent releases will be automatic.

## Code Style

- Follow PEP 8 guidelines
- Use type hints where possible
- Write docstrings for public functions and classes
- Keep functions focused and single-purpose
- Add tests for new functionality

## Pull Request Guidelines

- Include tests for new features
- Update documentation as needed
- Keep PRs focused on a single feature or fix
- Write clear commit messages
- Ensure all tests pass
- Update CHANGELOG if significant changes

## Questions?

Feel free to open an issue for questions or discussions about contributing!

