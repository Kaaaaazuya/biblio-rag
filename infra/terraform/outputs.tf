output "bucket" {
  value = aws_s3_bucket.biblio.id
}

output "queues" {
  value = { for k, q in aws_sqs_queue.stage : k => q.url }
}

output "functions" {
  value = [for f in aws_lambda_function.fn : f.function_name]
}
