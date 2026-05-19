"""Generate CSV report summarizing the full pipeline run."""

import csv
from pathlib import Path


def write_report(
    output_path: Path,
    devices: list[str],
    skipped: list[str],
    failures: list[dict],
    results: list[dict],
    parse_cost: float,
    search_cost: float,
    eval_cost: float,
):
    """
    Write a CSV report with one row per device covering all pipeline stages.

    Columns: device, status, doc_type, pdf_url, pages_total, pages_kept,
             pages_removed, pages_flagged, flagged_details, error, search_cost, eval_cost
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build lookup: device → list of result dicts (a device can have datasheet + manual)
    device_results = {}
    for r in results:
        for dev in r.get('devices', []):
            device_results.setdefault(dev, []).append(r)

    # Build failure lookup
    device_failures = {}
    for f in failures:
        for dev in f.get('devices', []):
            device_failures[dev] = f

    rows = []

    # Skipped devices (filtered at parse time)
    for name in skipped:
        rows.append({
            'device': name,
            'status': 'skipped',
            'doc_type': '',
            'pdf_url': '',
            'pages_total': '',
            'pages_kept': '',
            'pages_removed': '',
            'pages_flagged': '',
            'flagged_details': '',
            'error': 'Filtered as generic/non-device',
            'eval_cost': '',
        })

    # Devices that went through the pipeline
    for device in devices:
        # Search failure
        if device in device_failures:
            f = device_failures[device]
            rows.append({
                'device': device,
                'status': 'search_failed',
                'doc_type': '',
                'pdf_url': '',
                'pages_total': '',
                'pages_kept': '',
                'pages_removed': '',
                'pages_flagged': '',
                'flagged_details': '',
                'error': f.get('error', ''),
                'eval_cost': '',
            })
            continue

        # Processing results (could be multiple: datasheet + manual)
        if device not in device_results:
            rows.append({
                'device': device,
                'status': 'unknown',
                'doc_type': '',
                'pdf_url': '',
                'pages_total': '',
                'pages_kept': '',
                'pages_removed': '',
                'pages_flagged': '',
                'flagged_details': '',
                'error': 'No result recorded',
                'eval_cost': '',
            })
            continue

        for r in device_results[device]:
            doc_type = r.get('doc_type', '')
            grouped_with = [d for d in r.get('devices', []) if d != device]

            # Build flag details
            flag_details = ''
            decisions = r.get('decisions', [])
            if decisions:
                flagged = [d for d in decisions if d.decision == 'FLAG']
                if flagged:
                    flag_details = '; '.join(
                        f"p{d.page_number}: {d.reason}" for d in flagged
                    )

            status = 'ok'
            error = r.get('error', '') or ''
            if not r.get('success'):
                status = 'failed'
            elif error:
                status = 'skipped_pages'  # e.g. too many pages

            device_label = device
            if grouped_with:
                device_label = f"{device} (grouped with {', '.join(grouped_with)})"

            rows.append({
                'device': device_label,
                'status': status,
                'doc_type': doc_type,
                'pdf_url': r.get('pdf_url', ''),
                'pages_total': r.get('pages_total', ''),
                'pages_kept': r.get('pages_kept', ''),
                'pages_removed': r.get('pages_removed', ''),
                'pages_flagged': r.get('pages_flagged', ''),
                'flagged_details': flag_details,
                'error': error,
                'eval_cost': f"${r.get('eval_cost', 0):.4f}" if r.get('eval_cost') else '',
            })

    # Write CSV
    fieldnames = [
        'device', 'status', 'doc_type', 'pdf_url',
        'pages_total', 'pages_kept', 'pages_removed', 'pages_flagged',
        'flagged_details', 'error', 'eval_cost',
    ]

    with open(output_path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    # Summary row at bottom
    total_cost = parse_cost + search_cost + eval_cost
    with open(output_path, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([])
        writer.writerow(['SUMMARY'])
        writer.writerow(['Total devices', len(devices)])
        writer.writerow(['Skipped (generic)', len(skipped)])
        writer.writerow(['Search failures', len(failures)])
        writer.writerow(['Parse cost', f'${parse_cost:.4f}'])
        writer.writerow(['Search cost', f'${search_cost:.4f}'])
        writer.writerow(['Eval cost', f'${eval_cost:.4f}'])
        writer.writerow(['Total cost', f'${total_cost:.4f}'])

    return output_path
