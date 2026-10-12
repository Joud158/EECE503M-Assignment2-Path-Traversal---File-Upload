"""Bound multipart bytes before oversized files fill temporary storage."""
import logging

from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.core.files.uploadhandler import FileUploadHandler
from django.http import JsonResponse
from django.utils.deprecation import MiddlewareMixin
from helpdesk.validators import AttachmentValidationError

logger = logging.getLogger("helpdesk.uploads")


class LimitedUploadHandler(FileUploadHandler):
    def __init__(self, request=None):
        super().__init__(request)
        self.total = 0
        self.file_bytes = 0

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.file_bytes = 0

    def receive_data_chunk(self, raw_data, start):
        self.file_bytes += len(raw_data)
        self.total += len(raw_data)
        if self.file_bytes > settings.HELPDESK_MAX_UPLOAD_SIZE or self.total > 10 * 1024 * 1024:
            logger.info("upload rejected check=stream_size owner=%s size=%s",
                        getattr(getattr(self.request, "user", None), "pk", None), self.total)
            raise SuspiciousOperation("Upload size limit exceeded.")
        return raw_data

    def file_complete(self, file_size):
        return None


class UploadErrorMiddleware(MiddlewareMixin):
    def process_exception(self, request, exception):
        if isinstance(exception, AttachmentValidationError):
            logger.info("upload rejected check=validation owner=%s",
                        getattr(getattr(request, "user", None), "pk", None))
            return JsonResponse({"error": "Attachment rejected."}, status=400)
        return None
