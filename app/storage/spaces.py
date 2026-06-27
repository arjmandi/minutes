"""S3-protocol object storage (S3-compatible object storage in prod, MinIO locally) via aioboto3."""

from __future__ import annotations

import aioboto3


class SpacesStorage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        bucket: str,
        access_key: str,
        secret_key: str,
    ) -> None:
        self._session = aioboto3.Session()
        self._bucket = bucket
        self._client_kwargs = {
            "endpoint_url": endpoint_url,
            "region_name": region,
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
        }

    async def upload(self, key: str, data: bytes, *, content_type: str = "audio/wav") -> None:
        # Chunk uploads are infrequent (~ every rotation interval), so a client per call is fine.
        async with self._session.client("s3", **self._client_kwargs) as s3:
            await s3.put_object(Bucket=self._bucket, Key=key, Body=data, ContentType=content_type)
