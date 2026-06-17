-- 既存 DB への embed_model カラム追加（001_schema.sql は新規 DB 向け）
ALTER TABLE chunks
    ADD COLUMN IF NOT EXISTS embed_model TEXT NOT NULL DEFAULT 'bge-m3';
