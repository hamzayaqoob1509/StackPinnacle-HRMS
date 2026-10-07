"""PDF rendering with xhtml2pdf: output, email attachments, and what may be loaded."""

import os
import tempfile

from django.test import SimpleTestCase, override_settings

from base.methods import (
    html_to_pdf,
    pdf_attachment,
    pdf_link_callback,
    template_pdf,
)


class HtmlToPdfTests(SimpleTestCase):
    def test_renders_a_pdf(self):
        pdf = html_to_pdf("<h1>Offer letter</h1><p>Welcome aboard.</p>")
        self.assertTrue(pdf.startswith(b"%PDF-"))

    def test_non_latin_text_does_not_fail(self):
        pdf = html_to_pdf("<p>Salary: ₹50,000 — «confirmed»</p>")
        self.assertTrue(pdf.startswith(b"%PDF-"))


class TemplatePdfTests(SimpleTestCase):
    def test_returns_an_inline_pdf_response(self):
        response = template_pdf(
            "<table><tr><td>Basic</td><td>50000</td></tr></table>",
            html=True,
            filename="Document",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("Document", response["Content-Disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))

    def test_bootstrap_classes_in_a_mail_template_do_not_break_rendering(self):
        # The pdfkit version pulled Bootstrap from a CDN; templates written
        # for it still carry its class names.
        response = template_pdf(
            '<div class="container"><p class="text-center fw-bold">Hello</p></div>',
            html=True,
        )
        self.assertEqual(response.status_code, 200)


class PdfAttachmentTests(SimpleTestCase):
    def test_attachment_tuple_for_an_email(self):
        name, content, mimetype = pdf_attachment("<p>Dear candidate</p>")
        self.assertEqual(name, "Document.pdf")
        self.assertEqual(mimetype, "application/pdf")
        self.assertTrue(content.startswith(b"%PDF-"))

    def test_refused_body_gives_no_attachment(self):
        # Previously the 400 response's text was attached as "Document".
        self.assertIsNone(pdf_attachment("<script>alert(1)</script>"))


class PdfLinkCallbackTests(SimpleTestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.media = os.path.join(self.root, "media")
        os.makedirs(os.path.join(self.media, "company"))
        self.logo = os.path.join(self.media, "company", "logo.png")
        with open(self.logo, "wb") as handle:
            handle.write(b"png")
        with open(os.path.join(self.root, "secret.txt"), "w") as handle:
            handle.write("secret")
        settings = override_settings(
            MEDIA_URL="/media/",
            MEDIA_ROOT=self.media,
            STATIC_URL="/static/",
            STATIC_ROOT=os.path.join(self.root, "staticfiles"),
        )
        settings.enable()
        self.addCleanup(settings.disable)

    def test_media_url_becomes_a_file_on_disk(self):
        self.assertEqual(
            pdf_link_callback("/media/company/logo.png"), os.path.realpath(self.logo)
        )

    def test_media_url_cannot_escape_the_media_folder(self):
        self.assertEqual(pdf_link_callback("/media/../secret.txt"), "")

    def test_missing_media_file_is_dropped(self):
        self.assertEqual(pdf_link_callback("/media/company/missing.png"), "")

    def test_public_image_url_is_allowed(self):
        url = "https://cdn.example.com/logo.png"
        self.assertEqual(pdf_link_callback(url), url)

    def test_data_uri_is_allowed(self):
        uri = "data:image/png;base64,iVBORw0KGgo="
        self.assertEqual(pdf_link_callback(uri), uri)

    def test_internal_addresses_are_refused(self):
        for uri in (
            "http://169.254.169.254/latest/meta-data/",  # cloud metadata
            "http://127.0.0.1:8001/",
            "http://localhost/",
            "http://10.0.0.5/",
            "http://[::1]/",
        ):
            self.assertEqual(pdf_link_callback(uri), "", uri)

    def test_local_file_and_other_schemes_are_refused(self):
        for uri in ("file:///etc/passwd", "/etc/passwd", "ftp://example.com/x"):
            self.assertEqual(pdf_link_callback(uri), "", uri)


class ApiPayslipPdfFormatTests(SimpleTestCase):
    def test_format_pdf_is_a_registered_format(self):
        # DRF answers 404 for "?format=pdf" unless a renderer has that name,
        # which made the PDF branch of this view unreachable.
        from horilla_api.api_views.payroll.views import PayslipPDFAPIView

        formats = {renderer.format for renderer in PayslipPDFAPIView.renderer_classes}
        self.assertIn("pdf", formats)
        self.assertIn("json", formats)

    def test_renderer_passes_pdf_bytes_through_and_serialises_errors(self):
        from horilla_api.api_views.payroll.views import PayslipPDFRenderer

        renderer = PayslipPDFRenderer()
        self.assertEqual(renderer.render(b"%PDF-1.4"), b"%PDF-1.4")
        self.assertEqual(renderer.render({"detail": "failed"}), b'{"detail": "failed"}')
