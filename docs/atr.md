# ATR (Agent Threat Rules) によるローカル脅威スキャン

[ATR](https://github.com/Agent-Threat-Rule/agent-threat-rules) は AI エージェント向けのオープンな検出ルール集
（MIT。"Sigma is for SIEM, YARA is for malware, **ATR is for AI agents**"）。
`SKILL.md`・MCP 設定・LLM 入出力を対象に、プロンプトインジェクション／skill 改ざん／
コンテキスト持ち出し／ツールポイズニング等を YAML ルールで検出する。

このリポジトリには `.claude/skills/**/SKILL.md` があるため、**自分のエージェント成果物を
ローカルで点検する**用途で導入する。

## 方針（このプロジェクトでの使い方）

- **npm 非依存**: 公式 **Docker イメージ**で実行する（ATR の主流は `npx`/`npm` だが、本プロジェクトは npm を使わない方針）。
- **報告は最小限（外部送信ゼロ）**: `--no-report`（既定で有効な匿名 Threat Cloud 報告を無効化）に加え、
  `--network none` でコンテナをネットワーク遮断 → 外部送信が原理的に起きない。
- **ローカルのみ**: CI や Claude Code の常時 hook（`atr guard`/`atr init`）は今は入れない。手動スキャンに限定。
- **供給チェーン安全**: イメージは digest 固定（[scripts/atr-scan.sh](../scripts/atr-scan.sh) の `IMAGE`）。

## 実行

```bash
# .claude/skills を severity=medium でスキャン（既定）
./scripts/atr-scan.sh

# 重大度の閾値を変える / 対象を変える / 追加フラグ
ATR_SEVERITY=high ./scripts/atr-scan.sh
ATR_TARGET=.mcp.json ./scripts/atr-scan.sh      # .json は MCP 設定として判定
./scripts/atr-scan.sh --json                     # 追加フラグはそのまま atr へ
```

初回は `docker pull ghcr.io/agent-threat-rule/agent-threat-rules:latest` で取得される
（以後 digest 固定で再利用）。Apple Silicon では amd64 を Rosetta/QEMU 実行（警告は無害）。

## 結果の読み方（重要: 誤検知あり）

ATR は**セキュリティ方針を“説明”しているドキュメントで誤検知（false positive）しやすい**。
検出は必ず人間がレビューすること。

- **既知の誤検知**: `.claude/skills/allowlist/SKILL.md` が `ATR-2026-00576`
  （"Credential Harvester … key theft + exfil"）に CRITICAL でマッチする。
  これは当スキルが「秘密情報・キー・`git push` を許可しない」という方針を**記述**しているため反応するもので、
  実際に資格情報を盗む内容ではない（**良性**）。

## 更新

ルールはイメージに同梱。新しいルールを取り込みたいときは最新を pull して digest を貼り替える:

```bash
docker pull ghcr.io/agent-threat-rule/agent-threat-rules:latest   # 表示される digest を
# scripts/atr-scan.sh の IMAGE=...@sha256:... に反映
```

## 今後の拡張（任意）

- 重大度 `high`/`critical` のみを対象にした軽量チェックを pre-commit に追加（false positive を抑えた上で）。
- CI（GitHub Actions の `Agent-Threat-Rule/agent-threat-rules@v3` + SARIF を Security タブへ）。
- `atr guard` で Claude Code の実行時 hook として常時監視。
