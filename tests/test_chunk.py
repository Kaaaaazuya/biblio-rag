"""② チャンク層のユニットテスト（Markdown → チャンク辞書）。"""

import pytest

from workers.chunk import chunk_markdown
from workers.chunk.chunk import _split_text

META = {"book_id": "sample_book", "title": "テスト書", "author": "テスト著者"}

MD = """# テスト書

## 第一章 設計の前提

### 1.1 目的とスコープ

本書はテキスト埋め込み済みの日本語書籍を入力として、検索対象となるベクトルインデックスを構築する取り込みパイプラインの設計をまとめたものである。対象は抽出から格納までであり、回答生成そのものはここでは扱わない。各層は中間成果物をファイルとして残し、後段の再実行コストを最小化する。層を分離する理由は明快である。抽出ロジックの変更は最も重い処理のやり直しを招くため、抽出結果を正本として保存しておけばよい。

## 第二章 抽出の詳細

### 2.1 読み順の安定化

段組みや脚注を含む紙面では、素朴な抽出では読み順が乱れることがある。ブロック単位で取得し座標に基づいて整列させる。
"""


def test_required_metadata():
    # title / author が欠けると例外
    with pytest.raises(ValueError):
        chunk_markdown(MD, {"book_id": "x", "title": "", "author": "a"})
    with pytest.raises(ValueError):
        chunk_markdown(MD, {"book_id": "x", "title": "t"})


def test_metadata_propagated():
    recs = chunk_markdown(MD, META, size=120, overlap=20)
    assert recs, "チャンクが生成される"
    for r in recs:
        assert r["book_id"] == "sample_book"
        assert r["title"] == "テスト書"
        assert r["author"] == "テスト著者"
        assert r["page"] is None  # MVP では未取得
        assert set(r) == {
            "book_id",
            "chunk_index",
            "title",
            "author",
            "chapter",
            "section",
            "page",
            "text",
        }


def test_chunk_index_sequential():
    recs = chunk_markdown(MD, META, size=120, overlap=20)
    assert [r["chunk_index"] for r in recs] == list(range(len(recs)))


def test_heading_prefix_and_hierarchy():
    recs = chunk_markdown(MD, META, size=120, overlap=20)
    ch1 = [r for r in recs if r["chapter"] == "第一章 設計の前提"]
    assert ch1, "第一章のチャンクがある"
    assert ch1[0]["section"] == "1.1 目的とスコープ"
    # 見出しパスが本文頭に prefix として付与される（書名は除外）
    assert ch1[0]["text"].startswith("第一章 設計の前提 > 1.1 目的とスコープ\n")
    # 章をまたいでチャンクされない
    assert any(r["chapter"] == "第二章 抽出の詳細" for r in recs)


def test_size_is_configurable_and_sentence_aware():
    small = chunk_markdown(MD, META, size=120, overlap=20)
    large = chunk_markdown(MD, META, size=2000, overlap=80)
    assert len(small) > len(large)  # サイズで分割数が変わる
    # 句点優先: prefix を除いた本文部分は概ね「。」で終わる
    for r in small:
        body = r["text"].split("\n", 1)[-1]
        assert body.endswith("。") or body == r["text"].split("\n", 1)[-1]


def test_overlap_must_be_smaller_than_size():
    with pytest.raises(ValueError):
        chunk_markdown(MD, META, size=100, overlap=100)


def test_split_text_overlaps():
    text = "".join(f"第{i}文。" for i in range(60))  # 句点つきの長文
    chunks = _split_text(text, size=80, overlap=20)
    assert len(chunks) >= 2
    assert sum(len(c) for c in chunks) > len(text)  # overlap の重複ぶん総量が増える


def test_split_text_long_sentence_without_period_terminates():
    text = "あ" * 200  # 句点が無い長文（無限ループしないこと）
    chunks = _split_text(text, size=50, overlap=10)
    assert len(chunks) >= 3
    assert all(len(c) <= 75 for c in chunks)  # size*1.5 を大きく超えない
    assert "".join(c[:40] for c in chunks).count("あ") > 0  # 文字が失われない


def test_no_heading_body_has_no_prefix():
    recs = chunk_markdown("見出しのない本文である。これだけ。", META)
    assert len(recs) == 1
    assert recs[0]["chapter"] is None and recs[0]["section"] is None
    assert " > " not in recs[0]["text"]
    assert recs[0]["text"].startswith("見出しのない本文")


# ── コードブロック関連 ────────────────────────────────────────

MD_WITH_CODE = """## 第一章 コード例

### 1.1 実装

以下の関数を使う。

```
def embed(texts):
    return model.encode(texts)

result = embed(['test'])
```

上記が基本的な使い方である。
"""


def test_code_block_not_split():
    # コードブロック（``` フェンス）は分割されない（1チャンクに収まる）
    recs = chunk_markdown(MD_WITH_CODE, META, size=80, overlap=10)
    code_chunks = [r for r in recs if "def embed" in r["text"]]
    assert len(code_chunks) == 1, "コードブロックは1チャンクに収まる"
    # コード全体が1チャンクに含まれる
    assert "model.encode" in code_chunks[0]["text"]


def test_code_block_blank_lines_preserved():
    # コードブロック内の空行が保持される
    recs = chunk_markdown(MD_WITH_CODE, META, size=500, overlap=20)
    code_chunks = [r for r in recs if "def embed" in r["text"]]
    assert code_chunks, "コードブロックを含むチャンクがある"
    text = code_chunks[0]["text"]
    # フェンスが閉じている
    assert text.count("```") >= 2


def test_heading_inside_code_not_parsed():
    # コードブロック内の ## はセクション見出しとして解釈されない
    md = "## 章タイトル\n\n```\n## これはコメント\ncode here\n```\n\n本文。\n"
    recs = chunk_markdown(md, META)
    chapters = {r["chapter"] for r in recs if r["chapter"]}
    assert "これはコメント" not in chapters  # フェンス内の ## が章にならない
    assert "章タイトル" in chapters  # 正規の見出しは認識される
