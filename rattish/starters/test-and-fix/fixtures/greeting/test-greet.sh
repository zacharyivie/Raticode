#!/bin/sh
# The test for greet.sh. Don't change this file.
here=$(dirname "$0")
got=$(sh "$here/greet.sh" Ada)
if [ "$got" = "Hello, Ada!" ]; then
  echo "ok: greet.sh says Hello, Ada!"
  exit 0
fi
echo "FAIL: expected 'Hello, Ada!' but greet.sh printed '$got'"
exit 1
