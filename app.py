import sqlite3
import json
import streamlit as st
import pandas as pd
from barcode import Code128
from barcode.writer import ImageWriter
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import base64
from streamlit.components.v1 import html

# ---------------------------------------------------------
# DATABASE SETUP (Using Permanent DB Name so data NEVER gets deleted)
# ---------------------------------------------------------
DB_NAME = "raut_enterprise_permanent.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            force_logout INTEGER DEFAULT 0
        )
    """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT UNIQUE NOT NULL,
            barcode_id TEXT UNIQUE NOT NULL,
            price TEXT DEFAULT '',
            slot_category TEXT DEFAULT ''
        )
    """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS trash_bin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_id INTEGER,
            item_name TEXT UNIQUE NOT NULL,
            barcode_id TEXT UNIQUE NOT NULL,
            price TEXT DEFAULT '',
            slot_category TEXT DEFAULT '',
            deleted_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS downloaded_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            barcode_id TEXT NOT NULL,
            price TEXT DEFAULT '',
            slot_category TEXT DEFAULT '',
            downloaded_by TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            action_type TEXT NOT NULL,
            details TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_name TEXT UNIQUE NOT NULL,
            description_text TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """
    )

    c.execute("SELECT id FROM custom_pages ORDER BY id ASC LIMIT 1")
    first_page = c.fetchone()
    if not first_page:
        c.execute(
            "INSERT INTO custom_pages (page_name, description_text, updated_by) VALUES (?, ?, ?)",
            ("Page 1 - Main Sheet", "", "system"),
        )
        conn.commit()
        c.execute("SELECT id FROM custom_pages ORDER BY id ASC LIMIT 1")
        first_page = c.fetchone()
    default_page_id = first_page[0]

    c.execute(
        """
        CREATE TABLE IF NOT EXISTS custom_sheet_rows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL DEFAULT 1,
            product TEXT DEFAULT '',
            history_note TEXT DEFAULT '',
            price TEXT DEFAULT '',
            extra_info TEXT DEFAULT '',
            updated_by TEXT DEFAULT '',
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (page_id) REFERENCES custom_pages(id) ON DELETE CASCADE
        )
    """
    )

    c.execute("PRAGMA table_info(custom_sheet_rows)")
    existing_cols = [col[1] for col in c.fetchall()]
    if "page_id" not in existing_cols:
        c.execute(
            f"ALTER TABLE custom_sheet_rows ADD COLUMN page_id INTEGER NOT NULL DEFAULT {default_page_id}"
        )
        c.execute(
            "UPDATE custom_sheet_rows SET page_id = ? WHERE page_id IS NULL OR page_id = 0",
            (default_page_id,),
        )

    c.execute("SELECT * FROM users WHERE username = ?", ("admin",))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users (username, password, role, force_logout) VALUES (?, ?, ?, ?)",
            ("admin", "raut123", "Admin", 0),
        )

    c.execute("SELECT * FROM users WHERE username = ?", ("employee",))
    if not c.fetchone():
        c.execute(
            "INSERT INTO users (username, password, role, force_logout) VALUES (?, ?, ?, ?)",
            ("employee", "emp123", "Employee", 0),
        )

    c.execute("SELECT * FROM inventory")
    if not c.fetchone():
        c.execute(
            "INSERT INTO inventory (item_name, barcode_id, price, slot_category) VALUES (?, ?, ?, ?)",
            ("10 no nut - 1/2 inch", "0000000000001", "₹50", "Hardware"),
        )

    conn.commit()
    conn.close()

init_db()

def get_db_connection():
    conn = sqlite3.connect(DB_NAME)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

def log_activity(username, action_type, details):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO activity_logs (username, action_type, details) VALUES (?, ?, ?)",
            (username, action_type, details)
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

def record_download_history(item_name, barcode_id, username):
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "SELECT price, slot_category FROM inventory WHERE barcode_id = ?",
            (barcode_id,)
        )
        res = c.fetchone()
        price = res[0] if res else ""
        slot = res[1] if res else ""
        
        c.execute(
            "INSERT INTO downloaded_history (item_name, barcode_id, price, slot_category, downloaded_by) VALUES (?, ?, ?, ?, ?)",
            (item_name, barcode_id, price, slot, username)
        )
        conn.commit()
        conn.close()
        log_activity(username, "DOWNLOAD_PRINT_STICKER", f"Downloaded/Printed sticker for {item_name} ({barcode_id}).")
    except Exception:
        pass

def _load_font(size_px, bold=True):
    font_paths = (
        [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
            "DejaVuSans-Bold.ttf",
            "arialbd.ttf",
        ]
        if bold
        else [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
            "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
            "DejaVuSans.ttf",
            "arial.ttf",
        ]
    )
    for path in font_paths:
        try:
            return ImageFont.truetype(path, size_px)
        except IOError:
            continue
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()

def _render_scannable_bars_only(barcode_text, scale=4):
    """Generates clean, high-contrast Code128 bars without overlapping text."""
    writer_options = {
        "module_width": 0.36 * scale,
        "module_height": 14.0 * scale,
        "quiet_zone": 5 * scale,
        "write_text": False,
    }
    rv = BytesIO()
    Code128(str(barcode_text), writer=ImageWriter()).write(rv, options=writer_options)
    rv.seek(0)
    return Image.open(rv).convert("RGB")

# Helper function: Clean professional sticker sized for 63.5mm x 33.9mm (24 stickers per A4 sheet, 3x8)
def generate_custom_barcode_image(item_name, barcode_text):
    scale = 4
    target_w = 635 * scale
    target_h = 339 * scale
    sticker_img = Image.new("RGB", (target_w, target_h), "white")
    draw = ImageDraw.Draw(sticker_img)

    font_title = _load_font(26 * scale, bold=True)
    font_code = _load_font(18 * scale, bold=True)

    text_to_draw = f"{item_name.upper()}"
    bbox = draw.textbbox((0, 0), text_to_draw, font=font_title)
    text_w = bbox[2] - bbox[0]
    if text_w > target_w - 36 * scale:
        text_to_draw = text_to_draw[:26] + "..."
        bbox = draw.textbbox((0, 0), text_to_draw, font=font_title)
        text_w = bbox[2] - bbox[0]

    text_x = max(12 * scale, (target_w - text_w) // 2)
    text_y = 12 * scale
    draw.text((text_x, text_y), text_to_draw, fill="#111111", font=font_title)

    line_y = 50 * scale
    draw.line([(20 * scale, line_y), (target_w - 20 * scale, line_y)], fill="#222222", width=2 * scale)

    bc_str = str(barcode_text).strip()
    code_bbox = draw.textbbox((0, 0), bc_str, font=font_code)
    code_h = (code_bbox[3] - code_bbox[1]) + 10 * scale

    bars_img = _render_scannable_bars_only(bc_str, scale=scale)
    bw, bh = bars_img.size

    avail_h = max(40 * scale, target_h - (line_y + 12 * scale) - code_h)
    avail_w = target_w - 32 * scale
    ratio = min(avail_w / max(1, bw), avail_h / max(1, bh))
    new_bw = max(1, int(bw * ratio))
    new_bh = max(1, int(bh * ratio))
    resized_bars = bars_img.resize((new_bw, new_bh), Image.Resampling.LANCZOS)

    bc_x = (target_w - new_bw) // 2
    bc_y = line_y + 8 * scale + max(0, (avail_h - new_bh) // 2)
    sticker_img.paste(resized_bars, (bc_x, bc_y))

    code_w = code_bbox[2] - code_bbox[0]
    code_x = max(12 * scale, (target_w - code_w) // 2)
    code_y = min(target_h - code_h + 2 * scale, bc_y + new_bh + 6 * scale)
    draw.text((code_x, code_y), bc_str, fill="#111111", font=font_code)

    final_w = target_w // scale
    final_h = target_h // scale
    sticker_img = sticker_img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    final_io = BytesIO()
    sticker_img.save(final_io, format="PNG")
    final_io.seek(0)
    return final_io

def generate_vendor_custom_sticker(
    barcode_no,
    sku_code="",
    batch_code="",
    net_qty="",
    mrp_text="",
    serial_code="",
):
    """
    Generates a clean, scannable sticker sized for 105mm x 70mm (A4-8 sheet, 2x4)
    with manually entered barcode number and optional fields.
    """
    scale = 2
    target_w = 1050 * scale
    target_h = 700 * scale
    img = Image.new("RGB", (target_w, target_h), "white")
    draw = ImageDraw.Draw(img)

    font_sku = _load_font(34 * scale, bold=True)
    font_meta = _load_font(24 * scale, bold=True)
    font_mrp = _load_font(26 * scale, bold=True)
    font_code = _load_font(28 * scale, bold=True)
    font_serial = _load_font(24 * scale, bold=True)

    pad_x = 32 * scale
    y_top = 24 * scale

    right_parts = []
    if batch_code.strip():
        right_parts.append(batch_code.strip())
    if net_qty.strip():
        right_parts.append(net_qty.strip())
    right_str = "    ".join(right_parts)

    right_w = 0
    right_h = 0
    if right_str:
        rb = draw.textbbox((0, 0), right_str, font=font_meta)
        right_w = rb[2] - rb[0]
        right_h = rb[3] - rb[1]
        draw.text((target_w - pad_x - right_w, y_top + 5 * scale), right_str, fill="#111111", font=font_meta)

    sku_h = 0
    if sku_code.strip():
        max_sku_w = max(140 * scale, target_w - right_w - (pad_x * 2) - 24 * scale)
        sku_txt = sku_code.strip().upper()
        sb = draw.textbbox((0, 0), sku_txt, font=font_sku)
        while sb[2] - sb[0] > max_sku_w and len(sku_txt) > 3:
            sku_txt = sku_txt[:-1]
            sb = draw.textbbox((0, 0), sku_txt, font=font_sku)
        sku_h = sb[3] - sb[1]
        draw.text((pad_x, y_top), sku_txt, fill="#111111", font=font_sku)

    y_cursor = y_top + max(sku_h, right_h) + 16 * scale

    if mrp_text.strip():
        mrp_clean = mrp_text.strip()
        if "mrp" not in mrp_clean.lower():
            mrp_clean = f"MRP Rs. {mrp_clean} (Incl.of all taxes)"
        mb = draw.textbbox((0, 0), mrp_clean, font=font_mrp)
        mw = mb[2] - mb[0]
        if mw > target_w - (pad_x * 2):
            mrp_clean = mrp_clean[:46] + "..."
            mb = draw.textbbox((0, 0), mrp_clean, font=font_mrp)
        draw.text((pad_x, y_cursor), mrp_clean, fill="#111111", font=font_mrp)
        y_cursor += (mb[3] - mb[1]) + 18 * scale

    serial_clean = serial_code.strip()
    serial_h = 0
    if serial_clean:
        ser_b = draw.textbbox((0, 0), serial_clean, font=font_serial)
        serial_h = (ser_b[3] - ser_b[1]) + 14 * scale

    bc_str = str(barcode_no).strip()
    code_b = draw.textbbox((0, 0), bc_str, font=font_code)
    code_h = (code_b[3] - code_b[1]) + 16 * scale

    bars_area_h = max(90 * scale, target_h - y_cursor - code_h - serial_h - 18 * scale)
    bars_img = _render_scannable_bars_only(bc_str, scale=scale)
    bw, bh = bars_img.size

    bars_area_w = target_w - (pad_x * 2)
    ratio = min(bars_area_w / max(1, bw), bars_area_h / max(1, bh))
    new_bw = max(1, int(bw * ratio))
    new_bh = max(1, int(bh * ratio))
    resized_bars = bars_img.resize((new_bw, new_bh), Image.Resampling.LANCZOS)

    bc_x = (target_w - new_bw) // 2
    bc_y = y_cursor + max(0, (bars_area_h - new_bh) // 2)
    img.paste(resized_bars, (bc_x, bc_y))

    code_w = code_b[2] - code_b[0]
    code_x = max(pad_x, (target_w - code_w) // 2)
    code_y = bc_y + new_bh + 10 * scale
    draw.text((code_x, code_y), bc_str, fill="#111111", font=font_code)

    if serial_clean:
        ser_b = draw.textbbox((0, 0), serial_clean, font=font_serial)
        sw = ser_b[2] - ser_b[0]
        if sw > target_w - (pad_x * 2):
            serial_clean = serial_clean[:42] + "..."
            ser_b = draw.textbbox((0, 0), serial_clean, font=font_serial)
            sw = ser_b[2] - ser_b[0]
        ser_x = max(pad_x, (target_w - sw) // 2)
        ser_y = min(target_h - (ser_b[3] - ser_b[1]) - 16 * scale, code_y + (code_b[3] - code_b[1]) + 12 * scale)
        draw.text((ser_x, ser_y), serial_clean, fill="#111111", font=font_serial)

    final_img = img.resize((target_w // scale, target_h // scale), Image.Resampling.LANCZOS)
    out_io = BytesIO()
    final_img.save(out_io, format="PNG")
    out_io.seek(0)
    return out_io

def build_a4_8_grid_html(slots_8, auto_print=False):
    """slots_8: list of 8 dicts {'name','barcode','b64'} or None (2 cols x 4 rows, 105mm x 70mm)."""
    cells_html = []
    for idx in range(8):
        slot = slots_8[idx]
        if slot:
            cells_html.append(
                f"""
                <div class="label-cell">
                    <img src="data:image/png;base64,{slot['b64']}" />
                </div>
                """
            )
        else:
            cells_html.append('<div class="label-cell blank-cell"></div>')

    grid_body = "\n".join(cells_html)
    print_script = (
        """
        <script>
            window.onload = function() {
                setTimeout(function() {
                    window.print();
                }, 300);
            };
        </script>
        """
        if auto_print
        else ""
    )

    return f"""
    <html>
    <head>
        <meta charset="utf-8" />
        <title>A4 8-Sticker Sheet (105mm x 70mm)</title>
        <style>
            @page {{
                size: A4 portrait;
                margin: 0;
            }}
            * {{
                box-sizing: border-box;
                page-break-inside: avoid;
                break-inside: avoid;
            }}
            html, body {{
                margin: 0;
                padding: 0;
                width: 210mm;
                height: 294mm;
                overflow: hidden;
                background: #ffffff;
            }}
            .a4-sheet {{
                width: 210mm;
                height: 280mm;
                margin: 8.5mm 0 0 0;
                background: white;
                display: grid;
                grid-template-columns: repeat(2, 105mm);
                grid-template-rows: repeat(4, 70mm);
                page-break-after: avoid;
                break-after: avoid;
            }}
            .label-cell {{
                width: 105mm;
                height: 70mm;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 1mm;
                overflow: hidden;
            }}
            .label-cell img {{
                width: 103mm;
                height: 68mm;
                object-fit: contain;
            }}
        </style>
    </head>
    <body>
        <div class="a4-sheet">
            {grid_body}
        </div>
        {print_script}
    </body>
    </html>
    """

def render_interactive_8_sheet_and_print(sticker_item, enabled_box_indices):
    """
    Renders an interactive 8-box (2x4) A4 sheet (105mm x 70mm per sticker) where clicking
    any box toggles selection and printing outputs a clean 1-page A4 sheet.
    """
    item_json = json.dumps(sticker_item)
    enabled_json = json.dumps(sorted(list(set(enabled_box_indices))))

    component_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8" />
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            padding: 6px 0 12px 0;
            font-family: 'Segoe UI', Arial, sans-serif;
            background: transparent;
        }}
        .top-bar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 10px;
            padding: 9px 14px;
            background: #f8fafc;
            border-radius: 8px;
            border: 1px solid #cbd5e1;
        }}
        .top-bar .info {{
            font-size: 13px;
            color: #0f172a;
            font-weight: 600;
        }}
        .top-bar .btn-group {{
            display: flex;
            gap: 8px;
        }}
        button.action-btn {{
            border: none;
            border-radius: 6px;
            padding: 7px 14px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-clear {{
            background: #e2e8f0;
            color: #1e293b;
        }}
        .btn-print {{
            background: #16a34a;
            color: #ffffff;
        }}
        .sheet-wrapper {{
            width: 100%;
            max-width: 660px;
            aspect-ratio: 210 / 280;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            display: grid;
            grid-template-columns: repeat(2, 1fr);
            grid-template-rows: repeat(4, 1fr);
            box-shadow: 0 3px 12px rgba(15, 23, 42, 0.08);
            border-radius: 4px;
        }}
        .box-cell {{
            border: 1.5px solid #334155;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 4px;
            cursor: pointer;
            position: relative;
            user-select: none;
            transition: background 0.12s ease;
            overflow: hidden;
        }}
        .box-cell:hover {{
            background: #f0fdf4;
            border-color: #16a34a;
        }}
        .box-cell.selected {{
            border: 2px solid #15803d;
            background: #ffffff;
        }}
        .box-num {{
            position: absolute;
            top: 4px;
            left: 6px;
            font-size: 11px;
            color: #475569;
            font-weight: 700;
            background: rgba(255,255,255,0.9);
            padding: 1px 5px;
            border-radius: 3px;
        }}
        .box-empty-hint {{
            font-size: 12px;
            color: #94a3b8;
        }}
        .sticker-img {{
            width: 98%;
            height: 96%;
            object-fit: contain;
        }}
    </style>
    </head>
    <body>
        <div class="top-bar">
            <div class="info" id="selectionSummary8">Selected: 0 / 8 — Click any box to toggle</div>
            <div class="btn-group">
                <button type="button" class="action-btn btn-clear" onclick="clearAllBoxes8()">Clear Boxes</button>
                <button type="button" class="action-btn btn-print" onclick="triggerSinglePagePrint8()">🖨️ Print Selected (1 Page)</button>
            </div>
        </div>

        <div class="sheet-wrapper" id="stickerSheet8"></div>

        <script>
            const stickerItem = {item_json};
            let selectedSet8 = new Set({enabled_json});

            function updateSummary8() {{
                const arr = Array.from(selectedSet8).sort((a, b) => a - b);
                const labelText = arr.length > 0 ? "Boxes #" + arr.join(", #") : "None";
                document.getElementById("selectionSummary8").innerText =
                    "Selected: " + arr.length + " / 8 (" + labelText + ") — Click any box to toggle";
            }}

            function renderSheet8() {{
                const sheet = document.getElementById("stickerSheet8");
                sheet.innerHTML = "";

                for (let i = 1; i <= 8; i++) {{
                    const isActive = selectedSet8.has(i) && stickerItem !== null;

                    const cell = document.createElement("div");
                    cell.className = "box-cell" + (isActive ? " selected" : "");
                    cell.onclick = function() {{
                        if (selectedSet8.has(i)) {{
                            selectedSet8.delete(i);
                        }} else {{
                            selectedSet8.add(i);
                        }}
                        renderSheet8();
                    }};

                    const numTag = document.createElement("span");
                    numTag.className = "box-num";
                    numTag.innerText = "#" + i;
                    cell.appendChild(numTag);

                    if (isActive) {{
                        const imgEl = document.createElement("img");
                        imgEl.className = "sticker-img";
                        imgEl.src = "data:image/png;base64," + stickerItem.b64;
                        cell.appendChild(imgEl);
                    }} else {{
                        const hintEl = document.createElement("span");
                        hintEl.className = "box-empty-hint";
                        hintEl.innerText = "Off";
                        cell.appendChild(hintEl);
                    }}

                    sheet.appendChild(cell);
                }}
                updateSummary8();
            }}

            function clearAllBoxes8() {{
                selectedSet8.clear();
                renderSheet8();
            }}

            function triggerSinglePagePrint8() {{
                let printableCount = 0;
                let cellsMarkup = "";

                for (let i = 1; i <= 8; i++) {{
                    if (selectedSet8.has(i) && stickerItem) {{
                        printableCount += 1;
                        cellsMarkup += '<div class="cell"><img src="data:image/png;base64,' + stickerItem.b64 + '" /></div>';
                    }} else {{
                        cellsMarkup += '<div class="cell"></div>';
                    }}
                }}

                if (printableCount === 0) {{
                    alert("Pehle kam se kam 1 active sticker box select karein.");
                    return;
                }}

                const printWin = window.open("", "_blank", "width=850,height=1100");
                if (!printWin) {{
                    alert("Browser ne popup block kiya hai. Please allow popups for this site ya Download 1-Page A4 Print File (.html) use karein.");
                    return;
                }}

                printWin.document.open();
                printWin.document.write(`
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="utf-8" />
                        <title>A4 8-Sticker Print Sheet (105mm x 70mm)</title>
                        <style>
                            @page {{
                                size: A4 portrait;
                                margin: 0;
                            }}
                            * {{
                                box-sizing: border-box;
                            }}
                            html, body {{
                                margin: 0;
                                padding: 0;
                                width: 210mm;
                                height: 294mm;
                                overflow: hidden;
                                background: #ffffff;
                            }}
                            .sheet {{
                                width: 210mm;
                                height: 280mm;
                                margin: 8.5mm 0 0 0;
                                display: grid;
                                grid-template-columns: repeat(2, 105mm);
                                grid-template-rows: repeat(4, 70mm);
                            }}
                            .cell {{
                                width: 105mm;
                                height: 70mm;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                padding: 1mm;
                                overflow: hidden;
                            }}
                            .cell img {{
                                width: 103mm;
                                height: 68mm;
                                object-fit: contain;
                            }}
                        </style>
                    </head>
                    <body>
                        <div class="sheet">${{cellsMarkup}}</div>
                        <script>
                            window.onload = function() {{
                                setTimeout(function() {{
                                    window.focus();
                                    window.print();
                                }}, 250);
                            }};
                        <\/script>
                    </body>
                    </html>
                `);
                printWin.document.close();
            }}

            renderSheet8();
        </script>
    </body>
    </html>
    """
    html(component_html, height=960)

def render_clickable_vendor_sticker(item, key_prefix="vendor_click"):
    """Renders a single 105mm x 70mm custom sticker card that prints directly on click."""
    if not item:
        return
    payload = json.dumps(item)
    comp_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8" />
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            padding: 4px 0 10px 0;
            font-family: 'Segoe UI', Arial, sans-serif;
            background: transparent;
        }}
        .sticker-card {{
            max-width: 440px;
            border: 1.5px solid #cbd5e1;
            border-radius: 8px;
            padding: 12px;
            background: #ffffff;
            cursor: pointer;
            transition: all 0.14s ease;
            box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
        }}
        .sticker-card:hover {{
            border-color: #16a34a;
            background: #f0fdf4;
            transform: translateY(-1px);
            box-shadow: 0 4px 10px rgba(22, 163, 74, 0.14);
        }}
        .sticker-card img {{
            width: 100%;
            height: auto;
            display: block;
            border-radius: 4px;
            border: 1px solid #e2e8f0;
        }}
        .card-meta {{
            margin-top: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            font-size: 12px;
            color: #334155;
            font-weight: 600;
        }}
        .print-badge {{
            background: #dcfce7;
            color: #166534;
            padding: 3px 9px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 700;
            white-space: nowrap;
        }}
    </style>
    </head>
    <body>
        <div class="sticker-card" id="card_{key_prefix}" onclick="printVendorOne()">
            <img id="img_{key_prefix}" src="" />
            <div class="card-meta">
                <span id="meta_{key_prefix}"></span>
                <span class="print-badge">Click to Print (105×70mm)</span>
            </div>
        </div>
        <script>
            const vItem_{key_prefix} = {payload};
            document.getElementById("img_{key_prefix}").src = "data:image/png;base64," + vItem_{key_prefix}.b64;
            document.getElementById("meta_{key_prefix}").innerText = "ID: " + vItem_{key_prefix}.barcode;

            function printVendorOne() {{
                const win = window.open("", "_blank", "width=850,height=1100");
                if (!win) {{
                    alert("Browser ne popup block kiya hai. Please allow popups for this site.");
                    return;
                }}
                win.document.open();
                win.document.write(`
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="utf-8" />
                        <title>${{vItem_{key_prefix}.name}}</title>
                        <style>
                            @page {{
                                size: A4 portrait;
                                margin: 0;
                            }}
                            * {{
                                box-sizing: border-box;
                            }}
                            html, body {{
                                margin: 0;
                                padding: 0;
                                width: 210mm;
                                height: 294mm;
                                overflow: hidden;
                                background: #ffffff;
                            }}
                            .sheet {{
                                width: 210mm;
                                height: 280mm;
                                margin: 8.5mm 0 0 0;
                                display: grid;
                                grid-template-columns: repeat(2, 105mm);
                                grid-template-rows: repeat(4, 70mm);
                            }}
                            .cell {{
                                width: 105mm;
                                height: 70mm;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                padding: 1mm;
                            }}
                            .cell img {{
                                width: 103mm;
                                height: 68mm;
                                object-fit: contain;
                            }}
                        </style>
                    </head>
                    <body>
                        <div class="sheet">
                            <div class="cell">
                                <img src="data:image/png;base64,${{vItem_{key_prefix}.b64}}" />
                            </div>
                        </div>
                        <script>
                            window.onload = function() {{
                                setTimeout(function() {{
                                    window.focus();
                                    window.print();
                                }}, 250);
                            }};
                        <\/script>
                    </body>
                    </html>
                `);
                win.document.close();
            }}
        </script>
    </body>
    </html>
    """
    html(comp_html, height=345)

def build_a4_24_grid_html(slots_data, auto_print=False):
    """slots_data: list of 24 dicts {'name','barcode','b64'} or None (3 cols x 8 rows, 63.5mm x 33.9mm)."""
    cells_html = []
    for idx in range(24):
        slot = slots_data[idx]
        if slot:
            cells_html.append(
                f"""
                <div class="label-cell">
                    <img src="data:image/png;base64,{slot['b64']}" />
                </div>
                """
            )
        else:
            cells_html.append('<div class="label-cell blank-cell"></div>')

    grid_body = "\n".join(cells_html)
    print_script = (
        """
        <script>
            window.onload = function() {
                setTimeout(function() {
                    window.print();
                }, 300);
            };
        </script>
        """
        if auto_print
        else ""
    )

    return f"""
    <html>
    <head>
        <meta charset="utf-8" />
        <title>A4 24-Sticker Sheet (63.5mm x 33.9mm)</title>
        <style>
            @page {{
                size: A4 portrait;
                margin: 0;
            }}
            * {{
                box-sizing: border-box;
                page-break-inside: avoid;
                break-inside: avoid;
            }}
            html, body {{
                margin: 0;
                padding: 0;
                width: 210mm;
                height: 294mm;
                overflow: hidden;
                background: #ffffff;
            }}
            .a4-sheet {{
                width: 190.5mm;
                height: 270mm;
                margin: 12mm 9.75mm 0 9.75mm;
                background: white;
                display: grid;
                grid-template-columns: repeat(3, 63.5mm);
                grid-template-rows: repeat(8, 33.75mm);
                page-break-after: avoid;
                break-after: avoid;
            }}
            .label-cell {{
                width: 63.5mm;
                height: 33.75mm;
                display: flex;
                justify-content: center;
                align-items: center;
                padding: 0.5mm;
                overflow: hidden;
            }}
            .label-cell img {{
                width: 62.5mm;
                height: 32.8mm;
                object-fit: contain;
            }}
        </style>
    </head>
    <body>
        <div class="a4-sheet">
            {grid_body}
        </div>
        {print_script}
    </body>
    </html>
    """

def render_interactive_24_sheet_and_print(items_catalog, box_item_map, enabled_box_indices):
    """
    Renders an interactive 24-box (3x8) A4 sticker sheet where each box (#1 to #24) can have
    its own product/barcode from items_catalog, clicking toggles active boxes, and printing
    outputs a clean 1-page A4 sheet.
    """
    catalog_json = json.dumps(items_catalog)
    box_map_json = json.dumps(box_item_map)
    enabled_json = json.dumps(sorted(list(set(enabled_box_indices))))

    component_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8" />
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            padding: 6px 0 12px 0;
            font-family: 'Segoe UI', Arial, sans-serif;
            background: transparent;
        }}
        .top-bar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 10px;
            padding: 9px 14px;
            background: #f8fafc;
            border-radius: 8px;
            border: 1px solid #cbd5e1;
        }}
        .top-bar .info {{
            font-size: 13px;
            color: #0f172a;
            font-weight: 600;
        }}
        .top-bar .btn-group {{
            display: flex;
            gap: 8px;
        }}
        button.action-btn {{
            border: none;
            border-radius: 6px;
            padding: 7px 14px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-clear {{
            background: #e2e8f0;
            color: #1e293b;
        }}
        .btn-print {{
            background: #16a34a;
            color: #ffffff;
        }}
        .sheet-wrapper {{
            width: 100%;
            max-width: 720px;
            aspect-ratio: 210 / 297;
            border: 1px solid #cbd5e1;
            background: #ffffff;
            padding: 4.1% 4.6%;
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            grid-template-rows: repeat(8, 1fr);
            box-shadow: 0 3px 12px rgba(15, 23, 42, 0.08);
            border-radius: 4px;
        }}
        .box-cell {{
            border: 1px dashed #cbd5e1;
            display: flex;
            justify-content: center;
            align-items: center;
            padding: 2px;
            cursor: pointer;
            position: relative;
            user-select: none;
            transition: background 0.12s ease;
            overflow: hidden;
        }}
        .box-cell:hover {{
            background: #f0fdf4;
            border-color: #16a34a;
        }}
        .box-cell.selected {{
            border: 1.5px solid #15803d;
            background: #ffffff;
        }}
        .box-num {{
            position: absolute;
            top: 2px;
            left: 4px;
            font-size: 9px;
            color: #64748b;
            font-weight: 700;
            background: rgba(255,255,255,0.9);
            padding: 0 3px;
            border-radius: 2px;
        }}
        .box-empty-hint {{
            font-size: 10px;
            color: #94a3b8;
        }}
        .sticker-img {{
            width: 98%;
            height: 95%;
            object-fit: contain;
        }}
    </style>
    </head>
    <body>
        <div class="top-bar">
            <div class="info" id="selectionSummary">Selected: 0 / 24 — Click any box to toggle</div>
            <div class="btn-group">
                <button type="button" class="action-btn btn-clear" onclick="clearAllBoxes()">Clear Boxes</button>
                <button type="button" class="action-btn btn-print" onclick="triggerSinglePagePrint()">🖨️ Print Selected (1 Page)</button>
            </div>
        </div>

        <div class="sheet-wrapper" id="stickerSheet"></div>

        <script>
            const catalog = {catalog_json};
            const boxItemMap = {box_map_json};
            let selectedSet = new Set({enabled_json});

            function getItemForBox(boxNum) {{
                const key = boxItemMap[String(boxNum)];
                return (key && catalog[key]) ? catalog[key] : null;
            }}

            function updateSummary() {{
                const arr = Array.from(selectedSet).sort((a, b) => a - b);
                const labelText = arr.length > 0 ? "Boxes #" + arr.join(", #") : "None";
                document.getElementById("selectionSummary").innerText =
                    "Selected: " + arr.length + " / 24 (" + labelText + ") — Click any box to toggle";
            }}

            function renderSheet() {{
                const sheet = document.getElementById("stickerSheet");
                sheet.innerHTML = "";

                for (let i = 1; i <= 24; i++) {{
                    const item = getItemForBox(i);
                    const isActive = selectedSet.has(i) && item !== null;

                    const cell = document.createElement("div");
                    cell.className = "box-cell" + (isActive ? " selected" : "");
                    cell.onclick = function() {{
                        if (selectedSet.has(i)) {{
                            selectedSet.delete(i);
                        }} else {{
                            selectedSet.add(i);
                        }}
                        renderSheet();
                    }};

                    const numTag = document.createElement("span");
                    numTag.className = "box-num";
                    numTag.innerText = "#" + i;
                    cell.appendChild(numTag);

                    if (isActive) {{
                        const imgEl = document.createElement("img");
                        imgEl.className = "sticker-img";
                        imgEl.src = "data:image/png;base64," + item.b64;
                        cell.appendChild(imgEl);
                    }} else {{
                        const hintEl = document.createElement("span");
                        hintEl.className = "box-empty-hint";
                        hintEl.innerText = item ? "Off" : "Empty";
                        cell.appendChild(hintEl);
                    }}

                    sheet.appendChild(cell);
                }}
                updateSummary();
            }}

            function clearAllBoxes() {{
                selectedSet.clear();
                renderSheet();
            }}

            function triggerSinglePagePrint() {{
                let printableCount = 0;
                let cellsMarkup = "";

                for (let i = 1; i <= 24; i++) {{
                    const item = getItemForBox(i);
                    if (selectedSet.has(i) && item) {{
                        printableCount += 1;
                        cellsMarkup += '<div class="cell"><img src="data:image/png;base64,' + item.b64 + '" /></div>';
                    }} else {{
                        cellsMarkup += '<div class="cell"></div>';
                    }}
                }}

                if (printableCount === 0) {{
                    alert("Pehle kam se kam 1 active sticker box select karein.");
                    return;
                }}

                const printWin = window.open("", "_blank", "width=850,height=1100");
                if (!printWin) {{
                    alert("Browser ne popup block kiya hai. Please allow popups for this site ya Download 1-Page A4 Print File (.html) use karein.");
                    return;
                }}

                printWin.document.open();
                printWin.document.write(`
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="utf-8" />
                        <title>Sticker Print Sheet</title>
                        <style>
                            @page {{
                                size: A4 portrait;
                                margin: 0;
                            }}
                            * {{
                                box-sizing: border-box;
                            }}
                            html, body {{
                                margin: 0;
                                padding: 0;
                                width: 210mm;
                                height: 294mm;
                                overflow: hidden;
                                background: #ffffff;
                            }}
                            .sheet {{
                                width: 190.5mm;
                                height: 270mm;
                                margin: 12mm 9.75mm 0 9.75mm;
                                display: grid;
                                grid-template-columns: repeat(3, 63.5mm);
                                grid-template-rows: repeat(8, 33.75mm);
                            }}
                            .cell {{
                                width: 63.5mm;
                                height: 33.75mm;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                padding: 0.5mm;
                                overflow: hidden;
                            }}
                            .cell img {{
                                width: 62.5mm;
                                height: 32.8mm;
                                object-fit: contain;
                            }}
                        </style>
                    </head>
                    <body>
                        <div class="sheet">${{cellsMarkup}}</div>
                        <script>
                            window.onload = function() {{
                                setTimeout(function() {{
                                    window.focus();
                                    window.print();
                                }}, 250);
                            }};
                        <\/script>
                    </body>
                    </html>
                `);
                printWin.document.close();
            }}

            renderSheet();
        </script>
    </body>
    </html>
    """
    html(component_html, height=1060)

def render_clickable_barcode_list(items_list, key_prefix="bc_click"):
    """
    Renders each barcode as a clickable sticker card: clicking any sticker opens a clean
    single-sticker print window for that exact sticker (no separate 'Print Single Sticker' button).
    """
    if not items_list:
        return

    payload = json.dumps(items_list)
    comp_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
    <meta charset="utf-8" />
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            margin: 0;
            padding: 4px 0 10px 0;
            font-family: 'Segoe UI', Arial, sans-serif;
            background: transparent;
        }}
        .sticker-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
            gap: 14px;
        }}
        .sticker-card {{
            border: 1.5px solid #cbd5e1;
            border-radius: 8px;
            padding: 12px;
            background: #ffffff;
            cursor: pointer;
            transition: all 0.14s ease;
            box-shadow: 0 1px 4px rgba(15, 23, 42, 0.05);
        }}
        .sticker-card:hover {{
            border-color: #16a34a;
            background: #f0fdf4;
            transform: translateY(-1px);
            box-shadow: 0 4px 10px rgba(22, 163, 74, 0.14);
        }}
        .sticker-card img {{
            width: 100%;
            height: auto;
            display: block;
            border-radius: 4px;
        }}
        .card-meta {{
            margin-top: 10px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            font-size: 11px;
            color: #334155;
            font-weight: 600;
        }}
        .print-badge {{
            background: #dcfce7;
            color: #166534;
            padding: 3px 8px;
            border-radius: 999px;
            font-size: 10px;
            font-weight: 700;
            white-space: nowrap;
        }}
    </style>
    </head>
    <body>
        <div class="sticker-grid" id="grid_{key_prefix}"></div>
        <script>
            const listData_{key_prefix} = {payload};
            const container_{key_prefix} = document.getElementById("grid_{key_prefix}");

            function printClickedSticker(item) {{
                const win = window.open("", "_blank", "width=750,height=600");
                if (!win) {{
                    alert("Browser ne popup block kiya hai. Please allow popups for this site.");
                    return;
                }}
                win.document.open();
                win.document.write(`
                    <!DOCTYPE html>
                    <html>
                    <head>
                        <meta charset="utf-8" />
                        <title>${{item.name}}</title>
                        <style>
                            @page {{
                                size: A4 portrait;
                                margin: 0;
                            }}
                            * {{
                                box-sizing: border-box;
                            }}
                            html, body {{
                                margin: 0;
                                padding: 0;
                                width: 210mm;
                                height: 294mm;
                                overflow: hidden;
                                background: #ffffff;
                            }}
                            .sheet {{
                                width: 190.5mm;
                                height: 270mm;
                                margin: 12mm 9.75mm 0 9.75mm;
                                display: grid;
                                grid-template-columns: repeat(3, 63.5mm);
                                grid-template-rows: repeat(8, 33.75mm);
                            }}
                            .cell {{
                                width: 63.5mm;
                                height: 33.75mm;
                                display: flex;
                                justify-content: center;
                                align-items: center;
                                padding: 0.5mm;
                            }}
                            .cell img {{
                                width: 62.5mm;
                                height: 32.8mm;
                                object-fit: contain;
                            }}
                        </style>
                    </head>
                    <body>
                        <div class="sheet">
                            <div class="cell">
                                <img src="data:image/png;base64,${{item.b64}}" />
                            </div>
                        </div>
                        <script>
                            window.onload = function() {{
                                setTimeout(function() {{
                                    window.focus();
                                    window.print();
                                }}, 250);
                            }};
                        <\/script>
                    </body>
                    </html>
                `);
                win.document.close();
            }}

            listData_{key_prefix}.forEach(function(item) {{
                const card = document.createElement("div");
                card.className = "sticker-card";
                card.onclick = function() {{
                    printClickedSticker(item);
                }};

                const img = document.createElement("img");
                img.src = "data:image/png;base64," + item.b64;
                card.appendChild(img);

                const meta = document.createElement("div");
                meta.className = "card-meta";
                meta.innerHTML = "<span>ID: " + item.barcode + "</span><span class='print-badge'>Click to Print</span>";
                card.appendChild(meta);

                container_{key_prefix}.appendChild(card);
            }});
        </script>
    </body>
    </html>
    """
    row_count = max(1, (len(items_list) + 2) // 3)
    comp_height = min(1400, max(225, row_count * 210 + 30))
    html(comp_html, height=comp_height)

def get_next_barcode_id():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT MAX(id) FROM inventory")
    result = c.fetchone()
    c.execute("SELECT MAX(original_id) FROM trash_bin")
    trash_max = c.fetchone()[0] or 0
    conn.close()
    
    max_id = max(result[0] or 0, trash_max)
    next_id = max_id + 1
    return f"{next_id:013d}"

# ---------------------------------------------------------
# SESSION STATE MANAGEMENT
# ---------------------------------------------------------
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.session_state.role = ""

if "active_custom_page_id" not in st.session_state:
    st.session_state.active_custom_page_id = None

if "generated_preview_item" not in st.session_state:
    st.session_state.generated_preview_item = None

if "vendor_sticker_preview" not in st.session_state:
    st.session_state.vendor_sticker_preview = None

if st.session_state.logged_in:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT force_logout FROM users WHERE username = ?", (st.session_state.username,))
    res = c.fetchone()
    conn.close()
    if res and res[0] == 1:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.warning("⚠️ Aapko Admin dwara force logout kar diya gaya hai.")
        st.rerun()

# ---------------------------------------------------------
# LOGIN SCREEN
# ---------------------------------------------------------
if not st.session_state.logged_in:
    st.title("🔐 Raut Enterprise - Login")
    st.write("Apna Username aur Password darj karke login karein.")

    with st.form("login_form"):
        u_input = st.text_input("Username")
        p_input = st.text_input("Password", type="password")
        submit_btn = st.form_submit_button("Login")

        if submit_btn:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute(
                "SELECT username, role FROM users WHERE username = ? AND password = ?",
                (u_input, p_input),
            )
            user = c.fetchone()
            if user:
                username, role = user
                c.execute("UPDATE users SET force_logout = 0 WHERE username = ?", (username,))
                conn.commit()
            conn.close()

            if user:
                username, role = user
                st.session_state.logged_in = True
                st.session_state.username = username
                st.session_state.role = role
                log_activity(username, "LOGIN", f"User {username} logged in successfully.")
                st.success("Login Successful!")
                st.rerun()
            else:
                st.error("❌ Galat Username ya Password!")

# ---------------------------------------------------------
# MAIN APPLICATION WORKFLOW
# ---------------------------------------------------------
else:
    st.sidebar.title("Raut Enterprise")
    st.sidebar.write(f"Logged in: **{st.session_state.username}** ({st.session_state.role})")

    if st.sidebar.button("Logout"):
        log_activity(st.session_state.username, "LOGOUT", f"User {st.session_state.username} logged out.")
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""
        st.rerun()

    if st.session_state.role == "Admin":
        menu = st.sidebar.radio(
            "Navigation", 
            [
                "📊 Inventory Dashboard",
                "📊 Excel Sheet & Notes",
                "📦 App Barcode Storage", 
                "🖨️ Print 24-Sticker A4 (63.5x33.9mm)",
                "🏷️ Custom / Vendor Barcode (105x70mm)",
                "📂 App Downloaded History", 
                "♻️ Trash / Bin Folder", 
                "📜 Activity Audit Logs",
                "➕ Generate & Save Barcode", 
                "⚙️ Manage & Edit Inventory", 
                "⚙️ Manage Employee Access"
            ]
        )
    else:
        menu = st.sidebar.radio(
            "Navigation", 
            [
                "📊 Inventory Dashboard",
                "📊 Excel Sheet & Notes",
                "📦 App Barcode Storage", 
                "🖨️ Print 24-Sticker A4 (63.5x33.9mm)",
                "🏷️ Custom / Vendor Barcode (105x70mm)",
                "📂 App Downloaded History", 
                "♻️ Trash / Bin Folder", 
                "➕ Generate & Save Barcode", 
                "⚙️ Manage & Edit Inventory"
            ]
        )

    # --- SECTION 0: INVENTORY DASHBOARD ---
    if menu == "📊 Inventory Dashboard":
        st.title("📊 Raut Enterprise - Summary Dashboard")
        st.write("Yahan aapko poori inventory ka quick overview mil jayega.")
        st.markdown("---")

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM inventory")
        total_inv = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM trash_bin")
        total_trash = c.fetchone()[0]

        c.execute("SELECT COUNT(*) FROM downloaded_history")
        total_history = c.fetchone()[0]
        conn.close()

        col_d1, col_d2, col_d3 = st.columns(3)
        with col_d1:
            st.metric(label="📦 Active Products", value=total_inv)
        with col_d2:
            st.metric(label="♻️ Items in Trash", value=total_trash)
        with col_d3:
            st.metric(label="📥 Downloaded Records", value=total_history)

        st.markdown("---")
        st.subheader("💡 Quick Actions & CSV Export")
        
        conn = get_db_connection()
        full_df = pd.read_sql_query("SELECT id, item_name, barcode_id, price, slot_category FROM inventory ORDER BY id DESC", conn)
        conn.close()

        if not full_df.empty:
            csv_export = full_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Download Full Inventory CSV Report",
                data=csv_export,
                file_name="raut_full_inventory_report.csv",
                mime="text/csv",
            )
        else:
            st.info("Export karne ke liye inventory mein koi items nahi hain.")

    # --- SECTION 0.5: MULTI-PAGE EXCEL SHEETS + DESCRIPTION ---
    elif menu == "📊 Excel Sheet & Notes":
        st.title("📊 Excel Sheets & Description")

        conn = get_db_connection()
        pages_df = pd.read_sql_query(
            "SELECT id, page_name, description_text FROM custom_pages ORDER BY id ASC",
            conn,
        )
        conn.close()

        if pages_df.empty:
            conn = get_db_connection()
            c = conn.cursor()
            c.execute(
                "INSERT INTO custom_pages (page_name, description_text, updated_by) VALUES (?, ?, ?)",
                ("Page 1 - Main Sheet", "", st.session_state.username),
            )
            conn.commit()
            pages_df = pd.read_sql_query(
                "SELECT id, page_name, description_text FROM custom_pages ORDER BY id ASC",
                conn,
            )
            conn.close()

        page_map = {str(r["page_name"]): int(r["id"]) for _, r in pages_df.iterrows()}
        page_names = list(page_map.keys())

        default_idx = 0
        if st.session_state.active_custom_page_id in page_map.values():
            for idx, pname in enumerate(page_names):
                if page_map[pname] == st.session_state.active_custom_page_id:
                    default_idx = idx
                    break

        col_p_sel, col_p_add = st.columns([2, 2])
        with col_p_sel:
            selected_page_name = st.selectbox(
                "📄 Page / Sheet Chunein",
                page_names,
                index=default_idx,
                key="custom_page_selector",
            )
            active_page_id = page_map[selected_page_name]
            st.session_state.active_custom_page_id = active_page_id

        with col_p_add:
            with st.form("create_new_custom_page_form", clear_on_submit=True):
                new_p_name = st.text_input("➕ Naya Page / Sheet Name", placeholder="e.g. Page 2 - Rate List")
                create_p_btn = st.form_submit_button("➕ Naya Page Banayein")
                if create_p_btn:
                    clean_p = new_p_name.strip()
                    if clean_p:
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute(
                                "INSERT INTO custom_pages (page_name, description_text, updated_by) VALUES (?, ?, ?)",
                                (clean_p, "", st.session_state.username),
                            )
                            new_pid = c.lastrowid
                            conn.commit()
                            conn.close()
                            st.session_state.active_custom_page_id = new_pid
                            log_activity(st.session_state.username, "CREATE_SHEET_PAGE", f"Created sheet page: {clean_p}")
                            st.success(f"✅ Naya page '{clean_p}' ban gaya!")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("⚠️ Is naam ka page pehle se maujood hai.")
                    else:
                        st.warning("Page ka naam likhein.")

        if len(page_names) > 1:
            with st.expander("🗑️ Current Page Delete Karein"):
                st.warning(f"'{selected_page_name}' page aur uski rows delete ho jayengi.")
                if st.button("❌ Delete This Page", key="delete_current_custom_page"):
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute("DELETE FROM custom_sheet_rows WHERE page_id = ?", (active_page_id,))
                    c.execute("DELETE FROM custom_pages WHERE id = ?", (active_page_id,))
                    conn.commit()
                    conn.close()
                    st.session_state.active_custom_page_id = None
                    log_activity(st.session_state.username, "DELETE_SHEET_PAGE", f"Deleted sheet page: {selected_page_name}")
                    st.success("✅ Page delete ho gaya!")
                    st.rerun()

        st.markdown("---")

        conn = get_db_connection()
        sheet_df = pd.read_sql_query(
            "SELECT id, product, history_note, price, extra_info FROM custom_sheet_rows WHERE page_id = ? ORDER BY id ASC",
            conn,
            params=(active_page_id,),
        )
        c = conn.cursor()
        c.execute("SELECT description_text FROM custom_pages WHERE id = ?", (active_page_id,))
        p_desc_row = c.fetchone()
        conn.close()

        curr_desc = p_desc_row[0] if p_desc_row else ""

        edited_df = st.data_editor(
            sheet_df,
            use_container_width=True,
            num_rows="dynamic",
            height=240,
            disabled=["id"],
            column_config={
                "id": st.column_config.NumberColumn("ID", width="small"),
                "product": st.column_config.TextColumn("Product"),
                "history_note": st.column_config.TextColumn("History"),
                "price": st.column_config.TextColumn("Price"),
                "extra_info": st.column_config.TextColumn("Other / Info"),
            },
            key=f"manual_excel_table_{active_page_id}",
        )

        col_t1, col_t2, col_t3 = st.columns(3)
        with col_t1:
            if st.button("💾 Save Table", type="primary", key=f"save_tbl_{active_page_id}"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("DELETE FROM custom_sheet_rows WHERE page_id = ?", (active_page_id,))
                for _, r in edited_df.iterrows():
                    p_val = str(r.get("product") or "").strip()
                    h_val = str(r.get("history_note") or "").strip()
                    pr_val = str(r.get("price") or "").strip()
                    ex_val = str(r.get("extra_info") or "").strip()
                    if any([p_val, h_val, pr_val, ex_val]):
                        c.execute(
                            """
                            INSERT INTO custom_sheet_rows (page_id, product, history_note, price, extra_info, updated_by)
                            VALUES (?, ?, ?, ?, ?, ?)
                            """,
                            (active_page_id, p_val, h_val, pr_val, ex_val, st.session_state.username),
                        )
                conn.commit()
                conn.close()
                log_activity(st.session_state.username, "SHEET_SAVE", f"Saved table edits on page '{selected_page_name}'.")
                st.success("✅ Table save ho gaya!")
                st.rerun()

        with col_t2:
            sheet_csv = edited_df.to_csv(index=False).encode("utf-8")
            safe_fname = selected_page_name.lower().replace(" ", "_")
            st.download_button(
                label="📥 Download CSV",
                data=sheet_csv,
                file_name=f"{safe_fname}.csv",
                mime="text/csv",
                key=f"dl_csv_{active_page_id}",
            )

        with col_t3:
            if st.button("🗑️ Clear All Rows", key=f"clr_rows_{active_page_id}"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("DELETE FROM custom_sheet_rows WHERE page_id = ?", (active_page_id,))
                conn.commit()
                conn.close()
                log_activity(st.session_state.username, "SHEET_CLEAR", f"Cleared rows on page '{selected_page_name}'.")
                st.success("✅ Is page ke saare rows delete ho gaye!")
                st.rerun()

        if not sheet_df.empty:
            with st.expander("✏️ Edit or Delete Specific Row"):
                row_options = {
                    f"Row #{int(r['id'])} — {r['product'] or '(Empty)'} | {r['price'] or '-'}": int(r["id"])
                    for _, r in sheet_df.iterrows()
                }
                sel_label = st.selectbox("Select Row", list(row_options.keys()), key=f"sel_sheet_row_{active_page_id}")
                sel_row_id = row_options[sel_label]
                curr_row = sheet_df[sheet_df["id"] == sel_row_id].iloc[0]

                e1, e2, e3, e4 = st.columns(4)
                with e1:
                    ed_prod = st.text_input("Product", value=str(curr_row["product"] or ""), key=f"ed_prod_{active_page_id}_{sel_row_id}")
                with e2:
                    ed_hist = st.text_input("History", value=str(curr_row["history_note"] or ""), key=f"ed_hist_{active_page_id}_{sel_row_id}")
                with e3:
                    ed_price = st.text_input("Price", value=str(curr_row["price"] or ""), key=f"ed_price_{active_page_id}_{sel_row_id}")
                with e4:
                    ed_extra = st.text_input("Other / Info", value=str(curr_row["extra_info"] or ""), key=f"ed_extra_{active_page_id}_{sel_row_id}")

                b_upd, b_del = st.columns(2)
                with b_upd:
                    if st.button("✏️ Update Selected Row", key=f"upd_row_btn_{active_page_id}_{sel_row_id}"):
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute(
                            """
                            UPDATE custom_sheet_rows
                            SET product = ?, history_note = ?, price = ?, extra_info = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                            WHERE id = ? AND page_id = ?
                            """,
                            (ed_prod.strip(), ed_hist.strip(), ed_price.strip(), ed_extra.strip(), st.session_state.username, sel_row_id, active_page_id),
                        )
                        conn.commit()
                        conn.close()
                        log_activity(st.session_state.username, "SHEET_ROW_UPDATE", f"Updated row #{sel_row_id} on '{selected_page_name}'.")
                        st.success("✅ Row update ho gaya!")
                        st.rerun()

                with b_del:
                    if st.button("❌ Delete Selected Row", key=f"del_row_btn_{active_page_id}_{sel_row_id}"):
                        conn = get_db_connection()
                        c = conn.cursor()
                        c.execute("DELETE FROM custom_sheet_rows WHERE id = ? AND page_id = ?", (sel_row_id, active_page_id))
                        conn.commit()
                        conn.close()
                        log_activity(st.session_state.username, "SHEET_ROW_DELETE", f"Deleted row #{sel_row_id} on '{selected_page_name}'.")
                        st.success("✅ Selected row delete ho gaya!")
                        st.rerun()

        st.markdown("---")
        desc_input = st.text_area(
            f"📝 Description ({selected_page_name})",
            value=curr_desc,
            height=140,
            placeholder="Yahan description likhein...",
            key=f"page_description_area_{active_page_id}",
        )

        if st.button("💾 Save Description", key=f"save_desc_{active_page_id}"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute(
                """
                UPDATE custom_pages
                SET description_text = ?, updated_by = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (desc_input, st.session_state.username, active_page_id),
            )
            conn.commit()
            conn.close()
            log_activity(st.session_state.username, "SHEET_DESC_SAVE", f"Saved description on page '{selected_page_name}'.")
            st.success("✅ Description save ho gaya!")
            st.rerun()

    # --- SECTION 1: APP STORAGE WITH SEARCH BAR ---
    elif menu == "📦 App Barcode Storage":
        st.title("📦 App Central Barcode Storage")
        st.write("Kisi bhi sticker par click karke seedha print karein ya PNG download/history mein save karein.")
        st.markdown("---")

        search_query = st.text_input("🔍 Search Products (Type Item Name or Barcode ID)...", "").strip().lower()

        conn = get_db_connection()
        if search_query:
            query = "SELECT id, item_name, barcode_id, price, slot_category FROM inventory WHERE LOWER(item_name) LIKE ? OR barcode_id LIKE ? ORDER BY id DESC"
            df = pd.read_sql_query(query, conn, params=(f"%{search_query}%", f"%{search_query}%"))
        else:
            df = pd.read_sql_query("SELECT id, item_name, barcode_id, price, slot_category FROM inventory ORDER BY id DESC", conn)
        conn.close()

        if df.empty:
            st.info("Koi matching barcode nahi mila.")
        else:
            csv_data = df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Filtered List as CSV",
                data=csv_data,
                file_name="raut_filtered_inventory.csv",
                mime="text/csv",
            )
            st.markdown("---")

            clickable_items = []
            for _, row in df.iterrows():
                item_name = str(row['item_name'])
                barcode_id = str(row['barcode_id'])
                img_io_item = generate_custom_barcode_image(item_name, barcode_id)
                clickable_items.append({
                    "name": item_name,
                    "barcode": barcode_id,
                    "b64": base64.b64encode(img_io_item.getvalue()).decode("utf-8"),
                })

            st.caption("💡 Jis sticker par click karenge, wahi sticker turant print preview mein khulega.")
            render_clickable_barcode_list(clickable_items, key_prefix="storage_list")
            st.markdown("---")

            for _, row in df.iterrows():
                item_id = row['id']
                item_name = row['item_name']
                barcode_id = row['barcode_id']
                price = row['price']
                slot_category = row['slot_category']

                col1, col2, col3 = st.columns([3, 1.5, 1.5])
                with col1:
                    st.write(f"**{item_name.upper()}** (`{barcode_id}`) | Price: {price or 'N/A'} | Slot: {slot_category or 'N/A'}")
                with col2:
                    if st.button("📥 Save History", key=f"save_app_{item_id}"):
                        record_download_history(item_name, barcode_id, st.session_state.username)
                        st.success("Saved!")
                with col3:
                    local_img_io = generate_custom_barcode_image(item_name, barcode_id)
                    if st.download_button(
                        label="💾 Download PNG",
                        data=local_img_io,
                        file_name=f"{item_name}_{barcode_id}.png",
                        mime="image/png",
                        key=f"download_device_{item_id}",
                    ):
                        record_download_history(item_name, barcode_id, st.session_state.username)

    # --- SECTION 1.5: 24-STICKER A4 GRID PRINT (63.5mm x 33.9mm, 3x8) ---
    elif menu == "🖨️ Print 24-Sticker A4 (63.5x33.9mm)":
        st.title("🖨️ Print 24-Sticker A4 Sheet (63.5mm × 33.9mm)")
        st.write("Pehle sticker aur boxes chunein — kisi bhi sticker ya box par click karke print karein.")
        st.markdown("---")

        conn = get_db_connection()
        p_df = pd.read_sql_query("SELECT id, item_name, barcode_id, price, slot_category FROM inventory ORDER BY id DESC", conn)
        conn.close()

        if p_df.empty:
            st.info("Print karne ke liye koi products nahi hain.")
        else:
            view_mode = st.radio(
                "View / Print Mode",
                ["🎯 Ek Product (24-Box Sheet)", "📋 Sab Barcodes (All Products Sheet & List)"],
                horizontal=True,
                key="a4_24_view_mode",
            )
            st.markdown("---")

            all_catalog = {}
            label_to_key = {}
            for _, row in p_df.iterrows():
                b_key = str(row["barcode_id"])
                i_name = str(row["item_name"])
                img_io_item = generate_custom_barcode_image(i_name, b_key)
                b64_item = base64.b64encode(img_io_item.getvalue()).decode("utf-8")
                all_catalog[b_key] = {
                    "name": i_name,
                    "barcode": b_key,
                    "b64": b64_item,
                }
                label_to_key[f"{i_name.upper()} (ID: {b_key})"] = b_key

            if view_mode == "🎯 Ek Product (24-Box Sheet)":
                selected_p_str = st.selectbox("1️⃣ Kaunsa Sticker / Product Print Karna Hai?", list(label_to_key.keys()), key="a4_24_sel_prod")
                active_key = label_to_key[selected_p_str]
                active_item = all_catalog[active_key]
                p_name = active_item["name"]
                p_barcode = active_item["barcode"]

                c_start, c_qty = st.columns(2)
                with c_start:
                    start_box = st.number_input("2️⃣ Start Box # (1 to 24)", min_value=1, max_value=24, value=1, step=1)
                max_avail = 25 - int(start_box)
                with c_qty:
                    qty = st.number_input("3️⃣ Kitne Stickers Chahiye?", min_value=1, max_value=max_avail, value=min(6, max_avail), step=1)

                default_boxes = list(range(int(start_box), min(25, int(start_box) + int(qty))))
                manual_boxes_str = st.text_input(
                    "4️⃣ Ya Specific Box Numbers Likhein (Optional, comma se alag karein e.g. 1, 2, 5, 12)",
                    value=", ".join(str(x) for x in default_boxes),
                    key="a4_24_manual_boxes",
                )

                parsed_boxes = []
                for part in manual_boxes_str.split(","):
                    p_clean = part.strip()
                    if p_clean.isdigit():
                        b_num = int(p_clean)
                        if 1 <= b_num <= 24 and b_num not in parsed_boxes:
                            parsed_boxes.append(b_num)

                single_io = generate_custom_barcode_image(p_name, p_barcode)
                box_item_map = {str(b_num): active_key for b_num in parsed_boxes}

                slots_24 = [None] * 24
                for b_num in parsed_boxes:
                    slots_24[b_num - 1] = active_item

                col_dl1, col_dl2 = st.columns(2)
                with col_dl1:
                    if st.download_button(
                        label="💾 Download Single Sticker PNG",
                        data=single_io,
                        file_name=f"{p_name}_{p_barcode}_63.5x33.9mm.png",
                        mime="image/png",
                    ):
                        record_download_history(p_name, p_barcode, st.session_state.username)

                with col_dl2:
                    printable_a4_html = build_a4_24_grid_html(slots_24, auto_print=False)
                    if st.download_button(
                        label="📄 Download 1-Page A4 Print File (.html)",
                        data=printable_a4_html.encode("utf-8"),
                        file_name=f"A4_24_Stickers_{p_name}_{p_barcode}.html",
                        mime="text/html",
                    ):
                        record_download_history(p_name, p_barcode, st.session_state.username)

                st.markdown("#### 📋 Interactive 24-Box Sheet (Kisi bhi box par click karke ON/OFF karein, phir 'Print Selected (1 Page)' dabayein)")
                render_interactive_24_sheet_and_print(
                    items_catalog={active_key: active_item},
                    box_item_map=box_item_map,
                    enabled_box_indices=parsed_boxes,
                )

                st.info("💡 **Print Selected (1 Page)** dabane par alag clean print window khulegi jisme **1 sheet of paper** hi banega. Agar browser popup block kare toh **Download 1-Page A4 Print File (.html)** download karke khol lein.")

            else:
                st.subheader("📋 Sab Saved Barcodes (All Products)")
                search_all_bc = st.text_input("🔍 Sab Barcodes Mein Search Karein (Product Name ya Barcode ID)...", "", key="search_all_bc_mode").strip().lower()

                filtered_df = p_df
                if search_all_bc:
                    filtered_df = p_df[
                        p_df["item_name"].str.lower().str.contains(search_all_bc, na=False)
                        | p_df["barcode_id"].astype(str).str.lower().str.contains(search_all_bc, na=False)
                    ]

                if filtered_df.empty:
                    st.info("Koi matching barcode nahi mila.")
                else:
                    with st.expander("🧩 Mixed 24-Box Sheet (Alag-Alag Barcodes Ek Hi Sheet Par)", expanded=True):
                        c_fill_from, c_fill_to = st.columns(2)
                        with c_fill_from:
                            fill_start = st.number_input("Fill From Box #", min_value=1, max_value=24, value=1, step=1, key="mix_fill_start")
                        with c_fill_to:
                            fill_end = st.number_input("Fill Till Box #", min_value=int(fill_start), max_value=24, value=min(24, int(fill_start) + len(filtered_df) - 1), step=1, key="mix_fill_end")

                        filtered_keys = [str(r["barcode_id"]) for _, r in filtered_df.iterrows()]
                        mixed_map = {}
                        mixed_enabled = []

                        for idx_box in range(int(fill_start), int(fill_end) + 1):
                            item_idx = (idx_box - int(fill_start)) % len(filtered_keys)
                            chosen_k = filtered_keys[item_idx]
                            mixed_map[str(idx_box)] = chosen_k
                            mixed_enabled.append(idx_box)

                        custom_box_override = st.text_input(
                            "Specific Box Override (Optional — format `box:barcode_id`, e.g. `1:0000000000001, 2:0000000000002`)",
                            value="",
                            key="mix_box_override",
                        )
                        if custom_box_override.strip():
                            for pair in custom_box_override.split(","):
                                if ":" in pair:
                                    b_part, k_part = pair.split(":", 1)
                                    b_s = b_part.strip()
                                    k_s = k_part.strip()
                                    if b_s.isdigit() and 1 <= int(b_s) <= 24 and k_s in all_catalog:
                                        mixed_map[b_s] = k_s
                                        if int(b_s) not in mixed_enabled:
                                            mixed_enabled.append(int(b_s))

                        slots_mixed = [None] * 24
                        for b_num in mixed_enabled:
                            k_val = mixed_map.get(str(b_num))
                            if k_val and k_val in all_catalog:
                                slots_mixed[b_num - 1] = all_catalog[k_val]

                        mixed_html = build_a4_24_grid_html(slots_mixed, auto_print=False)
                        st.download_button(
                            label="📄 Download Mixed 1-Page A4 Print File (.html)",
                            data=mixed_html.encode("utf-8"),
                            file_name="A4_24_Mixed_Stickers.html",
                            mime="text/html",
                            key="dl_mixed_a4_html",
                        )

                        render_interactive_24_sheet_and_print(
                            items_catalog=all_catalog,
                            box_item_map=mixed_map,
                            enabled_box_indices=mixed_enabled,
                        )

                    st.markdown("---")
                    st.subheader("🏷️ Sab Barcodes (Jis Sticker Par Click Karenge Wahi Print Hoga)")
                    clickable_all = []
                    for _, row in filtered_df.iterrows():
                        r_name = str(row["item_name"])
                        r_barcode = str(row["barcode_id"])
                        r_img_io = generate_custom_barcode_image(r_name, r_barcode)
                        clickable_all.append({
                            "name": r_name,
                            "barcode": r_barcode,
                            "b64": base64.b64encode(r_img_io.getvalue()).decode("utf-8"),
                        })
                    render_clickable_barcode_list(clickable_all, key_prefix="all_stickers_click")

    # --- SECTION 1.8: CUSTOM / VENDOR MANUAL BARCODE PANEL (105mm x 70mm, A4-8) ---
    elif menu == "🏷️ Custom / Vendor Barcode (105x70mm)":
        st.title("🏷️ Custom / Vendor Barcode Panel (105mm × 70mm — A4-8)")
        st.write("Dusri company ke product ke liye barcode number manually enter karein. Baaki sab details optional hain.")
        st.markdown("---")

        with st.form("vendor_custom_barcode_form"):
            v_barcode = st.text_input(
                "Barcode Number (Manual Entry)",
                placeholder="e.g. 8905253127653",
            )
            c1, c2 = st.columns(2)
            with c1:
                v_sku = st.text_input("Model / SKU Code (Optional)", placeholder="e.g. NN8976SL10W")
                v_batch = st.text_input("Batch / Lot Code (Optional)", placeholder="e.g. 8DJ720")
            with c2:
                v_net = st.text_input("Net Qty (Optional)", placeholder="e.g. Net : 01 N")
                v_mrp = st.text_input("MRP / Price Info (Optional)", placeholder="e.g. MRP Rs. 775.00 (Incl.of all taxes)")
            v_serial = st.text_input("Serial / Extra Code (Optional)", placeholder="e.g. w 1801AJWH302983JX.T")

            gen_vendor_btn = st.form_submit_button("Generate Custom Sticker (105×70mm)")

            if gen_vendor_btn:
                clean_bc = v_barcode.strip()
                if not clean_bc:
                    clean_bc = get_next_barcode_id()

                try:
                    sticker_io = generate_vendor_custom_sticker(
                        barcode_no=clean_bc,
                        sku_code=v_sku,
                        batch_code=v_batch,
                        net_qty=v_net,
                        mrp_text=v_mrp,
                        serial_code=v_serial,
                    )
                    label_title = v_sku.strip() or clean_bc
                    st.session_state.vendor_sticker_preview = {
                        "title": label_title,
                        "barcode": clean_bc,
                        "b64": base64.b64encode(sticker_io.getvalue()).decode("utf-8"),
                    }
                    log_activity(
                        st.session_state.username,
                        "CUSTOM_VENDOR_BARCODE",
                        f"Generated 105x70mm custom vendor sticker for barcode {clean_bc} ({label_title}).",
                    )
                except Exception as e:
                    st.error(f"Barcode generate karne mein error aaya: {e}")

        if st.session_state.vendor_sticker_preview:
            vp = st.session_state.vendor_sticker_preview
            st.markdown("---")
            st.subheader("🖨️ Generated Sticker (Click karke print karein)")
            render_clickable_vendor_sticker(
                {
                    "name": vp["title"],
                    "barcode": vp["barcode"],
                    "b64": vp["b64"],
                },
                key_prefix="vendor_custom_preview",
            )
            st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

            col_v_dl1, col_v_dl2 = st.columns(2)
            with col_v_dl1:
                st.download_button(
                    label="💾 Download Sticker PNG (105×70mm)",
                    data=base64.b64decode(vp["b64"]),
                    file_name=f"custom_105x70_{vp['barcode']}.png",
                    mime="image/png",
                    key="dl_vendor_sticker_png",
                )

            with col_v_dl2:
                full_8_html = build_a4_8_grid_html([vp] * 8, auto_print=False)
                st.download_button(
                    label="📄 Download 8-Sticker A4 Print File (.html)",
                    data=full_8_html.encode("utf-8"),
                    file_name=f"A4_8_Stickers_105x70_{vp['barcode']}.html",
                    mime="text/html",
                    key="dl_vendor_a4_8_html",
                )

            st.markdown("---")
            st.subheader("📋 A4 - 8 Sheet Preview (105mm × 70mm — 2×4 Grid)")
            st.caption("Kisi bhi box (#1 to #8) par click karke ON/OFF karein, phir **Print Selected (1 Page)** dabayein.")
            render_interactive_8_sheet_and_print(
                sticker_item={
                    "name": vp["title"],
                    "barcode": vp["barcode"],
                    "b64": vp["b64"],
                },
                enabled_box_indices=[1],
            )

    # --- SECTION 2: APP DOWNLOADED HISTORY ---
    elif menu == "📂 App Downloaded History":
        st.title("📂 App Downloaded & Printed History")
        st.write("Yahan aap aur aapke employees dekh sakte hain ki kisne kaunsa barcode download ya print kiya hai.")
        st.markdown("---")

        conn = get_db_connection()
        history_df = pd.read_sql_query(
            "SELECT id, item_name, barcode_id, price, slot_category, downloaded_by, timestamp FROM downloaded_history ORDER BY id DESC", 
            conn
        )
        conn.close()

        if history_df.empty:
            st.info("Abhi tak app ke andar koi barcode download ya print nahi kiya gaya hai.")
        else:
            hist_csv = history_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Download History CSV",
                data=hist_csv,
                file_name="download_history.csv",
                mime="text/csv"
            )
            st.markdown("---")

            for _, row in history_df.iterrows():
                h_id = row['id']
                h_name = row['item_name']
                h_barcode = row['barcode_id']
                h_price = row['price']
                h_slot = row['slot_category']
                h_user = row['downloaded_by']
                h_time = row['timestamp']

                with st.container():
                    st.markdown(f"### 📦 Product: **{h_name}**")
                    st.write(f"**Barcode ID:** `{h_barcode}` | **Price:** {h_price or 'N/A'} | **Slot/Cat:** {h_slot or 'N/A'}")
                    st.caption(f"Action performed by user: **{h_user}** at {h_time}")
                    
                    try:
                        custom_io = generate_custom_barcode_image(h_name, h_barcode)
                        st.image(custom_io, width=280)
                    except Exception:
                        pass
                    
                    st.markdown("---")

    # --- SECTION 3: TRASH / BIN FOLDER ---
    elif menu == "♻️ Trash / Bin Folder":
        st.title("♻️ Trash / Bin Folder (Deleted Items)")
        st.write("Agar koi product galti se delete ho gaya ho, toh aap use yahan se wapas **Restore** kar sakte hain.")
        st.markdown("---")

        conn = get_db_connection()
        trash_df = pd.read_sql_query("SELECT id, original_id, item_name, barcode_id, price, slot_category, deleted_at FROM trash_bin ORDER BY id DESC", conn)
        conn.close()

        if trash_df.empty:
            st.info("Trash bin bilkul khali hai.")
        else:
            for _, row in trash_df.iterrows():
                t_id = row['id']
                orig_id = row['original_id']
                t_name = row['item_name']
                t_barcode = row['barcode_id']
                t_price = row['price']
                t_slot = row['slot_category']
                t_time = row['deleted_at']

                with st.container():
                    st.markdown(f"### 🗑️ Deleted Product: **{t_name}**")
                    st.write(f"**Barcode ID:** `{t_barcode}` | **Price:** {t_price or 'N/A'} | **Slot/Cat:** {t_slot or 'N/A'}")
                    st.caption(f"Deleted at: {t_time}")

                    try:
                        custom_io = generate_custom_barcode_image(t_name, t_barcode)
                        st.image(custom_io, width=280)
                    except Exception:
                        pass

                    col_r1, col_r2 = st.columns(2)
                    with col_r1:
                        if st.button(f"♻️ Restore Product", key=f"restore_{t_id}"):
                            try:
                                conn = get_db_connection()
                                c = conn.cursor()
                                c.execute(
                                    "INSERT INTO inventory (id, item_name, barcode_id, price, slot_category) VALUES (?, ?, ?, ?, ?)",
                                    (orig_id, t_name, t_barcode, t_price, t_slot)
                                )
                                c.execute("DELETE FROM trash_bin WHERE id = ?", (t_id,))
                                conn.commit()
                                conn.close()
                                log_activity(st.session_state.username, "RESTORE", f"Restored product {t_name} from trash.")
                                st.success(f"✅ Product '{t_name}' successfully restore ho gaya hai!")
                                st.rerun()
                            except sqlite3.IntegrityError:
                                try:
                                    c.execute(
                                        "INSERT INTO inventory (item_name, barcode_id, price, slot_category) VALUES (?, ?, ?, ?)",
                                        (t_name, t_barcode, t_price, t_slot)
                                    )
                                    c.execute("DELETE FROM trash_bin WHERE id = ?", (t_id,))
                                    conn.commit()
                                    conn.close()
                                    log_activity(st.session_state.username, "RESTORE", f"Restored product {t_name} from trash.")
                                    st.success(f"✅ Product '{t_name}' successfully restore ho gaya hai!")
                                    st.rerun()
                                except Exception as err:
                                    conn.close()
                                    st.error(f"Restore error: {err}")
                            except Exception as e:
                                st.error(f"Error: {e}")

                    with col_r2:
                        if st.button(f"🔥 Delete Permanently", key=f"perm_del_{t_id}"):
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute("DELETE FROM trash_bin WHERE id = ?", (t_id,))
                            conn.commit()
                            conn.close()
                            log_activity(st.session_state.username, "PERM_DELETE", f"Permanently deleted product {t_name}.")
                            st.success(f"🔥 Product permanently delete ho gaya.")
                            st.rerun()

                    st.markdown("---")

    # --- SECTION 4: ACTIVITY AUDIT LOGS (Admin Only) ---
    elif menu == "📜 Activity Audit Logs" and st.session_state.role == "Admin":
        st.title("📜 Team Activity Audit Logs")
        st.write("Yahan aap dekh sakte hain ki kis user ne kab kaunsa action perform kiya hai.")
        st.markdown("---")

        conn = get_db_connection()
        logs_df = pd.read_sql_query("SELECT id, username, action_type, details, timestamp FROM activity_logs ORDER BY id DESC", conn)
        conn.close()

        if logs_df.empty:
            st.info("Abhi tak koi activity record nahi hai.")
        else:
            log_csv = logs_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                label="📥 Export Activity Logs CSV",
                data=log_csv,
                file_name="activity_audit_logs.csv",
                mime="text/csv"
            )
            st.markdown("---")
            st.dataframe(logs_df, use_container_width=True)

    # --- SECTION 5: GENERATE & SAVE NEW ---
    elif menu == "➕ Generate & Save Barcode":
        st.title("➕ Naya Barcode Generate Karein")
        st.write("Item Name zaroori hai. Price aur Slot/Category optional hain. Barcode banate hi niche dikhega, aur pehle se bane naam par wahi barcode aayega.")

        with st.form("generate_barcode_form"):
            item_name = st.text_input("Item Name (Required)", placeholder="e.g. 12 no nut - 1 inch")
            
            col_p, col_s = st.columns(2)
            with col_p:
                price_input = st.text_input("Price (Optional)", placeholder="e.g. ₹150")
            with col_s:
                slot_input = st.text_input("Slot / Category (Optional)", placeholder="e.g. Rack A-3 / Hardware")
            
            save_btn = st.form_submit_button("Generate & Save to App")

            if save_btn:
                clean_name = item_name.strip()
                if clean_name:
                    conn = get_db_connection()
                    c = conn.cursor()
                    
                    c.execute(
                        "SELECT item_name, barcode_id, price, slot_category FROM inventory WHERE LOWER(item_name) = LOWER(?)",
                        (clean_name,),
                    )
                    existing_item = c.fetchone()
                    
                    if existing_item:
                        conn.close()
                        st.session_state.generated_preview_item = {
                            "item_name": existing_item[0],
                            "barcode_id": existing_item[1],
                            "price": existing_item[2] or "",
                            "slot_category": existing_item[3] or "",
                            "is_existing": True,
                        }
                    else:
                        auto_barcode_id = get_next_barcode_id()
                        p_clean = price_input.strip()
                        s_clean = slot_input.strip()
                        try:
                            c.execute(
                                "INSERT INTO inventory (item_name, barcode_id, price, slot_category) VALUES (?, ?, ?, ?)",
                                (clean_name, auto_barcode_id, p_clean, s_clean),
                            )
                            conn.commit()
                            conn.close()
                            log_activity(st.session_state.username, "CREATE", f"Created product {clean_name} with ID {auto_barcode_id}.")
                            st.session_state.generated_preview_item = {
                                "item_name": clean_name,
                                "barcode_id": auto_barcode_id,
                                "price": p_clean,
                                "slot_category": s_clean,
                                "is_existing": False,
                            }
                        except Exception as e:
                            conn.close()
                            st.error(f"Error: {e}")
                else:
                    st.warning("Kripya Item Name zaroor bharein.")

        if st.session_state.generated_preview_item:
            prev = st.session_state.generated_preview_item
            prev_name = prev["item_name"]
            prev_barcode = prev["barcode_id"]
            prev_price = prev["price"]
            prev_slot = prev["slot_category"]

            st.markdown("---")
            if prev["is_existing"]:
                st.info(f"ℹ️ '{prev_name}' ka barcode pehle se bana hua hai — niche wahi barcode show ho raha hai:")
            else:
                st.success(f"✅ '{prev_name}' ka naya barcode ban gaya hai — niche preview dekhein:")

            st.write(f"**Item:** {prev_name} | **Barcode ID:** `{prev_barcode}` | **Price:** {prev_price or 'N/A'} | **Slot/Cat:** {prev_slot or 'N/A'}")

            try:
                gen_img_io = generate_custom_barcode_image(prev_name, prev_barcode)
                st.caption("💡 Sticker par click karke seedha print karein:")
                render_clickable_barcode_list(
                    [{
                        "name": prev_name,
                        "barcode": prev_barcode,
                        "b64": base64.b64encode(gen_img_io.getvalue()).decode("utf-8"),
                    }],
                    key_prefix="gen_preview",
                )

                if st.download_button(
                    label="💾 Download Sticker PNG",
                    data=gen_img_io,
                    file_name=f"{prev_name}_{prev_barcode}.png",
                    mime="image/png",
                    key=f"gen_dl_{prev_barcode}",
                ):
                    record_download_history(prev_name, prev_barcode, st.session_state.username)
            except Exception as e:
                st.error(f"Error rendering barcode: {e}")

    # --- SECTION 6: MANAGE & EDIT ---
    elif menu == "⚙️ Manage & Edit Inventory":
        st.title("⚙️ Manage Inventory (Rename / Edit / Delete)")
        st.write("Yahan se aap kisi bhi product ka naam, price ya slot badal sakte hain, ya delete karke Trash mein bhej sakte hain.")
        st.markdown("---")

        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT id, item_name, barcode_id FROM inventory ORDER BY id DESC")
        items = c.fetchall()
        conn.close()

        if not items:
            st.info("Manage karne ke liye koi items nahi hain.")
        else:
            item_dict = {f"{item[1]} ({item[2]})": item[0] for item in items}
            selected_item_str = st.selectbox("Select Product to Modify", list(item_dict.keys()))
            selected_id = item_dict[selected_item_str]

            conn = get_db_connection()
            c = conn.cursor()
            c.execute("SELECT item_name, barcode_id, price, slot_category FROM inventory WHERE id = ?", (selected_id,))
            curr_name, curr_barcode, curr_price, curr_slot = c.fetchone()
            conn.close()

            with st.form("edit_delete_form"):
                new_name = st.text_input("Rename Product Name", value=curr_name)
                new_price = st.text_input("Update Price", value=curr_price)
                new_slot = st.text_input("Update Slot / Category", value=curr_slot)
                
                col1, col2 = st.columns(2)
                with col1:
                    update_btn = st.form_submit_button("💾 Save Changes")
                with col2:
                    delete_btn = st.form_submit_button("🗑️ Move to Trash")

                if update_btn:
                    if new_name.strip():
                        try:
                            conn = get_db_connection()
                            c = conn.cursor()
                            c.execute(
                                "UPDATE inventory SET item_name = ?, price = ?, slot_category = ? WHERE id = ?",
                                (new_name.strip(), new_price.strip(), new_slot.strip(), selected_id)
                            )
                            conn.commit()
                            conn.close()
                            log_activity(st.session_state.username, "UPDATE", f"Updated product details for ID {selected_id}.")
                            st.success(f"✅ Product details successfully updated!")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("⚠️ Yeh product name pehle se exist karta hai.")
                    else:
                        st.warning("Product name khali nahi ho sakta.")

                if delete_btn:
                    conn = get_db_connection()
                    c = conn.cursor()
                    try:
                        c.execute(
                            "INSERT INTO trash_bin (original_id, item_name, barcode_id, price, slot_category) VALUES (?, ?, ?, ?, ?)",
                            (selected_id, curr_name, curr_barcode, curr_price, curr_slot)
                        )
                        c.execute("DELETE FROM inventory WHERE id = ?", (selected_id,))
                        conn.commit()
                        conn.close()
                        log_activity(st.session_state.username, "MOVE_TO_TRASH", f"Moved product {curr_name} to trash.")
                        st.success(f"🗑️ Product successfully Trash folder mein bhej diya gaya hai!")
                        st.rerun()
                    except Exception as e:
                        conn.close()
                        st.error(f"Error moving to trash: {e}")

    # --- SECTION 7: MANAGE EMPLOYEE ACCESS, ADMIN PASSWORD & FORCE LOGOUT ---
    elif menu == "⚙️ Manage Employee Access" and st.session_state.role == "Admin":
        st.title("⚙️ Access Control, Password & Force Logout")
        st.write("Yahan se aap Admin ya Employee ka password badal sakte hain aur employee ko force-logout kar sakte hain.")
        st.markdown("---")

        st.subheader("🛡️ Change Admin Password")
        with st.form("change_admin_pass_form"):
            new_admin_pass = st.text_input("Naya Admin Password", type="password")
            update_admin_btn = st.form_submit_button("Update Admin Password")

            if update_admin_btn:
                if new_admin_pass.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute(
                        "UPDATE users SET password = ? WHERE username = 'admin'",
                        (new_admin_pass.strip(),),
                    )
                    conn.commit()
                    conn.close()
                    log_activity(st.session_state.username, "CHANGE_ADMIN_PASSWORD", "Updated admin password.")
                    st.success("✅ Admin ka password badal diya gaya hai!")
                else:
                    st.warning("Naya admin password enter karein.")

        st.markdown("---")
        st.subheader("🔑 Change Employee Password")
        with st.form("change_pass_form"):
            new_emp_pass = st.text_input("Naya Employee Password", type="password")
            update_btn = st.form_submit_button("Update Employee Password")

            if update_btn:
                if new_emp_pass.strip():
                    conn = get_db_connection()
                    c = conn.cursor()
                    c.execute(
                        "UPDATE users SET password = ? WHERE username = 'employee'",
                        (new_emp_pass.strip(),),
                    )
                    conn.commit()
                    conn.close()
                    log_activity(st.session_state.username, "CHANGE_PASSWORD", "Updated employee password.")
                    st.success("✅ Employee ka password badal diya gaya hai!")
                else:
                    st.warning("Naya password enter karein.")

        st.markdown("---")
        st.subheader("🚪 Force Logout Employee")
        st.write("Agar employee login karke chhod gaya hai, toh aap yahan click karke use turant log out kar sakte hain.")
        
        if st.button("🚨 Force Logout Employee Now"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("UPDATE users SET force_logout = 1 WHERE username = 'employee'")
            conn.commit()
            conn.close()
            log_activity(st.session_state.username, "FORCE_LOGOUT", "Admin force-logged out the employee.")
            st.success("✅ Employee ko successfully force logout kar diya gaya hai!")
