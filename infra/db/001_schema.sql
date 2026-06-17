-- pgvector スキーマ（開発 Docker / 本番 Aurora 共通）
-- postgres 初回起動時に /docker-entrypoint-initdb.d 経由で自動実行される。

CREATE EXTENSION IF NOT EXISTS vector;

-- チャンク本体 + メタデータ + 埋め込みベクトル
-- 次元は開発 bge-m3 / 本番 Titan V2 ともに 1024 で統一。
CREATE TABLE IF NOT EXISTS chunks (
    id           BIGSERIAL PRIMARY KEY,
    book_id      TEXT        NOT NULL,
    chunk_index  INTEGER     NOT NULL,         -- 書籍内のチャンク連番（upsert の冪等キー）
    title        TEXT,
    author       TEXT,
    chapter      TEXT,
    section      TEXT,
    page         INTEGER,
    text         TEXT        NOT NULL,
    embedding    VECTOR(1024) NOT NULL,
    embed_model  TEXT        NOT NULL DEFAULT 'bge-m3',  -- 再埋め込み対象の特定に使う
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (book_id, chunk_index)              -- 再投入時は重複させず upsert する
);

-- 近似最近傍探索用インデックス（コサイン距離・HNSW）。
-- bge-m3 / Titan はコサイン類似度が標準。HNSW は学習不要で小規模でも扱いやすい。
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
    ON chunks USING hnsw (embedding vector_cosine_ops);

-- 書籍単位の絞り込み・削除用
CREATE INDEX IF NOT EXISTS chunks_book_id_idx ON chunks (book_id);
