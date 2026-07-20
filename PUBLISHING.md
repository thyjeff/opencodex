# Publishing

This repository is published on GitHub as
`thyjeff/opencodex`.

## Release surface

- Package: `opencodex`
- Current version: `0.1.2`
- CLI entry point: `opencodex`
- Python: `>=3.11`
- Build backend: `uv_build`
- Verification: `uv run python -m pytest tests -v`,
  `uvx ruff check`, `uv build`

## Install

```bash
uvx --from git+https://github.com/thyjeff/opencodex opencodex
```

No PyPI, no AUR. `uvx` from git is the only install path.

## Release flow

1. Bump version in `pyproject.toml`, `src/opencodex_proxy/__init__.py`, this file.
2. Add `CHANGELOG.md` entry.
3. Commit, tag `vX.Y.Z`, push.
4. CI builds the wheel and creates the GitHub release automatically.

## License

MIT. See [LICENSE](LICENSE).
