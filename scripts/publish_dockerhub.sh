#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
    printf 'Usage: bash scripts/publish_dockerhub.sh <namespace/repository> [version]\n' >&2
    exit 2
fi

hub_repository="$1"
hub_version="${2:-0.1.0}"

if [[ ! "$hub_repository" =~ ^[a-z0-9][a-z0-9_-]*/[a-z0-9][a-z0-9._-]*$ ]]; then
    printf 'Repository must use the Docker Hub format: namespace/repository (lowercase).\n' >&2
    exit 2
fi
if [[ ! "$hub_version" =~ ^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$ ]]; then
    printf 'Invalid image version tag.\n' >&2
    exit 2
fi

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd -- "$project_dir"

# Authentication is managed by docker login; no tokens belong in this script.
docker build --pull --tag "$hub_repository:$hub_version" --tag "$hub_repository:latest" .
docker push "$hub_repository:$hub_version"
if [[ "$hub_version" != latest ]]; then
    docker push "$hub_repository:latest"
fi
docker buildx imagetools inspect "$hub_repository:$hub_version"
