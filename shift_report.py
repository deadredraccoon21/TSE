import os
import sys
import json
import smtplib
import yaml
import requests
from datetime import datetime, date, timedelta
from email.message import EmailMessage
from flask import render_template

# --- SYSTEM CONFIGURATION FOR EXE ---
# THIS FIXES THE BROWSER FOR THE EXE (PyInstaller + NSSM)
if getattr(sys, 'frozen', False):
    # Setting this to '0' tells Playwright to look strictly inside 
    # the local PyInstaller folder instead of the user profile.
    os.environ['PLAYWRIGHT_BROWSERS_PATH'] = '0'
    
# --- DATA LOADING FUNCTIONS ---
def load_shift_data(base_dir):
    path = os.path.join(base_dir, 'shift_data.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return {"shifts": {}, "global_config": {}}

def load_app_config():
    if getattr(sys, 'frozen', False):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    yaml_path = os.path.join(base_dir, "input.yaml")
    if os.path.exists(yaml_path):
        try:
            with open(yaml_path, "r") as f:
                return yaml.safe_load(f) or {}
        except Exception:
            pass
    return {}

# Filters alarms exactly within a shift's start and end time (TRIPS ONLY)
def get_shift_alarms(base_dir, from_time, to_time):
    path = os.path.join(base_dir, 'alarmList.json')
    shift_alarms = []
    if not os.path.exists(path): return []
    
    is_overnight = from_time > to_time
    today_str = date.today().strftime("%Y-%m-%d")
    yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")

    try:
        with open(path, 'r') as f:
            data = json.load(f)
            for alarm in data.get('alarms', []):
                msg_upper = alarm.get('message', '').upper()
                if "TRIP" not in msg_upper and "FAULT" not in msg_upper: continue
                
                alarm_dt_str = alarm.get('time', '')
                if not alarm_dt_str: continue
                try:
                    parts = alarm_dt_str.split(' ')
                    if len(parts) != 2: continue
                    a_date, a_time_full = parts
                    a_time = a_time_full[:5] # "HH:MM"
                    
                    if is_overnight:
                        # Overnight spans Yesterday -> Today
                        if (a_date == yesterday_str and a_time >= from_time) or \
                           (a_date == today_str and a_time <= to_time):
                            shift_alarms.append(alarm)
                    else:
                        # FIX: Daytime shifts now strictly look at YESTERDAY
                        if a_date == yesterday_str and from_time <= a_time <= to_time:
                            shift_alarms.append(alarm)
                except: pass
    except Exception as e: print(f"Error reading alarms: {e}")
    return shift_alarms

def fetch_aggregated_department_data(db_engine, run_db_task, dept_map, from_time, to_time, selected_fields):
    is_overnight = from_time > to_time
    temp_related_fields = ['Set_Temp', 'Act_Temp', 'Act_SA_Temp', 'Set_SA_Temp', 'Damper_Output' ]
    
    WCS_FIELDS = ['Set_PR', 'Act_PR', 'RadialFan_Speed', 'Act_TDS', 'Act_DP_PDF','Act_DP_PDF1', 'Act_DP_PDF2', 'Act_DP_COMP', 'Act_DP_COMP1', 'Act_DP_COMP2']
    
    categorized_data = {
        "H-PLANT": {"departments": [], "valid_fields": [f for f in selected_fields if f not in WCS_FIELDS]},
        "WCS": {"departments": [], "valid_fields": [f for f in selected_fields if f in WCS_FIELDS]}
    }

    for dept_name, info in dept_map.items():
        cat = info.get('category', 'H-PLANT')
        if cat not in categorized_data:
            categorized_data[cat] = {"departments": [], "valid_fields": []}

        dept_stats = {'name': dept_name, 'stats': {}}
        rh_table = info.get('rh_table')
        temp_table = info.get('temp_table')

        for field in categorized_data[cat]["valid_fields"]:
            dept_stats['stats'][field] = '-' 
            target_table = temp_table if field in temp_related_fields and temp_table else rh_table
            if not target_table: continue

            def get_stats():
                if field == 'Pump_Status':
                    avg_col = f"AVG(CAST([{field}] AS FLOAT)) * 100"
                else:
                    avg_col = f"AVG(NULLIF([{field}], 0))"

                if is_overnight:
                    # Overnight spans Yesterday -> Today
                    query = f"""
                        SELECT {avg_col} FROM "{target_table}"
                        WHERE (
                            ("date" = CAST(DATEADD(day, -1, GETDATE()) AS DATE) AND "time" >= ?)
                            OR ("date" = CAST(GETDATE() AS DATE) AND "time" <= ?)
                        )
                    """
                else:
                    # FIX: Daytime shifts explicitly pull from DATEADD(day, -1, GETDATE()) (Yesterday)
                    query = f"""
                        SELECT {avg_col} FROM "{target_table}"
                        WHERE "date" = CAST(DATEADD(day, -1, GETDATE()) AS DATE) AND "time" >= ? AND "time" <= ?
                    """

                try:
                    conn = db_engine.raw_connection()
                    cursor = conn.cursor()
                    cursor.execute(query, (from_time, to_time))
                    row = cursor.fetchone()
                    conn.close()
                    
                    if row and row[0] is not None:
                        if field in ['Pump_Output', 'Pump_Status']: return f"{round(row[0], 1)}%"
                        else: return round(row[0], 1)
                except: pass
                return '-'

            dept_stats['stats'][field] = run_db_task(get_stats)
        
        categorized_data[cat]["departments"].append(dept_stats)
        
    return categorized_data

def generate_daily_report_pdf(client_name, date_str, multi_shift_data, selected_fields):
    """Generates a high-fidelity A4 Landscape PDF with embedded base64 images."""
    
    # --- IMAGE HANDLING ---
    # Determine base directory for both exe and local dev
    if getattr(sys, 'frozen', False): base_dir = sys._MEIPASS
    else: base_dir = os.path.dirname(os.path.abspath(__file__))
        
    import base64
    def get_b64_img(filename):
        path = os.path.join(base_dir, 'static', 'images', filename)
        if os.path.exists(path):
            with open(path, "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode('utf-8')
        return ""

    # Fetch Base64 data for both the Logo and the Footer Signature
    logo_b64 = get_b64_img('LOGO.png')
    sig_b64 = get_b64_img('SIGNATURE.png') # Make sure image_598351.png is saved as static/images/SIGNATURE.png

    # --- FIELD LABEL MAPPING ---
    FIELD_LABELS = {
        'Set_RH': 'Set RH %', 'Act_RH': 'RH %', 'Pump_Output': 'Pump Output %',
        'Pump_Status': 'Pump Status', 'SAF_Speed': 'SAF Speed %', 'RetAF_Speed': 'RAF Speed %',
        'Set_Temp': 'Set Temp °C', 'Act_Temp': 'Temperature °C', 'Act_SA_Temp': 'SA Temperature °C',
        'Set_SA_Temp': 'Set SA Temp °C', 'Damper_Output': 'Damper Output %',
        'Set_PR': 'Set PR Pa', 'Act_PR': 'Act PR Pa', 'RadialFan_Speed': 'Radial Fan Speed %',
        'Act_TDS': 'TDS ppm','Act_DP_RDF':'RDF DP Pa', 'Act_DP_PDF': 'PDF DP Pa', 'Act_DP_PDF1': 'PDF1 DP Pa',
        'Act_DP_PDF2': 'PDF2 DP Pa', 'Act_DP_COMP': 'COMP DP Pa',
        'Act_DP_COMP1': 'COMP1 DP Pa', 'Act_DP_COMP2': 'COMP2 DP Pa'
    }

    # --- RENDER HTML TEMPLATE ---
    html = render_template(
        'shift_report_template.html',
        client_name=client_name,
        date=date_str,
        multi_shift_data=multi_shift_data,
        logo_b64=logo_b64,
        sig_b64=sig_b64, # Pass the signature base64 here
        field_labels=FIELD_LABELS
    )
    
    # --- PDF GENERATION ---
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            # FIX: Removed the os.environ override from here! It now safely relies 
            # on the sys.frozen check at the very top of shift_report.py
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.set_content(html)
            
            # Strict A4 Landscape with proper margins to prevent cutoff
            pdf_bytes = page.pdf(
                format="A4", 
                landscape=True, 
                margin={"top": "0.4in", "right": "0.4in", "bottom": "1.2in", "left": "0.4in"}, 
                print_background=True
            )
            browser.close()
            return pdf_bytes
        
    except Exception as e:
        # Safe logging for NSSM/Windows services
        safe_error = str(e).encode('ascii', 'ignore').decode('ascii')
        print(f"PDF Error: {safe_error}")
        return None
        
# --- DISTRIBUTION CHANNELS ---
def send_email_report(to_email, pdf_bytes, report_date, report_title="Daily_Report"):
    """Sends the PDF via Corporate Mail using credentials from input.yaml and an embedded HTML Signature."""
    config = load_app_config()
    SMTP_SERVER = config.get("SMTP_SERVER", "mail.tsengg.com")
    SMTP_PORT = int(config.get("SMTP_PORT", 587))
    SENDER_EMAIL = config.get("SENDER_EMAIL", "reports@tsengg.com")
    SENDER_PASSWORD = str(config.get("SENDER_PASSWORD", "Tech$#22345"))

    if not to_email or not pdf_bytes:
        return False, "Invalid data"

    filename = f"{report_title}_{report_date}.pdf".replace(" ", "_")
    msg = EmailMessage()
    msg['Subject'] = f"Automated Plant Report - {report_date}"
    msg['From'] = SENDER_EMAIL
    msg['To'] = to_email

    # 1. Fallback text for older email clients
    text_content = f"Hello,\n\nPlease find attached the automated plant report for {report_date}.\n\nWith Regards\nTech Sphere Engineering Pvt Ltd\nSy.no 85/1,Byandahalli,Kadabagere post, Dasanapura Hobli,\nBangalore North dist, Bangalore-562130 | Karnataka | India\nE-mail: reports@tsengg.com | https://tsengg.com"
    msg.set_content(text_content)

    # 2. HTML Signature with the CID image tag
   # 2. HTML Signature - Streamlined for exact alignment
    html_content = f"""\
    <html>
      <body style="font-family: Arial, sans-serif; color: #333; line-height: 1.6;">
        <p>Hello,</p>
        <p>Please find attached the automated plant report for <b>{report_date}</b>.</p>
        <br>
        <p>With Regards,</p>
        
        <p style="margin-top: 10px;">
          <table border="0" cellpadding="0" cellspacing="0" style="border-collapse: collapse;">
            <tr>
              <td style="vertical-align: middle; padding-right: 8px;">
                <img src="cid:company_logo" alt="TSE Logo" style="display: block; height: 25px; width: auto; max-height: 25px;">
              </td>
              <td style="vertical-align: middle;">
                <strong style="color: #000; font-size: 16px; font-weight: bold; white-space: nowrap;">
                  Tech Sphere Engineering Pvt Ltd
                </strong>
              </td>
            </tr>
          </table>
          
          <span style="font-size: 13px; color: #555; line-height: 1.4; display: block; margin-top: 5px;">
            Sy.no 85/1, Byandahalli, Kadabagere post, Dasanapura Hobli,<br>
            Bangalore North dist, Bangalore-562130 | Karnataka | India<br>
            E-mail: <a href="mailto:reports@tsengg.com" style="color: #0056b3; text-decoration: none;">reports@tsengg.com</a> | <a href="https://tsengg.com" style="color: #0056b3; text-decoration: none;">https://tsengg.com</a>
          </span>
        </p>
        </body>
    </html>
    """
    msg.add_alternative(html_content, subtype='html')

    # 3. Attach the LOGO.png as an inline image (CID) so it shows up in the signature
    if getattr(sys, 'frozen', False): base_dir = sys._MEIPASS
    else: base_dir = os.path.dirname(os.path.abspath(__file__))
    
    logo_path = os.path.join(base_dir, 'static', 'images', 'LOGO.png')
    if os.path.exists(logo_path):
        with open(logo_path, 'rb') as img:
            # This safely injects the image into the HTML layer of the email
            msg.get_payload()[1].add_related(img.read(), 'image', 'png', cid='company_logo')

    # 4. Finally, attach the actual PDF document
    msg.add_attachment(pdf_bytes, maintype='application', subtype='pdf', filename=filename)

    try:
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(SENDER_EMAIL, SENDER_PASSWORD)
            server.send_message(msg)
        return True, "Email sent successfully"
    except Exception as e:
        return False, str(e)
            
def send_telegram_report(pdf_bytes, report_date, report_title="Daily_Report"):
    """Sends the PDF via Telegram Bot directly to a Central Group using input.yaml credentials."""
    config = load_app_config()
    
    # 100% Dynamic - No more hardcoded tokens!
    TELEGRAM_BOT_TOKEN = config.get("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_GROUP_ID = str(config.get("TELEGRAM_GROUP_ID", ""))

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_GROUP_ID or not pdf_bytes: 
        return False, "Missing Telegram Bot Token, Group ID, or PDF"

    filename = f"{report_title}_{report_date}.pdf".replace(" ", "_")
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    
    try:
        payload = {"chat_id": TELEGRAM_GROUP_ID, "caption": f"Automated Plant Report ({report_date})"}
        files = {"document": (filename, pdf_bytes, "application/pdf")}
        res = requests.post(url, data=payload, files=files)
        
        if res.status_code != 200: print(f"TELEGRAM ERROR: {res.text}")
        return res.status_code == 200, res.text
    except Exception as e:
        print(f"TELEGRAM CRASH: {e}") 
        return False, str(e)
        
def send_whatsapp_report(to_mobile, pdf_bytes, report_date, report_title="Daily_Report"):
    """Sends the PDF via Meta WhatsApp Cloud API using input.yaml credentials."""
    config = load_app_config()
    
    # Dynamically pull from input.yaml
    ACCESS_TOKEN = config.get("ACCESS_TOKEN", "")
    PHONE_NUMBER_ID = str(config.get("PHONE_NUMBER_ID", ""))
    
    if not ACCESS_TOKEN or not PHONE_NUMBER_ID:
        print("WHATSAPP ERROR: Missing Access Token or Phone Number ID in input.yaml")
        return False, "Missing Credentials"
        
    if not to_mobile or not pdf_bytes: return False, "Missing Info"
    
    # Strip any special characters from the phone number
    to_mobile = "".join(filter(str.isdigit, str(to_mobile)))
    filename = f"{report_title}_{report_date}.pdf".replace(" ", "_")

    try:
        # Step 1: Upload the PDF to Meta's servers
        upload_url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/media"
        headers = {"Authorization": f"Bearer {ACCESS_TOKEN}"}
        files = {"file": (filename, pdf_bytes, "application/pdf"), "type": (None, "document"), "messaging_product": (None, "whatsapp")}
        upload_res = requests.post(upload_url, headers=headers, files=files).json()
        
        if "id" not in upload_res: 
            print(f"WHATSAPP UPLOAD ERROR: {upload_res}")
            return False, "Upload failed"
        
        # Step 2: Send the uploaded PDF to the user
        send_url = f"https://graph.facebook.com/v17.0/{PHONE_NUMBER_ID}/messages"
        payload = {
            "messaging_product": "whatsapp", 
            "to": to_mobile, 
            "type": "document",
            "document": {"id": upload_res["id"], "filename": filename, "caption": f"Automated Plant Report ({report_date})"}
        }
        res = requests.post(send_url, headers=headers, json=payload)
        
        # Print exact error to console if Meta blocks it
        if res.status_code != 200:
            print(f"WHATSAPP SEND ERROR: {res.text}")
            
        return res.status_code == 200, res.text
    except Exception as e:
        print(f"WHATSAPP CRASH: {e}")
        return False, str(e)