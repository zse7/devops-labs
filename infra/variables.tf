variable "bucket_name" {
  type        = string
  description = "Имя шкафа S3, куда скрапер потом положит JSON"
  default     = "quotes-results"
}

variable "queue_name" {
  type        = string
  description = "Имя очереди SQS, откуда скрапер потом заберёт задание"
  default     = "quotes-jobs"
}
