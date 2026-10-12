"""Regression checks for upload rejection, quotas and authorized downloads."""
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.files.uploadedfile import SimpleUploadedFile
from django.http import Http404
from django.test import RequestFactory, SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from PIL import Image

from helpdesk.models import FollowUp, FollowUpAttachment, KBCategory, KBItem, KBIAttachment, Queue, Ticket
from helpdesk.upload_handlers import LimitedUploadHandler, UploadErrorMiddleware
from helpdesk.validators import AttachmentValidationError, validate_file_extension
from helpdesk.views.staff import download_attachment


def png():
    out = BytesIO()
    Image.new("RGB", (2, 2), "red").save(out, format="PNG")
    return out.getvalue()


class UploadValidationTests(SimpleTestCase):
    def setUp(self):
        self.flag = patch("helpdesk.validators.helpdesk_settings.HELPDESK_VALIDATE_ATTACHMENT_TYPES", True)
        self.flag.start()
        self.addCleanup(self.flag.stop)

    def test_valid_png_ignores_claimed_mime(self):
        file = SimpleUploadedFile("image.png", png(), content_type="text/html")
        validate_file_extension(file)
        self.assertEqual(file.content_type, "image/png")
        self.assertEqual(file.tell(), 0)

    def test_bad_names_and_types_rejected(self):
        for name in ("shell.php", "notice.html", "logo.svg", "shell.php.png", "file", "file."):
            with self.subTest(name=name), self.assertRaises(AttachmentValidationError):
                validate_file_extension(SimpleUploadedFile(name, b"hello"))

    def test_signature_without_valid_image_rejected(self):
        with self.assertRaises(AttachmentValidationError):
            validate_file_extension(SimpleUploadedFile("image.png", b"\x89PNG\r\n\x1a\ninvalid"))

    @override_settings(HELPDESK_MAX_IMAGE_PIXELS=3)
    def test_pixel_limit(self):
        with self.assertRaises(AttachmentValidationError):
            validate_file_extension(SimpleUploadedFile("image.png", png()))

    @override_settings(HELPDESK_MAX_UPLOAD_SIZE=3)
    def test_file_limit(self):
        with self.assertRaises(AttachmentValidationError):
            validate_file_extension(SimpleUploadedFile("file.txt", b"four"))

    @override_settings(HELPDESK_MAX_UPLOAD_SIZE=3)
    def test_stream_limit_rejects_before_storage(self):
        from django.core.exceptions import SuspiciousOperation
        handler = LimitedUploadHandler()
        with self.assertRaises(SuspiciousOperation):
            handler.receive_data_chunk(b"four", 0)

    def test_error_response_is_generic(self):
        middleware = UploadErrorMiddleware(lambda request: None)
        response = middleware.process_exception(RequestFactory().post("/"), AttachmentValidationError("private path"))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn(b"private path", response.content)


class AttachmentStorageTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = override_settings(MEDIA_ROOT=self.directory.name)
        self.config.enable()
        self.addCleanup(self.config.disable)
        self.flag = patch("helpdesk.validators.helpdesk_settings.HELPDESK_VALIDATE_ATTACHMENT_TYPES", True)
        self.flag.start()
        self.addCleanup(self.flag.stop)
        self.user = get_user_model().objects.create_user("upload-test", is_staff=True, is_superuser=True)
        queue = Queue.objects.create(title="Upload tests", slug="upload-tests")
        self.ticket = Ticket.objects.create(title="Test", queue=queue, created=timezone.now(), submitter_email="test@example.invalid")
        self.followup = FollowUp.objects.create(ticket=self.ticket, date=timezone.now(), title="Test", public=True, user=self.user)

    def attachment(self, name="example.txt", data=b"hello"):
        return FollowUpAttachment.objects.create(followup=self.followup, file=SimpleUploadedFile(name, data), size=1)

    def request(self, path, user=None, **extra):
        request = RequestFactory().get("/attachments/download/", {"path": path, **extra})
        request.user = user or self.user
        return request

    def test_random_name_original_label_and_actual_size(self):
        attachment = self.attachment()
        self.assertEqual(attachment.filename, "example.txt")
        self.assertEqual(attachment.size, 5)
        self.assertRegex(Path(attachment.file.name).name, r"^[0-9a-f]{32}\.txt$")

    def test_direct_model_save_cannot_bypass_validation(self):
        with self.assertRaises(AttachmentValidationError):
            self.attachment("bad.png", b"fake image")
        self.assertFalse(FollowUpAttachment.objects.exists())

    def test_replacement_removes_previous_bytes_after_commit(self):
        attachment = self.attachment()
        old_path = Path(attachment.file.path)
        with self.captureOnCommitCallbacks(execute=True):
            attachment.file = SimpleUploadedFile("replacement.txt", b"new")
            attachment.save()
        self.assertFalse(old_path.exists())
        self.assertTrue(Path(attachment.file.path).exists())

    def test_queryset_delete_removes_bytes_after_commit(self):
        attachment = self.attachment()
        old_path = Path(attachment.file.path)
        with self.captureOnCommitCallbacks(execute=True):
            FollowUpAttachment.objects.filter(pk=attachment.pk).delete()
        self.assertFalse(old_path.exists())

    def test_kb_attachment_enforces_category_access(self):
        category = KBCategory.objects.create(name="Private", title="Private", slug="private", public=False)
        item = KBItem.objects.create(category=category, title="Private item")
        attachment = KBIAttachment.objects.create(kbitem=item, file=SimpleUploadedFile("kb.txt", b"hello"), size=5)
        other = get_user_model().objects.create_user("other-agent", is_staff=True)
        with self.assertRaises(Http404):
            download_attachment(self.request(attachment.file.name, other))
        self.assertEqual(download_attachment(self.request(attachment.file.name)).status_code, 200)

    def test_unprivileged_agent_cannot_download_other_queue(self):
        attachment = self.attachment()
        other = get_user_model().objects.create_user("other-agent", is_staff=True)
        with self.assertRaises(Http404):
            download_attachment(self.request(attachment.file.name, other))

    @override_settings(HELPDESK_ATTACHMENT_QUOTA=7)
    def test_owner_quota(self):
        self.attachment()
        with self.assertRaises(AttachmentValidationError):
            self.attachment()
        self.assertEqual(FollowUpAttachment.objects.count(), 1)

    @override_settings(HELPDESK_TOTAL_ATTACHMENT_QUOTA=4)
    def test_global_quota(self):
        with self.assertRaises(AttachmentValidationError):
            self.attachment()

    def test_image_reencoding_removes_trailing_bytes(self):
        attachment = self.attachment("image.png", png() + b"trailing-untrusted-bytes")
        with attachment.file.open("rb") as file:
            self.assertNotIn(b"trailing-untrusted-bytes", file.read())

    def test_authorized_download_uses_safe_internal_url(self):
        attachment = self.attachment()
        response = download_attachment(self.request(attachment.file.name))
        self.assertEqual(response["X-Accel-Redirect"], "/media/" + attachment.file.name)
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_traversal_and_unregistered_file_rejected(self):
        Path(self.directory.name, "unregistered.txt").write_text("hello")
        for path in ("../../etc/passwd", "unregistered.txt", "\x00"):
            with self.subTest(path=path), self.assertRaises(Http404):
                download_attachment(self.request(path))

    def test_public_attachment_requires_ticket_credentials(self):
        attachment = self.attachment()
        with self.assertRaises(Http404):
            download_attachment(self.request(attachment.file.name, AnonymousUser()))
        response = download_attachment(self.request(attachment.file.name, AnonymousUser(),
            key=self.ticket.secret_key, email=self.ticket.submitter_email))
        self.assertEqual(response.status_code, 200)
        self.followup.public = False
        self.followup.save()
        with self.assertRaises(Http404):
            download_attachment(self.request(attachment.file.name, AnonymousUser(),
                key=self.ticket.secret_key, email=self.ticket.submitter_email))
