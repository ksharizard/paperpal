#!/usr/bin/env bash
# Resume the all-category archive scrape. Completed URLs are skipped from the
# manifest, and content duplicates are skipped by SHA-1.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

WORKERS="${PAPERPAL_WORKERS:-6}"
LOG_FILE="${PAPERPAL_SCRAPE_LOG:-papers/scrape-run.log}"

mkdir -p "$(dirname -- "$LOG_FILE")"
echo "[$(date -Is)] Resuming all-category scrape with $WORKERS workers" | tee -a "$LOG_FILE"
UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/paperpal-uv-cache}" \
  uv run paperpal scrape --workers "$WORKERS" "$@" 2>&1 | tee -a "$LOG_FILE"
