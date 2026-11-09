#!/usr/bin/env bash
# 復元テスト: 本番DBを週次バックアップと同じ手順でダンプ→暗号化→復号→使い捨てのローカルPostgresに復元し、全テーブルの行数を比べる。
# 実行: ops/restore-test.sh   （1Password の mooore-sign/BACKUP_DB_URL を読む。本番には書き込まない）
set -euo pipefail
B=/opt/homebrew/opt/postgresql@17/bin; T=$(mktemp -d); PASS=$(openssl rand -hex 16); export PASS
URL=$(op read 'op://Dev/mooore-sign/BACKUP_DB_URL')
trap '"$B/pg_ctl" -D "$T/pg" stop -m immediate >/dev/null 2>&1 || true; rm -rf "$T"' EXIT
"$B/pg_dump" "$URL" --schema=public --no-owner --no-acl --format=custom | openssl enc -aes-256-cbc -pbkdf2 -pass env:PASS -out "$T/db.enc"
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:PASS -in "$T/db.enc" -out "$T/db.dump"
"$B/initdb" -D "$T/pg" -A trust -U postgres >/dev/null && "$B/pg_ctl" -D "$T/pg" -o "-p 55432 -k $T" -l "$T/log" -w start >/dev/null
L="$B/psql -h $T -p 55432 -U postgres -qAt"
$L -c 'create database restoretest' postgres
# 復元先の下ごしらえ（本番復旧でも同じ）: ① 空の public を作り直す ② 本番で public に入っている拡張機能（pg_trgm など）を先に作る。
#   public だけのダンプには拡張機能そのものが入らず、これを省くと gin_trgm_ops の索引で復元が失敗する
$L -d restoretest -c 'drop schema public cascade; create schema public'
for e in $("$B/psql" "$URL" -qAt -c "select e.extname from pg_extension e join pg_namespace n on n.oid=e.extnamespace where n.nspname='public'"); do
  $L -d restoretest -c "create extension if not exists \"$e\" schema public"
done
"$B/pg_restore" --no-owner --no-acl --exit-on-error -h "$T" -p 55432 -U postgres -d restoretest "$T/db.dump"
count() { # $1=接続先 $2..=psql 引数。public の全テーブルの「名前 行数」を並べる
  local tabs; tabs=$("${@:2}" -c "select tablename from pg_tables where schemaname='public' order by 1")
  for t in $tabs; do echo "$t $("${@:2}" -c "select count(*) from public.\"$t\"")"; done
}
count src "$B/psql" "$URL" -qAt > "$T/src.txt"
count dst $L -d restoretest > "$T/dst.txt"
if diff -q "$T/src.txt" "$T/dst.txt" >/dev/null; then echo "OK: $(wc -l < "$T/src.txt" | tr -d ' ') テーブルの行数が一致。ユーザー数: $(grep -E '^User ' "$T/dst.txt" || true)"; else echo "NG: 行数が違う"; diff "$T/src.txt" "$T/dst.txt"; exit 1; fi
