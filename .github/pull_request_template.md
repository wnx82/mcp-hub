<!-- Thanks for contributing. Keep it short; the checklist matters more than prose. -->

## What this changes

<!-- One or two sentences. Link the issue if there is one. -->

## Why

<!-- What was wrong or missing. -->

## Checklist

- [ ] No real hostnames, IPs, MACs, domains, vault UUIDs or tokens anywhere in the diff
- [ ] No new hard-coded address or path — anything site-specific goes through `config.py`
- [ ] Any new mutating tool is listed in `config.MUTATING_TOOLS`
- [ ] Any new integration is opt-in via an `*_ENABLED` flag, documented in `.env.example`
- [ ] `ruff check .` and `ruff format --check .` pass
- [ ] Tool docstrings updated (they are what the model reads)
- [ ] README tool table updated if tools were added or renamed

## How you tested it

<!-- Real fleet, or the smoke test from CONTRIBUTING.md, or both. -->
