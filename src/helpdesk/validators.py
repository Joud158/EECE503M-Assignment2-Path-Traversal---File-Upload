"""Fail-closed validation of supported attachment types."""
import logging
import os
import warnings

from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import gettext as _
from PIL import Image, UnidentifiedImageError

from helpdesk import settings as helpdesk_settings

logger = logging.getLogger("helpdesk.uploads")
SUPPORTED_TYPES = {".png": "image/png", ".jpg": "image/jpeg",
                   ".jpeg": "image/jpeg", ".txt": "text/plain"}


class AttachmentValidationError(ValidationError):
    """Upload rejection that HTTP entry points can turn into a generic 400."""


def validate_file_extension(value):
    def reject(check):
        logger.info("upload rejected check=%s stored=none owner=none size=%s", check, value.size)
        raise AttachmentValidationError(_("Attachment rejected: invalid type, content, or size."))

    if value.size > getattr(settings, "HELPDESK_MAX_UPLOAD_SIZE", 5 * 1024 * 1024):
        reject("file_size")
    if not helpdesk_settings.HELPDESK_VALIDATE_ATTACHMENT_TYPES:
        return
    filename = value.name
    ext = os.path.splitext(filename)[1].lower()
    if filename.count(".") != 1 or "/" in filename or "\\" in filename:
        reject("filename")
    if ext not in helpdesk_settings.HELPDESK_VALID_EXTENSIONS or ext not in SUPPORTED_TYPES:
        reject("extension")
    try:
        value.seek(0)
        if ext == ".txt":
            text = value.read().decode("utf-8")
            if any(ord(char) < 32 and char not in "\t\r\n" for char in text):
                reject("text_content")
        else:
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(value) as image:
                    expected = "PNG" if ext == ".png" else "JPEG"
                    if image.format != expected:
                        reject("image_format")
                    if image.width * image.height > getattr(settings, "HELPDESK_MAX_IMAGE_PIXELS", 20_000_000):
                        reject("image_pixels")
                    image.verify()
                value.seek(0)
                with Image.open(value) as image:
                    image.load()
        value.content_type = SUPPORTED_TYPES[ext]
    except (OSError, ValueError, SyntaxError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        reject("content")
    finally:
        value.seek(0)
