variable "bucket_name" {
  type        = string
  description = "Имя бакета S3, куда скрапер сохраняет JSON"
  default     = "quotes-results"
}

variable "queue_name" {
  type        = string
  description = "Имя очереди SQS, откуда скрапер потом заберёт задание"
  default     = "quotes-jobs"
}
