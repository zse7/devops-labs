resource "aws_s3_bucket" "results" {
  bucket        = var.bucket_name
  force_destroy = true # разрешает удалить содержимое бакета при его уничтожении
}

resource "aws_sqs_queue" "jobs" {
  name = var.queue_name
}
