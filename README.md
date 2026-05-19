# o-m-automator

Point it at a device list. It finds and downloads the datasheets and manuals, strips the junk pages, and gives you clean PDFs.

## How it works

- Parses device names from images, CSVs, PDFs, or text files
- Searches Google (via Serper) for datasheets and manuals - two queries per device
- Downloads PDFs, handles product pages that link to PDFs rather than direct links
- Uses Claude to read each page and decide keep / remove / flag
- Outputs filtered per-device PDFs and a CSV report

Images go through two Claude passes - verbatim transcription first, then classification - to avoid hallucinated model numbers.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # fill in ANTHROPIC_API_KEY and SERPER_API_KEY
```

## Usage

Recommended flow:

```bash
# Parse devices and search, writes input/review.csv
python main.py input/devices/ --dry-run

# Check review.csv - fix any wrong model numbers or delete rows to skip
# Then run the full pipeline (re-searches using the corrected names)
python main.py input/review.csv
```

Or skip straight to it:

```bash
python main.py -d "SM7B"
python main.py -d "SLXD4UK=-G59" -d "SLXD2/SM58"
python main.py input/devices.csv
```

Build a combined PDF with a clickable index from an output folder:

```bash
python combine.py output/2026-05-19_120000/
```

## Cost

Roughly $0.15 for a dry run and $0.25–0.45 for a full pipeline run on ~24 devices (Claude Haiku).

## Requirements

- Python 3.10+
- [Anthropic API key](https://console.anthropic.com)
- [Serper API key](https://serper.dev)
