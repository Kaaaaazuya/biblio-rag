# 2nd ステージのインフラ（ローカルは LocalStack、本番は実 AWS）。
# ローカルは endpoints を LocalStack(4566) に向け、ダミー資格情報で動かす。
# 本番化時は endpoints ブロックと ダミー資格情報を外す（リソース定義は共通）。

terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region                      = var.region
  access_key                  = "test"
  secret_key                  = "test"
  s3_use_path_style           = true
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true

  endpoints {
    s3     = var.localstack_endpoint
    sqs    = var.localstack_endpoint
    lambda = var.localstack_endpoint
    iam    = var.localstack_endpoint
    sts    = var.localstack_endpoint
  }
}
