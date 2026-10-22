resource "aws_s3_bucket" "results" {
  bucket = var.bucket_name
}

resource "aws_sqs_queue" "jobs" {
  name = var.queue_name
}
