---
name: allowlist
description: 作業の最後に、その作業で実行した Bash コマンドのうち副作用のない安全なものを抽出し、人間の確認を経てプロジェクトの .claude/settings.json の permissions.allow に追記する。「許可一覧を更新」「allowlist を整理」や、まとまった作業の締めくくりに使う。
---

# allowlist — 許可コマンドの抽出と更新（人間チェック必須）

毎回の許可プロンプトを減らすため、作業で使った **副作用のない（読み取り専用の）コマンド**を
許可一覧に追記する。**自動では適用しない。必ず人間の確認を挟む。**

## 手順

1. **抽出**: 今回の作業で実行した Bash コマンドを振り返り、`.claude/settings.json` と
   `.claude/settings.local.json` の `permissions.allow` に**まだ無い**ものを洗い出す。
2. **選別**: 下の「追加してよい / 追加しない」基準でふるいにかける。
3. **提示**: 追加候補をパターン化（例 `Bash(jq*)`）して、根拠つきで一覧提示する。
   判断が要るものは「要確認」として分けて出す。
4. **承認待ち**: ユーザーの確認・承認を得る（ここで止まる。勝手に書かない）。
5. **追記**: 承認されたものだけ `.claude/settings.json` の `allow` に**マージ**（重複排除）。
   配列は置き換えず既存を保持。`jq -e '.permissions.allow|length' .claude/settings.json` で検証。
6. **コミット**: `/commit` で `chore(repo): 許可なし実行コマンドを追加` 等。

## 追加してよい（副作用なし＝読み取り専用）

- 参照系 Bash: `cat` `head` `tail` `ls` `jq` `which` `lsof` `echo` `git status/diff/log/show/ls-files/branch/rev-parse`
- localhost への問い合わせのみ: `curl http://localhost:*`
- このプロジェクトの安全な開発コマンド: `uv *`（方針で許可済み）, `pre-commit run*`, `docker ps/logs/images`

## 追加しない（要確認 or 恒久的に除外）

- **ファイルを書く/消す/移す**: `rm` `mv` `cp` `tee`、`>`/`>>` リダイレクト、`printf > file`
- **インストール/環境変更**: `brew install` `uv tool install`（※`uv` 全般は方針で許可済みだが新規ツール導入は留意）
- **ネットワーク送信（localhost 以外）**: 外部への `curl`/`wget`、`git push/pull/fetch/remote`（→ `ask` に置く）
- **状態を壊しうる git**: `git reset` `git restore` `git checkout --` `git clean`
- **権限/システム**: `sudo` `chmod` `chown` `kill`
- `docker compose up/down/exec` 等は副作用ありだが、本プロジェクトでは方針で許可済み（新規に広げる時は要確認）。

## 判断に迷ったら

「実行して**何も変化が残らない**（ファイル・プロセス・リモート・パッケージが不変）」なら追加候補。
少しでも書き込み・送信・インストールが絡むなら `ask` に回すか、毎回確認のままにする。

> 補足: 過去のトランスクリプト全体から候補を一括抽出したい場合は、組み込みの
> `/fewer-permission-prompts` スキルも使える。本スキルは「直近作業ぶんを副作用基準で厳選」する運用。
