variable "region" {
  type    = string
  default = "us-east-1"
}

variable "localstack_endpoint" {
  type    = string
  default = "http://localhost:4566"
}

variable "bucket" {
  type    = string
  default = "biblio"
}

variable "lambda_zip" {
  description = "Lambda デプロイ用 zip（workers + Linux 向け依存）。scripts でビルドする。"
  type        = string
  default     = "../../build/lambda.zip"
}

# Lambda 内から見たサービスのエンドポイント（compose 網のサービス名で解決）。
variable "s3_endpoint_internal" {
  type    = string
  default = "http://localstack:4566"
}

variable "database_url" {
  type    = string
  default = "postgresql://biblio:changeme_local_only@db:5432/biblio"
}

variable "ollama_host" {
  type    = string
  default = "http://ollama:11434"
}
