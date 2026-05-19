"""Build combined.pdf with a clickable index page from an output folder."""

import csv
import hashlib
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.lib import colors
from reportlab.pdfgen import canvas as rl_canvas
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link


PAGE_W, PAGE_H = A4
LEFT = 2.0 * cm
ROW_H = 0.72 * cm
TOP_MARGIN = 4.4 * cm
BOT_MARGIN = 2.0 * cm

COL_NUM   = LEFT
COL_BRAND = LEFT + 1.0 * cm
COL_MODEL = LEFT + 5.0 * cm
COL_PG    = PAGE_W - LEFT


def find_latest_output() -> Path | None:
    output_dir = Path("output")
    if not output_dir.exists():
        return None
    dirs = sorted([d for d in output_dir.iterdir() if d.is_dir()], reverse=True)
    return dirs[0] if dirs else None


MERGE_GROUPS: dict[str, list[str]] = {
    "HDMI-TPS-RX87, HDMI-TPS-TX87": ["HDMI-TPS-RX87", "HDMI-TPS-TX87"],
    "HDMI-TPS-RX97, HDMI-TPS-TX97": ["HDMI-TPS-RX97", "HDMI-TPS-TX97"],
}

FALLBACK_BRANDS = {
    'ELPLX02S': 'Epson', 'ELPEC01': 'Epson', 'EB-PU2010B': 'Epson',
    'Q-SYS_Core_110': 'QSC', 'TSC-70-G3': 'QSC', 'TSC-101-G3': 'QSC',
    'LH65WMRWBGCXUE': 'Samsung', 'LH75WMBWLGCXZA': 'Samsung',
    'HDMI-TPS-RX87, HDMI-TPS-TX87': 'Lightware', 'HDMI-TPS-RX97, HDMI-TPS-TX97': 'Lightware',
}

def load_brand_map(review_csv: Path) -> dict[str, str]:
    brand_map = dict(FALLBACK_BRANDS)
    if not review_csv.exists():
        return brand_map
    with open(review_csv, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            device = row.get('device_name', '').strip()
            original = row.get('original', '').strip()
            if device and ':' in original:
                brand = original.split(':')[0].strip().replace('Roland Video', 'Roland')
                key = device.replace(' ', '_').replace('/', '-')
                brand_map[key] = brand
    return brand_map


def collect_device_pdfs(output_dir: Path) -> dict[str, dict[str, Path]]:
    devices: dict[str, dict[str, Path]] = defaultdict(dict)

    # Filtered PDFs in root (evaluated)
    for pdf in sorted(output_dir.glob("*.pdf")):
        if pdf.name == "combined.pdf":
            continue
        stem = pdf.stem
        if stem.endswith("_datasheet+manual"):
            devices[stem[:-len("_datasheet+manual")]]["datasheet+manual"] = pdf
        elif stem.endswith("_datasheet"):
            devices[stem[:-len("_datasheet")]]["datasheet"] = pdf
        elif stem.endswith("_manual"):
            devices[stem[:-len("_manual")]]["manual"] = pdf

    # Raw PDFs in pdfs/ that were skipped (too many pages) — fill in gaps
    pdfs_dir = output_dir / "pdfs"
    if pdfs_dir.exists():
        for pdf in sorted(pdfs_dir.glob("*.pdf")):
            stem = pdf.stem
            if stem.endswith("_datasheet+manual"):
                key = stem[:-len("_datasheet+manual")]
                if "datasheet+manual" not in devices[key]:
                    devices[key]["datasheet+manual"] = pdf
            elif stem.endswith("_datasheet"):
                key = stem[:-len("_datasheet")]
                if "datasheet" not in devices[key]:
                    devices[key]["datasheet"] = pdf
            elif stem.endswith("_manual"):
                key = stem[:-len("_manual")]
                if "manual" not in devices[key]:
                    devices[key]["manual"] = pdf

    return dict(devices)


def rows_per_page() -> int:
    return int((PAGE_H - TOP_MARGIN - BOT_MARGIN) / ROW_H)


def generate_index_pdf(entries: list[dict], tmp_path: Path) -> list[dict]:
    rpp = rows_per_page()
    num_pages = max(1, -(-len(entries) // rpp))
    c = rl_canvas.Canvas(str(tmp_path), pagesize=A4)

    for page_idx in range(num_pages):
        page_entries = entries[page_idx * rpp:(page_idx + 1) * rpp]

        y = PAGE_H - 2.5 * cm
        c.setFont("Helvetica-Bold", 18)
        c.setFillColor(colors.black)
        c.drawString(LEFT, y, "Documentation Index")

        y -= 0.7 * cm
        c.setFont("Helvetica", 8)
        c.setFillColor(colors.HexColor('#888888'))
        c.drawString(LEFT, y, f"{len(entries)} device(s)")

        y -= 0.5 * cm
        c.setStrokeColor(colors.HexColor('#cccccc'))
        c.line(LEFT, y, PAGE_W - LEFT, y)
        y -= 0.45 * cm

        c.setFont("Helvetica-Bold", 7)
        c.setFillColor(colors.HexColor('#999999'))
        c.drawString(COL_NUM,   y, "#")
        c.drawString(COL_BRAND, y, "BRAND")
        c.drawString(COL_MODEL, y, "MODEL")
        c.drawRightString(COL_PG, y, "PG")
        y -= 0.85 * cm

        for i, entry in enumerate(page_entries):
            row_num = page_idx * rows_per_page() + i + 1
            if i % 2 == 0:
                c.setFillColor(colors.HexColor('#f8f8f8'))
                c.rect(LEFT - 0.2 * cm, y - 0.12 * cm,
                       PAGE_W - 2 * LEFT + 0.4 * cm, ROW_H, fill=1, stroke=0)

            c.setFont("Helvetica", 8)
            c.setFillColor(colors.HexColor('#aaaaaa'))
            c.drawString(COL_NUM, y + 0.14 * cm, str(row_num))

            c.setFillColor(colors.HexColor('#888888'))
            brand = entry.get('brand', '')
            c.drawString(COL_BRAND, y + 0.14 * cm, brand[:16])

            c.setFont("Helvetica-Bold", 10)
            c.setFillColor(colors.HexColor('#1a56db'))
            c.drawString(COL_MODEL, y + 0.12 * cm, entry['display_name'])

            c.setFont("Helvetica", 9)
            c.setFillColor(colors.HexColor('#444444'))
            c.drawRightString(COL_PG, y + 0.12 * cm, str(entry['display_page']))

            entry['link_rect'] = (
                LEFT - 0.2 * cm, y - 0.12 * cm,
                PAGE_W - LEFT + 0.2 * cm, y + ROW_H - 0.12 * cm,
            )
            entry['link_page'] = page_idx

            y -= ROW_H

        if page_idx < num_pages - 1:
            c.showPage()

    c.save()
    return entries


def _merge_pdfs(paths: list[Path]) -> Path:
    tmp = Path(tempfile.mktemp(suffix='_merged.pdf'))
    writer = PdfWriter()
    for path in paths:
        for page in PdfReader(path).pages:
            writer.add_page(page)
    with open(tmp, 'wb') as f:
        writer.write(f)
    return tmp


def build_combined(output_dir: Path, review_csv: Path | None = None, output_name: str = "combined.pdf"):
    brand_map = load_brand_map(review_csv) if review_csv and review_csv.exists() else {}
    devices = collect_device_pdfs(output_dir)

    if not devices:
        print(f"No device PDFs found in {output_dir}")
        return

    # Apply merge groups
    tmp_files: list[Path] = []
    for merged_key, members in MERGE_GROUPS.items():
        grouped: dict[str, list[Path]] = {}
        for member in members:
            if member not in devices:
                continue
            for doc_type, path in devices.pop(member).items():
                grouped.setdefault(doc_type, []).append(path)
        if grouped:
            devices[merged_key] = {}
            for dt, paths in grouped.items():
                seen, unique_paths = set(), []
                for p in paths:
                    h = hashlib.md5(p.read_bytes()).hexdigest()
                    if h not in seen:
                        seen.add(h)
                        unique_paths.append(p)
                if len(unique_paths) == 1:
                    devices[merged_key][dt] = unique_paths[0]
                else:
                    merged = _merge_pdfs(unique_paths)
                    tmp_files.append(merged)
                    devices[merged_key][dt] = merged

    sorted_keys = sorted(devices.keys())

    # Ordered list of (key, doc_type, path) — datasheet before manual per device
    ordered: list[tuple[str, str, Path]] = []
    for key in sorted_keys:
        docs = devices[key]
        if 'datasheet+manual' in docs:
            ordered.append((key, 'datasheet+manual', docs['datasheet+manual']))
        else:
            if 'datasheet' in docs:
                ordered.append((key, 'datasheet', docs['datasheet']))
            if 'manual' in docs:
                ordered.append((key, 'manual', docs['manual']))

    # Calculate index page count to know where content starts
    rpp = rows_per_page()
    index_page_count = max(1, -(-len(sorted_keys) // rpp))

    # Calculate page offsets
    offset = index_page_count
    offsets: list[tuple[str, str, Path, int, int]] = []
    for key, doc_type, path in ordered:
        count = len(PdfReader(path).pages)
        offsets.append((key, doc_type, path, offset, count))
        offset += count

    device_first_page: dict[str, int] = {}
    for key, _, _, start, _ in offsets:
        device_first_page.setdefault(key, start)

    # Build index entries
    entries = []
    for key in sorted_keys:
        docs = devices[key]
        if 'datasheet+manual' in docs:
            doc_label = 'DS+MAN'
        else:
            parts = []
            if 'datasheet' in docs:
                parts.append('DS')
            if 'manual' in docs:
                parts.append('MAN')
            doc_label = '+'.join(parts)

        entries.append({
            'key': key,
            'display_name': key.replace('_', ' '),
            'brand': brand_map.get(key, ''),
            'doc_label': doc_label,
            'target_page': device_first_page[key],
            'display_page': device_first_page[key] + 1,
        })

    tmp = Path(tempfile.mktemp(suffix='_index.pdf'))
    entries = generate_index_pdf(entries, tmp)

    writer = PdfWriter()

    for page in PdfReader(tmp).pages:
        writer.add_page(page)

    for key, doc_type, path, start, _ in offsets:
        for page in PdfReader(path).pages:
            writer.add_page(page)
        writer.add_outline_item(f"{key.replace('_', ' ')} ({doc_type})", start)

    for entry in entries:
        if 'link_rect' not in entry:
            continue
        writer.add_annotation(
            page_number=entry['link_page'],
            annotation=Link(
                rect=entry['link_rect'],
                target_page_index=entry['target_page'],
            ),
        )

    output_path = output_dir / output_name
    with open(output_path, 'wb') as f:
        writer.write(f)

    tmp.unlink(missing_ok=True)
    for t in tmp_files:
        t.unlink(missing_ok=True)

    content_pages = sum(c for _, _, _, _, c in offsets)
    print(f"Written: {output_path}")
    print(f"  {len(sorted_keys)} devices | {index_page_count} index page(s) | {content_pages} content pages | {index_page_count + content_pages} total")


if __name__ == '__main__':
    output_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else find_latest_output()
    output_name = sys.argv[2] if len(sys.argv) > 2 else "combined.pdf"
    if not output_dir:
        print("No output directory found.")
        sys.exit(1)
    print(f"Output dir: {output_dir}")
    build_combined(output_dir, Path(__file__).parent / "input" / "review.csv", output_name)
