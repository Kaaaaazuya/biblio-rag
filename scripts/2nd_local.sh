#!/usr/bin/env bash
# 2nd ステージ（AWS化）のローカル検証: Terraform + LocalStack で S3→SQS→Lambda→pgvector を通す。
#
# 前提: docker compose（db / ollama / localstack）が起動済みで、ollama に bge-m3 がある。
#   docker compose -f docker/docker-compose.yml up -d
#
# 使い方:
#   scripts/2nd_local.sh deploy   # Lambda zip をビルド → terraform apply
#   scripts/2nd_local.sh run      # サンプル PDF を投入し、pgvector に入るまで待つ
#   scripts/2nd_local.sh down     # terraform destroy
set -euo pipefail

cd "$(dirname "$0")/.."
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_REGION=us-east-1
ENDPOINT=http://localhost:4566
TF="terraform -chdir=infra/terraform"

build_zip() {
  echo "==> Lambda zip をビルド（aarch64 / cp312 wheel）"
  rm -rf build/pkg build/lambda.zip && mkdir -p build/pkg
  uv pip install --target build/pkg \
    --python-version 3.12 --python-platform aarch64-manylinux2014 --only-binary=:all: \
    pymupdf "psycopg[binary]" httpx python-dotenv >/dev/null
  cp -r workers build/pkg/workers
  find build/pkg -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
  ( cd build/pkg && zip -qr ../lambda.zip . )
  echo "    -> build/lambda.zip ($(du -h build/lambda.zip | cut -f1))"
}

case "${1:-}" in
  deploy)
    build_zip
    $TF init -input=false >/dev/null
    $TF apply -auto-approve -input=false
    ;;
  run)
    book_id="${2:-sample}"
    echo "==> raw/${book_id}.pdf を投入（メタは S3 object metadata に URL エンコードで）"
    docker exec biblio-db psql -U biblio -d biblio -c "DELETE FROM chunks WHERE book_id='${book_id}';" >/dev/null 2>&1 || true
    aws --endpoint-url=$ENDPOINT s3 rm "s3://biblio/normalized/${book_id}.md" >/dev/null 2>&1 || true
    aws --endpoint-url=$ENDPOINT s3api put-object --bucket biblio --key "raw/${book_id}.pdf" \
      --body tests/fixtures/sample_book.pdf --metadata title=Sample%20Book,author=Aozora%20Test >/dev/null
    echo "==> パイプライン完了を待機..."
    for i in $(seq 1 45); do
      rows=$(docker exec biblio-db psql -U biblio -d biblio -tAc \
        "SELECT count(*) FROM chunks WHERE book_id='${book_id}';" 2>/dev/null | tr -d ' ')
      printf "\r    [%ds] db_rows=%s " "$((i*3))" "${rows:-0}"
      if [ "${rows:-0}" -gt 0 ]; then echo "  ✅ COMPLETE"; exit 0; fi
      sleep 3
    done
    echo "  ❌ タイムアウト（docker logs biblio-localstack を確認）"; exit 1
    ;;
  down)
    $TF destroy -auto-approve -input=false
    ;;
  *)
    echo "usage: $0 {deploy|run [book_id]|down}"; exit 2
    ;;
esac
