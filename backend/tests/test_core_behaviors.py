import os
import sys
import unittest
from unittest import mock

import dns.exception
import dns.resolver
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.routers.discovery import discover_companies
from core.security import AuthenticatedUser
from main import app
from models.requests import DiscoverRequest
from services.email_verification import (
    validate_email_domain,
    validate_lead_emails,
)
from services.lead_validation import (
    compute_data_quality_score,
    validate_website_reachability,
)
from services.scanning_service import fetch_html
from utils.url_utils import domain_of, normalized_url


class UrlSafetyTests(unittest.TestCase):
    def test_normalized_url_removes_fragment_and_keeps_query(self):
        with mock.patch("utils.url_utils.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("8.8.8.8", 443))]):
            self.assertEqual(
                normalized_url("https://WWW.Example.com/path?q=one#section"),
                "https://WWW.Example.com/path?q=one",
            )

    def test_normalized_url_rejects_private_and_unresolvable_hosts(self):
        with self.assertRaisesRegex(HTTPException, "hostname is not allowed"):
            normalized_url("http://localhost/admin")

        with mock.patch("utils.url_utils.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("127.0.0.1", 80))]):
            with self.assertRaisesRegex(HTTPException, "Private or non-public"):
                normalized_url("http://example.test")

        with mock.patch("utils.url_utils.socket.getaddrinfo", side_effect=OSError):
            with self.assertRaisesRegex(HTTPException, "could not be resolved"):
                normalized_url("https://missing.example")

    def test_domain_of_normalizes_www_and_case(self):
        self.assertEqual(domain_of("https://WWW.Example.COM/path"), "example.com")


class ValidationTests(unittest.TestCase):
    def test_validate_email_domain_reports_dns_outcomes(self):
        record = mock.Mock(preference=10, exchange="mail.example.com.")
        with mock.patch("services.email_verification.dns.resolver.resolve", return_value=[record]):
            found = validate_email_domain("Example.com.")
        self.assertEqual(found["status"], "found")
        self.assertEqual(found["records"], [(10, "mail.example.com")])

        with mock.patch("services.email_verification.dns.resolver.resolve", side_effect=dns.resolver.NXDOMAIN):
            self.assertEqual(validate_email_domain("missing.example")["status"], "domain_not_found")

        with mock.patch("services.email_verification.dns.resolver.resolve", side_effect=dns.exception.Timeout):
            self.assertEqual(validate_email_domain("slow.example")["status"], "lookup_error")

    @mock.patch("services.email_verification.validate_email_domain")
    def test_email_validation_skips_duplicate_domain_and_retains_invalid_address(self, mock_domain):
        mock_domain.return_value = {"status": "found", "reason": "MX records were found.", "records": []}
        results = validate_lead_emails(["hello@example.com", "team@example.com", "invalid-address"])

        self.assertEqual(results[0]["status"], "found")
        self.assertIn("Duplicate domain", results[1]["reason"])
        self.assertIn("syntax is invalid", results[2]["reason"])
        mock_domain.assert_called_once_with("example.com")

    def test_quality_score_penalizes_failed_signals_and_rewards_mx(self):
        score, reasons, raw_score = compute_data_quality_score(
            [
                {"status": "found"},
                {"status": "no_mx_record"},
                {"status": "not_checked", "reason": "Email syntax is invalid: malformed"},
            ],
            {"status": "http_error"},
            [{"score": 20}],
        )
        self.assertEqual((score, raw_score), (15, 15))
        self.assertTrue(any("without MX" in reason for reason in reasons))
        self.assertTrue(any("Low Hunter" in reason for reason in reasons))


class HttpValidationTests(unittest.IsolatedAsyncioTestCase):
    @mock.patch("services.lead_validation.normalized_url", side_effect=lambda url: url)
    @mock.patch("services.lead_validation.httpx.AsyncClient")
    async def test_website_validation_follows_safe_redirect(self, mock_client, _normalized):
        redirect = mock.Mock(is_redirect=True, status_code=302, headers={"location": "/about"}, content=b"")
        response = mock.Mock(is_redirect=False, status_code=200, headers={"content-type": "text/html"}, content=b"<html></html>")
        client = mock.AsyncMock()
        client.get.side_effect = [redirect, response]
        context = mock.AsyncMock()
        context.__aenter__.return_value = client
        mock_client.return_value = context

        result = await validate_website_reachability("https://example.com")

        self.assertEqual(result["status"], "reachable")
        self.assertEqual(result["final_url"], "https://example.com/about")

    @mock.patch("services.lead_validation.normalized_url", side_effect=lambda url: url)
    @mock.patch("services.lead_validation.httpx.AsyncClient")
    async def test_website_validation_rejects_non_html_and_http_errors(self, mock_client, _normalized):
        pdf = mock.Mock(is_redirect=False, status_code=200, headers={"content-type": "application/pdf"}, content=b"pdf")
        client = mock.AsyncMock()
        client.get.return_value = pdf
        context = mock.AsyncMock()
        context.__aenter__.return_value = client
        mock_client.return_value = context
        self.assertEqual((await validate_website_reachability("https://example.com"))["status"], "blocked_or_invalid")

        server_error = mock.Mock(is_redirect=False, status_code=503, headers={"content-type": "text/html"}, content=b"")
        client.get.return_value = server_error
        self.assertEqual((await validate_website_reachability("https://example.com"))["status"], "http_error")

    @mock.patch("services.scanning_service.normalized_url", side_effect=lambda url: url)
    async def test_fetch_html_ignores_non_html_and_network_failures(self, _normalized):
        non_html = mock.Mock(status_code=200, headers={"content-type": "application/pdf"}, content=b"pdf", text="pdf")
        non_html.raise_for_status.return_value = None
        client = mock.AsyncMock()
        client.get.return_value = non_html
        self.assertIsNone(await fetch_html(client, "https://example.com"))

        client.get.side_effect = httpx.ConnectError("offline")
        self.assertIsNone(await fetch_html(client, "https://example.com"))


class DiscoveryEndpointTests(unittest.IsolatedAsyncioTestCase):
    @mock.patch("services.discovery_service.run_in_threadpool")
    async def test_discovery_filters_duplicates_social_sites_and_query_noise(self, mock_threadpool):
        mock_threadpool.return_value = [
            {"href": "https://linkedin.com/company/acme", "title": "Acme", "body": "skip"},
            {"href": "https://www.acme.example/products?utm_source=search#intro", "title": "Acme", "body": "first"},
            {"href": "https://acme.example/contact", "title": "Acme contact", "body": "duplicate"},
            {"href": "mailto:hello@acme.example", "title": "Mail", "body": "skip"},
            {"href": "https://beta.example", "title": "Beta", "body": "second"},
        ]

        result = await discover_companies(
            DiscoverRequest(industry="HVAC", location="Delhi", keywords="commercial", country="IN", limit=10),
            AuthenticatedUser(id="user-a", access_token="token-a"),
        )

        self.assertEqual(result["query"], "HVAC companies official website Delhi commercial")
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["candidates"][0]["url"], "https://www.acme.example/products")
        self.assertEqual(result["candidates"][0]["domain"], "acme.example")

    async def test_discovery_validation_and_health_routes_have_expected_contracts(self):
        client = TestClient(app)
        self.assertEqual(client.get("/health").json(), {"status": "ok", "service": "leadforge-api"})
        response = client.post("/api/discover", json={"industry": "x"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
