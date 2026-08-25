import os
import sys
from pathlib import Path
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

load_dotenv(ROOT / ".env", override=True)

from email_classification_agent.email_parser import parse_eml
from email_classification_agent.property_appraiser import MiamiDadePropertyClient
from email_classification_agent.universal_classifier import UniversalEmailClassifier, BASE_INSTRUCTIONS
from email_classification_agent.universal_models import ClassificationPolicy
from email_classification_agent.universal_agent import _miami_dade_label

def test_verbose(eml_path: str):
    print("=" * 80)
    print(f"TESTING EMAIL: {eml_path}")
    print("=" * 80)
    
    # 1. Parse Email
    parsed = parse_eml(Path(eml_path))
    print("\n--- [STEP 1: PARSED EMAIL METADATA & CONTENT] ---")
    print(f"From: {parsed.from_header}")
    print(f"Reply-To: {parsed.reply_to_header}")
    print(f"Subject: {parsed.subject}")
    print(f"Attachment names: {parsed.attachment_names}")
    print(f"Body text length: {len(parsed.body_text)} chars")
    print("Sample Body (first 300 chars):")
    print(parsed.body_text[:300] + "...\n")

    # 2. Prepare LLM Instructions & Input
    policy = ClassificationPolicy(
        prompt="Classify emails as 'Wholesale' if they offer real estate deals or off-market opportunities, otherwise null.",
        labels=["Wholesale", "Inquiry"],
        confidence_threshold=0.85,
    )
    
    print("\n--- [STEP 2: PRE-LLM PAYLOAD & ASSEMBLED PROMPT] ---")
    print(f"System Instructions:\n{BASE_INSTRUCTIONS}\n")
    print(f"User Policy:\n{policy.prompt}\n")
    print(f"Allowed Labels: {policy.labels}\n")
    
    api_key = os.getenv("OPENAI_API_KEY", "")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    print(f"Using OpenAI Model: {model}")
    print(f"API Key present: {bool(api_key)} (Starts with: {api_key[:10]}...)")
    
    classifier = UniversalEmailClassifier(api_key=api_key, model=model)
    
    print("\n--- [STEP 3: CALLING LLM & OBSERVING RESPONSE/THOUGHTS] ---")
    try:
        decision = classifier.classify(parsed, policy)
        print(f"LLM Proposed Label: {decision.label}")
        print(f"LLM Confidence: {decision.confidence}")
        print(f"LLM Reasoning: {decision.reason}")
        print(f"LLM Evidence: {decision.evidence}")
    except Exception as e:
        print(f"LLM Call Error: {e}")
        decision = None

    # 4. Property Appraiser Lookup
    print("\n--- [STEP 4: PROPERTY APPRAISER LOOKUP (PROPERTY_LOOKUP_ENABLED=true)] ---")
    prop_client = MiamiDadePropertyClient()
    records = prop_client.lookup_email(parsed)
    print(f"Extracted Property Candidates Count: {len(records)}")
    for i, r in enumerate(records, 1):
        print(f"\n  Candidate #{i}:")
        print(f"    Address: {r.address}")
        print(f"    Asking Price: {r.asking_price}")
        print(f"    Lookup Status: {r.lookup_status}")
        print(f"    Folio: {r.folio}")
        print(f"    Municipality: {r.municipality}")
        print(f"    Land Use: {r.land_use}")
        print(f"    Legal Description: {r.legal_description}")
        print(f"    Lot Size SqFt: {r.lot_size_sqft}")
        print(f"    Qualifies for Acquisition?: {r.qualifies}")
        print(f"    Qualification Reasons: {r.reasons}")

    # 5. Routing and Secondary Labels
    print("\n--- [STEP 5: FINAL ROUTING & SECONDARY LABELS] ---")
    proposed_label = decision.label if decision else "Wholesale"
    miami_dade_tag = _miami_dade_label(proposed_label, parsed)
    qualified_tag = f"{proposed_label}/Qualified" if any(r.qualifies for r in records) else None
    
    print(f"Primary Label: {proposed_label}")
    print(f"Miami-Dade Tag: {miami_dade_tag}")
    print(f"Qualified Tag: {qualified_tag}")
    print("=" * 80)

if __name__ == "__main__":
    sample = ROOT / "tests" / "fixtures" / "wholesale_09.eml"
    test_verbose(str(sample))
