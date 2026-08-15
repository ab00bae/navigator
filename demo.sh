#!/usr/bin/env bash
# Runs the pipeline end to end against a throwaway database and asserts on the
# result, including the claim that matters most: running it twice changes
# nothing. Exits non-zero if any check fails, so it doubles as a smoke test.
#
#   ./demo.sh

set -uo pipefail
cd "$(dirname "$0")"

DEMO_DB="demo.db"
SOURCE="data/raw.csv"

if [ -t 1 ]; then
  GREEN=$'\033[32m'; RED=$'\033[31m'; BOLD=$'\033[1m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
  GREEN=''; RED=''; BOLD=''; DIM=''; RESET=''
fi

if [ -x ".venv/Scripts/python.exe" ]; then
  PY=".venv/Scripts/python.exe"
elif [ -x ".venv/bin/python" ]; then
  PY=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PY="python3"
else
  PY="python"
fi

pass=0
fail=0

cleanup() { rm -rf "$DEMO_DB" .demo-tmp; }
trap cleanup EXIT

section() { printf "\n${BOLD}%s${RESET}\n" "$1"; }

record() {
  if [ "$1" = "1" ]; then
    printf "  ${GREEN}PASS${RESET}  %s\n" "$2"
    pass=$((pass + 1))
  else
    printf "  ${RED}FAIL${RESET}  %s\n" "$2"
    [ -n "${3:-}" ] && printf "        ${DIM}%s${RESET}\n" "$3"
    fail=$((fail + 1))
  fi
}

expect_eq() {
  if [ "$1" = "$2" ]; then
    record 1 "$3"
  else
    record 0 "$3" "expected '$1', got '$2'"
  fi
}

# Single-value SQL query against the demo database.
q() {
  "$PY" -c "
import sqlite3, sys
connection = sqlite3.connect('$DEMO_DB')
row = connection.execute(sys.argv[1]).fetchone()
print('' if row is None or row[0] is None else row[0])
" "$1" 2>/dev/null
}

export DATABASE_URL="sqlite:///${DEMO_DB}"

printf "${BOLD}navigator demo${RESET}\n"
printf "${DIM}python: %s   source: %s${RESET}\n" "$PY" "$SOURCE"

rm -f "$DEMO_DB"

# ---------------------------------------------------------------------------

section "First run — a cold load"
"$PY" -m navigator run --source "$SOURCE" 2>/dev/null
expect_eq "0" "$?" "navigator run exits successfully"

expect_eq "success" "$(q "select status from pipeline_runs where id=1")" \
  "run #1 is recorded as success"
expect_eq "4000" "$(q "select rows_extracted from pipeline_runs where id=1")" \
  "4,000 rows extracted from the source"
expect_eq "20" "$(q "select rows_rejected from pipeline_runs where id=1")" \
  "20 rows rejected by validation"
expect_eq "3980" "$(q "select rows_inserted from pipeline_runs where id=1")" \
  "3,980 rows inserted"
expect_eq "3980" "$(q "select count(*) from service_requests")" \
  "the table holds 3,980 rows"

section "Why rows were rejected"
expect_eq "20" "$(q "select count(*) from rejections where run_id=1")" \
  "every rejection is stored with its reason"
expect_eq "closed_before_created" \
  "$(q "select rule from rejections where run_id=1 limit 1")" \
  "the rule that fired is named"
expect_eq "1" \
  "$(q "select count(*) from (select 1 from rejections where run_id=1 and message like '%precedes%' limit 1)")" \
  "the message explains the contradiction"

section "Second run — the idempotency claim"
"$PY" -m navigator run --source "$SOURCE" --quiet 2>/dev/null >/dev/null
expect_eq "0" "$?" "re-running the same source succeeds"
expect_eq "0" "$(q "select rows_inserted from pipeline_runs where id=2")" \
  "nothing is inserted the second time"
expect_eq "3980" "$(q "select rows_updated from pipeline_runs where id=2")" \
  "all 3,980 rows are updated in place"
expect_eq "3980" "$(q "select count(*) from service_requests")" \
  "the table still holds exactly 3,980 rows"
expect_eq "0" \
  "$(q "select count(*) from service_requests where first_loaded_at = last_loaded_at")" \
  "first_loaded_at survived the update"

section "Data quality of what was loaded"
expect_eq "0" "$(q "select count(*) from service_requests where resolution_hours < 0")" \
  "no negative resolution times survived"
expect_eq "0" \
  "$(q "select count(*) from service_requests where borough='Unspecified' or status='Unspecified'")" \
  "placeholder values were normalised to NULL"
expect_eq "0" \
  "$(q "select count(*) from service_requests where incident_zip is not null and length(incident_zip)<>5")" \
  "every surviving ZIP is five digits"
expect_eq "0" \
  "$(q "select count(*) from service_requests where complaint_category is null")" \
  "every row was assigned a category"
expect_eq "0" \
  "$(q "select count(*) from service_requests where closed_at is not null and closed_at < created_at")" \
  "no row closes before it was created"

section "Reporting commands"
"$PY" -m navigator status >/dev/null 2>&1
expect_eq "0" "$?" "navigator status runs"
"$PY" -m navigator rejects --run 1 >/dev/null 2>&1
expect_eq "0" "$?" "navigator rejects runs"
expect_eq "2" "$(q "select count(*) from pipeline_runs where status='success'")" \
  "both runs are visible in the history"

section "A broken source fails loudly"
mkdir -p .demo-tmp
printf 'unique_key,created_date\n1,2026-08-12T01:00:00.000\n' > .demo-tmp/broken.csv
"$PY" -m navigator run --source .demo-tmp/broken.csv >/dev/null 2>&1
expect_eq "2" "$?" "a source missing columns exits with code 2"
expect_eq "failed" "$(q "select status from pipeline_runs order by id desc limit 1")" \
  "the failed run is still recorded"
expect_eq "3980" "$(q "select count(*) from service_requests")" \
  "the failure loaded nothing"

# ---------------------------------------------------------------------------

total=$((pass + fail))
printf "\n${BOLD}%s${RESET}\n" "Summary"
printf "  %d/%d checks passed\n" "$pass" "$total"

if [ "$fail" -gt 0 ]; then
  printf "  ${RED}%d failed${RESET}\n\n" "$fail"
  exit 1
fi

printf "  ${GREEN}all checks passed${RESET}\n\n"
