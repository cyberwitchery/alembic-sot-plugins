#!/bin/sh
# the change that arrives through git: ber1-leaf02 goes live.
set -eu
here="$(cd "$(dirname "$0")" && pwd)"
file="$here/.repo/sites/ber1.yaml"

# ber1-leaf02 is the only device in ber1.yaml whose status is planned.
sed -i.bak 's/^      status: planned$/      status: active/' "$file" && rm -f "$file.bak"
git -C "$here/.repo" -c user.name=demo -c user.email=demo@example.invalid \
  commit -q -am "ber1: leaf02 goes live"
git -C "$here/.repo" log --oneline -1
