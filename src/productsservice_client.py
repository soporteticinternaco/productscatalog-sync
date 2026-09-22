import os
import time
import logging
import unicodedata
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

# Locales requested from the products service. Characteristics and files are
# localized, so one pass is made per language; pictures are language-independent
# and taken from the es_ES pass.
ES_LOCALE = "es_ES"
PT_LOCALE = "pt_PT"


class ProductsServiceClient:
    """
    Client for the corporate (oauth-protected) products service.

    A single access token is fetched on construction and reused for every
    request during the run, as the token is valid for the whole execution.
    """

    def __init__(self, company_id, lang="es", chunk_size=PRODUCTS_SERVICE_CHUNK_SIZE,
                 excluded_characteristics=None):
        self.company_id = company_id
        self.lang = lang
        self.chunk_size = chunk_size
        # Characteristic names to drop, normalised for robust matching against
        # the PIM keys (see _normalize_name).
        self._excluded_characteristics = {
            self._normalize_name(name)
            for name in (excluded_characteristics or [])
        }
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

    def _fetch_chunk(self, refs, locale):
        """Request a single chunk of refs for one locale and return
        {product_ref: {picture urls + raw characteristics map}}."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        params = {
            "companyId": self.company_id,
            "cid": PRODUCTS_SERVICE_CID,
            "l": locale,
            "disc": 0
        }

        body = {"items": [{"product_ref": ref, "quantity": 1} for ref in refs]}

        logging.info(f"Requesting {locale} product data from products service for {len(refs)} products")
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
        logging.info(f"Products service responded ({locale}) for {len(refs)} products in {elapsed:.2f}s")

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
                "pictures_raw": pictures,
                "characteristics_raw": item.get("characteristics") or {},
                "files_raw": item.get("files") or [],
            }
        return result

    def _fetch_all_chunks(self, unique_refs, locale):
        """Fetch every ref for a single locale, chunk by chunk. A failing chunk is
        logged and skipped so a single error does not abort the whole import."""
        total_chunks = (len(unique_refs) + self.chunk_size - 1) // self.chunk_size
        logging.info(
            f"Fetching {locale} product data from products service for {len(unique_refs)} products "
            f"in {total_chunks} chunk(s) of up to {self.chunk_size}"
        )

        data = {}
        for chunk_index, start in enumerate(range(0, len(unique_refs), self.chunk_size), 1):
            chunk = unique_refs[start:start + self.chunk_size]
            logging.info(f"Processing {locale} chunk {chunk_index}/{total_chunks} ({len(chunk)} products)")
            try:
                data.update(self._fetch_chunk(chunk, locale))
            except Exception as e:
                logging.error(
                    f"Failed to fetch {locale} data for chunk {chunk_index}/{total_chunks} of "
                    f"{len(chunk)} products (starting ref '{chunk[0]}'): {str(e)}"
                )
        return data

    @staticmethod
    def _normalize_name(name):
        """
        Normalise a characteristic name for exclusion matching: NFKC (reconciles
        precomposed vs decomposed accents and folds odd spaces like NBSP into
        regular ones), collapse runs of whitespace, and casefold for
        case-insensitive comparison.
        """
        name = unicodedata.normalize("NFKC", str(name))
        return " ".join(name.split()).casefold()

    def _build_characteristics(self, raw, lang):
        """
        Turn one language's characteristics map ({name: value, ...}) into a list
        of {"lang", "name", "value"} entries matching the nested Elastic mapping.
        Empty names/values and names in the exclusion list are dropped; values
        are coerced to trimmed strings.
        """
        raw = raw or {}
        characteristics = []
        for name, value in raw.items():
            if name is None or value is None:
                continue
            name = str(name).strip()
            value = str(value).strip()
            if not name or not value:
                continue
            if self._normalize_name(name) in self._excluded_characteristics:
                continue
            characteristics.append({"lang": lang, "name": name, "value": value})
        return characteristics

    @staticmethod
    def _build_files(raw):
        """
        Turn one language's files list ([{name, url}, ...]) into the
        {"description", "url"} shape stored under files.<lang>. The products
        service names each file with `name`; that becomes our `description`.
        Entries missing a name or url are dropped.
        """
        raw = raw or []
        files = []
        for entry in raw:
            name = entry.get("name")
            url = entry.get("url")
            if name is None or url is None:
                continue
            name = str(name).strip()
            url = str(url).strip()
            if not name or not url:
                continue
            files.append({"description": name, "url": url})
        return files

    @staticmethod
    def _build_pictures(raw):
        """
        Turn the products service pictures list into the flat {"name", "url",
        "thumbnail_url"} shape stored under `pictures`. Pictures are
        language-independent, so this is built once from the es_ES pass.
        thumbnail_small_url is mapped to thumbnail_url; the source's larger
        thumbnail_url is not used.
        """
        raw = raw or []
        pictures = []
        for entry in raw:
            url = entry.get("url")
            if url is None:
                continue
            pictures.append({
                "name": entry.get("name"),
                "url": url,
                "thumbnail_url": entry.get("thumbnail_small_url"),
            })
        return pictures

    def fetch_main_pictures(self, product_refs):
        """
        Given an iterable of product refs, return a dict mapping each ref (as str)
        to {"main_picture_url", "main_picture_thumb_url", "pictures",
        "characteristics", "files"}.

        Two passes are made against the products service, one per locale (es_ES
        and pt_PT). Characteristics and files differ per language, so each
        language's entries are kept separately (characteristics tagged with
        `lang`; files grouped under files.es / files.pt). Pictures are
        language-independent and taken from the es_ES pass. A failing chunk is
        logged and skipped so a single error does not abort the whole import.
        """
        # Deduplicate while preserving order, normalising to str
        unique_refs = list(dict.fromkeys(str(ref) for ref in product_refs if ref is not None))

        overall_start = time.monotonic()
        es_data = self._fetch_all_chunks(unique_refs, ES_LOCALE)
        pt_data = self._fetch_all_chunks(unique_refs, PT_LOCALE)

        result = {}
        all_refs = list(dict.fromkeys(list(es_data.keys()) + list(pt_data.keys())))
        for ref in all_refs:
            es_entry = es_data.get(ref, {})
            pt_entry = pt_data.get(ref, {})
            result[ref] = {
                "main_picture_url": es_entry.get("main_picture_url"),
                "main_picture_thumb_url": es_entry.get("main_picture_thumb_url"),
                "pictures": self._build_pictures(es_entry.get("pictures_raw")),
                "characteristics": (
                    self._build_characteristics(es_entry.get("characteristics_raw"), "es")
                    + self._build_characteristics(pt_entry.get("characteristics_raw"), "pt")
                ),
                "files": {
                    "es": self._build_files(es_entry.get("files_raw")),
                    "pt": self._build_files(pt_entry.get("files_raw")),
                },
            }

        overall_elapsed = time.monotonic() - overall_start
        logging.info(
            f"Fetched product data from products service for {len(result)} of "
            f"{len(unique_refs)} products in {overall_elapsed:.2f}s"
        )
        return result
