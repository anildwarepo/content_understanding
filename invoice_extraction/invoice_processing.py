import json
import logging
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast
from dataclasses import dataclass
import os
import requests
from dotenv import load_dotenv
load_dotenv()

"""
Updates:
- Iterates over all entries in `file_urls` correctly
- Reads subscription key / AAD token from environment if present
- Writes TWO outputs per file:
    1) Raw Azure response (unchanged) under invoice_processing_result/raw_*.json
    2) Normalized JSON whose fields conform to custom_schema.json under
       invoice_processing_result/normalized_*.json
- Robustly extracts values (string/number/date/object/array) from the Azure
  response following the shape used by Content Understanding. The normalizer
  only includes fields that are actually present in the response.
"""

# ----------------------- Configuration -----------------------
file_urls = [
    #"https://stanildwa097846267954041.blob.core.windows.net/contentunderstanding/invoice_processing/POS1.pdf",
    "https://stanildwa097846267954041.blob.core.windows.net/contentunderstanding/invoice_processing/XZY1.pdf",
    #"https://stanildwa097846267954041.blob.core.windows.net/contentunderstanding/invoice_processing/XZY2.pdf",
]

# Optional: override via env vars
AZURE_CU_ENDPOINT = os.getenv(
    "AZURE_CONTENT_UNDERSTANDING_ENDPOINT",
    "https://azure-ai-service-anildwa-9030.services.ai.azure.com/",
)
AZURE_CU_API_VERSION = os.getenv("AZURE_CONTENT_UNDERSTANDING_API_VERSION", "2025-05-01-preview")
AZURE_CU_SUBSCRIPTION_KEY = os.getenv("AZURE_CONTENT_UNDERSTANDING_SUBSCRIPTION_KEY", "")
AZURE_CU_AAD_TOKEN = os.getenv("AZURE_CONTENT_UNDERSTANDING_AAD_TOKEN", "")
ANALYZER_ID = os.getenv("AZURE_CONTENT_UNDERSTANDING_ANALYZER_ID", "custom_invoice_processing_v1")

# ----------------------- Client ------------------------------
@dataclass(frozen=True, kw_only=True)
class Settings:
    endpoint: str
    api_version: str
    subscription_key: str | None = None
    aad_token: str | None = None
    analyzer_id: str
    file_location: str

    def __post_init__(self):
        key_not_provided = (not self.subscription_key)
        token_not_provided = (not self.aad_token)
        if key_not_provided and token_not_provided:
            raise ValueError("Either 'subscription_key' or 'aad_token' must be provided")

    @property
    def token_provider(self) -> Callable[[], str] | None:
        aad_token = self.aad_token
        if not aad_token:
            return None
        return lambda: aad_token


class AzureContentUnderstandingClient:
    def __init__(
        self,
        endpoint: str,
        api_version: str,
        subscription_key: str | None = None,
        token_provider: Callable[[], str] | None = None,
        x_ms_useragent: str = "cu-sample-code",
    ) -> None:
        if not subscription_key and token_provider is None:
            raise ValueError("Either subscription key or token provider must be provided")
        if not api_version:
            raise ValueError("API version must be provided")
        if not endpoint:
            raise ValueError("Endpoint must be provided")

        self._endpoint: str = endpoint.rstrip("/")
        self._api_version: str = api_version
        self._logger: logging.Logger = logging.getLogger(__name__)
        self._logger.setLevel(logging.INFO)
        self._headers: dict[str, str] = self._get_headers(
            subscription_key, token_provider and token_provider(), x_ms_useragent
        )

    def begin_analyze(self, analyzer_id: str, file_location: str):
        if Path(file_location).exists():
            with open(file_location, "rb") as file:
                data = file.read()
            headers = {"Content-Type": "application/octet-stream"}
        elif file_location.startswith(("https://", "http://")):
            data = {"url": file_location}
            headers = {"Content-Type": "application/json"}
        else:
            raise ValueError("File location must be a valid path or URL.")

        headers.update(self._headers)
        if isinstance(data, dict):
            response = requests.post(
                url=self._get_analyze_url(self._endpoint, self._api_version, analyzer_id),
                headers=headers,
                json=data,
            )
        else:
            response = requests.post(
                url=self._get_analyze_url(self._endpoint, self._api_version, analyzer_id),
                headers=headers,
                data=data,
            )

        response.raise_for_status()
        self._logger.info(f"Analyzing file {file_location} with analyzer: {analyzer_id}")
        return response

    def poll_result(
        self,
        response: requests.Response,
        timeout_seconds: int = 120,
        polling_interval_seconds: int = 2,
    ) -> dict[str, Any]:
        operation_location = response.headers.get("operation-location", "")
        if not operation_location:
            raise ValueError("Operation location not found in response headers.")

        start_time = time.time()
        while True:
            elapsed_time = time.time() - start_time
            if elapsed_time > timeout_seconds:
                raise TimeoutError(f"Operation timed out after {timeout_seconds:.2f} seconds.")

            poll = requests.get(operation_location, headers=self._headers)
            poll.raise_for_status()
            result = cast(dict[str, Any], poll.json())
            status = str(result.get("status", "")).lower()
            if status == "succeeded":
                return result
            if status == "failed":
                raise RuntimeError(f"Request failed: {result}")
            time.sleep(polling_interval_seconds)

    def _get_analyze_url(self, endpoint: str, api_version: str, analyzer_id: str):
        return f"{endpoint}/contentunderstanding/analyzers/{analyzer_id}:analyze?api-version={api_version}&stringEncoding=utf16"

    def _get_headers(self, subscription_key: str | None, api_token: str | None, x_ms_useragent: str) -> dict[str, str]:
        headers = ( {"Ocp-Apim-Subscription-Key": subscription_key} if subscription_key else {"Authorization": f"Bearer {api_token}"} )
        headers["x-ms-useragent"] = x_ms_useragent
        return headers


# ----------------------- Normalization -----------------------

def _best_value(field: dict[str, Any]) -> Any:
    """Return the most appropriate scalar value for a CU field."""
    for key in ("valueNumber", "valueDate", "valueString", "content"):
        if key in field and field[key] not in (None, ""):
            return field[key]
    # Currency type
    if "valueCurrency" in field and isinstance(field["valueCurrency"], dict):
        cur = field["valueCurrency"]
        # e.g., {"amount": 12.34, "currencyCode": "USD"}
        return cur.get("amount")
    # Object/array fallthrough
    if "valueObject" in field:
        return field["valueObject"]
    if "valueArray" in field:
        return field["valueArray"]
    return None

# Fields expected by custom_schema.json (flat list)
CUSTOM_FIELDS = [
    "vendor_name","vendor_taxid","vendor_address","vendor_address_recipient",
    "customer_name","customer_id","customer_address","customer_address_recipient",
    "shipping_address","shipping_address_recipient","remittance_address_recipient",
    "invoice_id","invoice_date","due_date","purchase_order",
    "previous_unpaid_balance","amount","amount_due","subtotal","tax","total_tax","taxrate","invoice_total",
]

LINE_ITEM_FIELDS = [
    "item_description","product_code","item_date","item_quantity","unit","unit_price","amount","tax",
]


def normalize_to_custom_schema(service_result: dict[str, Any]) -> dict[str, Any]:
    """Map Azure CU response into the structure defined by custom_schema.json.
    Only include keys that exist in the source response.
    """
    out: dict[str, Any] = {}

    # Where CU usually places fields:
    fields = (
        service_result.get("result", {})
        .get("contents", [{}])[0]
        .get("fields", {})
    )

    if not isinstance(fields, dict):
        return out

    # Flat fields
    for name in CUSTOM_FIELDS:
        f = fields.get(name)
        if isinstance(f, dict):
            val = _best_value(f)
            if val is not None:
                out[name] = val

    # Items array (if present)
    items_field = fields.get("items")
    items_out: list[dict[str, Any]] = []
    if isinstance(items_field, dict):
        # CU can return arrays as either valueArray or "values": [{valueObject: {...}}, ...]
        values = []
        if "valueArray" in items_field and isinstance(items_field["valueArray"], list):
            values = items_field["valueArray"]
        elif "values" in items_field and isinstance(items_field["values"], list):
            values = [v.get("valueObject", v) for v in items_field["values"]]

        for v in values:
            # Each v should be an object of fields
            if isinstance(v, dict):
                # Some shapes: {"item_description": {valueString:...}, ...}
                line: dict[str, Any] = {}
                for k in LINE_ITEM_FIELDS:
                    fv = v.get(k)
                    if isinstance(fv, dict):
                        val = _best_value(fv)
                        if val is not None:
                            line[k] = val
                # Also accept common alternates found in CU generic outputs
                # Map common aliases to our schema if our key missing
                aliases = {
                    "description": "item_description",
                    "productCode": "product_code",
                    "quantity": "item_quantity",
                    "unitPrice": "unit_price",
                    "lineTotal": "amount",
                    "date": "item_date",
                    "unit": "unit",
                    "tax": "tax",
                }
                for src, dst in aliases.items():
                    if dst not in line and isinstance(v.get(src), dict):
                        val = _best_value(v[src])
                        if val is not None:
                            line[dst] = val
                if line:
                    items_out.append(line)
    if items_out:
        out["items"] = items_out

    return out


# ----------------------- Main -------------------------------

def main():
    os.makedirs("invoice_processing_result", exist_ok=True)

    # Prepare client once
    settings_for_client = Settings(
        endpoint=AZURE_CU_ENDPOINT,
        api_version=AZURE_CU_API_VERSION,
        subscription_key=AZURE_CU_SUBSCRIPTION_KEY or None,
        aad_token=AZURE_CU_AAD_TOKEN or None,
        analyzer_id=ANALYZER_ID,
        file_location=file_urls[0],  # placeholder; not used by client directly
    )
    client = AzureContentUnderstandingClient(
        settings_for_client.endpoint,
        settings_for_client.api_version,
        subscription_key=settings_for_client.subscription_key,
        token_provider=settings_for_client.token_provider,
    )

    for file_url in file_urls:
        print(f"Processing file: {file_url}")
        # Start job
        response = client.begin_analyze(ANALYZER_ID, file_url)
        result = client.poll_result(response, timeout_seconds=60 * 60, polling_interval_seconds=1)

        # Write raw
        raw_path = Path(f"invoice_processing_result/raw_{Path(file_url).name}.json")
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)

        # Normalize
        normalized = normalize_to_custom_schema(result)
        norm_path = Path(f"invoice_processing_result/normalized_{Path(file_url).stem}.json")
        with open(norm_path, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)

        print(f"Finished processing file: {file_url}\n  - Raw: {raw_path}\n  - Normalized: {norm_path}")

    print("All files processed.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
