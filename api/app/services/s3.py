from __future__ import annotations

import re

import boto3
from botocore.exceptions import ClientError

from api.app.core.config import settings


def get_presigned_url(s3_url: str | None, expiry: int = 3600) -> str | None:
    """Convert s3://bucket/key to a temporary HTTPS pre-signed URL. Returns original if not an S3 URL."""
    if not s3_url or not s3_url.startswith("s3://"):
        return s3_url

    try:
        parts = s3_url.replace("s3://", "").split("/", 1)
        bucket, key = parts[0], parts[1] if len(parts) > 1 else ""
        client = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=expiry,
        )
    except (ClientError, Exception):
        return s3_url


async def upload_bytes_to_s3(data: bytes, key: str, content_type: str = "application/octet-stream") -> str:
    """Upload bytes to S3 and return the S3 URL."""
    client = boto3.client(
        "s3",
        aws_access_key_id=settings.aws_access_key_id,
        aws_secret_access_key=settings.aws_secret_access_key,
        region_name=settings.aws_region,
    )
    client.put_object(
        Bucket=settings.s3_bucket_name,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return f"s3://{settings.s3_bucket_name}/{key}"
