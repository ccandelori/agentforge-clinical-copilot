#!/usr/bin/env bash
# Run scripts/seed/validate_seed_data.sql inside the dev stack and
# exit non-zero if any check has violations > 0.
#
# Usage:
#   ./scripts/seed/validate_seed.sh                   # local dev stack
#   MYSQL_CONTAINER=other-name ./scripts/seed/validate_seed.sh
#
# The wrapper is intentionally minimal — the SQL file is the source of
# truth for what's checked; this script just enforces a non-zero exit
# on failure so it can be slotted into a CI step.

set -euo pipefail

MYSQL_CONTAINER="${MYSQL_CONTAINER:-development-easy-mysql-1}"
DB_USER="${DB_USER:-openemr}"
DB_PASS="${DB_PASS:-openemr}"
DB_NAME="${DB_NAME:-openemr}"
SQL_FILE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/validate_seed_data.sql"

color() { printf '\033[1;%sm%s\033[0m\n' "$1" "$2"; }

if ! [[ -f "$SQL_FILE" ]]; then
    color 31 "✗ SQL file not found: $SQL_FILE"
    exit 2
fi

raw=$(docker exec -i "$MYSQL_CONTAINER" \
    mariadb -u"$DB_USER" -p"$DB_PASS" "$DB_NAME" --silent --skip-column-names \
    < "$SQL_FILE")

if [[ -z "$raw" ]]; then
    color 31 "✗ no output from validation SQL — connection issue?"
    exit 2
fi

# Each line is: check_name<TAB>violations<TAB>detail
fail_count=0
pass_count=0
while IFS=$'\t' read -r name violations detail; do
    [[ -z "$name" ]] && continue
    if [[ "$violations" == "0" ]]; then
        color 32 "  ✓ $name — $detail"
        pass_count=$((pass_count + 1))
    else
        color 31 "  ✗ $name (violations=$violations) — $detail"
        fail_count=$((fail_count + 1))
    fi
done <<< "$raw"

echo
if (( fail_count > 0 )); then
    color 31 "✗ $fail_count check(s) failed, $pass_count passed"
    exit 1
fi
color 32 "✓ all $pass_count checks passed"
