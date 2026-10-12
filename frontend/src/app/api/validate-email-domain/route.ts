import { NextResponse } from "next/server";
import { domainToASCII } from "node:url";
import { resolveMx, resolve4, resolve6 } from "node:dns/promises";

export const runtime = "nodejs";

async function domainHasDnsRecords(domain: string): Promise<boolean> {
  // Prefer MX records, which are used for email delivery.
  try {
    const records = await resolveMx(domain);
    if (records.length > 0) return true;
  } catch {
    // Some domains have no MX records, so check address records too.
  }

  try {
    const records = await resolve4(domain);
    if (records.length > 0) return true;
  } catch {}

  try {
    const records = await resolve6(domain);
    if (records.length > 0) return true;
  } catch {}

  return false;
}

export async function POST(request: Request) {
  try {
    const body = await request.json();
    const email = typeof body.email === "string" ? body.email.trim() : "";

    const parts = email.split("@");

    if (parts.length !== 2 || !parts[0] || !parts[1] || /\s/.test(email)) {
      return NextResponse.json({
        valid: false,
        message: "Enter a valid email address.",
      });
    }

    const domain = domainToASCII(parts[1].toLowerCase());

    if (!domain || domain.length > 253) {
      return NextResponse.json({
        valid: false,
        message: "Enter an email address with a valid domain.",
      });
    }

    const labels = domain.split(".");
    const validDomainFormat =
      labels.length >= 2 &&
      labels.every(
        (label) =>
          label.length >= 1 &&
          label.length <= 63 &&
          /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label),
      ) &&
      labels[labels.length - 1].length >= 2;

    if (!validDomainFormat) {
      return NextResponse.json({
        valid: false,
        message:
          "Enter an email address with a standard domain, such as gmail.com.",
      });
    }

    const exists = await domainHasDnsRecords(domain);

    if (!exists) {
      return NextResponse.json({
        valid: false,
        message:
          "This email domain does not appear to exist. Check the address and try again.",
      });
    }

    return NextResponse.json({ valid: true });
  } catch {
    return NextResponse.json(
      {
        valid: false,
        message: "Unable to validate the email domain. Please try again.",
      },
      { status: 400 },
    );
  }
}
