import os
import shutil
import unittest
from unittest.mock import MagicMock, patch

from mcp_servers import microsoft_mcp as microsoft


class AttachmentTests(unittest.TestCase):
    def test_shared_mailbox_identifiers_are_encoded(self):
        self.assertEqual(
            microsoft.mail_attachment_path({"mailbox": "a@example.com", "message_id": "a/b?c"}),
            "/users/a%40example.com/messages/a%2Fb%3Fc/attachments",
        )

    def test_listing_pages_without_attachment_bytes(self):
        next_url = microsoft.GRAPH_BASE + "/me/messages/m/attachments?$skip=100"
        with patch.object(microsoft, "graph_request", side_effect=[
            {"value": [{"id": "inline", "isInline": True}], "@odata.nextLink": next_url},
            {"value": [{"id": "file"}]},
        ]) as request:
            result = microsoft.list_mail_attachments({"message_id": "m"})
        self.assertEqual(result["count"], 2)
        self.assertIn("$select=id,name,contentType,size,isInline", request.call_args_list[0].args[1])

    def test_reject_foreign_pagination(self):
        with patch.object(microsoft, "graph_request", return_value={"@odata.nextLink": "https://example.com/"}):
            with self.assertRaises(ValueError):
                microsoft.list_mail_attachments({"message_id": "m"})

    def test_download_binary_and_private_safe_filename(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 200
        response.iter_content.return_value = [b"\x89PNG\x00", b"test"]
        with patch.object(microsoft, "graph_request", return_value={"name": "../../image.png", "contentType": "image/png"}), patch.object(microsoft, "get_access_token", return_value=("token", None)), patch.object(microsoft.requests, "get", return_value=response) as request:
            result = microsoft.download_mail_attachment({"message_id": "m", "attachment_id": "a/b"})
        self.addCleanup(shutil.rmtree, os.path.dirname(result["path"]))
        with open(result["path"], "rb") as downloaded:
            self.assertEqual(downloaded.read(), b"\x89PNG\x00test")
        self.assertEqual(os.path.basename(result["path"]), "image.png")
        self.assertEqual(os.stat(result["path"]).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(os.path.dirname(result["path"])).st_mode & 0o777, 0o700)
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertTrue(request.call_args.args[0].endswith("/a%2Fb/$value"))

    def test_reference_attachment_is_explicitly_unsupported(self):
        with patch.object(microsoft, "graph_request", return_value={"@odata.type": "#microsoft.graph.referenceAttachment"}), patch.object(microsoft.requests, "get") as request:
            result = microsoft.download_mail_attachment({"message_id": "m", "attachment_id": "a"})
        self.assertIn("error", result)
        request.assert_not_called()

    def test_http_error_does_not_create_file(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status_code = 404
        with patch.object(microsoft, "graph_request", return_value={"name": "image.png"}), patch.object(microsoft, "get_access_token", return_value=("token", None)), patch.object(microsoft.requests, "get", return_value=response), patch.object(microsoft.tempfile, "mkdtemp") as directory:
            result = microsoft.download_mail_attachment({"message_id": "m", "attachment_id": "a"})
        self.assertIn("404", result["error"])
        directory.assert_not_called()
