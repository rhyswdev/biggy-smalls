#!/bin/sh
# The only way to read the incident: the eleven tools, as of 14:52 UTC on the incident's day.
#   ./tools list                      the tools and their caps
#   ./tools schema                    every tool's description and arguments
#   ./tools call <tool> key=value ... one call; a value is read as JSON when it parses as JSON
# Every call is logged, with its time, to calls.log in this folder.
here="$(cd "$(dirname "$0")" && pwd)"
for a in "$@"; do
  case "$a" in --now*|--bundle*|--tags*) echo "tools: the clock, the data and the tags are fixed; leave out $a" >&2; exit 2;; esac
done
cmd="$1"; shift 2>/dev/null
case "$cmd" in
  call) printf '%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$here/calls.log"
        exec uv run --quiet --project /Users/rw/code/me/biggy-smalls --python 3.12 smalls tools call --now 14:52 --bundle "$here/bundle/larkspur-checkout-2026-10-13" "$@" ;;
  list|schema) exec uv run --quiet --project /Users/rw/code/me/biggy-smalls --python 3.12 smalls tools "$cmd" ;;
  *) echo "usage: ./tools list | ./tools schema | ./tools call <tool> key=value ..." >&2; exit 2 ;;
esac
