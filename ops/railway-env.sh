#!/usr/bin/env bash
# 使い方（リポジトリのルートで、1Password から値を渡して実行）:
#   op run --env-file=.env.mooore.tpl -- ops/railway-env.sh create   # 初回: サービスを作りながら変数を入れる
#   op run --env-file=.env.mooore.tpl -- ops/railway-env.sh set      # 変数だけ更新（再デプロイあり）
# 事前に `railway link` でプロジェクトにつないでおく。値は画面に出ない（op run がマスクする）。
set -euo pipefail
args=()
for k in $(grep -oE '^[A-Z0-9_]+' .env.mooore.tpl); do args+=("$k=${!k}"); done
case "${1:-}" in
  create)
    v=(); for a in "${args[@]}"; do v+=(--variables "$a"); done
    railway add --service documenso --image documenso/documenso:v2.21.1 "${v[@]}" ;;
  set)
    s=(); for a in "${args[@]}"; do s+=(--set "$a"); done
    railway variables --service documenso "${s[@]}" ;;
  *) echo "usage: $0 create|set" >&2; exit 1 ;;
esac
