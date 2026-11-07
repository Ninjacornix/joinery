#!/usr/bin/env bash
# Check a downloaded file against its recorded hash. Fails closed: an absent
# entry, an empty checksums file or a mismatch all abort the build, so nothing
# unverified is ever unpacked.
#
# Usage: joinery-verify-download <file> <name>
set -euo pipefail

file=$1
name=$2
checksums=${JOINERY_CHECKSUMS:-/opt/joinery/checksums.txt}

if [[ ! -s ${checksums} ]]; then
  echo "verify: ${checksums} is missing or empty." >&2
  echo "verify: run sandbox/fetch-checksums.sh to record hashes first." >&2
  exit 1
fi

expected=$(awk -v want="${name}" '$1 == want { print $2 }' "${checksums}")
if [[ -z ${expected} ]]; then
  echo "verify: no hash recorded for '${name}' in ${checksums}." >&2
  echo "verify: re-run sandbox/fetch-checksums.sh after changing versions.env." >&2
  exit 1
fi

actual=$(sha256sum "${file}" | cut -d' ' -f1)
if [[ ${actual} != "${expected}" ]]; then
  echo "verify: ${name} hash mismatch." >&2
  echo "  expected ${expected}" >&2
  echo "  actual   ${actual}" >&2
  exit 1
fi

echo "verify: ${name} ok"
