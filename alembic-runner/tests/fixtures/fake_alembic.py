#!/usr/bin/env python3
"""stands in for the alembic binary. the runner hands it a scrubbed environment,
so it takes its instructions from files next to itself and records its call there."""

import json
import os
import sys
import time
from pathlib import Path

here = Path(__file__)


def sibling(suffix, default):
    path = here.with_suffix(suffix)
    return path.read_text().strip() if path.exists() else default


if sys.argv[1:] == ["--version"]:
    print(sibling(".version", "alembic 0.13.0"))
    sys.exit(0)

here.with_suffix(".record").write_text(
    json.dumps({"argv": sys.argv[1:], "env": dict(os.environ), "cwd": os.getcwd()})
)

behaviour = sibling(".mode", "ok")
if behaviour == "sleep":
    time.sleep(30)
if behaviour == "fail":
    print("Error: backend unreachable", file=sys.stderr)
    sys.exit(1)
if behaviour.startswith("exit "):
    print("Error: as told", file=sys.stderr)
    sys.exit(int(behaviour.split()[1]))
sys.exit(0)
