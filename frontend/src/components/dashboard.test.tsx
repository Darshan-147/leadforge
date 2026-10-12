import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { auth, createClient, toast } = vi.hoisted(() => {
  const auth = { getSession: vi.fn(), signOut: vi.fn() };
  return {
    auth,
    createClient: vi.fn(() => ({ auth })),
    toast: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  };
});

vi.mock("@/lib/supabase/client", () => ({ createClient }));
vi.mock("@/components/ui/sonner", () => ({ Toaster: () => null }));
vi.mock("sonner", () => ({ toast }));

import Dashboard, {
  formatApiErrorDetail,
  getCompanyDisplayName,
} from "./dashboard";

function jsonResponse(body: unknown, ok = true, status = 200) {
  return {
    ok,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response;
}

describe("dashboard helpers", () => {
  it("formats FastAPI validation details and protects challenge titles", () => {
    expect(formatApiErrorDetail([{ loc: ["body", "url"], msg: "invalid URL" }])).toBe("url: invalid URL");
    expect(formatApiErrorDetail({ message: "Unexpected response" })).toBe("Unexpected response");
    expect(getCompanyDisplayName("One moment, please...")).toBe("Company name unavailable");
    expect(getCompanyDisplayName("Acme Ltd")).toBe("Acme Ltd");
  });
});

describe("Dashboard", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    auth.getSession.mockResolvedValue({ data: { session: { access_token: "session-token" } } });
    auth.signOut.mockResolvedValue({ error: null });
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.includes("/api/stats")) {
          return Promise.resolve(jsonResponse({ total: 1, qualified: 0, with_email: 1, high_score: 1 }));
        }
        if (url.includes("/api/leads")) {
          return Promise.resolve(jsonResponse({ leads: [{ id: "lead-1", domain: "acme.example", website_url: "https://acme.example", company_name: "Acme", description: null, emails: ["hello@acme.example"], phones: [], social_links: {}, lead_score: 80, score_reasons: [], status: "new" }] }));
        }
        return Promise.resolve(jsonResponse({}));
      }),
    );
  });

  it("loads private leads using the Supabase access token", async () => {
    render(<Dashboard />);

    expect(await screen.findByText("Acme")).toBeInTheDocument();
    const calls = vi.mocked(fetch).mock.calls;
    expect(calls).toHaveLength(2);
    for (const [, init] of calls) {
      expect(new Headers(init?.headers).get("Authorization")).toBe("Bearer session-token");
    }
  });

  it("validates a blank website before issuing a scan request", async () => {
    render(<Dashboard />);
    await screen.findByText("Acme");

    await userEvent.click(screen.getByRole("button", { name: "Discover leads" }));

    expect(toast.error).toHaveBeenCalledWith("Enter a public website URL first.");
    expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  });

  it("submits a discovery request and renders returned candidates", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/discover")) {
        return Promise.resolve(jsonResponse({ count: 1, candidates: [{ title: "Beta Services", url: "https://beta.example", domain: "beta.example", snippet: "A local provider" }] }));
      }
      if (url.includes("/api/stats")) return Promise.resolve(jsonResponse({ total: 0, qualified: 0, with_email: 0, high_score: 0 }));
      return Promise.resolve(jsonResponse({ leads: [] }));
    });
    render(<Dashboard />);
    await screen.findByText("Lead pipeline");

    await userEvent.type(screen.getByLabelText("Industry *"), "HVAC");
    fireEvent.submit(screen.getByRole("button", { name: "Find companies" }).closest("form")!);

    expect(await screen.findByText("Beta Services")).toBeInTheDocument();
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Found 1 candidate website(s). Review them below."));
  });

  it("scans a submitted website and refreshes the saved lead list", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/scan")) {
        return Promise.resolve(jsonResponse({ message: "Lead saved.", pages_checked: 2, lead: { domain: "beta.example" } }));
      }
      if (url.includes("/api/stats")) return Promise.resolve(jsonResponse({ total: 1, qualified: 0, with_email: 0, high_score: 0 }));
      return Promise.resolve(jsonResponse({ leads: [] }));
    });
    render(<Dashboard />);
    await screen.findByText("No leads found");

    await userEvent.type(screen.getByPlaceholderText("https://company.com"), "https://beta.example");
    await userEvent.click(screen.getByRole("button", { name: "Discover leads" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Lead saved. Checked 2 page(s) on beta.example."));
    expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/api/scan"))).toBe(true);
  });

  it("updates a lead status and reports a successful validation", async () => {
    vi.mocked(fetch).mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/status")) return Promise.resolve(jsonResponse({ lead: { id: "lead-1", status: "qualified" } }));
      if (url.includes("/validate")) return Promise.resolve(jsonResponse({ validation: { status: "passed", summary: "Website reachable" } }));
      if (url.includes("/api/stats")) return Promise.resolve(jsonResponse({ total: 1, qualified: 1, with_email: 1, high_score: 1 }));
      return Promise.resolve(jsonResponse({ leads: [{ id: "lead-1", domain: "acme.example", website_url: "https://acme.example", company_name: "Acme", description: null, emails: [], phones: [], social_links: {}, lead_score: 80, score_reasons: [], status: "new" }] }));
    });
    render(<Dashboard />);
    await screen.findByText("Acme");

    await userEvent.selectOptions(screen.getByLabelText("Status for Acme"), "qualified");
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([url]) => String(url).includes("/status"))).toBe(true));
    await userEvent.click(screen.getByRole("button", { name: "Validate" }));

    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Acme: Website reachable"));
  });
});
