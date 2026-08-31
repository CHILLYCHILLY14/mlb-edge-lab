#!/usr/bin/env bash
# Scheduled-workflow regression test. It replaces Python with a tiny fake
# command, so this checks argument handling and retry behaviour without calling
# any live MLB/ESPN service.
set -Eeuo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT
mkdir -p "${tmp_dir}/bin"

cat > "${tmp_dir}/bin/python" <<'PY'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${RUNNER_TEST_LOG}"
count="$(wc -l < "${RUNNER_TEST_LOG}")"
[[ "${count}" -ge 2 ]]
PY
chmod +x "${tmp_dir}/bin/python"

export PATH="${tmp_dir}/bin:${PATH}"
export RUNNER_TEST_LOG="${tmp_dir}/calls.log"

BUILD_DATE="2026-08-23" \
BUILD_DAYS="3" \
MLB_BUILD_ATTEMPTS="2" \
MLB_BUILD_RETRY_SECONDS="0" \
  bash "${repo_root}/scripts/run_build.sh"

mapfile -t calls < "${RUNNER_TEST_LOG}"
expected="-u -m pipeline.build --date 2026-08-23 --days 3"
[[ "${#calls[@]}" -eq 2 ]]
[[ "${calls[0]}" == "${expected}" ]]
[[ "${calls[1]}" == "${expected}" ]]

if BUILD_DATE="not-a-date" MLB_BUILD_RETRY_SECONDS="0" \
    bash "${repo_root}/scripts/run_build.sh" >/dev/null 2>&1; then
  echo "invalid BUILD_DATE was accepted" >&2
  exit 1
fi

if grep -q "name: Grade yesterday" "${repo_root}/.github/workflows/build.yml"; then
  echo "workflow still contains the latest.json-overwriting build" >&2
  exit 1
fi

echo "scheduled build runner: PASS"
