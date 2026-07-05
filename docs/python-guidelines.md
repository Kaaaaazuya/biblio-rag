# Python AI-First 開発ガイドライン

本プロジェクトは Claude Code を用いた AI 支援開発を前提とする。
そのため「人間が読みやすく」「AI が理解・修正しやすく」「静的解析で品質を担保できる」
コードを目標とし、**AI が自由に書くのではなく、静的解析・型チェック・テストで品質を保証する。**

> このドキュメントは方針の正本。実際のツール設定は `pyproject.toml` /
> `.pre-commit-config.yaml` / `.github/workflows/ci.yml` に置く。

## 品質ゲート

以下をすべて通過することをマージ条件とする（CI・pre-commit で自動実行）。

```
Ruff (lint + format)  →  BasedPyright (型)  →  pytest (テスト)
```

| ツール | 役割 |
| --- | --- |
| Ruff | Lint / Format / import 整理 |
| BasedPyright | 型解析（pyright 上位互換） |
| pytest | テスト |
| pre-commit | コミット前チェック |
| GitHub Actions | CI |

### 型チェッカーは BasedPyright 1本

- 元方針は BasedPyright + mypy strict の二本立てだったが、**二重運用は診断の重複・矛盾で
  メンテコストが高い**ため BasedPyright に一本化する。mypy は導入しない。
- BasedPyright は PyPI から入る（`uv add --dev basedpyright`）。node 同梱 wheel のため
  **本プロジェクトの「npm を使わない」方針と衝突しない。**

## 段階導入（ratchet）

一括で最厳格にはしない。既存コードへの churn とレビュー負荷を抑えるため段階的に締める。

1. **現在(Phase 1)**: BasedPyright を `standard` モードで本体（`workers` / `webui`）に適用。
   Ruff の select を厳選拡張。テスト・スクリプトは新カテゴリを一部免除。
2. **今後**: `strict` 化、`tests` への型チェック拡大、カバレッジ閾値の CI ゲート化、
   `RUF` / `TC`（TYPE_CHECKING 整理）/ `PL` 等の追加。

> `select = ["ALL"]` は採用しない。Ruff のバージョン更新で新ルールが増えて CI が
> 突然赤になる・競合ルールで `ignore` が肥大化する、という運用上の理由による。
> 必要なカテゴリを明示的に select する。

## 型

- **すべての関数・メソッドに型を書く**（引数・戻り値）。Ruff の `ANN` で検出する。
- `Optional` は `X | None` で明示する（暗黙の `= None` に頼らない）。
- **`Any` は原則禁止。** ただし外部由来で静的に型が定まらない値
  （boto3 クライアント、JSON リクエストボディ等）は**理由コメントを添えて**許容する。
  この方針のため Ruff の `ANN401`（明示 Any 禁止）は無効化している。
- 型エラーは警告ではなく**修正対象**。

## 関数・クラス・例外・ログ

- **単一責任 / 20〜30行目安 / ネスト最大3段（ガード節優先）。**
- クラスは状態を持つものだけに使う。Utility クラスではなくモジュール関数を使う。
- `from module import *` 禁止。import 順は Ruff に任せる。
- `except Exception: pass` で握りつぶさない。ログ・再送出・独自例外への変換を行う。
- `print` デバッグ禁止。`logging` を使い、レベル（DEBUG〜CRITICAL）を使い分ける。

## Docstring

公開 API は必須。Google Style を採用する。

```python
def create_user(name: str) -> User:
    """Create a new user.

    Args:
        name: User name.

    Returns:
        Created user.
    """
```

## テスト

- pytest を使用。目標カバレッジは**コアロジック 100% / 全体 90% 以上**。
- 実装ループは CLAUDE.md の TDD（Red → Green → Review → Commit）に従う。
- 外部サービス（DB・Ollama・S3）はモックし、外部依存なしで全テストが通ること。

## Claude Code 向けルール

**必須**: Ruff エラーを残さない / 型エラーを残さない / BasedPyright(standard) を通す /
pytest を通す / 既存設計を尊重する / 必要最小限の変更に留める。

**推奨**: 型を積極的に追加 / 小さい関数へ分割 / 重複を減らす / 可読性優先 /
マジックナンバーを定数化。

**禁止**: `Any` を安易に使う / `noqa`・`type: ignore` でエラーを隠す / print デバッグ /
不要なコメント・リファクタリング / 無関係なファイルの変更。
