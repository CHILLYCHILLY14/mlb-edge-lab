#!/usr/bin/env bash
# One safe entry point for scheduled, push and manually-dispatched builds.
set -Eeuo pipefail

args=()

if [[ -n "${BUILD_DATE:-}" ]]; then
  if [[ ! "${BUILD_DATE}" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "::error::BUILD_DATE must use YYYY-MM-DD (received: ${BUILD_DATE})"
    exit 2
  fi
  args+=(--date "${BUILD_DATE}")
fi

if [[ -n "${BUILD_DAYS:-}" ]]; then
  if [[ ! "${BUILD_DAYS}" =~ ^[1-9][0-9]*$ ]]; then
    echo "::error::BUILD_DAYS must be a positive whole number (received: ${BUILD_DAYS})"
    exit 2
  fi
  args+=(--days "${BUILD_DAYS}")
fi

attempts="${MLB_BUILD_ATTEMPTS:-2}"
retry_seconds="${MLB_BUILD_RETRY_SECONDS:-15}"
if [[ ! "${attempts}" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::MLB_BUILD_ATTEMPTS must be a positive whole number"
  exit 2
fi
if [[ ! "${retry_seconds}" =~ ^[0-9]+$ ]]; then
  echo "::error::MLB_BUILD_RETRY_SECONDS must be zero or a positive whole number"
  exit 2
fi

last_status=1
for ((attempt = 1; attempt <= attempts; attempt++)); do
  echo "::group::MLB slate build (attempt ${attempt}/${attempts})"
  if python -u -m pipeline.build "${args[@]}"; then
    echo "::endgroup::"
    exit 0
  else
    last_status=$?
  fi
  echo "::endgroup::"

  if (( attempt < attempts )); then
    echo "::warning::Slate build exited ${last_status}; retrying in ${retry_seconds} seconds"
    sleep "${retry_seconds}"
  fi
done

echo "::error::Slate build failed after ${attempts} attempts (last exit: ${last_status})"
exit "${last_status}"
