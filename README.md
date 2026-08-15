# navigator

A staged ETL pipeline over NYC 311 service requests. It extracts a CSV,
validates it against explicit quality rules, cleans and derives fields, loads
the result into a database, and records what happened on every run, including
the rows it refused and why.

Running it twice does not change the answer. That is the property the whole
design is arranged around.

```
navigator run --source data/raw.csv

  extract   4,000 rows                         30ms
  validate  3,980 valid / 20 rejected          26ms
  transform 3,980 rows                         70ms
  load      3,980 inserted / 0 updated        850ms

                  Run #1  ·  success
 Stage     │  Rows │ Detail                    │  Time
───────────┼───────┼───────────────────────────┼───────
 extract   │ 4,000 │ read from source          │  30ms
 validate  │ 3,980 │ 20 rejected               │  26ms
 transform │ 3,980 │ cleaned and derived       │  70ms
 load      │ 3,980 │ 3,980 inserted, 0 updated │ 850ms
───────────┼───────┼───────────────────────────┼───────
 total     │       │                           │ 976ms

                              Rejections by rule
 Rule                  │ Rows │ Example
───────────────────────┼──────┼──────────────────────────────────────────────
 closed_before_created │   20 │ line 1088: closed_date 2026-08-11 21:58:00
                       │      │ precedes created_date 2026-08-11 21:58:29
```

## What this demonstrates

| Practice | Where to look |
| --- | --- |
| Staged pipeline with one job per stage | [`navigator/pipeline/`](navigator/pipeline/) |
| Idempotent load — safe to re-run | [`pipeline/load.py`](navigator/pipeline/load.py) |
| Data quality rules with specific failure messages | [`pipeline/validate.py`](navigator/pipeline/validate.py) |
| Pure, deterministic transforms | [`pipeline/transform.py`](navigator/pipeline/transform.py) |
| Schema design, including run and rejection tracking | [`navigator/models.py`](navigator/models.py) |
| Structured logging, separated from human output | [`navigator/logging_conf.py`](navigator/logging_conf.py) |
| Timezone correctness across DST boundaries | [`pipeline/transform.py`](navigator/pipeline/transform.py) |
| 95 tests, most of them on transform and validation logic | [`tests/`](tests/) |

## Design decisions

**Re-running is a no-op, by construction.** The source's natural key
(`unique_key`) is the table's primary key and the load upserts, so a second run
over the same file reports *0 inserted, 3,980 updated* and leaves the row count
untouched. `first_loaded_at` is deliberately excluded from the update, so it
keeps recording when a row was first seen. This is asserted three different
ways in [`tests/test_load.py`](tests/test_load.py).

**Rejecting and normalising are different things.** A row is rejected only when
it is genuinely unusable — no identity, or a timeline that contradicts itself.
Merely missing detail (no ZIP, no coordinates, a borough of `Unspecified`) is
normalised to NULL and kept. Throwing away a complaint because nobody recorded
its ZIP would lose real information to no benefit.

**Every rejection is explained and kept.** Rejections go to a table, not just a
log line, with the source line number, the rule, and a message naming the actual
values. `navigator rejects --run 1` prints them long after the run.

```
 Source line │ unique_key │ Rule                  │ Why
─────────────┼────────────┼───────────────────────┼──────────────────────────────
        1088 │ 70035383   │ closed_before_created │ closed_date 2026-08-11
             │            │                       │ 21:58:00 precedes
             │            │                       │ created_date 2026-08-11
             │            │                       │ 21:58:29
```

**Timestamps are converted, not assumed.** The source publishes naive *local*
New York wall-clock times. They are localised to `America/New_York` and stored
as UTC, so a July record and a January record are directly comparable. The
awkward cases are handled explicitly and tested: the hour that does not exist in
spring is shifted forward, and the hour that happens twice in autumn is read as
daylight time. A 23-hour wall-clock span across the spring change correctly
yields 22 real hours.

**The stages do not leak into each other.** Extract does no parsing, so a
malformed source produces a precise schema error rather than a pandas
traceback. Transform re-parses the timestamps that validate already checked —
marginally redundant, but it keeps each stage independently testable, which is
worth more than the microseconds.

**Logs and reports go to different places.** Structured `key=value` logs go to
stderr; the tables go to stdout. `navigator run 2>/dev/null` still prints a
clean report, and `navigator run >/dev/null` still gives you a parseable trace.

```
02:53:45 INFO    validated rows valid=3980 rejected=20 violations=20
02:53:46 INFO    loaded rows inserted=3980 updated=0
02:53:46 INFO    run finished run_id=1 status=success rows_loaded=3980 total_ms=976
```

## Pipeline

```
  data/raw.csv
       │
   ┌───▼──────────┐
   │ extract      │  read as text, check the column contract
   ├──────────────┤
   │ validate     │  8 rules → valid rows + explained rejections
   ├──────────────┤
   │ transform    │  pure: clean, normalise, derive, convert to UTC
   ├──────────────┤
   │ load         │  upsert on the natural key
   └───┬──────────┘
       │
  service_requests · pipeline_runs · rejections
```

### Validation rules

| Rule | Rejects a row when |
| --- | --- |
| `missing_unique_key` | the identifier is blank |
| `duplicate_unique_key` | the identifier already appeared earlier in the file |
| `missing_created_date` | there is no creation timestamp |
| `unparseable_created_date` | the creation timestamp is not ISO-8601 |
| `unparseable_closed_date` | a closing timestamp is present but unreadable |
| `closed_before_created` | the ticket closes before it opens |
| `missing_complaint_type` | there is no complaint type |
| `coordinates_out_of_range` | the point is not in New York City |

The sample dataset only trips `closed_before_created`, so the other seven are
proven against purpose-built rows in [`tests/test_validate.py`](tests/test_validate.py)
rather than being claimed but never exercised.

### Derived fields

| Field | How |
| --- | --- |
| `complaint_category` | 119 complaint types rolled up into 11 categories |
| `resolution_hours` | `closed_at - created_at`, NULL while open |
| `is_closed` | whether a closing timestamp exists |
| `borough` | title-cased, placeholders nulled |
| `incident_zip` | kept only if it is five digits |

## Quickstart

Requires Python 3.11+.

```bash
git clone https://github.com/ab00bae/navigator.git
cd navigator

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt

python -m navigator run          # uses the bundled data/raw.csv
python -m navigator status
```

No database server is needed; it writes a local SQLite file. Point
`DATABASE_URL` at PostgreSQL to use that instead — the upsert is written
against whichever dialect is in use.

## CLI demo

`demo.sh` runs the pipeline twice against a throwaway database and asserts on
the outcome, including the idempotency claim and a deliberately broken source.
It exits non-zero if any check fails.

```bash
./demo.sh
```

```
Second run — the idempotency claim
  PASS  re-running the same source succeeds
  PASS  nothing is inserted the second time
  PASS  all 3,980 rows are updated in place
  PASS  the table still holds exactly 3,980 rows
  PASS  first_loaded_at survived the update

Summary
  25/25 checks passed
```

## Commands

| Command | Purpose |
| --- | --- |
| `navigator run [--source PATH] [--quiet]` | Run the pipeline |
| `navigator status [--limit N]` | Recent runs, with counts and timings |
| `navigator rejects [--run ID] [--limit N]` | Rows a run refused, and why |
| `navigator fetch [--limit N] [--out PATH]` | Download a fresh extract |
| `navigator --log-level DEBUG run` | Turn up the structured logs |

## Tests

```bash
pytest
```

95 tests. The bulk cover validation rules and transform logic — the parts where
a silent mistake corrupts data rather than crashing. Each test gets its own
SQLite file, so nothing leaks between cases.

## The data

Sampled from [NYC 311 Service Requests](https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9),
published by the City of New York as open data. `data/raw.csv` holds 4,000 rows
retrieved on 2026-08-14, committed so the demo runs without network access.
Only non-identifying columns are included — no addresses. Refresh it with
`navigator fetch`.

## Project layout

```
navigator/
  cli.py              Typer commands
  config.py           settings and the source API definition
  db.py               engine, session scope, schema creation
  models.py           service_requests, pipeline_runs, rejections
  types.py            UTC-normalising timestamp column
  logging_conf.py     structured key=value logging
  report.py           rich tables (stdout)
  pipeline/
    extract.py        stage 1 — read, check the column contract
    validate.py       stage 2 — quality rules and rejections
    transform.py      stage 3 — pure cleaning and derivation
    load.py           stage 4 — idempotent upsert
    runner.py         sequences and times the stages, records the run
data/raw.csv          bundled sample
tests/                95 tests
demo.sh               scripted end-to-end demo
```

## License

[MIT](LICENSE)
