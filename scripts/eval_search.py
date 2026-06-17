"""検索精度の簡易評価ハーネス（チューニング用）。

クエリ集 JSON を読み、各クエリで検索して hit@k と MRR を出す。
relevance は「期待キーワードのいずれかが取得チャンク本文に含まれるか」の軽量プロキシ
（手動の relevance ラベル不要。設定間の相対比較に使う）。

クエリ集の形式（1 行 1 クエリの JSON 配列）:
    [
      {"q": "命名規則のつけ方", "any": ["命名", "名前"]},
      {"q": "テストを安定させるには", "any": ["テスト"], "book_id": "..."}
    ]
- any: いずれか1語でも本文に含めば relevant とみなす。
- book_id: 指定時はその書籍のチャンクだけを対象に評価（任意）。

クエリ集は書籍固有データなので books/ 配下（gitignore 済み）に置く想定。
実行: uv run python scripts/eval_search.py books/eval_queries.json --top-k 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers import config  # noqa: E402
from workers.embed.ollama_embedder import OllamaEmbedder  # noqa: E402
from workers.embed.pgvector_store import PgVectorStore  # noqa: E402


def _relevant(hit: dict, item: dict) -> bool:
    """relevance 判定。chapter 指定があれば章一致（厳しめ）、無ければ本文キーワード一致。"""
    if item.get("book_id") and hit.get("book_id") != item["book_id"]:
        return False
    if "chapter" in item:
        return item["chapter"] in (hit.get("chapter") or "")
    return any(kw in hit.get("text", "") for kw in item.get("any", []))


def _first_relevant_rank(hits: list[dict], item: dict) -> int | None:
    """relevant な最初のヒットの順位（1始まり）。無ければ None。"""
    for rank, hit in enumerate(hits, start=1):
        if _relevant(hit, item):
            return rank
    return None


def evaluate(queries: list[dict], embedder: OllamaEmbedder, store: PgVectorStore, top_k: int):
    """各クエリを評価して (結果リスト, 集計) を返す。"""
    rows = []
    for item in queries:
        vec = embedder.embed([item["q"]])[0]
        # book_id 指定時は取りこぼし防止に多めに取る
        hits = store.search(vec, top_k * 3 if item.get("book_id") else top_k)
        rank = _first_relevant_rank(hits, item)
        hit = rank is not None and rank <= top_k
        rows.append({"q": item["q"], "rank": rank, "hit": hit})
    n = len(rows)
    hit_at_k = sum(r["hit"] for r in rows) / n if n else 0.0
    mrr = sum(1.0 / r["rank"] for r in rows if r["rank"] and r["rank"] <= top_k) / n if n else 0.0
    return rows, {"n": n, "hit@k": hit_at_k, "mrr": mrr, "top_k": top_k}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="検索精度の簡易評価（hit@k / MRR）")
    parser.add_argument("queries", help="クエリ集 JSON のパス")
    parser.add_argument("--top-k", type=int, default=5)
    args = parser.parse_args(argv)

    path = Path(args.queries)
    if not path.exists():
        print(f"クエリ集が見つかりません: {path}", file=sys.stderr)
        return 1
    queries = json.loads(path.read_text(encoding="utf-8"))

    embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
    store = PgVectorStore(config.database_url())
    try:
        rows, summary = evaluate(queries, embedder, store, args.top_k)
    finally:
        store.close()

    for r in rows:
        mark = "○" if r["hit"] else "×"
        rank = r["rank"] if r["rank"] else "-"
        print(f"  {mark} rank={rank:<3} {r['q']}")
    print(
        f"\n[top_k={summary['top_k']}] hit@k={summary['hit@k']:.2f} "
        f"MRR={summary['mrr']:.3f}  (n={summary['n']})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
