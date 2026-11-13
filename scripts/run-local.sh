#!/usr/bin/env bash

set -euo pipefail

project_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_root"

export NETOOLS_UID="$(id -u)"
export NETOOLS_GID="$(id -g)"

exec docker compose --project-directory "$project_root" \
  -f "$project_root/compose.yaml" up -d --build "$@"
