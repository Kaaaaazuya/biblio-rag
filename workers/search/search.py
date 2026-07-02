"""T5 検索の最小確認: クエリ → 埋め込み → pgvector 検索 → 出典つき表示。

CLI: uv run python -m workers.search "検索したい文章" [--top-k 5]
"""

from __future__ import annotations

import argparse
import sys

from workers import config
from workers.embed import OllamaEmbedder, PgVectorStore
from workers.embed.pipeline import active_embed_model


def _crumbs(rec: dict) -> str:
    return " > ".join(c for c in (rec.get("chapter"), rec.get("section")) if c)


def _body(rec: dict) -> str:
    """本文頭に付与された見出し prefix を、それが prefix のときだけ取り除く。"""
    text = rec["text"]
    crumbs = _crumbs(rec)
    first, sep, rest = text.partition("\n")
    return rest if (crumbs and sep and first == crumbs) else text


def _snippet(text: str, length: int = 70) -> str:
    text = text.strip()
    return text[:length] + ("…" if len(text) > length else "")


def format_result(rank: int, rec: dict) -> str:
    """1 件の検索結果を出典つきの文字列に整形する。"""
    page = f" p.{rec['page']}" if rec.get("page") is not None else ""
    source = " / ".join(s for s in (rec.get("title"), _crumbs(rec)) if s)
    score = rec.get("score")
    score_str = f"{score:.3f}" if isinstance(score, int | float) else "-"
    return f"[{rank}] score={score_str}  {source}{page}\n    {_snippet(_body(rec))}"


def _cli(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="T5 検索: クエリに近いチャンクを出典つきで表示")
    parser.add_argument("query", nargs="+", help="検索クエリ")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)
    query = " ".join(args.query)

    embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
    store = PgVectorStore(config.database_url())
    try:
        query_vec = embedder.embed([query])[0]
        results = store.search(query_vec, args.top_k, embed_model=active_embed_model())
    finally:
        store.close()

    if not results:
        print("該当するチャンクがありません（先に③で格納したか確認してください）", file=sys.stderr)
        return 1

    print(f'クエリ: "{query}"  (上位 {len(results)} 件)\n')
    for i, rec in enumerate(results, 1):
        print(format_result(i, rec))
    return 0
