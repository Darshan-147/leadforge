"""CSV export generation and S3 upload."""

import csv
import io
import os
import uuid

import boto3
from fastapi import HTTPException
from supabase import Client

from repositories.lead_repository import exportable_leads


def export_leads_csv_to_s3(db: Client, user_id: str) -> dict:
    """Generate a CSV from the user's leads and upload it to S3.

    Returns a dict with download_url, expires_in_seconds, and rows_exported.
    """
    bucket = os.getenv("AWS_S3_BUCKET")
    region = os.getenv("AWS_REGION", "ap-south-1")

    if not bucket:
        raise HTTPException(
            status_code=503,
            detail="AWS S3 export is not configured.",
        )

    # This client uses credentials from Render's environment.
    s3 = boto3.client("s3", region_name=region)

    # Use the authenticated user's Supabase client so RLS applies.
    leads = exportable_leads(db)

    fields = [
        "company_name", "domain", "website_url",
        "emails", "phones", "lead_score",
        "status", "created_at",
    ]

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()

    for lead in leads:
        row = dict(lead)
        for field in ("emails", "phones"):
            value = row.get(field) or []
            row[field] = "; ".join(map(str, value))
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8-sig")
    object_key = f"exports/{user_id}/{uuid.uuid4()}.csv"

    try:
        s3.put_object(
            Bucket=bucket,
            Key=object_key,
            Body=csv_bytes,
            ContentType="text/csv; charset=utf-8",
            ServerSideEncryption="AES256",
        )

        # The link works temporarily; it does not make
        # the S3 bucket public.
        download_url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": bucket,
                "Key": object_key,
            },
            ExpiresIn=300,
        )
    except Exception as exc:
        # Log the underlying exception in server logs
        # during debugging; don't expose credentials.
        raise HTTPException(
            status_code=502,
            detail="Could not create the S3 export.",
        ) from exc

    return {
        "download_url": download_url,
        "expires_in_seconds": 300,
        "rows_exported": len(leads),
    }

