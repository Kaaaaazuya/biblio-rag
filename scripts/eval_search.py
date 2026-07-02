"""検索精度の簡易評価ハーネス（チューニング用）。

クエリ集 JSON を読み、各クエリで検索して hit@k と MRR を出す。
relevance は「期待キーワードのいずれかが取得チャンク本文に含まれるか」の軽量プロキシ
（手動の relevance ラベル不要。設定間の相対比較に使う）。

クエリ集の形式（JSON 配列）:
    [
      {"q": "命名規則のつけ方", "any": ["命名", "名前"]},
      {"q": "テストを安定させるには", "any": ["テスト"], "book_id": "..."},
      {"q": "...", "any": [...], "note": "任意のメモ"}
    ]
- any    : いずれか1語でも本文に含めば relevant とみなす。
- book_id: 指定時はその書籍のチャンクだけを対象に評価（任意）。
- note   : 人間用の補足メモ（評価には使わない）。

fixture クエリ（著作権フリー素材ベース）:
    uv run python scripts/eval_search.py tests/fixtures/eval_queries.json

書籍固有クエリ（gitignore 済み）:
    uv run python scripts/eval_search.py books/eval_queries.json --top-k 5

RAG 改善フラグの比較（--compare）:
    uv run python scripts/eval_search.py tests/fixtures/eval_queries.json --compare
    → baseline / RERANK / HyDE / RERANK+HyDE の4条件を自動で順次評価して並べる。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from workers import config  # noqa: E402
from workers.embed.ollama_embedder import OllamaEmbedder  # noqa: E402
from workers.embed.pgvector_store import PgVectorStore  # noqa: E402
from workers.embed.pipeline import active_embed_model  # noqa: E402


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
    if config.RERANK_ENABLED:
        from workers.rerank import SentenceReranker

        reranker = SentenceReranker(config.RERANK_MODEL)
    else:
        reranker = None

    rows = []
    for item in queries:
        vec = embedder.embed([item["q"]])[0]

        if config.HYDE_ENABLED:
            import httpx

            try:
                resp = httpx.post(
                    f"{config.OLLAMA_HOST}/api/chat",
                    json={
                        "model": config.CHAT_MODEL,
                        "messages": [
                            {
                                "role": "user",
                                "content": f"次の質問に対して簡潔に答えてください: {item['q']}",
                            }
                        ],
                        "stream": False,
                    },
                    timeout=30.0,
                )
                if resp.is_success:
                    hypo = resp.json().get("message", {}).get("content") or ""
                    if hypo:
                        vec = embedder.embed([hypo])[0]
                else:
                    print(
                        f"警告: HyDE生成に失敗しました (HTTP {resp.status_code})",
                        file=sys.stderr,
                    )
            except (httpx.HTTPError, ValueError) as e:
                print(f"警告: HyDE生成中にエラーが発生しました: {e}", file=sys.stderr)

        candidate_k = max(config.RERANK_CANDIDATE_K, top_k) if config.RERANK_ENABLED else top_k
        # book_id 指定時は取りこぼし防止に多めに取る
        fetch_k = candidate_k * 3 if item.get("book_id") else candidate_k
        hits = store.search(vec, fetch_k, embed_model=active_embed_model())

        if reranker and hits:
            hits = reranker.rerank(item["q"], hits, top_k)

        rank = _first_relevant_rank(hits, item)
        hit = rank is not None and rank <= top_k
        rows.append({"q": item["q"], "rank": rank, "hit": hit})

    n = len(rows)
    hit_at_k = sum(r["hit"] for r in rows) / n if n else 0.0
    mrr = sum(1.0 / r["rank"] for r in rows if r["rank"] and r["rank"] <= top_k) / n if n else 0.0
    return rows, {"n": n, "hit@k": hit_at_k, "mrr": mrr, "top_k": top_k}


def _run_condition(label: str, flags: dict, queries: list[dict], top_k: int) -> dict:
    """環境変数を一時的に上書きして1条件を評価する。"""
    saved = {k: os.environ.get(k) for k in flags}
    try:
        for k, v in flags.items():
            os.environ[k] = v
        import importlib

        importlib.reload(config)

        embedder = OllamaEmbedder(config.OLLAMA_HOST, config.EMBED_MODEL, config.EMBED_DIM)
        store = PgVectorStore(config.database_url())
        try:
            rows, summary = evaluate(queries, embedder, store, top_k)
        finally:
            store.close()
        return {"label": label, "rows": rows, "summary": summary}
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import importlib

        importlib.reload(config)


_COMPARE_CONDITIONS = [
    ("baseline", {"RERANK_ENABLED": "false", "HYDE_ENABLED": "false"}),
    ("RERANK", {"RERANK_ENABLED": "true", "HYDE_ENABLED": "false"}),
    ("HyDE", {"RERANK_ENABLED": "false", "HYDE_ENABLED": "true"}),
    ("RERANK+HyDE", {"RERANK_ENABLED": "true", "HYDE_ENABLED": "true"}),
]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="検索精度の簡易評価（hit@k / MRR）")
    parser.add_argument("queries", help="クエリ集 JSON のパス")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--compare",
        action="store_true",
        help="baseline / RERANK / HyDE / RERANK+HyDE の4条件を比較する",
    )
    args = parser.parse_args(argv)

    path = Path(args.queries)
    if not path.exists():
        print(f"クエリ集が見つかりません: {path}", file=sys.stderr)
        return 1
    queries = json.loads(path.read_text(encoding="utf-8"))

    if args.compare:
        results = []
        for label, flags in _COMPARE_CONDITIONS:
            print(f"\n--- {label} ---")
            r = _run_condition(label, flags, queries, args.top_k)
            for row in r["rows"]:
                mark = "○" if row["hit"] else "×"
                rank = row["rank"] if row["rank"] else "-"
                print(f"  {mark} rank={rank:<3} {row['q']}")
            s = r["summary"]
            print(f"  hit@{s['top_k']}={s['hit@k']:.2f}  MRR={s['mrr']:.3f}  (n={s['n']})")
            results.append(r)

        print("\n=== 比較サマリ ===")
        print(f"{'条件':<14} {'hit@k':>6} {'MRR':>7}")
        print("-" * 30)
        for r in results:
            s = r["summary"]
            print(f"{r['label']:<14} {s['hit@k']:>6.2f} {s['mrr']:>7.3f}")
        return 0

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
