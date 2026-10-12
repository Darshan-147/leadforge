"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Search,
  Radar,
  Download,
  Globe,
  Mail,
  Phone,
  Users,
  TrendingUp,
  RefreshCw,
  ExternalLink,
  CheckCircle2,
  Filter,
  Facebook,
  Instagram,
  Linkedin,
  Youtube,
  Twitter,
  ChevronDown,
  type LucideIcon,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { createClient } from "@/lib/supabase/client";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";

const API = (
  process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000"
).replace(/\/$/, "");

async function authenticatedFetch(input: string, init?: RequestInit) {
  const {
    data: { session },
  } = await createClient().auth.getSession();
  if (!session?.access_token) {
    window.location.assign("/login");
    throw new Error("Your session has expired. Please sign in again.");
  }
  const headers = new Headers(init?.headers);
  headers.set("Authorization", `Bearer ${session.access_token}`);
  return fetch(input, { ...init, headers });
}

export function formatApiErrorDetail(detail: unknown): string | null {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    const messages = detail.map((item) => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return null;

      const error = item as { loc?: unknown[]; msg?: unknown };
      const message = typeof error.msg === "string" ? error.msg : null;
      const location = Array.isArray(error.loc)
        ? error.loc.filter(
            (part): part is string | number =>
              typeof part === "string" || typeof part === "number",
          )
        : [];
      const field = location.filter((part) => part !== "body").join(".");

      if (message && field) return `${field}: ${message}`;
      return message;
    });
    const formatted = messages.filter((message): message is string =>
      Boolean(message),
    );
    if (formatted.length) return formatted.join("; ");
  }
  if (detail && typeof detail === "object") {
    const error = detail as { message?: unknown; msg?: unknown };
    if (typeof error.message === "string") return error.message;
    if (typeof error.msg === "string") return error.msg;
  }
  return null;
}

type Lead = {
  id: string;
  domain: string;
  website_url: string;
  company_name: string | null;
  description: string | null;
  emails: string[];
  phones: string[];
  social_links: Record<string, string[]>;
  lead_score: number;
  score_reasons: string[];
  status: string;
  data_quality_score?: number | null;
  validation_status?: "passed" | "warning" | "failed" | null;
  validation_summary?: string | null;
  validation_last_run_at?: string | null;
  created_at?: string;
};

type Stats = {
  total: number;
  qualified: number;
  with_email: number;
  high_score: number;
};

type Candidate = {
  title: string;
  url: string;
  domain: string;
  snippet: string;
};

function scoreColor(score: number) {
  if (score >= 70) return "bg-emerald-50 text-emerald-700 ring-emerald-200";
  if (score >= 40) return "bg-amber-50 text-amber-700 ring-amber-200";
  return "bg-slate-100 text-slate-600 ring-slate-200";
}

function validationStatusColor(status: Lead["validation_status"]) {
  switch (status) {
    case "passed":
      return "bg-emerald-50 text-emerald-700 ring-emerald-200";
    case "warning":
      return "bg-amber-50 text-amber-700 ring-amber-200";
    case "failed":
      return "bg-rose-50 text-rose-700 ring-rose-200";
    default:
      return "bg-slate-100 text-slate-600 ring-slate-200";
  }
}

function validationStatusLabel(status: Lead["validation_status"]) {
  switch (status) {
    case "passed":
      return "Passed";
    case "warning":
      return "Warning";
    case "failed":
      return "Failed";
    default:
      return "Not checked";
  }
}

function formatSocialPlatform(platform: string) {
  return platform
    .replace(/[-_]/g, " ")
    .replace(/\b\w/g, (char) => char.toUpperCase());
}

const CHALLENGE_COMPANY_TITLES = new Set([
  "one moment please",
  "just a moment",
  "checking your browser",
  "verify you are human",
  "attention required cloudflare",
]);

export function getCompanyDisplayName(companyName: string | null) {
  const normalized = (companyName || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();

  return !companyName || CHALLENGE_COMPANY_TITLES.has(normalized)
    ? "Company name unavailable"
    : companyName;
}

const socialPlatformIcons: Record<string, LucideIcon> = {
  facebook: Facebook,
  instagram: Instagram,
  linkedin: Linkedin,
  youtube: Youtube,
  twitter: Twitter,
  x: Twitter,
};

export function getSocialPlatformIcon(platform: string) {
  const normalizedPlatform = platform.toLowerCase().replace(/[^a-z0-9]/g, "");
  return socialPlatformIcons[normalizedPlatform] ?? Globe;
}

export default function Home() {
  const [leads, setLeads] = useState<Lead[]>([]);
  const [stats, setStats] = useState<Stats>({
    total: 0,
    qualified: 0,
    with_email: 0,
    high_score: 0,
  });
  const [url, setUrl] = useState("");
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [minScore, setMinScore] = useState("0");
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [validatingLeadId, setValidatingLeadId] = useState<string | null>(null);

  // Company discovery form
  const [industry, setIndustry] = useState("");
  const [location, setLocation] = useState("");
  const [keywords, setKeywords] = useState("");
  const [country, setCountry] = useState("IN");
  const [discoveryLimit, setDiscoveryLimit] = useState("10");

  // Search results and loading state
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [enrichingDomain, setEnrichingDomain] = useState("");

  // Track candidates enriched during this page session
  const [enrichedDomains, setEnrichedDomains] = useState<string[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({
        search,
        status,
        min_score: minScore,
        limit: "200",
      });
      const [lr, sr] = await Promise.all([
        authenticatedFetch(`${API}/api/leads?${params}`),
        authenticatedFetch(`${API}/api/stats`),
      ]);
      if (!lr.ok) throw new Error(await lr.text());
      if (!sr.ok) throw new Error(await sr.text());
      const ldata = await lr.json();
      const sdata = await sr.json();
      setLeads(ldata.leads || []);
      setStats(sdata);
    } catch (e) {
      toast.error(
        `Could not load leads. Check that the API is running and configured. ${e instanceof Error ? e.message : ""}`,
      );
    } finally {
      setLoading(false);
    }
  }, [search, status, minScore]);

  useEffect(() => {
    void load();
  }, [load]);

  async function scan() {
    if (!url.trim()) {
      toast.error("Enter a public website URL first.");
      return;
    }
    setScanning(true);

    try {
      const response = await authenticatedFetch(`${API}/api/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });
      const responseBody = await response.text();
      let data: {
        detail?: unknown;
        message?: string;
        pages_checked?: number;
        lead?: { domain?: string };
      } = {};
      if (responseBody) {
        try {
          data = JSON.parse(responseBody);
        } catch {
          if (!response.ok) {
            throw new Error(
              `Scan failed (${response.status}): ${responseBody}`,
            );
          }
          throw new Error("The API returned an invalid response.");
        }
      }
      if (!response.ok) {
        throw new Error(
          formatApiErrorDetail(data.detail) ||
            `Scan failed (${response.status}).`,
        );
      }
      toast.success(
        `${data.message || "Lead saved."} Checked ${data.pages_checked ?? 0} page(s) on ${data.lead?.domain || "the website"}.`,
      );
      setUrl("");
      await load();
    } catch (e) {
      toast.error(
        e instanceof TypeError && e.message === "Failed to fetch"
          ? `Could not reach the LeadForge API at ${API}. Check that the API is running and that FRONTEND_ORIGIN allows this app's origin.`
          : e instanceof Error
            ? e.message
            : "Unable to scan this website.",
      );
    } finally {
      setScanning(false);
    }
  }

  async function discoverCompanies() {
    setCandidates([]);

    if (industry.trim().length < 2) {
      toast.error("Please enter an industry to search.");
      return;
    }

    setDiscovering(true);

    try {
      const response = await authenticatedFetch(`${API}/api/discover`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          industry: industry.trim(),
          location: location.trim(),
          keywords: keywords.trim(),
          country,
          limit: Number(discoveryLimit),
        }),
      });

      const data = (await response.json()) as {
        detail?: string;
        count?: number;
        candidates?: Candidate[];
        message?: string;
      };

      if (!response.ok) {
        throw new Error(data.detail || "Company discovery failed.");
      }

      setCandidates(data.candidates ?? []);

      if (data.count) {
        toast.success(
          `Found ${data.count} candidate website(s). Review them below.`,
        );
      } else {
        toast.warning("No candidates found. Try broader search terms.");
      }
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not discover companies.",
      );
    } finally {
      setDiscovering(false);
    }
  }

  async function enrichCandidate(candidate: Candidate) {
    setEnrichingDomain(candidate.domain);

    try {
      const response = await authenticatedFetch(`${API}/api/scan`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          url: candidate.url,
        }),
      });

      const data = (await response.json()) as {
        detail?: string;
        message?: string;
        lead?: Lead;
        pages_checked?: number;
      };

      if (!response.ok) {
        throw new Error(data.detail || "Could not enrich this website.");
      }

      setEnrichedDomains((previous) =>
        previous.includes(candidate.domain)
          ? previous
          : [...previous, candidate.domain],
      );

      toast.success(`${candidate.domain} was enriched and saved successfully.`);

      // Refresh the existing lead table and dashboard statistics.
      await load();
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not save this candidate.",
      );
    } finally {
      setEnrichingDomain("");
    }
  }

  async function updateStatus(lead: Lead, next: string) {
    try {
      const response = await authenticatedFetch(
        `${API}/api/leads/${lead.id}/status`,
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status: next }),
        },
      );
      if (!response.ok) throw new Error("Status update failed.");
      await load();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Status update failed.");
    }
  }

  async function validateLead(lead: Lead) {
    setValidatingLeadId(lead.id);

    try {
      const response = await authenticatedFetch(
        `${API}/api/leads/${lead.id}/validate`,
        {
          method: "POST",
        },
      );
      const data = (await response.json().catch(() => ({}))) as {
        detail?: string;
        validation?: {
          status?: "passed" | "warning" | "failed";
          score?: number;
          summary?: string;
        };
      };

      if (!response.ok) {
        throw new Error(data.detail || "Lead validation failed.");
      }

      const validationMessage = `${lead.company_name || lead.domain}: ${data.validation?.summary || "Validation complete."}`;

      if (data.validation?.status === "warning") {
        toast.warning(validationMessage);
      } else if (data.validation?.status === "failed") {
        toast.error(validationMessage);
      } else {
        toast.success(validationMessage);
      }
      await load();
    } catch (e) {
      toast.error(
        e instanceof Error ? e.message : "Lead validation request failed.",
      );
    } finally {
      setValidatingLeadId(null);
    }
  }

  const visibleLeads = useMemo(() => leads, [leads]);

  async function exportCsv() {
    setExporting(true);

    try {
      const response = await authenticatedFetch(`${API}/api/exports/leads`, {
        method: "GET",
      });

      const data = (await response.json()) as {
        detail?: string;
        download_url?: string;
        expires_in_seconds?: number;
        rows_exported?: number;
      };

      if (!response.ok) {
        throw new Error(data.detail || `Export failed (${response.status}).`);
      }

      if (!data.download_url) {
        throw new Error("The API did not return a download URL.");
      }

      // Open the temporary S3 download URL.
      window.location.assign(data.download_url);

      toast.success(`Export ready: ${data.rows_exported ?? 0} lead(s).`);
    } catch (error) {
      toast.error(
        error instanceof Error
          ? error.message
          : "Could not export leads to AWS S3.",
      );
    } finally {
      setExporting(false);
    }
  }

  async function logout() {
    await createClient().auth.signOut();
    window.location.assign("/login");
  }

  return (
    <main className="min-h-screen">
      <Toaster position="top-center" duration={6000} closeButton richColors />
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-7xl items-center justify-between px-5 py-4 lg:px-8">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-indigo-600 text-white">
              <Radar size={22} />
            </div>
            <div>
              <div className="text-lg font-bold tracking-tight">LeadForge</div>
              <div className="text-xs text-slate-500">
                Lead intelligence workspace
              </div>
            </div>
          </div>

          <button
            onClick={() => void logout()}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50"
          >
            Sign out
          </button>
        </div>
      </header>

      <div className="mx-auto max-w-7xl px-5 py-8 lg:px-8">
        <div className="mb-8 flex flex-col justify-between gap-4 md:flex-row md:items-end">
          <div>
            <p className="mb-2 text-sm font-semibold uppercase tracking-[.16em] text-indigo-600">
              Prospecting, made actionable
            </p>
            <h1 className="text-3xl font-bold tracking-tight text-slate-950 md:text-4xl">
              Find the right leads.
              <br className="hidden sm:block" /> Spend less time cleaning data.
            </h1>
            <p className="mt-3 max-w-2xl text-sm leading-6 text-slate-600">
              Discover public business information, prioritize leads with
              transparent signals, and keep your pipeline organized in one
              place.
            </p>
          </div>
          <button
            onClick={() => void load()}
            className="inline-flex h-10 items-center justify-center gap-2 self-start rounded-lg border border-slate-200 bg-white px-4 text-sm font-semibold hover:bg-slate-50 md:self-auto"
          >
            <RefreshCw size={15} /> Refresh
          </button>
        </div>

        <section className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
          {[
            {
              label: "Total leads",
              value: stats.total,
              icon: Users,
              note: "Unique company domains",
            },
            {
              label: "Email available",
              value: stats.with_email,
              icon: Mail,
              note: "Contact signal found",
            },
            {
              label: "High-priority",
              value: stats.high_score,
              icon: TrendingUp,
              note: "Score of 70 or higher",
            },
            {
              label: "Qualified",
              value: stats.qualified,
              icon: CheckCircle2,
              note: "Marked by your team",
            },
          ].map((item) => (
            <div
              key={item.label}
              className="rounded-2xl border border-slate-200 bg-white p-5 shadow-sm"
            >
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium text-slate-500">
                  {item.label}
                </span>
                <item.icon size={18} className="text-indigo-600" />
              </div>
              <div className="mt-3 text-3xl font-bold tracking-tight">
                {item.value}
              </div>
              <div className="mt-1 text-xs text-slate-500">{item.note}</div>
            </div>
          ))}
        </section>

        <Card className="mb-6">
          <CardHeader>
            <CardTitle>Discover companies</CardTitle>
            <CardDescription>
              Find potential businesses by industry and location. Review each
              website before enriching it.
            </CardDescription>
          </CardHeader>

          <CardContent>
            <form
              onSubmit={(event) => {
                event.preventDefault();
                void discoverCompanies();
              }}
              className="grid gap-4 md:grid-cols-6"
            >
              <div className="space-y-2 md:col-span-3">
                <label htmlFor="industry" className="text-sm font-medium">
                  Industry *
                </label>
                <Input
                  id="industry"
                  value={industry}
                  onChange={(event) => setIndustry(event.target.value)}
                  placeholder="e.g. HVAC contractors"
                  maxLength={100}
                  required
                />
              </div>

              <div className="space-y-2 md:col-span-3">
                <label htmlFor="location" className="text-sm font-medium">
                  Location
                </label>
                <Input
                  id="location"
                  value={location}
                  onChange={(event) => setLocation(event.target.value)}
                  placeholder="e.g. Dallas, Texas"
                  maxLength={100}
                />
              </div>

              <div className="space-y-2 md:col-span-2">
                <label htmlFor="keywords" className="text-sm font-medium">
                  Additional keywords
                </label>
                <Input
                  id="keywords"
                  value={keywords}
                  onChange={(event) => setKeywords(event.target.value)}
                  placeholder="e.g. commercial, family-owned"
                  maxLength={100}
                />
              </div>

              <div className="space-y-2 md:col-span-2">
                <label htmlFor="country" className="text-sm font-medium">
                  Country
                </label>
                <div className="relative">
                  <select
                    id="country"
                    value={country}
                    onChange={(event) => setCountry(event.target.value)}
                    className="h-10 w-full appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <option value="IN">India</option>
                    <option value="US">United States</option>
                    <option value="GB">United Kingdom</option>
                    <option value="CA">Canada</option>
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-slate-500" />
                </div>
              </div>

              <div className="space-y-2 md:col-span-2">
                <label
                  htmlFor="discovery-limit"
                  className="text-sm font-medium"
                >
                  Limit results to
                </label>
                <div className="relative">
                  <select
                    id="discovery-limit"
                    value={discoveryLimit}
                    onChange={(event) => setDiscoveryLimit(event.target.value)}
                    className="h-10 w-full appearance-none rounded-md border border-input bg-background px-3 pr-9 text-sm shadow-xs outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    <option value="5">5 results</option>
                    <option value="10">10 results</option>
                    <option value="15">15 results</option>
                    <option value="20">20 results</option>
                  </select>
                  <ChevronDown className="pointer-events-none absolute right-3 top-1/2 size-4 -translate-y-1/2 text-slate-500" />
                </div>
              </div>

              <div className="md:col-span-6">
                <Button
                  type="submit"
                  disabled={discovering || industry.trim().length < 2}
                >
                  <Search className="mr-2 h-4 w-4" />
                  {discovering ? "Searching..." : "Find companies"}
                </Button>
                <p className="mt-2 text-xs text-muted-foreground">
                  Up to {discoveryLimit} search candidates per request. Results
                  may include directories or irrelevant websites.
                </p>
              </div>
            </form>

            {candidates.length > 0 && (
              <div className="mt-6 space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="font-semibold">Candidate websites</h3>
                  <Badge variant="secondary">{candidates.length} found</Badge>
                </div>

                {candidates.map((candidate) => {
                  const added = enrichedDomains.includes(candidate.domain);
                  const isEnriching = enrichingDomain === candidate.domain;

                  return (
                    <div
                      key={candidate.domain}
                      className="flex flex-col gap-4 rounded-lg border p-4 sm:flex-row sm:items-start sm:justify-between"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <h4 className="font-semibold">{candidate.title}</h4>
                          {added && (
                            <Badge variant="secondary">Added to leads</Badge>
                          )}
                        </div>

                        <p className="mt-1 break-all text-xs text-muted-foreground">
                          {candidate.domain}
                        </p>

                        <p className="mt-2 text-sm text-muted-foreground">
                          {candidate.snippet || "No description available."}
                        </p>

                        <a
                          href={candidate.url}
                          target="_blank"
                          rel="noreferrer"
                          className="mt-3 inline-flex items-center gap-1 text-sm text-primary underline underline-offset-4"
                        >
                          Review website
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      </div>

                      <Button
                        type="button"
                        size="sm"
                        variant={added ? "outline" : "default"}
                        disabled={added || enrichingDomain !== ""}
                        onClick={() => void enrichCandidate(candidate)}
                      >
                        {isEnriching
                          ? "Enriching..."
                          : added
                            ? "Added"
                            : "Enrich & Save"}
                      </Button>
                    </div>
                  );
                })}
              </div>
            )}
          </CardContent>
        </Card>

        <section className="mb-6 rounded-2xl border border-slate-200 bg-white p-5 shadow-sm md:p-6">
          <div className="mb-4 flex items-start gap-3">
            <div className="rounded-xl bg-indigo-50 p-2.5 text-indigo-700">
              <Globe size={20} />
            </div>
            <div>
              <h2 className="font-bold text-slate-900">Discover a business</h2>
              <p className="mt-1 text-sm text-slate-500">
                Submit a public company website. We’ll check a small number of
                relevant pages.
              </p>
            </div>
          </div>
          <div className="flex flex-col gap-3 sm:flex-row">
            <input
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !scanning) void scan();
              }}
              placeholder="https://company.com"
              type="url"
              className="h-12 min-w-0 flex-1 rounded-xl border border-slate-200 bg-slate-50 px-4 text-sm outline-none transition focus:border-indigo-400 focus:bg-white focus:ring-4 focus:ring-indigo-50"
            />
            <button
              disabled={scanning}
              onClick={() => void scan()}
              className="inline-flex h-12 items-center justify-center gap-2 rounded-xl bg-indigo-600 px-5 text-sm font-semibold text-white transition hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {scanning ? (
                <RefreshCw className="animate-spin" size={17} />
              ) : (
                <Search size={17} />
              )}{" "}
              {scanning ? "Discovering..." : "Discover leads"}
            </button>
          </div>
          <p className="mt-3 text-xs text-slate-500">
            Only public pages. No login bypass, CAPTCHA bypass, or
            access-control circumvention.
          </p>
        </section>

        <section className="overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-sm">
          <div className="flex flex-col gap-4 border-b border-slate-200 p-5 md:flex-row md:items-center md:justify-between">
            <div>
              <h2 className="font-bold text-slate-900">Lead pipeline</h2>
              <p className="mt-1 text-sm text-slate-500">
                {visibleLeads.length} result(s) · sorted by newest first
              </p>
            </div>
            <button
              onClick={() => void exportCsv()}
              disabled={!visibleLeads.length || exporting}
              className="inline-flex h-10 items-center justify-center gap-2 rounded-lg border border-slate-200 px-4 text-sm font-semibold hover:bg-slate-50 disabled:opacity-40"
            >
              <Download size={16} />
              {exporting ? "Exporting..." : "Export CSV"}
            </button>
          </div>
          <div className="grid gap-3 border-b border-slate-100 bg-slate-50/70 p-4 md:grid-cols-[1fr_180px_180px]">
            <div className="relative">
              <Search
                size={16}
                className="absolute left-3 top-3.5 text-slate-400"
              />
              <input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search company, domain, description..."
                className="h-11 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm outline-none focus:border-indigo-400"
              />
            </div>
            <div className="relative">
              <Filter
                size={15}
                className="absolute left-3 top-3.5 text-slate-400"
              />
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="h-11 w-full rounded-lg border border-slate-200 bg-white pl-9 pr-3 text-sm"
              >
                <option value="all">All statuses</option>
                <option value="new">New</option>
                <option value="reviewed">Reviewed</option>
                <option value="qualified">Qualified</option>
                <option value="disqualified">Disqualified</option>
              </select>
            </div>
            <select
              value={minScore}
              onChange={(e) => setMinScore(e.target.value)}
              className="h-11 rounded-lg border border-slate-200 bg-white px-3 text-sm"
            >
              <option value="0">Any score</option>
              <option value="40">Score 40+</option>
              <option value="70">Score 70+</option>
              <option value="85">Score 85+</option>
            </select>
          </div>
          {loading ? (
            <div className="p-12 text-center text-sm text-slate-500">
              Loading lead pipeline…
            </div>
          ) : visibleLeads.length === 0 ? (
            <div className="p-12 text-center">
              <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-slate-100 text-slate-500">
                <Search size={22} />
              </div>
              <p className="font-semibold text-slate-800">No leads found</p>
              <p className="mt-1 text-sm text-slate-500">
                Discover a business website or adjust your filters.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-212.5 text-left text-sm">
                <thead className="bg-white text-xs uppercase tracking-wide text-slate-500">
                  <tr>
                    <th className="px-5 py-3 font-semibold">Company</th>
                    <th className="px-5 py-3 font-semibold">Contact signals</th>
                    <th className="px-5 py-3 font-semibold">Social links</th>
                    <th className="px-5 py-3 font-semibold">Lead score</th>
                    <th className="px-5 py-3 font-semibold">Status</th>
                    <th className="px-5 py-3 font-semibold">Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-100">
                  {visibleLeads.map((lead) => {
                    const socialLinks = Object.entries(
                      lead.social_links ?? {},
                    ).flatMap(([platform, urls]) =>
                      urls.map((url) => ({
                        platform: formatSocialPlatform(platform),
                        url,
                      })),
                    );

                    return (
                      <tr
                        key={lead.id}
                        className="align-top transition hover:bg-slate-50/80"
                      >
                        <td className="px-5 py-4">
                          <div className="font-semibold text-slate-900">
                            {getCompanyDisplayName(lead.company_name)}
                          </div>
                          <div className="mt-1 text-xs text-slate-500">
                            {lead.domain}
                          </div>
                          {lead.description && (
                            <p className="mt-2 max-w-sm text-xs leading-5 text-slate-600">
                              {lead.description}
                            </p>
                          )}
                        </td>
                        <td className="px-5 py-4">
                          <div className="flex max-w-xs flex-col gap-2 text-xs text-slate-600">
                            {lead.emails?.length ? (
                              <span className="flex items-start gap-1.5">
                                <Mail
                                  size={14}
                                  className="mt-0.5 shrink-0 text-slate-400"
                                />
                                <span className="break-all">
                                  {lead.emails.slice(0, 2).join(", ")}
                                  {lead.emails.length > 2 && (
                                    <span
                                      className="cursor-help"
                                      title={lead.emails.slice(2).join("\n")}
                                    >
                                      {" "}
                                      +{lead.emails.length - 2}
                                    </span>
                                  )}
                                </span>
                              </span>
                            ) : (
                              <span className="text-slate-400">
                                No email found
                              </span>
                            )}
                            {lead.phones?.length ? (
                              <span className="flex items-center gap-1.5">
                                <Phone
                                  size={14}
                                  className="shrink-0 text-slate-400"
                                />
                                {lead.phones.slice(0, 2).join(", ")}
                                {lead.phones.length > 2 && (
                                  <span
                                    className="cursor-help"
                                    title={lead.phones.slice(2).join("\n")}
                                  >
                                    {" "}
                                    +{lead.phones.length - 2}
                                  </span>
                                )}
                              </span>
                            ) : (
                              <span className="text-slate-400">
                                No phone number found
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="px-5 py-4">
                          {socialLinks.length ? (
                            <div className="flex max-w-[180px] flex-wrap items-center gap-1.5">
                              {socialLinks
                                .slice(0, 4)
                                .map(({ platform, url }) => {
                                  const SocialIcon =
                                    getSocialPlatformIcon(platform);

                                  return (
                                    <a
                                      key={`${platform}-${url}`}
                                      href={url}
                                      target="_blank"
                                      rel="noreferrer"
                                      aria-label={`${platform}: ${url}`}
                                      className="inline-flex size-8 items-center justify-center rounded-md border border-slate-200 text-slate-600 hover:border-indigo-200 hover:text-indigo-700"
                                      title={`${platform}: ${url}`}
                                    >
                                      <SocialIcon
                                        size={16}
                                        aria-hidden="true"
                                      />
                                    </a>
                                  );
                                })}
                              {socialLinks.length > 4 && (
                                <span
                                  className="cursor-help"
                                  title={socialLinks
                                    .slice(4)
                                    .map(
                                      (link) => `${link.platform}: ${link.url}`,
                                    )
                                    .join("\n")}
                                >
                                  +{socialLinks.length - 4}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span className="text-slate-400">
                              No social links
                            </span>
                          )}
                        </td>
                        <td className="px-5 py-4">
                          <span
                            title={lead.score_reasons?.join("\n")}
                            className={`inline-flex rounded-full px-2.5 py-1 text-xs font-bold ring-1 ring-inset ${scoreColor(lead.lead_score)}`}
                          >
                            {lead.lead_score}/100
                          </span>
                          <div className="mt-2 max-w-40 text-[11px] leading-4 text-slate-500">
                            {lead.score_reasons?.slice(0, 2).join(" · ")}
                          </div>
                        </td>
                        <td className="px-5 py-4">
                          <div className="flex flex-col gap-2">
                            <select
                              aria-label={`Status for ${lead.company_name || lead.domain}`}
                              value={lead.status}
                              onChange={(e) =>
                                void updateStatus(lead, e.target.value)
                              }
                              className="rounded-lg border border-slate-200 bg-white px-2 py-2 text-xs"
                            >
                              <option value="new">New</option>
                              <option value="reviewed">Reviewed</option>
                              <option value="qualified">Qualified</option>
                              <option value="disqualified">Disqualified</option>
                            </select>
                            <button
                              type="button"
                              onClick={() => void validateLead(lead)}
                              disabled={validatingLeadId === lead.id}
                              className="inline-flex items-center justify-center rounded-lg border border-indigo-200 bg-indigo-50 px-2.5 py-1.5 text-[11px] font-semibold text-indigo-700 transition hover:bg-indigo-100 disabled:cursor-not-allowed disabled:opacity-60"
                            >
                              {validatingLeadId === lead.id
                                ? "Validating..."
                                : "Validate"}
                            </button>
                            <div className="flex flex-wrap items-center gap-1.5">
                              <span
                                className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-bold ring-1 ring-inset ${validationStatusColor(lead.validation_status)}`}
                              >
                                {validationStatusLabel(lead.validation_status)}
                              </span>
                              {lead.data_quality_score != null && (
                                <span className="text-[10px] font-semibold text-slate-500">
                                  {lead.data_quality_score}/100
                                </span>
                              )}
                            </div>
                            {lead.validation_summary && (
                              <div className="max-w-40 text-[11px] leading-4 text-slate-500">
                                {lead.validation_summary}
                              </div>
                            )}
                          </div>
                        </td>
                        <td className="px-5 py-4">
                          <a
                            href={lead.website_url}
                            target="_blank"
                            rel="noreferrer"
                            className="inline-flex items-center gap-1.5 text-xs font-semibold text-indigo-700 hover:text-indigo-900"
                          >
                            <ExternalLink size={13} /> Open website
                          </a>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
          <div className="flex items-center justify-between border-t border-slate-100 px-5 py-3 text-xs text-slate-500">
            <span>Showing up to 200 matching records</span>
            <span>LeadForge</span>
          </div>
        </section>
        <p className="mt-5 text-center text-xs leading-5 text-slate-500">
          Scores are heuristic quality signals, not guaranteed sales outcomes.
          Verify information before outreach.
        </p>
      </div>
    </main>
  );
}
