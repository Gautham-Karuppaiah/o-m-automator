"""CLI orchestrator for PDF documentation compiler."""

import argparse
import csv
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# Suppress noisy third-party loggers
for _name in ('pdfminer', 'httpcore', 'httpx', 'anthropic'):
    logging.getLogger(_name).setLevel(logging.WARNING)

from config import INPUT_DIR, OUTPUT_DIR  # noqa: E402
from parser import parse_devices_from_file, SUPPORTED_EXTENSIONS  # noqa: E402
from searcher import search_pdf_urls, resolve_model_number  # noqa: E402
from downloader import download_pdf  # noqa: E402
from extractor import extract_page_data, get_page_summary  # noqa: E402
from evaluator import evaluate_pages, get_decision_summary  # noqa: E402
from filter import write_filtered_pdf  # noqa: E402
from reporter import write_report  # noqa: E402


def load_devices(input_path: Path) -> list[str]:
    """Load device names from CSV file."""
    devices = []
    with open(input_path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            name = row.get('device_name', '').strip()
            if name:
                devices.append(name)
    return devices


def search_all_devices(devices: list[str]) -> tuple[dict[str, list[str]], list[dict], float]:
    """
    Search for all devices and group by PDF URL.

    Returns:
        url_groups: {url: [device_names]} for successful searches
        failures: list of status dicts for failed searches
        total_search_cost: total cost of all searches
    """
    url_groups = defaultdict(lambda: {'devices': [], 'doc_type': None})
    failures = []
    total_search_cost = 0.0

    for i, device in enumerate(devices, 1):
        print(f"  [{i}/{len(devices)}] {device}...", end=' ', flush=True)
        search_result = search_pdf_urls(device)

        search_tokens = search_result.tokens
        search_cost = search_tokens.cost_estimate
        total_search_cost += search_cost

        if search_result.failure_reason:
            print(f"failed - {search_result.failure_reason}")
            failures.append({
                'devices': [device],
                'success': False,
                'error': search_result.failure_reason,
                'search_cost': search_cost,
            })
        elif not (search_result.datasheet_url or search_result.manual_url):
            print("no PDFs found")
            failures.append({
                'devices': [device],
                'success': False,
                'error': 'No PDFs found',
                'search_cost': search_cost,
            })
        else:
            found_types = []
            if search_result.datasheet_url:
                found_types.append('datasheet')
            if search_result.manual_url:
                found_types.append('manual')
            print(f"found {'+'.join(found_types)} ({search_result.confidence})")

            # Add each unique URL as a separate group entry
            urls_added = set()
            if search_result.datasheet_url:
                print(f"         Datasheet: {search_result.datasheet_url}")
                url_groups[search_result.datasheet_url]['devices'].append(device)
                url_groups[search_result.datasheet_url]['doc_type'] = 'datasheet'
                urls_added.add(search_result.datasheet_url)
            if search_result.manual_url and search_result.manual_url not in urls_added:
                print(f"         Manual:    {search_result.manual_url}")
                url_groups[search_result.manual_url]['devices'].append(device)
                url_groups[search_result.manual_url]['doc_type'] = 'manual'
            elif search_result.manual_url:
                print("         Manual:    same as datasheet")

    return dict(url_groups), failures, total_search_cost


def process_group(pdf_url: str, device_names: list[str], doc_type: str,
                  output_dir: Path, max_pages: int = 100) -> dict:
    """
    Process a group of devices that share the same PDF.
    Downloads once, extracts once, evaluates once with all device names.
    """
    group_label = ", ".join(device_names)
    status = {
        'devices': device_names,
        'pdf_url': pdf_url,
        'doc_type': doc_type,
        'success': False,
        'pages_total': 0,
        'pages_kept': 0,
        'pages_flagged': 0,
        'pages_removed': 0,
        'eval_cost': 0.0,
        'error': None,
    }

    # Download
    print("  Downloading...", end=' ', flush=True)
    safe_name = device_names[0].replace(' ', '_').replace('/', '-')
    pdfs_dir = output_dir / "pdfs"
    pdfs_dir.mkdir(exist_ok=True)
    pdf_path = pdfs_dir / f"{safe_name}_{doc_type}.pdf"

    dl_result = download_pdf(pdf_url, pdf_path)
    if not dl_result.success:
        print("failed")
        status['error'] = dl_result.failure_reason
        return status

    print(f"{dl_result.file_size:,} bytes")
    print(f"    Saved: {pdf_path}")

    # Extract
    print("  Extracting...", end=' ', flush=True)
    pages, meta = extract_page_data(pdf_path)
    summary = get_page_summary(pages)
    status['pages_total'] = summary['total_pages']
    print(f"{summary['total_pages']} pages")

    if max_pages and summary['total_pages'] > max_pages:
        print(f"  Skipped: {summary['total_pages']} pages exceeds limit ({max_pages})")
        status['error'] = f"Too many pages ({summary['total_pages']} > {max_pages})"
        status['success'] = True
        return status

    # Evaluate (all device names in one call)
    print(f"  Evaluating for: {group_label}...", end=' ', flush=True)
    eval_result = evaluate_pages(pages, device_names, doc_type)

    eval_tokens = eval_result.tokens
    status['eval_cost'] = eval_tokens.cost_estimate

    dec_summary = get_decision_summary(eval_result.decisions)
    status['pages_kept'] = dec_summary['keep']
    status['pages_flagged'] = dec_summary['flag']
    status['pages_removed'] = dec_summary['remove']

    print(f"{dec_summary['keep']} keep, {dec_summary['flag']} flag, {dec_summary['remove']} remove")
    print(f"    Cost: {eval_tokens.input_tokens:,} in + {eval_tokens.output_tokens:,} out = ${status['eval_cost']:.4f}")

    if dec_summary['flag_pages']:
        print(f"    Flagged: {dec_summary['flag_pages']}")
    if dec_summary['remove_pages']:
        print(f"    Removed: {dec_summary['remove_pages']}")

    status['pdf_path'] = pdf_path
    status['decisions'] = eval_result.decisions
    status['doc_type'] = doc_type
    status['success'] = True
    return status


def main():
    parser = argparse.ArgumentParser(
        description='Compile PDF documentation for device list'
    )
    parser.add_argument(
        'input',
        nargs='?',
        default=INPUT_DIR / 'devices.csv',
        type=Path,
        help='Input file with devices - CSV, TXT, PDF, image, etc. (default: input/devices.csv)'
    )
    parser.add_argument(
        '-o', '--output',
        default=None,
        type=Path,
        help='Output directory (default: output/<timestamp>)'
    )
    parser.add_argument(
        '-n', '--limit',
        type=int,
        help='Process only first N devices'
    )
    parser.add_argument(
        '-d', '--device',
        type=str,
        action='append',
        help='Process a single device (can be repeated: -d "DEV1" -d "DEV2")'
    )
    parser.add_argument(
        '--max-pages',
        type=int,
        default=100,
        help='Skip evaluation for PDFs with more than N pages (default: 100)'
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Parse devices and show what would be processed, then stop'
    )

    args = parser.parse_args()

    if args.output is None:
        args.output = OUTPUT_DIR / datetime.now().strftime("%Y-%m-%d_%H%M%S")

    # Set up file logging (skip for dry run)
    if not args.dry_run:
        args.output.mkdir(parents=True, exist_ok=True)
        log_path = args.output / "pipeline.log"
        file_handler = logging.FileHandler(log_path, mode='w')
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
        logging.getLogger().addHandler(file_handler)
        logging.getLogger().setLevel(logging.DEBUG)

    # Load devices
    parse_cost = 0.0
    all_skipped = []
    all_unresolved = []
    resolve_map = {}
    failed_resolves = []
    if args.device:
        devices = args.device
    elif not args.input.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)
    else:
        # Collect files to parse
        if args.input.is_dir():
            input_files = sorted(
                f for f in args.input.iterdir()
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            if not input_files:
                print(f"Error: No supported files found in {args.input}")
                sys.exit(1)
        else:
            input_files = [args.input]

        devices = []

        for input_file in input_files:
            # Try CSV with device_name column first
            if input_file.suffix.lower() == '.csv':
                csv_devices = load_devices(input_file)
                if csv_devices:
                    print(f"Loaded {len(csv_devices)} device(s) from: {input_file.name}")
                    devices.extend(csv_devices)
                    continue

            # Claude parsing for everything else
            print(f"Parsing devices from: {input_file.name}")
            try:
                file_devices, skipped, unresolved, parse_tokens = parse_devices_from_file(input_file)
                parse_cost += parse_tokens.cost_estimate
                print(f"  Extracted {len(file_devices)} device(s):")
                for d in file_devices:
                    print(f"    - {d}")
                if unresolved:
                    print(f"  Unresolved {len(unresolved)} (brand+description, no model number):")
                    for u in unresolved:
                        print(f"    ? {u['brand']}: {u['description']}")
                if skipped:
                    print(f"  Skipped {len(skipped)} generic item(s):")
                    for s in skipped:
                        print(f"    - {s}")
                devices.extend(file_devices)
                all_skipped.extend(skipped)
                all_unresolved.extend(unresolved)
            except Exception as e:
                print(f"  Error parsing {input_file.name}: {e}")

        if not devices and not all_unresolved:
            print("Error: No device names extracted from any file")
            sys.exit(1)

        # Resolve brand+description items
        resolve_map = {}   # model_number → original "brand: description"
        failed_resolves = []  # [{brand, description, reason}]
        if all_unresolved:
            print(f"\nResolving {len(all_unresolved)} unresolved item(s)...")
            resolve_cost = 0.0
            for u in all_unresolved:
                print(f"  {u['brand']}: {u['description']}...", end=' ', flush=True)
                result = resolve_model_number(u['brand'], u['description'])
                resolve_cost += result.tokens.cost_estimate
                parse_cost += result.tokens.cost_estimate
                original = f"{u['brand']}: {u['description']}"
                if result.model_number:
                    print(f"→ {result.model_number}")
                    devices.append(result.model_number)
                    resolve_map[result.model_number] = original
                else:
                    print(f"failed ({result.failure_reason})")
                    failed_resolves.append({
                        'brand': u['brand'],
                        'description': u['description'],
                        'reason': result.failure_reason,
                    })
            print(f"  Resolve cost: ${resolve_cost:.4f}")

        if not devices:
            print("Error: No device names extracted from any file")
            sys.exit(1)

        # Deduplicate while preserving order
        seen = set()
        unique_devices = []
        for d in devices:
            if d not in seen:
                seen.add(d)
                unique_devices.append(d)
        if len(devices) != len(unique_devices):
            print(f"\nDeduplicated: {len(devices)} → {len(unique_devices)} device(s)")
        devices = unique_devices

        print(f"Parse cost: ${parse_cost:.4f}")

        # Save parsed list for reuse
        parsed_csv = INPUT_DIR / "parsed_devices.csv"
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(parsed_csv, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['device_name'])
            for d in devices:
                writer.writerow([d])
        print(f"Saved to: {parsed_csv}\n")

    if args.limit:
        devices = devices[:args.limit]

    if args.dry_run:
        print(f"\n{len(devices)} device(s) parsed. Searching...")
        url_groups, failures, search_cost = search_all_devices(devices)

        # Build per-device URL view
        device_to_urls = defaultdict(dict)
        for url, group in url_groups.items():
            for device in group['devices']:
                device_to_urls[device][group['doc_type']] = url
        failed_search = {', '.join(f['devices']): f['error'] for f in failures}

        # Write review CSV
        review_path = INPUT_DIR / "review.csv"
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        with open(review_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=[
                'device_name', 'original', 'datasheet_url', 'manual_url', 'notes'
            ])
            writer.writeheader()

            # Devices with search results
            for device in devices:
                urls = device_to_urls.get(device, {})
                notes = failed_search.get(device, '')
                writer.writerow({
                    'device_name': device,
                    'original': resolve_map.get(device, device),
                    'datasheet_url': urls.get('datasheet', ''),
                    'manual_url': urls.get('manual', ''),
                    'notes': notes,
                })

            # Failed resolver items — blank device_name for user to fill in
            for fr in failed_resolves:
                writer.writerow({
                    'device_name': '',
                    'original': f"{fr['brand']}: {fr['description']}",
                    'datasheet_url': '',
                    'manual_url': '',
                    'notes': f"resolve failed: {fr['reason']}",
                })

        print(f"\nReview file written: {review_path}")
        print("  - Fill in any blank device_name cells (or delete the row to skip)")
        print("  - Correct any wrong model numbers")
        print("  - Verify the URLs look right")
        print(f"\nThen run: python main.py {review_path}")
        print(f"\nTotal dry run cost: ${parse_cost + search_cost:.4f}")
        return

    print(f"Processing {len(devices)} device(s)\n")

    # Step 1: Search all devices
    print("Step 1: Searching...")
    url_groups, failures, search_cost = search_all_devices(devices)

    # Show grouping
    print(f"\nFound {len(url_groups)} unique PDF(s) for {len(devices) - len(failures)} device(s)")
    print(f"Search cost: ${search_cost:.4f}")

    for url, group in url_groups.items():
        if len(group['devices']) > 1:
            print(f"  Grouped: {', '.join(group['devices'])}")
            print(f"       -> {url}")

    # Step 2: Process each group
    results = []
    total_eval_cost = 0.0

    for i, (url, group) in enumerate(url_groups.items(), 1):
        device_names = group['devices']
        doc_type = group['doc_type']
        group_label = ", ".join(device_names)

        print(f"\n[{i}/{len(url_groups)}] {group_label}")
        print(f"  URL: {url}")

        status = process_group(url, device_names, doc_type, args.output, args.max_pages)
        results.append(status)
        total_eval_cost += status['eval_cost']

        if status['success']:
            print(f"  Done (${status['eval_cost']:.4f})")
        else:
            print(f"  Failed: {status['error']}")

    # Step 3: Write one filtered PDF per URL group
    writable = [r for r in results if r.get('success') and r.get('decisions')]
    if writable:
        print("\nStep 3: Writing filtered PDFs...")
        for r in writable:
            device_names = r['devices']
            doc_type = r.get('doc_type', 'datasheet')
            safe_name = '+'.join(d.replace(' ', '_').replace('/', '-') for d in device_names)
            out_path = args.output / f"{safe_name}_{doc_type}.pdf"
            cr = write_filtered_pdf(r['pdf_path'], r['decisions'], out_path)
            label = ', '.join(device_names)
            print(f"  {label} [{doc_type}]: {cr.total_pages_included}/{cr.total_original_pages} pages → {out_path.name}")

    # Step 4: Write report
    report_path = args.output / "report.csv"
    write_report(
        output_path=report_path,
        devices=devices,
        skipped=all_skipped,
        failures=failures,
        results=results,
        parse_cost=parse_cost,
        search_cost=search_cost,
        eval_cost=total_eval_cost,
    )

    # Summary
    all_results = results + failures
    successful = sum(1 for r in all_results if r.get('success'))
    failed = len(all_results) - successful
    total_cost = parse_cost + search_cost + total_eval_cost

    print(f"\n{'=' * 40}")
    print(f"Complete: {successful} succeeded, {failed} failed")
    cost_parts = []
    if parse_cost > 0:
        cost_parts.append(f"parse: ${parse_cost:.4f}")
    cost_parts.append(f"search: ${search_cost:.4f}")
    cost_parts.append(f"eval: ${total_eval_cost:.4f}")
    print(f"Total cost: ${total_cost:.4f} ({', '.join(cost_parts)})")
    print(f"Report: {report_path}")


if __name__ == '__main__':
    main()
