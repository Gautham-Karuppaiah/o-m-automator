"""Configuration settings for the documentation compiler."""

from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"

CLAUDE_MODEL = "claude-haiku-4-5-20251001"

DOWNLOAD_TIMEOUT = 60
MAX_DOWNLOAD_RETRIES = 3
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"

SERPER_API_URL = "https://google.serper.dev/search"
SERPER_NUM_RESULTS = 10

MAX_PAGES_PER_BATCH = 20

# Device parsing prompt template
DEVICE_PARSE_PROMPT = """Extract all device and product model numbers from the following content.

CRITICAL: Only extract text that is literally present in the content. Do not infer, guess, or add model numbers based on your knowledge of brands or product lines. If a model number is not explicitly written, do not include it.

Classify each entry into one of three buckets:

DEVICES — alphanumeric model numbers explicitly written as the product's own identifier:
  e.g. "SM7B", "SLXD4=-G59", "SRH1540", "GS728TPP-100EUS"
  Preserve the exact model string as written (including suffixes like =-G59, -100EUS, UK, etc.)
  Deduplicate: if the same model appears multiple times, include it only once.
  If you are not sure whether something is a model number, it is NOT a model number — put it in unresolved or skipped instead.
  NOT a model number:
  - Frequency bands: "470-514MHz", "G59", "2.4GHz"
  - Technical specs: "128x128", "1600W", "8x8", "4K", "70/100V"
  - Model numbers embedded inside another item's description: e.g. "CHARGER FOR SLXD1/2" — SLXD1/2 is what the charger is compatible with, not the charger's own model number. e.g. "HANDHELD W/ SM58 MIC" — SM58 is the capsule type, not the transmitter's model number
  - Product line or family names without a specific model: "FlexAmp", "EB Series", "WM Series"
  - Descriptions that are a sentence or phrase rather than a part number

UNRESOLVED — rows where there is a specific brand + precise description but no model number is written:
  Only use this if the brand + description together are specific enough to uniquely identify one product via a web search.
  e.g. brand="Samsung", description="24 inch Touch Display" — specific enough
  e.g. brand="Lightware", description="8 input 8 output Full 4K HDMI 2.0 matrix switcher with analog audio" — specific enough
  NOT unresolved: brand="Lightware", description="HDMI receiver" — too vague, skip instead
  NOT unresolved: brand="QSC", description="amplifier" — too vague

SKIPPED — everything else:
  - Generic accessories: cables, adapters, racks, mounts, brackets
  - Category labels with no brand: "PoE Switch", "NVMe SSD"
  - Placeholders: "TBD", "MISC", "N/A"
  - Standalone brand names only: "Infinova", "LEGRAND", "Cisco"
  - Descriptions too vague to uniquely identify a product

Content:
{content}

Respond with JSON only:
{{"devices": ["MODEL1", "MODEL2", ...], "unresolved": [{{"brand": "Samsung", "description": "24 inch Touch Display"}}, ...], "skipped": ["GENERIC/CABLES", ...]}}
"""

# Page evaluation prompt template
EVALUATION_PROMPT = """You are filtering PDF pages for a technical documentation library.

Target device(s): {device_names}
Document type: {doc_type}

For each page, decide: KEEP, REMOVE, or FLAG.

KEEP pages that are about ANY of the target devices:
- Specs, pinouts, wiring, dimensions for a target model
- Setup/configuration instructions that apply to a target model
- Shared pages that explicitly include a target model (e.g. a specs table listing it)
- General content (e.g. battery info, accessories) that applies to a target model from context

REMOVE pages that are specifically about a DIFFERENT named model:
- If a page names a specific model (e.g. "SLXD4 Receiver") and that model is NOT in the target list, it's about a different device - remove it
- A page about a compatible/paired product is still about that other product, not the target

REMOVE only when you are 100% certain a page is irrelevant to ALL target devices based on its text content:
- Text clearly about OTHER models not in the target list
- Text is purely legal/copyright boilerplate
- Text is "about us" / company history with no product info
- Not in English (even if the page is about a target device, remove non-English pages — we only want English documentation)

You can ONLY remove a page if there is enough readable text to confirm irrelevance.
If a page has little or no text, you cannot judge it - FLAG it instead.

FLAG pages when:
- Little or no extractable text (regardless of images/indicators)
- Shared family/series content where target devices are among many listed
- Mixed content (some specs, some marketing)
- Uncertain whether it applies to any target model
- Page appears blank or nearly blank (could be a rendering issue)

When genuinely uncertain, err on KEEP.

{pages_content}

Respond with JSON array only:
[{{"page": 1, "decision": "KEEP", "reason": "..."}}, ...]
"""
