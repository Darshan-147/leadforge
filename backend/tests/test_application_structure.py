"""Regression checks for application composition and deployment entry points."""

import os
import sys
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from api.index import app as vercel_app
from main import app, create_application


class ApplicationStructureTests(unittest.TestCase):
    def test_application_factory_keeps_every_public_route_and_method(self):
        route_methods = {
            (route.path, method)
            for route in create_application().routes
            if hasattr(route, "methods")
            for method in route.methods
        }
        expected_routes = {
            ("/health", "GET"),
            ("/api/discover", "POST"),
            ("/api/scan", "POST"),
            ("/api/leads", "GET"),
            ("/api/leads/{lead_id}/status", "PATCH"),
            ("/api/leads/{lead_id}/validate", "POST"),
            ("/api/stats", "GET"),
            ("/api/exports/leads", "GET"),
        }
        self.assertTrue(expected_routes.issubset(route_methods))

    def test_vercel_adapter_imports_the_composed_application(self):
        self.assertIs(vercel_app, app)

    def test_protected_routes_keep_authentication_boundary(self):
        client = TestClient(app)
        requests = [
            ("post", "/api/discover", {"json": {"industry": "HVAC"}}),
            ("post", "/api/scan", {"json": {"url": "https://example.com"}}),
            ("get", "/api/leads", {}),
            ("patch", "/api/leads/lead-a/status", {"json": {"status": "new"}}),
            ("post", "/api/leads/lead-a/validate", {}),
            ("get", "/api/stats", {}),
            ("get", "/api/exports/leads", {}),
        ]
        for method, path, kwargs in requests:
            with self.subTest(method=method, path=path):
                response = getattr(client, method)(path, **kwargs)
                self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
