import unittest
from datetime import datetime, timezone
from unittest import mock

import os
import sys
from pathlib import Path

from fastapi import HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.routers.leads import list_leads
from api.routers.scans import scan
from api.routers.stats import stats
from core.security import AuthenticatedUser, get_user_db
from main import app
from models.requests import ScanRequest
from repositories.lead_repository import require_owned_lead
from services.email_verification import (
    get_hunter_payload_status,
    verify_email_with_hunter,
)
from services.lead_scoring import score_company_name, score_lead
from services.lead_validation import build_lead_validation
from services.scanning_service import extract_page, is_access_challenge_page


class CompanyNameExtractionTests(unittest.IsolatedAsyncioTestCase):
    def test_missing_title_does_not_award_company_name_points(self):
        result = extract_page("https://acme.com", "<html><head></head><body></body></html>")

        self.assertIsNone(result["company_name"])
        self.assertIsNone(result["company_name_source"])
        self.assertEqual(score_company_name(result["company_name"], result["company_name_source"]), 0)
        _, reasons = score_lead(
            result["company_name"],
            None,
            [],
            [],
            {},
            name_source=result["company_name_source"],
        )
        self.assertNotIn("Company name identified (+20)", reasons)

    def test_untrusted_name_source_does_not_award_company_name_points(self):
        self.assertEqual(score_company_name("acme.com", None), 0)

    def test_trusted_name_source_awards_company_name_points(self):
        self.assertEqual(score_company_name("Acme Technologies", "page_title"), 20)

    def test_generic_title_is_rejected(self):
        result = extract_page("https://acme.com", "<html><head><title>Home</title></head></html>")

        self.assertIsNone(result["company_name"])
        self.assertIsNone(result["company_name_source"])

    def test_security_challenge_title_is_not_extracted_as_a_company(self):
        html = """
            <html><head><title>One moment, please...</title></head>
            <body>Enable JavaScript and cookies to continue</body></html>
        """
        result = extract_page("https://acme.com", html)

        self.assertIsNone(result["company_name"])
        self.assertIsNone(result["company_name_source"])
        score, reasons = score_lead(
            result["company_name"],
            result["description"],
            result["emails"],
            result["phones"],
            result["social_links"],
            name_source=result["company_name_source"],
        )
        self.assertEqual(score, 10)
        self.assertNotIn("Company name identified (+20)", reasons)
        self.assertTrue(is_access_challenge_page(html))

    def test_title_prefix_is_removed(self):
        result = extract_page(
            "https://acme.com",
            "<html><head><title>Welcome to Acme Technologies</title></head></html>",
        )

        self.assertEqual(result["company_name"], "Acme Technologies")
        self.assertEqual(result["company_name_source"], "page_title")

    def test_organization_schema_has_priority_over_other_names(self):
        result = extract_page(
            "https://acme.com",
            """
            <html><head>
              <title>Page Title</title>
              <meta property="og:site_name" content="Open Graph Name">
              <script type="application/ld+json">
                {"@type": "Organization", "name": "Schema Name"}
              </script>
            </head></html>
            """,
        )

        self.assertEqual(result["company_name"], "Schema Name")
        self.assertEqual(result["company_name_source"], "organization_schema")

    def test_open_graph_name_precedes_page_title(self):
        result = extract_page(
            "https://acme.com",
            """
            <html><head>
              <title>Page Title</title>
              <meta property="og:site_name" content="Open Graph Name">
            </head></html>
            """,
        )

        self.assertEqual(result["company_name"], "Open Graph Name")
        self.assertEqual(result["company_name_source"], "og_site_name")

    def test_hunter_status_mapping_handles_common_variants(self):
        self.assertEqual(get_hunter_payload_status("accept_all"), "accept_all")
        self.assertEqual(get_hunter_payload_status("rate_limit_exceeded"), "rate_limited")
        self.assertEqual(get_hunter_payload_status("not checked"), "not_checked")
        self.assertEqual(get_hunter_payload_status("do not send"), "accept_all")
        self.assertEqual(get_hunter_payload_status("unknown"), "unknown")

    @mock.patch("services.email_verification.settings")
    @mock.patch("services.email_verification.httpx.AsyncClient")
    async def test_verify_email_with_hunter_uses_cache_and_success_response(self, mock_client, mock_settings):
        mock_db = mock.Mock()
        mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [{
            "email": "alice@example.com",
            "status": "accept_all",
            "score": 72,
            "details": {"source": "hunter"},
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }]
        mock_settings.hunter_api_key = "test-key"
        mock_settings.hunter_cache_ttl_hours = 24
        mock_settings.hunter_timeout_seconds = 6.0
        mock_settings.hunter_api_base_url = "https://api.hunter.io/v2"

        result = await verify_email_with_hunter(mock_db, "Alice@Example.com")
        self.assertEqual(result["status"], "accept_all")
        self.assertEqual(result["email"], "alice@example.com")
        self.assertEqual(result["score"], 72)

    @mock.patch("services.email_verification.settings")
    @mock.patch("services.email_verification.httpx.AsyncClient")
    async def test_verify_email_with_hunter_handles_missing_key_and_invalid_credentials(self, mock_client, mock_settings):
        mock_db = mock.Mock()
        mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        mock_settings.hunter_api_key = ""
        mock_settings.hunter_cache_ttl_hours = 24

        result = await verify_email_with_hunter(mock_db, "still@example.com")
        self.assertEqual(result["status"], "not_configured")

        mock_settings.hunter_api_key = "bad-key"
        response = mock.Mock()
        response.status_code = 401
        response.content = b'{"message":"Unauthorized"}'
        response.json.return_value = {"message": "Unauthorized"}
        mock_client.return_value.__aenter__.return_value.get.return_value = response
        result = await verify_email_with_hunter(mock_db, "still@example.com")
        self.assertEqual(result["status"], "credentials_invalid")

    @mock.patch("services.email_verification.settings")
    @mock.patch("services.email_verification.httpx.AsyncClient")
    async def test_verify_email_with_hunter_handles_rate_limits_and_timeouts(self, mock_client, mock_settings):
        mock_db = mock.Mock()
        mock_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        mock_settings.hunter_api_key = "test-key"
        mock_settings.hunter_cache_ttl_hours = 24
        mock_settings.hunter_timeout_seconds = 6.0
        mock_settings.hunter_api_base_url = "https://api.hunter.io/v2"

        response = mock.Mock()
        response.status_code = 429
        response.content = b'{"data": {}}'
        response.json.return_value = {"data": {}}
        mock_client.return_value.__aenter__.return_value.get.return_value = response
        result = await verify_email_with_hunter(mock_db, "retry@example.com")
        self.assertEqual(result["status"], "rate_limited")

        mock_client.return_value.__aenter__.return_value.get.side_effect = TimeoutError()
        result = await verify_email_with_hunter(mock_db, "timeout@example.com")
        self.assertEqual(result["status"], "verification_unavailable")

    @mock.patch("api.routers.scans.get_db")
    @mock.patch("services.scanning_service.httpx.AsyncClient")
    @mock.patch(
        "services.scanning_service.normalized_url",
        return_value="https://example.com/",
    )
    async def test_scan_rejects_an_antibot_page_without_saving_a_lead(
        self, mock_normalized_url, mock_client, mock_get_db
    ):
        html = (
            "<html><head><title>One moment, please...</title></head>"
            "<body>Enable JavaScript and cookies to continue</body></html>"
        )
        response = mock.Mock(
            status_code=403,
            headers={"content-type": "text/html"},
            content=html.encode(),
            text=html,
        )
        response.raise_for_status.return_value = None
        http_client = mock.AsyncMock()
        http_client.get.return_value = response
        context_manager = mock.AsyncMock()
        context_manager.__aenter__.return_value = http_client
        mock_client.return_value = context_manager

        with self.assertRaises(HTTPException) as raised:
            await scan(
                ScanRequest(url="https://example.com"),
                AuthenticatedUser(id="user-a", access_token="test-token"),
            )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertIn("anti-bot or security challenge", raised.exception.detail)
        mock_get_db.assert_not_called()
        response.raise_for_status.assert_not_called()


class AuthenticationAndOwnershipTests(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_protected_endpoint_rejects_missing_and_invalid_tokens(self):
        client = TestClient(app)
        self.assertEqual(client.get("/api/leads").status_code, 401)
        self.assertEqual(
            client.get("/api/leads", headers={"Authorization": "Bearer bad-token"}).status_code,
            401,
        )

    @mock.patch("core.security.settings")
    def test_request_scoped_client_uses_the_verified_access_token(self, mock_settings):
        mock_settings.supabase_url = "https://example.supabase.co"
        mock_settings.supabase_publishable_key = "publishable-test-key"

        client = get_user_db(AuthenticatedUser(id="user-a", access_token="token-a"))

        self.assertEqual(client.options.headers["Authorization"], "Bearer token-a")

    def test_other_user_cannot_update_a_lead(self):
        service_db = mock.Mock()
        service_db.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "lead-a", "user_id": "user-a"}
        ]

        with self.assertRaises(HTTPException) as raised:
            require_owned_lead(service_db, "lead-a", "user-b")

        self.assertEqual(raised.exception.status_code, 403)

    @mock.patch("api.routers.leads.get_user_db")
    def test_authenticated_list_uses_the_request_users_rls_client(self, mock_get_user_db):
        user_db = mock.Mock()
        user_db.table.return_value.select.return_value.gte.return_value.order.return_value.limit.return_value.execute.return_value.data = [
            {"id": "lead-a", "user_id": "user-a", "domain": "a.example"}
        ]
        mock_get_user_db.return_value = user_db
        user = AuthenticatedUser(id="user-a", access_token="token-a")

        result = list_leads(search="", status="all", min_score=0, limit=100, user=user)

        self.assertEqual(result["leads"][0]["user_id"], "user-a")
        mock_get_user_db.assert_called_once_with(user)

    @mock.patch("api.routers.stats.get_user_db")
    def test_dashboard_stats_use_only_rows_returned_by_the_users_rls_client(self, mock_get_user_db):
        user_db = mock.Mock()
        user_db.table.return_value.select.return_value.limit.return_value.execute.return_value.data = [
            {"id": "lead-a", "lead_score": 85, "status": "qualified", "emails": ["a@example.com"]}
        ]
        mock_get_user_db.return_value = user_db

        result = stats(user=AuthenticatedUser(id="user-a", access_token="token-a"))

        self.assertEqual(result, {"total": 1, "qualified": 1, "with_email": 1, "high_score": 1})

    def test_ownership_migration_defines_composite_unique_key_and_all_rls_policies(self):
        migration = (Path(__file__).parents[2] / "database" / "migrations" / "005_add_lead_ownership.sql").read_text()
        self.assertIn("on public.leads (user_id, domain)", migration)
        self.assertIn("check (user_id is not null) not valid", migration)
        for operation in ("for select", "for insert", "for update", "for delete"):
            self.assertIn(operation, migration)


class LeadRescanOwnershipTests(unittest.IsolatedAsyncioTestCase):
    @mock.patch(
        "services.scanning_service.build_lead_validation",
        new_callable=mock.AsyncMock,
    )
    @mock.patch(
        "services.scanning_service.verify_email_with_hunter",
        new_callable=mock.AsyncMock,
    )
    @mock.patch("api.routers.scans.get_db")
    @mock.patch("api.routers.scans.get_user_db")
    @mock.patch("services.scanning_service.fetch_html", new_callable=mock.AsyncMock)
    @mock.patch("services.scanning_service.httpx.AsyncClient")
    @mock.patch(
        "services.scanning_service.normalized_url",
        return_value="https://example.com/",
    )
    async def test_rescan_keeps_the_same_users_qualified_status(
        self, _normalized_url, mock_client, mock_fetch_html, mock_get_user_db, mock_get_db,
        _verify_hunter, mock_build_validation,
    ):
        mock_fetch_html.return_value = ("https://example.com/", "<html><head><title>Example Co</title></head></html>")
        mock_build_validation.return_value = {
            "data_quality_score": 95,
            "validation_status": "passed",
            "validation_summary": "Website reachable (+25)",
            "validation_details": {},
            "validation_last_run_at": "2026-01-01T00:00:00+00:00",
        }
        context_manager = mock.AsyncMock()
        context_manager.__aenter__.return_value = mock.AsyncMock()
        mock_client.return_value = context_manager

        existing_query = mock.Mock()
        existing_query.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [
            {"id": "lead-a", "status": "qualified"}
        ]
        save_query = mock.Mock()
        save_query.upsert.return_value.execute.return_value.data = []
        user_db = mock.Mock()
        user_db.table.side_effect = [existing_query, save_query]
        mock_get_user_db.return_value = user_db
        mock_get_db.return_value = mock.Mock()

        user = AuthenticatedUser(id="user-a", access_token="token-a")
        result = await scan(ScanRequest(url="https://example.com"), user)

        payload = save_query.upsert.call_args.args[0]
        self.assertEqual(payload["user_id"], "user-a")
        self.assertEqual(payload["status"], "qualified")
        self.assertEqual(payload["data_quality_score"], 95)
        self.assertEqual(payload["validation_status"], "passed")
        self.assertEqual(save_query.upsert.call_args.kwargs["on_conflict"], "user_id,domain")
        self.assertEqual(result["lead"]["user_id"], "user-a")


class AutomaticValidationTests(unittest.IsolatedAsyncioTestCase):
    @mock.patch("services.lead_validation.validate_lead_emails")
    @mock.patch(
        "services.lead_validation.validate_website_reachability",
        new_callable=mock.AsyncMock,
    )
    async def test_automatic_validation_marks_a_reachable_lead_as_passed(
        self, mock_website_check, mock_email_checks
    ):
        mock_website_check.return_value = {
            "status": "reachable",
            "reason": "Website is reachable.",
            "final_url": "https://example.com/",
        }
        mock_email_checks.return_value = [{"status": "found"}]

        validation = await build_lead_validation(
            "https://example.com/",
            ["hello@example.com"],
            [{"status": "accept_all", "score": 85}],
        )

        self.assertEqual(validation["data_quality_score"], 100)
        self.assertEqual(validation["validation_status"], "passed")
        self.assertIn("raw total 105/100, capped at 100", validation["validation_summary"])
        self.assertEqual(validation["validation_details"]["raw_score"], 105)
        self.assertEqual(validation["validation_details"]["final_score"], 100)
        self.assertTrue(
            any(
                "High Hunter email-verification confidence" in reason
                for reason in validation["validation_details"]["score_reasons"]
            )
        )


if __name__ == "__main__":
    unittest.main()
