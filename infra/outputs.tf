output "bucket_name" {
  description = "Имя шкафа S3. Makefile передаст его скраперу."
  value       = aws_s3_bucket.results.bucket
}

output "queue_url" {
  description = "Адрес очереди SQS. Makefile передаст его скраперу."
  value       = aws_sqs_queue.jobs.url
}
