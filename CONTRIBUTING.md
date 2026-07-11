# Contributing

Contributions are welcome. Please open an issue before making a large behavioral change.

## Setup

```bash
git clone https://github.com/ApeWorX/ape-sourcify.git
cd ape-sourcify
uv sync --all-groups
uv run --locked pre-commit install
```

## Validation

Before opening a pull request, run:

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy ape_sourcify
uv build
uv run --locked --group release twine check dist/*
```

Tests must not depend on production Sourcify unless marked `live`. Use small response fixtures for
API behavior and include regression coverage for any bug fix.

Commit messages and pull request titles use [Conventional Commits](https://www.conventionalcommits.org/).
