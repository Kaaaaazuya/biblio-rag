# コミットメッセージ規約

このリポジトリのコミットメッセージは **[Conventional Commits 1.0.0](https://www.conventionalcommits.org/ja/v1.0.0/)** に準拠する。

採用理由:
- コミットメッセージ規約の事実上の標準で、国内の解説記事・採用事例が豊富。
- `commitlint` 等で機械チェック可能。将来の CHANGELOG 自動生成・PR 運用にもそのまま乗る。

> 実作業は `/commit` スキル（[.claude/skills/commit/SKILL.md](../.claude/skills/commit/SKILL.md)）が本ドキュメントに沿って支援する。本ファイルが規約の正本。

## フォーマット

```
<type>(<scope>): <subject>

<body>            # 任意。なぜ/何を。subject の後に1行空けて書く

<footer>          # 任意。Refs / BREAKING CHANGE / Co-Authored-By
```

例:
```
feat(extract): PyMuPDF でブロック単位の本文抽出を実装

最頻フォントサイズを本文、それより大きいものを見出しとして相対判定し、
# / ## に変換する。読み順は get_text("blocks") で安定化。

Refs: T2
Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

## type（必須・1つだけ）

| type | 用途 |
|---|---|
| `feat` | 機能追加 |
| `fix` | バグ修正 |
| `docs` | ドキュメントのみの変更 |
| `style` | 動作に影響しない整形（空白・フォーマット） |
| `refactor` | 挙動を変えないコード改善 |
| `perf` | パフォーマンス改善 |
| `test` | テストの追加・修正 |
| `build` | ビルド・依存関係（uv / pyproject 等） |
| `ci` | CI 設定 |
| `chore` | その他雑務（リポジトリ初期化・設定ファイル等） |
| `revert` | コミットの取り消し |

複数 type にまたがる場合は主たる変更の type を1つ選ぶ。分けられるならコミットを分割する。

## scope（任意・このプロジェクトの層）

`extract` / `chunk` / `embed` / `db` / `infra` / `docker` / `repo` / `docs` / `deps`
該当が無ければ省略してよい。

## subject（必須）

- 日本語でよい。命令形・簡潔に（「〜を実装」「〜を修正」）。
- 約50字以内。末尾に句点「。」を付けない。

## body（任意）

- subject の後に1行空けて書く。**なぜ / 何を**を説明（how より why）。1行は概ね72字で折り返す。

## footer（任意だが規約あり）

- **タスク参照**: キックオフの T 番号があれば `Refs: T0` のように残す。
- **破壊的変更**: `BREAKING CHANGE: <説明>`（または type の後ろに `!`、例 `feat!:`）。
- **Co-Authored-By（必須）**: Claude が作るコミットの末尾に必ず付ける。
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

## 禁止事項

- 規約を無視した自由形式メッセージ。
- `--no-verify` での pre-commit フック迂回。
- 1コミットに無関係な変更を混ぜること。
