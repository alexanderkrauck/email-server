# Contributing

Bug reports are more useful than pull requests right now, and a bug report that
names the mail provider and what the server did is worth ten that say it does not
work.

## Running it locally

The test suite is self-contained: in-memory SQLite, a temporary data directory,
no network, no Docker.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
pytest -q
ruff check .
```

Both must pass. CI additionally applies every migration against a real
PostgreSQL from an empty database, seeds rows in the pre-migration shape, and
asserts the planner actually picks the full-text index — because a migration
that works on your machine and not on someone's four-year-old install is the
failure mode that matters here.

## What this project cares about

Reviews will push on these, so they are worth knowing before you write code.

**Never lose mail.** Deletion is a tombstone with a grace period, not a
`DELETE`. Inference about what happened upstream is refused when the evidence is
incomplete rather than acted on. If a change can make mail disappear, it needs an
argument for why it cannot.

**Say what you do not know.** A message whose flags were never fetched reports
`null`, not `false`. A search whose scope excluded messages it could not judge
returns a warning naming the count. The model acting on this data has no other
way to find out.

**The server confirms, then the index records.** No write is stored locally that
the mail server did not acknowledge. Optimistic local updates are the reason
other tools drift.

**Comments explain why, not what.** `# increment counter` above `counter += 1`
is noise. `# Not .SILENT: the untagged FETCH it suppresses is the only evidence
the write landed` is the kind that survives.

**Tests describe the failure, not the function.** `test_a_short_census_is_refused_rather_than_tombstoning_the_account`
tells you what breaks. `test_apply_folder_snapshots_2` does not.

## Pull requests

- One concern per PR. A refactor bundled with a fix gets both reviewed badly.
- Commit messages explain what was wrong and why the fix is right. The history
  is the design document for this project; treat it as one.
- New behaviour needs a test that fails without it.
- If it touches sync, migrations or the write path, say how you verified it
  against a real mailbox. Unit tests cannot catch a server applying `\Seen` on
  a non-peek fetch, and one did not.

## Things that are deliberate

Before proposing to change them, know they were decided on purpose:

- **`UP` lint rules are disabled.** Modernising ~53 call sites is churn.
- **The MCP tool surface is small.** Adding a tool needs an argument for why an
  existing one cannot express it.
- **`simple` is always the first search configuration**, so an exact search can
  never be widened by a stemmer.
- **Attachment binaries are never stored.**
- **Writes are ordinary behaviour, not a feature flag.** Safety belongs in the
  mechanism — leases, confirmation, tombstones — not in a switch that turns the
  product off.
