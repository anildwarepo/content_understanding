#!/usr/bin/env python3
import os
import json
import textwrap
from pathlib import Path
from time import sleep, time
import requests
from dotenv import load_dotenv

load_dotenv()  # take environment variables from .env file if present


# running script path
SCRIPT_PATH = Path(__file__).parent

# ---- Config (edit or use env vars) ------------------------------------------
AZURE_CONTENT_UNDERSTANDING_ENDPOINT = os.getenv(
    "AZURE_CONTENT_UNDERSTANDING_ENDPOINT"    
)
AZURE_CONTENT_UNDERSTANDING_SUBSCRIPTION_KEY = os.getenv("AZURE_CONTENT_UNDERSTANDING_SUBSCRIPTION_KEY", "")  # or paste your key here
API_VERSION = "2025-05-01-preview"

ANALYZER_ID = os.getenv("ANALYZER_ID", "custom_invoice_processing_v1")

# Extra IDs from your REST file (optional)
OP_ANALYZER_ID = os.getenv("OP_ANALYZER_ID", "custom_invoice_processing")
OPERATION_ID = os.getenv(
    "OPERATION_ID", "8d18c0f6-722a-4671-9134-4af62866955f"
)

CUSTOM_SCHEMA_PATH = SCRIPT_PATH / "custom_schema.json"


TIMEOUT = 60  # seconds
# -----------------------------------------------------------------------------

def print_json(title: str, resp: requests.Response):
    print(f"\n=== {title} [{resp.status_code}] ===")
    try:
        print(json.dumps(resp.json(), indent=2))
    except Exception:
        print(resp.text[:2000])  # fallback

def build_url(path: str, *, versioned: bool = True) -> str:
    if versioned:
        sep = "&" if "?" in path else "?"
        return f"{AZURE_CONTENT_UNDERSTANDING_ENDPOINT}{path}{sep}api-version={API_VERSION}"
    return f"{AZURE_CONTENT_UNDERSTANDING_ENDPOINT}{path}"

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "Ocp-Apim-Subscription-Key": AZURE_CONTENT_UNDERSTANDING_SUBSCRIPTION_KEY,
        # requests sets Content-Type automatically when using json=...
        "Accept": "application/json",
    })
    return s

def list_analyzers(session: requests.Session):
    url = build_url("/contentunderstanding/analyzers")
    resp = session.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    print_json("List analyzers", resp)

def get_analyzer(session: requests.Session, analyzer_id: str):
    url = build_url(f"/contentunderstanding/analyzers/{analyzer_id}")
    resp = session.get(url, timeout=TIMEOUT)
    if resp.status_code == 404:
        print(f"\nAnalyzer {analyzer_id} not found. You may need to create it first.")
        return
    
    json_resp = resp.json()
    resp.raise_for_status()
    #print_json(f"Get analyzer {analyzer_id}", resp)
    return json_resp

def put_analyzer(session: requests.Session, analyzer_id: str, schema_path: Path):
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_path}")
    with open(schema_path, "r", encoding="utf-8") as f:
        payload = json.load(f)

    url = build_url(f"/contentunderstanding/analyzers/{analyzer_id}")
    # Use json= to send application/json
    resp = session.put(url, json=payload, timeout=TIMEOUT)

    if resp.status_code == 201:
        operation_location = resp.headers.get("Operation-Location")
        if operation_location:

            # 'https://azure-ai-service-anildwa-9030.services.ai.azure.com/contentunderstanding/analyzers/custom_invoice_processing_v1/operations/4ab5cacc-098a-464f-abee-200c71e15a44?api-version=2025-05-01-preview'
            
            print(f"\nAnalyzer creation started. Check status at: {operation_location}")
            while True:
                print("checking operation status...")
                operation_status = get_operation_status(session, analyzer_id, operation_location)
                if operation_status['status'] == "Succeeded":
                    print(f"\nAnalyzer {analyzer_id} created successfully.")
                    break
                sleep(5)  # wait before polling

    resp.raise_for_status()
    #print_json(f"PUT analyzer {analyzer_id} ({schema_path.name})", resp)

def get_operation_status(session: requests.Session, analyzer_id: str, operation_location: str):
    resp = session.get(operation_location, timeout=TIMEOUT)
    if resp.status_code == 404:
        print(f"\nOperation not found for analyzer {analyzer_id}.")
        return
    json_resp = resp.json()
    global operation_status
    operation_status = json_resp.get("status", "unknown")
    resp.raise_for_status()
    #print_json(f"Operation status {analyzer_id}", resp)
    return json_resp

def analyze_with_prebuilt_document_analyzer(session: requests.Session, doc_url: str):
    # Note: fixed the stray quote at the end of the URL from your snippet
    url = build_url(
        "/contentunderstanding/analyzers/prebuilt-documentAnalyzer:analyze"
    )
    payload = {"url": doc_url}
    resp = session.post(url, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    print_json("Analyze with prebuilt-documentAnalyzer", resp)

def delete_analyzer(session: requests.Session, analyzer_id: str):
    url = build_url(f"/contentunderstanding/analyzers/{analyzer_id}")
    resp = session.delete(url, timeout=TIMEOUT)
    if resp.status_code == 404:
        print(f"\nAnalyzer {analyzer_id} not found; nothing to delete.")
        return
    resp.raise_for_status()
    print_json(f"Delete analyzer {analyzer_id}", resp)

def main():
    if not AZURE_CONTENT_UNDERSTANDING_SUBSCRIPTION_KEY:
        raise RuntimeError(
            textwrap.dedent(
                """\
                Missing API key.
                Set AZURE_CONTENT_UNDERSTANDING_SUBSCRIPTION_KEY env var.
                """
            )
        )

    session = make_session()

    # GET /analyzers
    #list_analyzers(session)

    # GET /analyzers/{analyzerId}
    analyzer = get_analyzer(session, ANALYZER_ID)

    if analyzer:
        print(f"\nAnalyzer {ANALYZER_ID} already exists; deleting and recreating...")
        delete_analyzer(session, ANALYZER_ID)
        

    # PUT /analyzers/{analyzerId} with custom_schema.json
    if CUSTOM_SCHEMA_PATH.exists():
        put_analyzer(session, ANALYZER_ID, CUSTOM_SCHEMA_PATH)
    else:
        print(f"\n(custom_schema.json not found at {CUSTOM_SCHEMA_PATH.resolve()}; skipping PUT)")

    # GET /analyzers/{analyzerId}/operations/{operationId}

   
    # POST prebuilt-documentAnalyzer:analyze
    # analyze_with_prebuilt_document_analyzer(session, SAMPLE_INVOICE_URL)

if __name__ == "__main__":
    main()
