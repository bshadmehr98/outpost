# Working in this repo

Outpost is a Python CLI (standard library + PyYAML). It fetches what projects need into a box,
uploads it into one Gitea server, and seals other servers so they reach only Gitea.

Before you change code, read:

- `docs/development/style.md`: how the code is written. `outpost/ssh/` is the model.
- `docs/glossary.md`: one word per idea. Use these words in code, docs and messages.

Before every commit, run all of these and make them pass:

```
scripts/format.sh             # ruff format + import sort
ruff check outpost tests      # lint (rules in pyproject.toml)
pyright                       # types (config in pyproject.toml)
python -m pytest -q           # unit tests
```

Install the tools with `pip install -e '.[dev]'` in `.venv`. Optional git hook in your clone:
`pre-commit install` (runs format and lint on each commit).

Integration tests need Docker and the internet: `OUTPOST_INTEGRATION=1 python -m pytest -q tests/integration`.

Rules:

- Never commit `backups/` or `secrets/`: they hold Gitea's keys, passwords and tokens.
- No backward-compat aliases: when you rename something, update every caller in the same change.
- Commit messages are lowercase "area: what changed", such as `ssh: split ssh.py into a package`.
