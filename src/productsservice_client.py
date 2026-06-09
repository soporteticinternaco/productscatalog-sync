import os
import time
import logging
import requests

# OAuth token endpoint and credentials (client auth is a pre-encoded Basic header value)
PRODUCTS_SERVICE_TOKEN_URL = os.environ.get("PRODUCTS_SERVICE_TOKEN_URL")
PRODUCTS_SERVICE_BASIC_AUTH = os.environ.get("PRODUCTS_SERVICE_BASIC_AUTH")
PRODUCTS_SERVICE_USERNAME = os.environ.get("PRODUCTS_SERVICE_USERNAME")
PRODUCTS_SERVICE_PASSWORD = os.environ.get("PRODUCTS_SERVICE_PASSWORD")

# Products endpoint and fixed query params
PRODUCTS_SERVICE_BASE_URL = os.environ.get("PRODUCTS_SERVICE_BASE_URL")
PRODUCTS_SERVICE_CID = os.environ.get("PRODUCTS_SERVICE_CID")

# How many product refs to request per call (the endpoint accepts a collection)
PRODUCTS_SERVICE_CHUNK_SIZE = int(os.environ.get("PRODUCTS_SERVICE_CHUNK_SIZE", "100"))

REQUEST_TIMEOUT = 60


class ProductsServiceClient:
    """
    Client for the corporate (oauth-protected) products service.

    A single access token is fetched on construction and reused for every
    request during the run, as the token is valid for the whole execution.
    """

    def __init__(self, company_id, lang="es", chunk_size=PRODUCTS_SERVICE_CHUNK_SIZE):
        self.company_id = company_id
        self.lang = lang
        self.chunk_size = chunk_size
        self.token = self._fetch_token()

    def _fetch_token(self):
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {PRODUCTS_SERVICE_BASIC_AUTH}",
        }
        data = {
            "grant_type": "password",
            "username": PRODUCTS_SERVICE_USERNAME,
            "password": PRODUCTS_SERVICE_PASSWORD,
            "scope": "offline_access",
        }
        logging.info(f"Requesting oauth token from products service ({PRODUCTS_SERVICE_TOKEN_URL})")
        start = time.monotonic()
        response = requests.post(
            PRODUCTS_SERVICE_TOKEN_URL,
            headers=headers,
            data=data,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        elapsed = time.monotonic() - start
        token = response.json().get("access_token")
        if not token:
            raise RuntimeError("Products service token response did not contain an access_token")
        logging.info(f"Obtained oauth token from products service in {elapsed:.2f}s")
        return token

    def _fetch_pictures_chunk(self, refs):
        """Request a single chunk of refs and return {product_ref: picture_urls}."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        params = {
            "companyId": self.company_id,
            "cid": PRODUCTS_SERVICE_CID,
            "l": self.lang,
            "disc": 0
        }

        body = {"items": [{"product_ref": ref, "quantity": 1} for ref in refs]}

        logging.info(f"Requesting pictures from products service for {len(refs)} products")
        start = time.monotonic()
        response = requests.post(
            PRODUCTS_SERVICE_BASE_URL,
            headers=headers,
            params=params,
            json=body,
            timeout=REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        elapsed = time.monotonic() - start
        logging.info(f"Products service responded for {len(refs)} products in {elapsed:.2f}s")

        result = {}
        for item in response.json().get("items", []):
            ref = str(item.get("product_ref"))
            pictures = item.get("pictures") or []
            main_picture_url = None
            main_picture_thumb_url = None
            if pictures:
                main_picture_url = pictures[0].get("url")
                main_picture_thumb_url = pictures[0].get("thumbnail_small_url")
            result[ref] = {
                "main_picture_url": main_picture_url,
                "main_picture_thumb_url": main_picture_thumb_url,
            }
        return result

    def fetch_main_pictures(self, product_refs):
        """
        Given an iterable of product refs, return a dict mapping each ref (as str)
        to {"main_picture_url": ..., "main_picture_thumb_url": ...}.

        Refs are requested in chunks. A failing chunk is logged and skipped so a
        single error does not abort the whole import; those products simply keep
        their picture fields as None.
        """
        # Deduplicate while preserving order, normalising to str
        unique_refs = list(dict.fromkeys(str(ref) for ref in product_refs if ref is not None))

        total_chunks = (len(unique_refs) + self.chunk_size - 1) // self.chunk_size
        logging.info(
            f"Fetching pictures from products service for {len(unique_refs)} products "
            f"in {total_chunks} chunk(s) of up to {self.chunk_size}"
        )

        overall_start = time.monotonic()
        pictures_map = {}
        for chunk_index, start in enumerate(range(0, len(unique_refs), self.chunk_size), 1):
            chunk = unique_refs[start:start + self.chunk_size]
            logging.info(f"Processing chunk {chunk_index}/{total_chunks} ({len(chunk)} products)")
            try:
                pictures_map.update(self._fetch_pictures_chunk(chunk))
            except Exception as e:
                logging.error(
                    f"Failed to fetch pictures for chunk {chunk_index}/{total_chunks} of "
                    f"{len(chunk)} products (starting ref '{chunk[0]}'): {str(e)}"
                )

        overall_elapsed = time.monotonic() - overall_start
        logging.info(
            f"Fetched pictures from products service for {len(pictures_map)} of "
            f"{len(unique_refs)} products in {overall_elapsed:.2f}s"
        )
        return pictures_map
