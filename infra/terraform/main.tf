# ── S3（raw/ → normalized/ → chunks/ を1バケットのプレフィックスで） ──
resource "aws_s3_bucket" "biblio" {
  bucket = var.bucket
}

# ── SQS: 3段（raw / norm / chunks）+ 各 DLQ ──
locals {
  stages = {
    raw    = { prefix = "raw/", suffix = ".pdf" }
    norm   = { prefix = "normalized/", suffix = ".md" }
    chunks = { prefix = "chunks/", suffix = ".jsonl" }
  }
}

resource "aws_sqs_queue" "dlq" {
  for_each = local.stages
  name     = "biblio-${each.key}-dlq"
}

resource "aws_sqs_queue" "stage" {
  for_each                   = local.stages
  name                       = "biblio-${each.key}"
  visibility_timeout_seconds = 360
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq[each.key].arn
    maxReceiveCount     = 3
  })
}

# S3 → SQS 送信を許可するキューポリシー
resource "aws_sqs_queue_policy" "allow_s3" {
  for_each  = local.stages
  queue_url = aws_sqs_queue.stage[each.key].id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = aws_sqs_queue.stage[each.key].arn
    }]
  })
}

# ── S3 イベント通知: プレフィックスごとに対応 SQS へ ──
resource "aws_s3_bucket_notification" "ingest" {
  bucket = aws_s3_bucket.biblio.id

  dynamic "queue" {
    for_each = local.stages
    content {
      queue_arn     = aws_sqs_queue.stage[queue.key].arn
      events        = ["s3:ObjectCreated:*"]
      filter_prefix = queue.value.prefix
      filter_suffix = queue.value.suffix
    }
  }

  depends_on = [aws_sqs_queue_policy.allow_s3]
}

# ── Lambda 実行ロール（LocalStack では強制されないが本番同形） ──
resource "aws_iam_role" "lambda" {
  name = "biblio-lambda-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# ── Lambda（1イメージ・関数ごとに command を上書き） ──
locals {
  env_common = {
    S3_ENDPOINT_URL       = var.s3_endpoint_internal
    S3_BUCKET             = var.bucket
    AWS_REGION            = var.region
    AWS_ACCESS_KEY_ID     = "test"
    AWS_SECRET_ACCESS_KEY = "test"
  }
  env_db = {
    DATABASE_URL = var.database_url
  }
  env_embed = {
    OLLAMA_HOST   = var.ollama_host
    EMBED_MODEL   = "bge-m3"
    EMBED_DIM     = "1024"
    EMBED_BACKEND = "ollama"
  }

  functions = {
    extract = {
      handler = "workers.lambda_fns.extract_handler.handler"
      timeout = 300
      memory  = 1536
      env     = local.env_common
      queue   = "raw"
    }
    chunk = {
      handler = "workers.lambda_fns.chunk_handler.handler"
      timeout = 60
      memory  = 512
      env     = merge(local.env_common, local.env_db)
      queue   = "norm"
    }
    embed = {
      handler = "workers.lambda_fns.embed_handler.handler"
      timeout = 600
      memory  = 512
      env     = merge(local.env_common, local.env_db, local.env_embed)
      queue   = "chunks"
    }
  }
}

# LocalStack community は zip 形式の Lambda のみ対応（コンテナイメージは Pro）。
# 3 関数で同一 zip を共有し、handler で振り分ける。
resource "aws_lambda_function" "fn" {
  for_each         = local.functions
  function_name    = "biblio-${each.key}"
  role             = aws_iam_role.lambda.arn
  runtime          = "python3.12"
  architectures    = ["arm64"]
  filename         = var.lambda_zip
  source_code_hash = filebase64sha256(var.lambda_zip)
  handler          = each.value.handler
  timeout          = each.value.timeout
  memory_size      = each.value.memory

  environment {
    variables = each.value.env
  }
}

# ── SQS → Lambda（イベントソースマッピング） ──
resource "aws_lambda_event_source_mapping" "trigger" {
  for_each         = local.functions
  event_source_arn = aws_sqs_queue.stage[each.value.queue].arn
  function_name    = aws_lambda_function.fn[each.key].arn
  batch_size       = 1
}
