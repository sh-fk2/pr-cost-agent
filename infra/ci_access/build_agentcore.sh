#!/usr/bin/env bash
# AgentCore Runtime に直接デプロイする zip の中身を .build/agentcore に作る（docs/spec.md §17.1）。
# Runtime は Linux arm64 だけなので、依存は aarch64 の wheel を取る（Docker は使わない）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OUT="$ROOT/.build/agentcore"
PYTHON_VERSION="3.12"
PLATFORM="aarch64-manylinux2014"
# ears-trace は git の純 Python のパッケージ。wheel が無いので、別に依存なしで入れる
EARS_TRACE="ears-trace @ git+https://github.com/sh-fk2/ears-trace@56b4c3f8c2c517dd994308a9c762b5198187f42b"

cd "$ROOT"
# 要件と閾値は zip に同梱するので、レビューを通った main の版だけから作る（指摘 M-3）
git fetch --quiet origin main
if ! git diff --quiet HEAD || ! git merge-base --is-ancestor HEAD origin/main; then
  echo "build from a clean commit that is already on origin/main" >&2
  exit 1
fi

rm -rf "$OUT" && mkdir -p "$OUT"
# uv.lock のハッシュで、取ってきた wheel を確かめる（指摘 M-4）
uv export --frozen --no-dev --group agent --no-emit-project \
  --no-emit-package ears-trace -o "$OUT.requirements.txt" >/dev/null
uv pip install --quiet --python-platform "$PLATFORM" --python-version "$PYTHON_VERSION" \
  --only-binary=:all: --require-hashes --target "$OUT" -r "$OUT.requirements.txt"
uv pip install --quiet --python-platform "$PLATFORM" --python-version "$PYTHON_VERSION" \
  --no-deps --target "$OUT" "$EARS_TRACE"

cp -R ledger pr_agent "$OUT/"
mkdir -p "$OUT/docs/requirements"
cp docs/requirements/weather-daily.ears.md docs/requirements/weather-daily.limits.json \
  "$OUT/docs/requirements/"
cat > "$OUT/main.py" <<'PY'
"""AgentCore Runtime の入口（zip の根）。中身は pr_agent.runtime。"""

from pr_agent.runtime import app

if __name__ == "__main__":
    app.run()
PY
git rev-parse HEAD > "$OUT/BUILD_COMMIT"
find "$OUT" -name __pycache__ -prune -exec rm -rf {} +
# 手元の OS の作業ファイルを入れない（指摘 L-2）
find "$OUT" \( -name .DS_Store -o -name .lock \) -delete
du -sh "$OUT"
