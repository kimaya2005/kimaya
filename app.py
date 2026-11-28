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

# Helper function: Sized for 63.5mm x 33.9mm (24 stickers per A4 sheet, 3x8) with SINGLE centered product name
def generate_custom_barcode_image(item_name, barcode_text):
    scale = 3

    writer_options = {
        "module_width": 0.30 * scale,
        "module_height": 11.0 * scale,
        "quiet_zone": 6 * scale,
        "write_text": True,
        "font_size": 11 * scale,
        "text_distance": 4.0 * scale,
    }

    rv = BytesIO()
    Code128(str(barcode_text), writer=ImageWriter()).write(rv, options=writer_options)
    rv.seek(0)

    barcode_img = Image.open(rv)
    bw, bh = barcode_img.size

    target_w = 635 * scale
    target_h = 339 * scale
    sticker_img = Image.new("RGB", (target_w, target_h), "white")
    draw = ImageDraw.Draw(sticker_img)

    font_title = None
    font_paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "DejaVuSans-Bold.ttf",
        "arialbd.ttf",
    ]

    for path in font_paths:
        try:
            font_title = ImageFont.truetype(path, 25 * scale)
            break
        except IOError:
            continue

    if not font_title:
        try:
            font_title = ImageFont.load_default(size=25 * scale)
        except TypeError:
            font_title = ImageFont.load_default()

    text_to_draw = f"{item_name.upper()}"
    bbox = draw.textbbox((0, 0), text_to_draw, font=font_title)
    text_w = bbox[2] - bbox[0]
    if text_w > target_w - 30 * scale:
        text_to_draw = text_to_draw[:30] + "..."
        bbox = draw.textbbox((0, 0), text_to_draw, font=font_title)
        text_w = bbox[2] - bbox[0]

    text_x = max(5 * scale, (target_w - text_w) // 2)
    text_y = 12 * scale
    draw.text((text_x, text_y), text_to_draw, fill="black", font=font_title)

    line_y = 48 * scale
    draw.line([(18 * scale, line_y), (target_w - 18 * scale, line_y)], fill="black", width=2 * scale)

    avail_h = target_h - (line_y + 14 * scale)
    avail_w = target_w - 28 * scale
    ratio = min(avail_w / max(1, bw), avail_h / max(1, bh))
    new_bw = max(1, int(bw * ratio))
    new_bh = max(1, int(bh * ratio))
    resized_bc = barcode_img.resize((new_bw, new_bh), Image.Resampling.LANCZOS)

    bc_x = (target_w - new_bw) // 2
    bc_y = line_y + 6 * scale + max(0, (avail_h - new_bh) // 2)
    sticker_img.paste(resized_bc, (bc_x, bc_y))

    final_w = target_w // scale
    final_h = target_h // scale
    sticker_img = sticker_img.resize((final_w, final_h), Image.Resampling.LANCZOS)

    final_io = BytesIO()
    sticker_img.save(final_io, format="PNG")
    final_io.seek(0)
    return final_io

def build_print_html(img_io):
    img_bytes = img_io.getvalue()
    encoded_img = base64.b64encode(img_bytes).decode("utf-8")
    return f"""
    <html>
    <head>
        <style>
            @page {{ size: 63.5mm 33.9mm; margin: 0; }}
            body {{ margin: 0; display: flex; justify-content: center; align-items: center; height: 100vh; background: white; }}
            img {{ width: 63.5mm; height: 33.9mm; object-fit: contain; }}
        </style>
    </head>
    <body>
        <img id="print_img" src="data:image/png;base64,{encoded_img}" />
        <script>
            document.getElementById('print_img').onload = function() {{
                window.print();
            }};
        </script>
    </body>
    </html>
    """

def build_a4_24_grid_html(slots_data, auto_print=False):
    """slots_data: list of 24 items (3 cols x 8 rows, 63.5mm x 33.9mm) strictly fitting 1 single A4 page."""
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
                padding: 1mm;
                overflow: hidden;
            }}
            .label-cell img {{
                width: 61.5mm;
                height: 31.8mm;
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

def render_interactive_24_sheet_and_print(items_catalog, selected_box_indices, active_item_key):
    """
    Renders an interactive 24-box (3x8) A4 sticker sheet right on screen where the user can click
    any box (#1 to #24) to toggle printing for that box, and opens a clean 1-page print window on click.
    """
    catalog_json = json.dumps(items_catalog)
    selected_json = json.dumps(sorted(list(set(selected_box_indices))))
    active_key_json = json.dumps(active_item_key)

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
            font-family: Arial, sans-serif;
            background: transparent;
        }}
        .top-bar {{
            display: flex;
            flex-wrap: wrap;
            justify-content: space-between;
            align-items: center;
            gap: 8px;
            margin-bottom: 10px;
            padding: 8px 12px;
            background: #f4f6f9;
            border-radius: 6px;
            border: 1px solid #dde2e8;
        }}
        .top-bar .info {{
            font-size: 13px;
            color: #1f2937;
            font-weight: 600;
        }}
        .top-bar .btn-group {{
            display: flex;
            gap: 8px;
        }}
        button.action-btn {{
            border: none;
            border-radius: 5px;
            padding: 7px 14px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
        }}
        .btn-clear {{
            background: #e5e7eb;
            color: #111827;
        }}
        .btn-print {{
            background: #dc2626;
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
            box-shadow: 0 2px 8px rgba(0,0,0,0.06);
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
            background: #f0f9ff;
            border-color: #0284c7;
        }}
        .box-cell.selected {{
            border: 1.5px solid #16a34a;
            background: #ffffff;
        }}
        .box-num {{
            position: absolute;
            top: 2px;
            left: 4px;
            font-size: 9px;
            color: #64748b;
            font-weight: 700;
            background: rgba(255,255,255,0.85);
            padding: 0 2px;
            border-radius: 2px;
        }}
        .box-empty-hint {{
            font-size: 10px;
            color: #94a3b8;
        }}
        .sticker-img {{
            max-width: 96%;
            max-height: 92%;
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
            const activeKey = {active_key_json};
            let selectedSet = new Set({selected_json});

            function getActiveItem() {{
                return catalog[activeKey] || null;
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
                const item = getActiveItem();

                for (let i = 1; i <= 24; i++) {{
                    const cell = document.createElement("div");
                    cell.className = "box-cell" + (selectedSet.has(i) ? " selected" : "");
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

                    if (selectedSet.has(i) && item) {{
                        const imgEl = document.createElement("img");
                        imgEl.className = "sticker-img";
                        imgEl.src = "data:image/png;base64," + item.b64;
                        cell.appendChild(imgEl);
                    }} else {{
                        const hintEl = document.createElement("span");
                        hintEl.className = "box-empty-hint";
                        hintEl.innerText = "Empty";
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
                const arr = Array.from(selectedSet).sort((a, b) => a - b);
                if (arr.length === 0) {{
                    alert("Pehle kam se kam 1 sticker box select karein.");
                    return;
                }}
                const item = getActiveItem();
                if (!item) return;

                let cellsMarkup = "";
                for (let i = 1; i <= 24; i++) {{
                    if (selectedSet.has(i)) {{
                        cellsMarkup += '<div class="cell"><img src="data:image/png;base64,' + item.b64 + '" /></div>';
                    }} else {{
                        cellsMarkup += '<div class="cell"></div>';
                    }}
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
                        <title>Print 1 A4 Sheet (24 Stickers)</title>
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
                                padding: 1mm;
                                overflow: hidden;
                            }}
                            .cell img {{
                                width: 61.5mm;
                                height: 31.8mm;
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
        st.write("Yahan saare saved barcodes 100% scanner-friendly pure numeric format mein hain.")
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
            col_exp1, _ = st.columns(2)
            with col_exp1:
                csv_data = df.to_csv(index=False).encode('utf-8')
                st.download_button(
                    label="📥 Export Filtered List as CSV",
                    data=csv_data,
                    file_name="raut_filtered_inventory.csv",
                    mime="text/csv",
                )
            st.markdown("---")

            for _, row in df.iterrows():
                item_id = row['id']
                item_name = row['item_name']
                barcode_id = row['barcode_id']
                price = row['price']
                slot_category = row['slot_category']

                with st.container():
                    try:
                        custom_barcode_io = generate_custom_barcode_image(item_name, barcode_id)
                        st.image(custom_barcode_io, width=320)
                    except Exception as e:
                        st.error(f"Barcode render error: {e}")

                    col1, col2 = st.columns(2)
                    
                    with col1:
                        if st.button(f"📥 Save to App History", key=f"save_app_{item_id}"):
                            record_download_history(item_name, barcode_id, st.session_state.username)
                            st.success(f"✅ App History mein save ho gaya!")

                    with col2:
                        try:
                            local_img_io = generate_custom_barcode_image(item_name, barcode_id)
                            if st.download_button(
                                label="💾 Download PNG File",
                                data=local_img_io,
                                file_name=f"{item_name}_{barcode_id}.png",
                                mime="image/png",
                                key=f"download_device_{item_id}"
                            ):
                                record_download_history(item_name, barcode_id, st.session_state.username)
                        except Exception as e:
                            st.error(f"Download error: {e}")

                    st.caption(f"Record ID: #{item_id} | Price: {price or 'N/A'} | Slot/Cat: {slot_category or 'N/A'}")
                    st.markdown("---")

    # --- SECTION 1.5: 24-STICKER A4 GRID PRINT (63.5mm x 33.9mm, 3x8) ---
    elif menu == "🖨️ Print 24-Sticker A4 (63.5x33.9mm)":
        st.title("🖨️ Print 24-Sticker A4 Sheet (63.5mm × 33.9mm)")
        st.write("Pehle sticker aur boxes chunein — print tab tak shuru nahi hoga jab tak aap print button na dabayein.")
        st.markdown("---")

        conn = get_db_connection()
        p_df = pd.read_sql_query("SELECT id, item_name, barcode_id, price, slot_category FROM inventory ORDER BY id DESC", conn)
        conn.close()

        if p_df.empty:
            st.info("Print karne ke liye koi products nahi hain.")
        else:
            item_dict_p = {
                f"{row['item_name'].upper()} (ID: {row['barcode_id']})": (str(row["barcode_id"]), row["item_name"], row["barcode_id"])
                for _, row in p_df.iterrows()
            }
            selected_p_str = st.selectbox("1️⃣ Kaunsa Sticker / Product Print Karna Hai?", list(item_dict_p.keys()), key="a4_24_sel_prod")
            active_key, p_name, p_barcode = item_dict_p[selected_p_str]

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
            single_b64 = base64.b64encode(single_io.getvalue()).decode("utf-8")

            catalog_payload = {
                active_key: {
                    "name": p_name,
                    "barcode": p_barcode,
                    "b64": single_b64,
                }
            }

            slots_24 = [None] * 24
            for b_num in parsed_boxes:
                slots_24[b_num - 1] = {
                    "name": p_name,
                    "barcode": p_barcode,
                    "b64": single_b64,
                }

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
                items_catalog=catalog_payload,
                selected_box_indices=parsed_boxes,
                active_item_key=active_key,
            )

            st.info("💡 **Print Selected (1 Page)** dabane par alag clean print window khulegi jisme **1 sheet of paper** hi banega. Agar browser popup block kare toh **Download 1-Page A4 Print File (.html)** download karke khol lein.")

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
                st.image(gen_img_io, width=280)

                col_b1, col_b2 = st.columns(2)
                with col_b1:
                    if st.download_button(
                        label="💾 Download Sticker PNG",
                        data=gen_img_io,
                        file_name=f"{prev_name}_{prev_barcode}.png",
                        mime="image/png",
                        key=f"gen_dl_{prev_barcode}",
                    ):
                        record_download_history(prev_name, prev_barcode, st.session_state.username)
                with col_b2:
                    if st.button("🖨️ Direct Print Sticker", type="primary", key=f"gen_pr_{prev_barcode}"):
                        record_download_history(prev_name, prev_barcode, st.session_state.username)
                        html(build_print_html(gen_img_io), height=120)
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
