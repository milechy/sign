# ムーア 署名サイト 運用手順書（sign.mooores.com）

自社専用の電子署名サイト。Documenso（OSS, AGPLv3）を fork して使う。お客様に売るものではない。
構成: Documenso ＋ Supabase（Postgres・Storage）＋ Resend（メール）＋ Railway（ホスト）。

## 0. 最初の確認結果（2026-11-09 時点）

| 項目 | 結果 |
|---|---|
| 現行バージョン | v2.21.1（2025-10-22） |
| フレームワーク | React Router 7 ＋ Hono の常駐 Node サーバー（`node build/server/main.js`） |
| 公式のセルフホスト先 | Docker / Docker Compose / Railway / Kubernetes。Vercel は記載なし |
| ホスト | **Railway に決定**（Vercel は常駐サーバー型のため不採用。`railway.toml` が fork に同梱され、`docker/Dockerfile` でビルドする） |
| 環境変数の正 | リポジトリの `.env.example`。名前は `.env.mooore.tpl` に反映済み |
| 日本語 UI | `packages/lib/translations/ja` あり。足りない画面は §10 で確認して記録する（未確認） |
| ライセンス | AGPLv3。`LICENSE` を消さない。改変は fork に置く。ロゴ差し替えは商標ルール確認後（未確認） |

### 環境変数（`.env.example` と指示書の名前の違い）
- 認証用の秘密鍵 = `NEXTAUTH_SECRET`
- 暗号化キー = `NEXT_PRIVATE_ENCRYPTION_KEY` / `NEXT_PRIVATE_ENCRYPTION_SECONDARY_KEY`（各 32 文字以上のランダム）
- DB = `NEXT_PRIVATE_DATABASE_URL`（プーラー）/ `NEXT_PRIVATE_DIRECT_DATABASE_URL`（直接）
- SMTP = `NEXT_PRIVATE_SMTP_*`（Resend は `smtp-auth`、host `smtp.resend.com`、465、user `resend`、password は API キー）
- ファイル保存 = `NEXT_PUBLIC_UPLOAD_TRANSPORT=s3` ＋ `NEXT_PRIVATE_UPLOAD_*`（Supabase は `FORCE_PATH_STYLE=true`）
- 署名証明書 = `NEXT_PRIVATE_SIGNING_LOCAL_FILE_CONTENTS`（base64）＋ `NEXT_PRIVATE_SIGNING_PASSPHRASE`
- 新規登録の停止 = `NEXT_PUBLIC_DISABLE_SIGNUP=true`
- 値は 1Password の Dev に `mooore-sign` として入れる。一覧は `.env.mooore.tpl`。平文の `.env` は作らない。

## 1. セットアップの順番（★ = あなたが実行する作業）

1. ★ Supabase: 東京リージョンでプロジェクト作成。接続文字列を2つ控える（Transaction pooler = アプリ用、Direct = マイグレーション用）。Storage に**非公開**バケットを作り、S3 接続の鍵を発行。日次バックアップが有効なことを確認。
2. ★ Resend: `mail.mooores.com` を追加し、DNS に SPF・DKIM・DMARC を登録して Verified にする。API キー発行。
3. ★ 署名証明書を作る（§2）。
4. ★ 1Password の Dev に `mooore-sign` を作り、`.env.mooore.tpl` の項目を入れる。
5. ★ Railway: 法人用ワークスペースに project `sign` を作り、このリポジトリ（`milechy/sign`、main）をつなぐ。Variables に §0 の値を入れる。**最初は `NEXT_PUBLIC_DISABLE_SIGNUP` を入れない**。
6. ★ Railway の Custom Domain に `sign.mooores.com` を追加し、表示された CNAME を DNS に登録。
7. 起動時に `prisma migrate deploy` が自動で走る（`docker/start.sh`）。ログで成功を確認。
8. ★ `https://sign.mooores.com/signup` で社員の管理者アカウントを作る。**作ったら直ちに `NEXT_PUBLIC_DISABLE_SIGNUP=true` を入れて再デプロイ**。`/signup` から登録できないことを確認（§9）。
9. ★ GitHub の Settings → Secrets に、`.github/workflows/mooore-backup.yml` 冒頭のコメントにある Secrets を登録。
> 注意: `NEXT_PUBLIC_*` は Docker のビルド時に値が固まる設定の場合がある。Variables を変えても反映されないときは再ビルド（Redeploy）する。

## 2. 署名用の証明書（.p12）
自己署名で始める。ファイルはリポジトリに置かない。
```bash
openssl genrsa -out private.key 2048
openssl req -new -x509 -key private.key -out certificate.crt -days 365 \
  -subj "/C=JP/O=ムーア株式会社/CN=sign.mooores.com"
read -s -p "パスフレーズ: " CERT_PASS; echo
openssl pkcs12 -export -out certificate.p12 -inkey private.key -in certificate.crt -password env:CERT_PASS
rm private.key certificate.crt
base64 -i certificate.p12 | pbcopy   # 1Password の SIGNING_P12_BASE64 に貼る
rm certificate.p12
```
- パスフレーズが無い .p12 は "Failed to get private key bags" で失敗する。
- **期限は365日**。作った日の 11 か月後に、Google カレンダーへ「署名証明書の更新（sign.mooores.com）」を入れる。更新時は同じ手順で作り直して 1Password と Railway を差し替える。過去に署名した PDF は影響を受けない。
- **検討事項（本番前）:** 自己署名だと Acrobat は「署名者の身元が不明」と表示する（署名が壊れていないことは確認できる）。取引先から指摘されうるので、本番前に (a) 公的な認証局の証明書、(b) `NEXT_PRIVATE_SIGNING_TIMESTAMP_AUTHORITY` による認定タイムスタンプ、のどちらかに替えるか決める。

## 3. バックアップと復元
- Supabase の日次自動バックアップ（プラン依存。契約プランで保持日数を確認）。
- 週1回（月曜 03:00 JST）: `.github/workflows/mooore-backup.yml` が、暗号化した `pg_dump` と Storage の中身を Cloudflare R2 に書き出す。手動実行は Actions の "Run workflow"。R2 側に保持期限（例: 90日）のライフサイクルを設定する。
- 復元（検証用の空の DB に対して）:
```bash
rclone copyto R2:<bucket>/db/db-YYYYMMDD.dump.enc ./db.dump.enc
openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in db.dump.enc -out db.dump
pg_restore --no-owner -d "<復元先 DB の接続文字列>" db.dump
rclone copy R2:<bucket>/files <復元先バケット>
```
- ★ **一度実際に復元して試す。結果を PR に書く。** 本番に上書きする前に、必ず別の DB で試す。

## 4. テンプレートと送る組み合わせ
入力元: Claude Docs「FDM・AIセキュリティ診断 契約書ひな形（下書き）」1〜5。**弁護士の確認が終わるまでは本番のお客様に送らない。** テンプレート名の頭に【下書き】を付ける。

| # | テンプレート名 | 入力する人 |
|---|---|---|
| 1 | 【下書き】NDA | お客様: 会社名・住所・代表者名・日付 |
| 2 | 【下書き】業務委託基本契約 | 同上 |
| 3a | 【下書き】個別契約（お試し） | ムーア（送信前）: 金額・期間・対象業務・測る数字 ／ お客様: 会社名等 |
| 3b | 【下書き】個別契約（本契約） | 同上 |
| 3c | 【下書き】個別契約（セキュリティ診断） | 同上 |
| 4 | 【下書き】診断作業の許可書 | 診断のときだけ |
| 5 | 【下書き】ムーア環境運用の同意書 | ムーア環境で運用するときだけ |

署名の順番は**お客様が先、ムーアが後**。完了後は両者に完了メールと署名済み PDF が届く。

| 場面 | 送る組み合わせ |
|---|---|
| 初回（お試し） | NDA → 基本契約 → 個別契約（お試し） |
| お試しから本契約 | 個別契約（本契約） |
| セキュリティ診断 | NDA → 基本契約 → 個別契約（セキュリティ診断）→ 診断作業の許可書 |
| ムーア環境で運用 | 上記に加えて 運用の同意書 |

**書類のタイトルの付け方（Slack 通知・Drive 保存・検索が、これに頼る）:** `【種別】取引先_金額`  例: `【個別・お試し】株式会社A_330000`。金額が無い書面は `_0`。

### 弁護士確認後のテンプレート差し替え手順
1. Claude Docs の最新版から PDF を作り直す（A4、明朝または游ゴシック、ページ番号付き）。
2. Documenso で該当テンプレートを複製し、新しい PDF に入れ替え、入力欄を同じ位置に付け直す。
3. 自分あてに送って署名まで通す（§9 の確認を再実行）。
4. 名前の【下書き】を外し、旧テンプレートは「旧版_YYYYMMDD」にして残す。送信済みの書類には影響しない。

## 5. 保存（電子帳簿保存法）
- 署名済み PDF と監査ログ（いつ・だれが・どの IP から）は、Documenso の書類詳細からダウンロードできる。7年保存する。
- 検索要件（取引年月日・取引先・金額）は、書類タイトルの規則（§4）＋ Google ドライブのファイル名 `取引年月日_取引先_金額.pdf` で満たす。
- タイムスタンプを付けない場合は事務処理規程で要件を満たす。ひな形: `docs/denchoho-kitei.md`（税理士の確認が必要）。
- **Google ドライブへの自動保存:** 最初は手で保存する。署名完了の Slack 通知を見たら、署名済み PDF と監査ログをダウンロードし、所定のフォルダに `取引年月日_取引先_金額.pdf` で保存する。自動化（Webhook → 関数 → Drive）は件数が増えてから。

## 6. 通知と請求（最初は手作業）
1. Documenso → Settings → Webhooks に、`ops/slack-notify-worker.js` を置いた関数の URL を登録。イベントは `DOCUMENT_COMPLETED`、Secret を設定。
2. ★ 関数（Cloudflare Worker 想定）に `SLACK_WEBHOOK_URL` と `WEBHOOK_SECRET` を入れる。
3. 署名完了が Slack に流れる（取引先・種別・金額はタイトルから）。
4. 個別契約なら Stripe で請求書を手作業で作る。月の契約が5件を超えたら自動連携を考える。

## 7. 日々の運用
- 送信: Templates → Use → 送信前に金額などを入力 → 宛先を入れて送る。
- 新規登録は止めてある。社員を増やすときだけ、一時的に `NEXT_PUBLIC_DISABLE_SIGNUP` を外すか、管理画面から招待する。
- 鍵を替えたら、旧い鍵の無効化を確認する。

## 8. 月額費用の見込み（要確認。契約前の目安）
Supabase（Pro $25）／ Resend（無料〜$20）／ Railway（Hobby $5 + 従量、実測は数ドル〜）／ R2（ほぼ無料）／ GitHub Actions（無料枠）。合計およそ $35〜70 / 月。実際の請求で見直す。

## 9. PR 前の確認項目（結果は PR に書く。未実施は「未確認」）
- [ ] 自分あてに5つのテンプレートを送り、別のメールアドレスで署名まで通した
- [ ] スマホでも一度通した
- [ ] 署名済み PDF を Acrobat Reader で開き、署名パネルで署名が壊れていない
- [ ] メールが Gmail と Outlook で迷惑メールに入らない
- [ ] バックアップから復元できた
- [ ] 新規登録画面から登録できない
- [ ] Slack 通知が届く（Webhook の Secret ヘッダー名を公式の Webhook Verification ページで確認）

## 10. 日本語 UI で足りない画面
（動かして確認したら、ここに書く。未確認）
