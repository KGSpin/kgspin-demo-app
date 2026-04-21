# Domain → lander registry

`src/kgspin_demo/domain_fetchers.py` holds the single source of truth for
which landers serve which domains. It is pure data + two lookup helpers,
behaves like a dict-shaped YAML file, and is safe to import from anywhere.

## The registry

```python
DOMAIN_FETCHERS: dict[str, list[str]] = {
    "financial": ["sec_edgar", "marketaux", "yahoo_rss"],
    "clinical":  ["clinicaltrials_gov", "newsapi"],
}
```

Keys are **domain names**. Values are lists of **backend-named lander IDs**
— these match the `name` attribute of each lander class and the fetcher
record id stored in admin (see
[ADR-004](architecture/decisions/ADR-004-backend-named-landers.md)).

A lander can appear under more than one domain. For example, `newsapi`
is listed only under `clinical` above, because `newsapi` in clinical mode
uses a specific trial-derived query path — but the lander itself is
domain-agnostic, and `--domain financial` works on the CLI.

## Helpers

Two pure functions. Neither performs network I/O nor logs.

- `fetchers_for(domain)` — returns the lander IDs that serve `domain`.
  Returns `[]` for unknown domains; callers decide whether that's an
  error.
- `domains_served_by(fetcher_id)` — returns the domains that reference
  `fetcher_id`. Returns `[]` for unknown IDs.

## Adding a new lander

1. Implement the lander under `src/kgspin_demo/landers/` following the
   `DocumentFetcher` ABC (see [ADR-003](architecture/decisions/ADR-003-fetcher-abc-and-admin-registry.md)).
2. Add a console script entry under `[project.scripts]` in
   `pyproject.toml`.
3. Append the lander ID to the appropriate list(s) in `DOMAIN_FETCHERS`.
4. Add the lander to the `FETCHER_SPECS` list in
   `src/kgspin_demo/cli/register_fetchers.py` so it shows up when
   operators register fetchers with admin.
5. Re-run `uv run kgspin-demo-register-fetchers`.
6. Add a section for the lander to
   [`docs/landers/README.md`](landers/README.md) — include its
   `source_extras` contract.

Sprint 13+ may migrate `DOMAIN_FETCHERS` to a top-level YAML file once
the mapping stabilizes; the two-helper API here is designed to keep the
swap trivial.

## Related

- [`docs/landers/README.md`](landers/README.md) — per-lander integration
  contract.
- [ADR-004 — Backend-named landers](architecture/decisions/ADR-004-backend-named-landers.md).
