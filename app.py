import eventlet
eventlet.monkey_patch()
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, flash, send_file, make_response
from flask_cors import CORS
from opcua import Client, ua
from collections import defaultdict
import os
import sys
import json
import yaml
from flask_socketio import SocketIO, emit
import threading
import time
import plotly
import pyodbc
import re
from werkzeug.utils import redirect
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, ValidationError
import bcrypt
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
import uuid
import logging
import subprocess

# --- PERFORMANCE: SQLAlchemy for Connection Pooling ---
from sqlalchemy import create_engine, text
import urllib.parse

app = Flask(__name__)

def slugify(value):
    """Sanitizes a string to be used as a valid CSS selector/ID."""
    value = re.sub(r'[^\w\-]+', '_', value).strip('_')
    return value.lower()

app.jinja_env.filters['slugify'] = slugify 
CORS(app)
app.secret_key = 'xyzsdfg'
app.permanent_session_lifetime = timedelta(minutes=20)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ===============================================================================
# === GLOBAL RUNTIME STORE                                                    ===
# ===============================================================================
# Stores the latest values in memory.
# "values" persists indefinitely. We only .update() it, never overwrite it blank.
RUNTIME_STORE = {
    "values": {},
    "lock": threading.Lock()
}

# ===============================================================================
# === SESSION MANAGEMENT                                                      ===
# ===============================================================================
@app.before_request
def before_request_handler():
    session.permanent = True
    if 'userloggedin' in session:
        if 'last_activity' in session:
            try:
                last_activity = datetime.fromisoformat(session['last_activity'])
                time_since_last_activity = datetime.now() - last_activity
                
                if time_since_last_activity.total_seconds() >= 1200:
                    session.clear()
                    flash('You were automatically logged out due to inactivity.', 'info')
                    return redirect(url_for('user_login'))
            except (ValueError, TypeError):
                session['last_activity'] = datetime.now().isoformat()
        
        session['last_activity'] = datetime.now().isoformat()

SECRET_KEY = Fernet.generate_key()
cipher = Fernet(SECRET_KEY)

# ===============================================================================
# === CONFIGURATION HELPERS                                                   ===
# ===============================================================================
def load_data():
    """Reads the input.yaml file freshly every time it is called."""
    yaml_path = os.path.join(BASE_DIR, "input.yaml")
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)

def save_yaml(data):
    """Writes data to input.yaml."""
    yaml_path = os.path.join(BASE_DIR, "input.yaml")
    with open(yaml_path, 'w') as file:
        yaml.dump(data, file)

initial_config = load_data()
DB_SERVER = initial_config.get('DB_SERVER')
DB_DATABASE = initial_config.get('DB_DATABASE')
DB_USER = initial_config.get('DB_USER')
DB_PASSWORD = initial_config.get('DB_PASSWORD')
OPC_UA_URL = initial_config.get('OPC_UA_URL')

# ===============================================================================
# === DATABASE ENGINE (POOLED)                                                ===
# ===============================================================================
# We use the pass-through method (odbc_connect) to ensure exact driver matching.
# This handles special characters in passwords and ensures ODBC Driver 11 is used.
try:
    params = urllib.parse.quote_plus(
        f'DRIVER={{ODBC Driver 11 for SQL Server}};'
        f'SERVER={DB_SERVER};'
        f'DATABASE={DB_DATABASE};'
        f'UID={DB_USER};'
        f'PWD={DB_PASSWORD}'
    )
    CONNECTION_STRING = f"mssql+pyodbc:///?odbc_connect={params}"
    
    db_engine = create_engine(
        CONNECTION_STRING, 
        pool_size=10, 
        max_overflow=20, 
        pool_timeout=30,
        pool_pre_ping=True # Checks connection before using it (auto-reconnect)
    )
    print("Database Engine Initialized (SQLAlchemy + ODBC 11)")
except Exception as e:
    print(f"CRITICAL: Error initializing DB Engine: {e}")
    db_engine = None

def get_db_connection():
    """Returns a raw connection from the pool for legacy pyodbc compatibility."""
    if db_engine:
        return db_engine.raw_connection()
    return None

# ===============================================================================
# === DYNAMIC DEPARTMENT MAPPING                                              ===
# ===============================================================================
def get_departments_map():
    config = load_data()
    dept_map = {}
    submodules_list = config.get('submodules', [])
    
    # 1. Load dynamic departments from input.yaml
    for sub in submodules_list:
        name = sub.get('name')
        prefix = sub.get('node_prefix')
        if name and prefix:
            display_key = f"{name}"
            dept_map[display_key] = {
                'rh_table': f"{prefix}_RH",
                'temp_table': f"{prefix}_T"
            }
            
    # 2. Manually add "Outside" (Not in input.yaml)
    # This assumes the DB tables are named "Outside_RH" and "Outside_T"
    dept_map['Outside'] = {
        'rh_table': 'Outside_RH',
        'temp_table': 'Outside_T'
    }
    
    return dept_map
# ===============================================================================
# === POWERSHELL HELPERS                                                      ===
# ===============================================================================
def run_powershell(script_name, args):
    try:
        script_path = os.path.join(BASE_DIR, "grafana_dashboards", script_name) 
        if not os.path.exists(script_path):
            print(f"ERROR: PowerShell script not found at {script_path}")
            return
        cmd = ["powershell.exe", "-ExecutionPolicy", "Bypass", "-File", script_path] + args
        print(f"--- Executing PowerShell: {script_name} ---")
        subprocess.run(cmd, capture_output=True, text=True)
    except Exception as e:
        print(f"CRITICAL ERROR executing PowerShell: {e}")

# ===============================================================================
# === REPORTING ENGINE HELPERS                                                ===
# ===============================================================================
def fetch_column_names(table_name):
    conn = get_db_connection()
    if not conn: return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?", (table_name,))
        column_names = [row.COLUMN_NAME for row in cursor.fetchall()]
        return column_names
    except pyodbc.Error:
        return []
    finally:
        if conn: conn.close()

def fetch_data(table_name, from_date, to_date, time_difference, columns_to_fetch):
    conn = get_db_connection()
    if not conn: return []
    try:
        cursor = conn.cursor()
        actual_table_columns = fetch_column_names(table_name)
        valid_cols = [col for col in columns_to_fetch if col in actual_table_columns]

        if 'date' in actual_table_columns and 'date' not in valid_cols: valid_cols.insert(0, 'date')
        if 'time' in actual_table_columns and 'time' not in valid_cols: valid_cols.insert(1, 'time')
        
        if not valid_cols: return []

        select_clause = ", ".join([f'"{col}"' for col in valid_cols])
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()

        time_diff_map = {'10 minutes': 10, '30 minutes': 30, '1 hour': 60, '2 hours': 120, '5 hours': 300, '1 Day': 1440}
        time_difference_minutes = time_diff_map.get(time_difference, 0)

        query = f'SELECT {select_clause} FROM "{table_name}" WHERE "date" >= ? AND "date" <= ?'
        params = [from_date_obj, to_date_obj]

        if time_difference_minutes > 0 and 'time' in actual_table_columns:
            query += " AND DATEDIFF(MINUTE, CAST('00:00:00' AS TIME), CAST(time AS TIME)) % ? = 0"
            params.append(time_difference_minutes)
            
        cursor.execute(query, tuple(params))
        column_names = [column[0] for column in cursor.description]
        rows = cursor.fetchall()
        
        data = []
        for row in rows:
            row_dict = dict(zip(column_names, row))
            if 'date' in row_dict and isinstance(row_dict['date'], datetime):
                 row_dict['date'] = row_dict['date'].strftime("%Y-%m-%d")
            if 'time' in row_dict:
                t_val = row_dict['time']
                if isinstance(t_val, datetime):
                    row_dict['time'] = t_val.strftime("%H:%M:%S")
                else:
                    row_dict['time'] = str(t_val)
            for key, value in row_dict.items():
                if isinstance(value, float):
                    row_dict[key] = round(value, 2)
            data.append(row_dict)
        return data
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []
    finally:
        if conn: conn.close()

# ===============================================================================
# === ADMIN / CRUD HELPERS                                                    ===
# ===============================================================================
def execute_query(query, params=(), fetch=None):
    conn = get_db_connection()
    if not conn: return None if fetch else False
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch == 'all':
            column_names = [column[0] for column in cursor.description]
            result = [dict(zip(column_names, row)) for row in cursor.fetchall()]
        elif fetch == 'one':
            result = cursor.fetchone()
        else:
            conn.commit()
            result = True
        return result
    except Exception as e:
        print(f"Query Error: {e}")
        return None if fetch else False
    finally:
        conn.close()

# ===============================================================================
# === USER AUTH & OPC UA LOGIC                                                ===
# ===============================================================================
def validate_user(username, password):
    conn = get_db_connection()
    if not conn: return None
    try:
        cursor = conn.cursor()
        for table in ["Operator", "Manager", "Tse"]:
            cursor.execute(f"SELECT * FROM {table} WHERE username = ?", (username,))
            user = cursor.fetchone()
            if user and user[1] == username:
                try:
                    if bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
                        session['role'] = table
                        session['username'] = username
                        return table
                except (ValueError, AttributeError):
                    continue
        return None
    finally:
        conn.close()

def load_node_ids():
    yaml_path = os.path.join(BASE_DIR, "nodeid.yaml")
    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
            return data.get("node_ids", {})
    except Exception:
        return {}

# =========================================================================
# === ALARMS & OPC UA SECTION                                           ===
# =========================================================================
ALARM_LIST_FILE_PATH = os.path.join(BASE_DIR, "alarmList.yaml")
YAML_ACCESS_LOCK = threading.Lock()
LAST_TRIP_STATES = {}

def read_values_periodically():
    global RUNTIME_STORE
    
    # Retry logic for initial connection
    client = Client(OPC_UA_URL)
    connected = False
    
    while not connected:
        try:
            client.connect()
            connected = True
            print("OPC UA Client Connected successfully.")
        except Exception as e:
            print(f"OPC UA Connection failed, retrying in 5s: {e}")
            socketio.sleep(5)

    # Pre-resolve nodes to avoid lookup overhead in the loop
    active_nodes = []
    active_node_names = []
    
    while True:
        try:
            # Reload NodeIDs if list is empty (first run or reload needed)
            if not active_nodes:
                node_ids_map = load_node_ids()
                temp_nodes = []
                temp_names = []
                for name, node_id in node_ids_map.items():
                    try:
                        node = client.get_node(node_id)
                        temp_nodes.append(node)
                        temp_names.append(name)
                    except Exception:
                        pass
                active_nodes = temp_nodes
                active_node_names = temp_names

            # --- BULK READ (Massive Performance Boost) ---
            if active_nodes:
                try:
                    values = client.get_values(active_nodes)
                    
                    # Prepare updates dictionary (merging logic)
                    updates = {}
                    for i, val in enumerate(values):
                        # Only update if val is not None (Prevents showing undefined/blank)
                        if val is not None and not isinstance(val, ua.StatusCode):
                            if isinstance(val, (int, float)):
                                updates[active_node_names[i]] = round(val, 1)
                            else:
                                updates[active_node_names[i]] = val
                    
                    # Update Memory Store (Merging new data into old data)
                    if updates:
                        with RUNTIME_STORE["lock"]:
                            # .update() ensures that keys NOT present in this cycle
                            # (due to error/null) retain their PREVIOUS value.
                            RUNTIME_STORE["values"].update(updates)
                            
                            # Create a copy to emit
                            full_snapshot = RUNTIME_STORE["values"].copy()
                        
                        # --- THIS LINE WAS MISSING ---
                        socketio.emit('update', full_snapshot) 
                        # -----------------------------

                        # Send Status (Green)
                        socketio.emit('opc_status', {'connected': True})

                except Exception as read_err:
                    print(f"Bulk read error (will retry): {read_err}")
                    # If reading fails completely, we likely lost connection
                    socketio.emit('opc_status', {'connected': False})
                    raise read_err

            socketio.sleep(0.5)

        except Exception as e:
            print(f"OPC Loop Error: {e}")
            connected = False
            # Reconnection Loop
            socketio.emit('opc_status', {'connected': False})
            while not connected:
                try:
                    client.disconnect() # Ensure clean slate
                except: pass
                
                socketio.sleep(2)
                try:
                    client = Client(OPC_UA_URL)
                    client.connect()
                    connected = True
                    active_nodes = [] # Force re-resolving nodes
                    print("OPC UA Reconnected.")
                except Exception as inner_e:
                    print(f"Reconnect failed: {inner_e}")
                    
def initialize_alarm_states():
    global LAST_TRIP_STATES
    with YAML_ACCESS_LOCK:
        try:
            if os.path.exists(ALARM_LIST_FILE_PATH):
                with open(ALARM_LIST_FILE_PATH, 'r') as f:
                    data = yaml.safe_load(f) or {}
                    LAST_TRIP_STATES = data.get('lastTripStates', {})
        except Exception:
            LAST_TRIP_STATES = {}

def alarm_processing_engine():
    global LAST_TRIP_STATES, RUNTIME_STORE
    
    # Load rules once 
    alarm_rules = []
    try:
        with open("alarms.yaml", "r") as f:
            alarm_rules = yaml.safe_load(f).get("alarms", [])
    except:
        print("Error loading alarms.yaml rules")

    while True:
        try:
            # 1. Get values from memory
            current_opc_values = {}
            with RUNTIME_STORE["lock"]:
                current_opc_values = RUNTIME_STORE["values"].copy()

            if not current_opc_values:
                socketio.sleep(1)
                continue

            something_changed = False
            
            # 2. Process logic
            with YAML_ACCESS_LOCK:
                full_alarm_data = {'alarms': [], 'lastTripStates': LAST_TRIP_STATES}
                if os.path.exists(ALARM_LIST_FILE_PATH):
                    try:
                        with open(ALARM_LIST_FILE_PATH, 'r') as f:
                            full_alarm_data = yaml.safe_load(f) or full_alarm_data
                    except: pass
                
                persistent_alarms = full_alarm_data.get('alarms', [])

                for rule in alarm_rules:
                    code = str(rule['code'])
                    current_trip = False
                    for node_name in rule.get('nodeids', []):
                        node_value = current_opc_values.get(node_name)
                        if node_value is not None:
                            # Logic checks
                            if isinstance(node_value, bool) and node_value: current_trip = True
                            elif isinstance(node_value, (int, float)) and node_value != 0: current_trip = True
                            elif isinstance(node_value, str) and node_value.lower() in ['true', '1', 'on', 'active']: current_trip = True
                    
                    previous_trip = LAST_TRIP_STATES.get(code, False)

                    if current_trip and not previous_trip:
                        has_active_alarm = any(str(alarm.get('code')) == code and alarm.get('status') not in ['Resolved', 'Acknowledged'] for alarm in persistent_alarms)
                        
                        if not has_active_alarm:
                            new_alarm = {
                                'id': str(uuid.uuid4()),
                                'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                'message': rule['message'],
                                'code': code,
                                'severity': rule['severity'],
                                'status': 'Not acknowledged',
                                'acknowledged': False
                            }
                            persistent_alarms.append(new_alarm)
                            something_changed = True
                        LAST_TRIP_STATES[code] = True
                        
                    elif not current_trip and previous_trip:
                        for alarm in reversed(persistent_alarms):
                            if str(alarm.get('code')) == code and alarm.get('status') not in ['Resolved']:
                                alarm['status'] = 'Resolved'
                                alarm['resolved_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                something_changed = True
                                break
                        LAST_TRIP_STATES[code] = False

                if something_changed:
                    temp_file_path = ALARM_LIST_FILE_PATH + ".tmp"
                    final_data = {'alarms': persistent_alarms, 'lastTripStates': LAST_TRIP_STATES}
                    with open(temp_file_path, 'w') as f:
                        yaml.dump(final_data, f, default_flow_style=False)
                    os.replace(temp_file_path, ALARM_LIST_FILE_PATH)
            
            socketio.sleep(0.5)
        except Exception as e:
            print(f"Alarm Engine Error: {e}")
            socketio.sleep(2)
                        
@app.route("/notifyAlarms")
def get_alarms_notification():
    with YAML_ACCESS_LOCK:
        alarms = load_alarms_from_yaml_list()
    sorted_alarms = sorted(alarms, key=lambda x: x.get('time', '1970-01-01 00:00:00'), reverse=True)
    return jsonify(sorted_alarms)

def load_alarms_from_yaml_list():
    try:
        with open(ALARM_LIST_FILE_PATH, 'r') as f:
            data = yaml.safe_load(f) or {}
            alarms = data.get('alarms', [])
            for alarm in alarms:
                try:
                    dt = datetime.strptime(alarm['time'], "%Y-%m-%d %H:%M:%S")
                    alarm['time'] = dt.strftime("%m/%d/%Y, %I:%M:%S %p")
                except (ValueError, KeyError):
                    pass
            return alarms
    except FileNotFoundError:
        return []

@app.route("/alarmslist")
def alarmslist():
    return render_template("iot/alarmslist.html")

def emit_alarms():
    while True:
        with YAML_ACCESS_LOCK:
            alarms = load_alarms_from_yaml_list()
        socketio.emit('alarm_update', alarms)
        socketio.sleep(1)

@app.route("/acknowledge", methods=["POST"])
def acknowledge_alarm():
    with YAML_ACCESS_LOCK:
        try:
            alarm_id = request.json.get("id")
            if not alarm_id: return jsonify({"success": False, "error": "Missing alarm ID"}), 400

            with open(ALARM_LIST_FILE_PATH, 'r') as f:
                data = yaml.safe_load(f) or {}
            alarms = data.get("alarms", [])
            updated = False
            for alarm in alarms:
                if alarm.get("id") == alarm_id and alarm.get("status") != "Resolved":
                    alarm["acknowledged"] = True
                    alarm["status"] = "Acknowledged"
                    updated = True
                    break
            
            if updated:
                data["alarms"] = alarms
                with open(ALARM_LIST_FILE_PATH, 'w') as f:
                    yaml.dump(data, f, default_flow_style=False)
                return jsonify({"success": True})
            return jsonify({"success": False, "error": "Not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

# =========================================================================
# === WRITES SECTION                                                    ===
# =========================================================================
@app.route('/write', methods=['POST'])
def write():
    if 'userloggedin' not in session: return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json()
    if not data: return jsonify({"success": False}), 400
    
    opcua_client = Client(OPC_UA_URL)
    try:
        opcua_client.connect()
        for nodeid, value in data.items():
            node = opcua_client.get_node(nodeid)
            node_data_type = node.get_data_type_as_variant_type()
            dv = None
            if node_data_type == ua.VariantType.Boolean: dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
            elif node_data_type == ua.VariantType.Int16: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int16))
            elif node_data_type == ua.VariantType.UInt16: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.UInt16))
            elif node_data_type == ua.VariantType.Int32: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
            elif node_data_type == ua.VariantType.UInt32: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.UInt32))
            elif node_data_type == ua.VariantType.Float: dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
            elif node_data_type == ua.VariantType.Double: dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Double))
            if dv: node.set_value(dv)
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try: opcua_client.disconnect()
        except: pass

@app.route('/writes', methods=['POST'])
def writes():
    if 'userloggedin' not in session: return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json()
    if not data: return jsonify({"success": False}), 400
    opcua_client = Client(OPC_UA_URL)
    try:
        opcua_client.connect()
        node_ids = load_node_ids()
        for nodeid, value in data.items():
            if nodeid not in node_ids: return jsonify({"success": False}), 400
            node = opcua_client.get_node(node_ids[nodeid])
            node_data_type = node.get_data_type_as_variant_type()
            dv = None
            if node_data_type == ua.VariantType.Boolean: dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
            elif node_data_type == ua.VariantType.Int32: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
            elif node_data_type == ua.VariantType.UInt16: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.UInt16))
            elif node_data_type == ua.VariantType.Float: dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
            if dv: node.set_value(dv)
        return jsonify({"success": True}), 200
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500
    finally:
        try: opcua_client.disconnect()
        except: pass

setting_suffix_mapping = {
    "Set Point": "_sp.html", "Digital Input": "_di.html", "Digital Output": "_do.html",
    "Analog Input": "_ai.html", "Analog Output": "_ao.html", "Preset Values": "_pv.html",
    "Timer": "_ti.html", "Controllers": "_co.html", "UPSS": "_up.html",
    "Pump Min Set": "_pm.html", "DeHumidity": "_dh.html",
}

@app.route('/load_template/<parent_submodule>/<setting_option>')
def load_template(parent_submodule, setting_option):
    config = load_data()
    submodules_list = config.get("submodules", [])
    target_dept = next((s for s in submodules_list if s['name'] == parent_submodule), None)
    if not target_dept: return "Not found", 404

    category = target_dept.get('category')
    prefix = target_dept.get('node_prefix')
    suffix = setting_suffix_mapping.get(setting_option)
    
    template_name = f"{slugify(category)}{suffix}"
    template_path = f"Settings/{template_name}"
    
    vals = {}
    with RUNTIME_STORE["lock"]:
        vals = RUNTIME_STORE["values"].copy()
        
    msg = {'payload': vals, 'node_ids': load_node_ids()}
    if vals:
        socketio.emit('update', vals)
        
    return render_template(template_path, msg=msg, prefix=prefix)

# =========================================================================
# === REPORTING ROUTES                                                  ===
# =========================================================================
@app.route('/report')
def reportpage():
    dept_map = get_departments_map()
    department_options = sorted(list(dept_map.keys()))
    config_data = load_data()
    client_name = config_data.get('client_name', 'Default Client')
    
    # --- NEW: Create a mapping of Department Name -> Category ---
    department_categories = {}
    for sub in config_data.get('submodules', []):
        department_categories[sub['name']] = sub['category']
    
    # Pass department_categories to the template
    response = make_response(render_template(
        'report.html', 
        department_options=department_options, 
        client_name=client_name,
        department_categories=department_categories 
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@app.route('/getTables', methods=['POST'])
def get_tables():
    department_key = request.form.get('department')
    dept_map = get_departments_map()
    dept_config = dept_map.get(department_key)
    
    if not dept_config: return jsonify({'error': 'Department not found'}), 400

    all_columns = set()
    if dept_config.get('rh_table'): all_columns.update(fetch_column_names(dept_config['rh_table']))
    if dept_config.get('temp_table'): all_columns.update(fetch_column_names(dept_config['temp_table']))

    display_columns = sorted([col for col in all_columns if col.lower() not in ['idx', 'id']])
    return jsonify({'columns': display_columns})

@app.route('/data', methods=['POST'])
def get_data_api():
    department_key = request.form.get('department')
    from_date = request.form.get('from_date')
    to_date = request.form.get('to_date')
    time_difference = request.form.get('time_difference')
    selected_fields_str = request.form.get('selected_fields', '')
    selected_fields = selected_fields_str.split(',') if selected_fields_str else []

    dept_map = get_departments_map()
    dept_config = dept_map.get(department_key)
    if not dept_config: return jsonify({'error': 'Invalid department'}), 400

    rh_data = fetch_data(dept_config.get('rh_table'), from_date, to_date, time_difference, selected_fields) if dept_config.get('rh_table') else []
    temp_data = fetch_data(dept_config.get('temp_table'), from_date, to_date, time_difference, selected_fields) if dept_config.get('temp_table') else []

    merged_data = {}
    def merge_into_dict(data_list):
        for row in data_list:
            key = (row.get('date'), row.get('time'))
            if key[0] and key[1]:
                if key not in merged_data: merged_data[key] = {}
                merged_data[key].update(row)

    merge_into_dict(rh_data)
    merge_into_dict(temp_data)
    final_data = list(merged_data.values())

    all_fetched_columns = set()
    for row in final_data: all_fetched_columns.update(row.keys())

    display_columns = []
    if 'date' in all_fetched_columns: display_columns.append('date')
    if 'time' in all_fetched_columns: display_columns.append('time')
    display_columns.extend([col for col in selected_fields if col in all_fetched_columns and col not in ['date', 'time']])

    return jsonify({'columns': display_columns, 'data': final_data})

@app.route('/time-differences')
def get_time_differences():
    return jsonify({'time_differences': ['10 minutes', '30 minutes', '1 hour', '2 hours', '5 hours', '1 Day']})

# =========================================================================
# === ADMIN / CRUD ROUTES                                               ===
# =========================================================================
@app.route('/admin')
def admin_dashboard():
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    return make_response(render_template('adminpage.html'))

@app.route('/admin_logout')
def admin_logout():
    return redirect(url_for('logout'))

@app.route('/userlogout')
def user_logout():
    return redirect(url_for('logout'))

@app.route('/users')
def manage_users(): 
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    records = execute_query("SELECT * FROM users", fetch='all') 
    return render_template('index.html', records=records, table_name='users')

@app.route('/admins')
def manage_admins(): 
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    records = execute_query("SELECT * FROM admins", fetch='all') 
    return render_template('admin.html', records=records, table_name='admins')

@app.route('/insert/<table_name>', methods=['POST'])
def crud_insert(table_name):
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    username = request.form['username']
    password = request.form['password']
    if table_name not in ['users', 'admins']: return "Invalid table", 400
    execute_query(f"INSERT INTO {table_name} (username, password) VALUES (?, ?)", (username, password))
    if table_name == 'users': return redirect(url_for('manage_users'))
    return redirect(url_for('manage_admins'))

@app.route('/delete/<table_name>/<int:record_id>')
def crud_delete(table_name, record_id):
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    if table_name not in ['users', 'admins']: return "Invalid table", 400
    execute_query(f"DELETE FROM {table_name} WHERE id=?", (record_id,))
    if table_name == 'users': return redirect(url_for('manage_users'))
    return redirect(url_for('manage_admins'))

@app.route('/update/<table_name>/<int:record_id>', methods=['POST'])
def crud_update(table_name, record_id):
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    username = request.form['username']
    password = request.form['password']
    if table_name not in ['users', 'admins']: return "Invalid table", 400
    execute_query(f"UPDATE {table_name} SET username=?, password=? WHERE id=?", (username, password, record_id))
    if table_name == 'users': return redirect(url_for('manage_users'))
    return redirect(url_for('manage_admins'))

# =========================================================================
# === MAIN APP ROUTES                                                   ===
# =========================================================================
@app.route('/')
def home():
    return redirect(url_for('dashboard'))

@app.route('/dashboard')
def dashboard():
    role = session.get('role')
    config_data = load_data()
    dashboard_items = []
    
    for submodule in config_data.get('submodules', []):
        node_prefix = submodule.get('node_prefix')
        category = submodule.get('category', '').lower()
        
        if node_prefix:
            item = {
                'name': submodule.get('name'),
                'category': category,
                'sensors': []
            }

            # Check for H-Plant (default behavior)
            if 'h-plant' in category:
                item['sensors'].append({
                    'key': f"{node_prefix}_iVa_Act_Temp", 
                    'label': 'Temp', 
                    'unit': '°C',
                    'color': 'rgba(255, 148, 112)' # Orange
                })
                item['sensors'].append({
                    'key': f"{node_prefix}_iVa_Act_RH", 
                    'label': 'Hum', 
                    'unit': '%RH',
                    'color': 'rgba(40, 67, 135)' # Blue
                })
            
            # Check for WCS variations
            elif 'wcs' in category:
                # WCS-1: Single PDF
                if 'wcs-1' in category:
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_PDF", 
                        'label': 'PDF', 
                        'unit': 'Pa',
                        'color': 'rgba(255, 99, 132)' # Red
                    })
                # WCS-2 or WCS-3: PDF and RDF
                elif 'wcs-2' in category or 'wcs-3' in category:
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_PDF", 
                        'label': 'PDF', 
                        'unit': 'Pa',
                        'color': 'rgba(255, 99, 132)' # Red
                    })
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_RDF", 
                        'label': 'RDF', 
                        'unit': 'Pa',
                        'color': 'rgba(54, 162, 235)' # Blue
                    })
                # WCS-4 or WCS-5: PDF1, PDF2, RDF
                elif 'wcs-4' in category or 'wcs-5' in category:
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_PDF1", 
                        'label': 'PDF1', 
                        'unit': 'Pa',
                        'color': 'rgba(255, 99, 132)' # Red
                    })
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_PDF2", 
                        'label': 'PDF2', 
                        'unit': 'Pa',
                        'color': 'rgba(255, 206, 86)' # Yellow
                    })
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_RDF", 
                        'label': 'RDF', 
                        'unit': 'Pa',
                        'color': 'rgba(54, 162, 235)' # Blue
                    })
                else:
                    # Fallback for generic WCS
                    item['sensors'].append({
                        'key': f"{node_prefix}_iVa_DPT_PDF", 
                        'label': 'PDF', 
                        'unit': 'Pa',
                        'color': 'rgba(255, 99, 132)'
                    })

            dashboard_items.append(item)

    return render_template('iot/dashboard.html', dashboard_items=dashboard_items, allowed_submodules=load_role_submodules(role))

def load_role_submodules(role):
    data = load_data()
    return data.get("roles", {}).get(role, [])

@app.context_processor
def inject_context():
    data = load_data()
    categorized_submodules = defaultdict(list)
    for submodule in data.get("submodules", []):
        categorized_submodules[submodule.get("category", "Uncategorized")].append(submodule)
    return {
        'submodules': data.get("submodules", []),
        'categorized_submodules': categorized_submodules,
        'client_name': data.get("client_name", "Default Client Name"),
        'dashboard_name': data.get("Dashboard"),
        # ADD THIS LINE BELOW:
        'show_outside_conditions': data.get("show_outside_conditions", True) 
    }

@app.route('/<submodule>')
def render_submodule(submodule):
    if submodule in ['login', 'report', 'dashboard', 'user_management', 'admin', 'users', 'admins']: return "Not found", 404
    config_data = load_data()
    target_submodule = next((s for s in config_data.get("submodules", []) if s['name'] == submodule), None)
    if not target_submodule: return "Page not found", 404

    role = session.get('role')
    role_allowed_settings = load_role_submodules(role)
    category = target_submodule.get('category', '').lower()
    
    cat_settings = []
    if category.startswith('h-plant'): cat_settings = config_data.get('H-plant', [])
    elif category.startswith('wcs'): cat_settings = config_data.get('WCS', [])

    final_visible_settings = [s for s in role_allowed_settings if s in cat_settings]
    template_name = target_submodule['template']
    
    if os.path.exists(os.path.join("templates", "iot", template_name)):
        vals = {}
        with RUNTIME_STORE["lock"]:
            vals = RUNTIME_STORE["values"].copy()
        msg = {"payload": vals}
        return render_template(f"iot/{template_name}", msg=msg, allowed_submodules=final_visible_settings, dept_name=submodule)
    return "Template file not found", 404

@app.route('/login', methods=['GET', 'POST'])
def user_login():
    if 'userloggedin' in session: return redirect(url_for('dashboard'))
    if request.method == 'POST':
        user_role = validate_user(request.form['username'], request.form['password'])
        if user_role:
            session['userloggedin'] = True
            session['username'] = request.form['username']
            session['role'] = user_role
            session['last_activity'] = datetime.now().isoformat()
            session['last_login'] = datetime.now().strftime("%d-%b-%Y %I:%M %p")
            session['allowed_submodules'] = load_role_submodules(user_role)
            return redirect(url_for('dashboard'))
        flash("Invalid credentials.", 'danger')
    return render_template('User management/userlogin.html')

@app.route('/index')
def index():
    return render_template('iot/index.html', msg={'payload': 0})

@socketio.on('connect')
def handle_connect():
    with RUNTIME_STORE["lock"]:
        if RUNTIME_STORE["values"]:
            emit('update', RUNTIME_STORE["values"])

# =========================================================================
# === USER MANAGEMENT ROUTES                                            ===
# =========================================================================
@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    if request.method == 'POST':
        role = request.form['role']
        table = role if role in ['Operator', 'Manager', 'Tse'] else None
        if table:
            conn = get_db_connection()
            if conn:
                try:
                    hashed = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt())
                    conn.cursor().execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (request.form['username'], hashed.decode('utf-8')))
                    conn.commit()
                    flash(f"User added to {table}.", 'success')
                except Exception as e: flash(f"Error: {e}", 'danger')
                finally: conn.close()
    return render_template('iot/add_user.html')

@app.route('/user_management')
def user_management():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    conn = get_db_connection()
    users = []
    if conn:
        cursor = conn.cursor()
        for table in ["Operator", "Manager", "Tse"]:
            cursor.execute(f"SELECT * FROM {table}")
            for row in cursor.fetchall():
                users.append({"username": row[1], "password": "Encrypted", "role": table})
        conn.close()
    return render_template('iot/user_management.html', users=users)

@app.route('/edit_user', methods=['POST'])
def edit_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    username = request.form['username']
    new_role = request.form['role']
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        for table in ["Operator", "Manager", "Tse"]:
            cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        hashed = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        cursor.execute(f"INSERT INTO {new_role} (username, password) VALUES (?, ?)", (username, hashed))
        conn.commit()
        conn.close()
    flash("User updated.", "success")
    return redirect(url_for('user_management'))

@app.route('/delete_user', methods=['POST'])
def delete_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    username = request.form['username']
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        for table in ["Operator", "Manager", "Tse"]:
            cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        conn.commit()
        conn.close()
    flash("User deleted.", "success")
    return redirect(url_for('user_management'))
    
@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for('user_login'))

@app.route('/trends')
def trends():
    return render_template('iot/trends.html', msg={'payload': 0})

@app.route('/input', methods=['GET', 'POST'])
@app.route('/input', methods=['GET', 'POST'])
def input_page():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    data = load_data()
    if request.method == 'POST':
        msg = ""
        if 'client_name' in request.form:
            data['client_name'] = request.form['client_name']
            msg = "Client name updated"
            
        # --- MODIFIED BLOCK FOR OPC UA URL ---
        elif 'opc_ua_url' in request.form:
            data['OPC_UA_URL'] = request.form['opc_ua_url']
            save_yaml(data) # Save immediately so the restart picks it up
            
            def restart_app():
                print("--- OPC UA URL Changed. Restarting Application... ---")
                socketio.sleep(1) # Wait 1s to ensure the frontend receives the success message
                # Restart the current process
                os.execv(sys.executable, ['python'] + sys.argv)
            
            # Start the restart countdown in the background
            socketio.start_background_task(restart_app)
            msg = "OPC UA URL updated. Application is restarting..."
            return jsonify(success=True, message=msg)
        # -------------------------------------

        elif 'toggle_outside_conditions' in request.form:
             # ... (Keep your existing logic for toggle_outside_conditions here)
            is_visible = request.form['toggle_outside_conditions'] == 'true'
            data['show_outside_conditions'] = is_visible
            msg = "Outside conditions visibility updated"
            
        elif 'submodule_name' in request.form:
            # ... (Keep existing submodule logic)
            new_sub = {
                'category': request.form['submodule_category'],
                'name': request.form['submodule_name'],
                'template': request.form['submodule_file'],
                'node_prefix': request.form['node_prefix']
            }
            if 'submodules' not in data: data['submodules'] = []
            data['submodules'].append(new_sub)
            msg = "Submodule added"
            run_powershell("Import-Dashboard.ps1", ["-DashboardName", new_sub['node_prefix'], "-NewTitle", new_sub['name']])
            
        save_yaml(data)
        return jsonify(success=True, message=msg)
    
    return render_template('iot/input.html', 
                           client_name=data.get('client_name', ''), 
                           opc_ua_url=data.get('OPC_UA_URL', ''), 
                           show_outside_conditions=data.get('show_outside_conditions', True),
                           submodules=data.get('submodules', []), 
                           roles=data.get('roles', {}), 
                           predefined_departments=data.get('predefined_departments', []))


@app.route('/remove_submodule', methods=['POST'])
def remove_submodule():
    data = load_data()
    sub_name = request.form['submodule_name']
    if 'submodules' in data:
        data['submodules'] = [s for s in data['submodules'] if s.get('name') != sub_name]
    save_yaml(data)
    run_powershell("Delete-Dashboard.ps1", ["-DashboardTitle", sub_name])
    return jsonify({'success': True})

@app.route('/edit_submodule', methods=['POST'])
def edit_submodule():
    data = load_data()
    old_name = request.form['submodule_name']
    new_name = request.form['new_submodule_name']
    new_prefix = request.form['node_prefix']
    
    if 'submodules' in data:
        for sub in data['submodules']:
            if sub.get('name') == old_name:
                sub['name'] = new_name
                sub['template'] = request.form['submodule_file']
                sub['category'] = request.form['submodule_category']
                sub['node_prefix'] = new_prefix
                break
    save_yaml(data)
    run_powershell("Delete-Dashboard.ps1", ["-DashboardTitle", old_name])
    run_powershell("Import-Dashboard.ps1", ["-DashboardName", new_prefix, "-NewTitle", new_name])
    return jsonify({'success': True})

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        new_order = request.json.get('new_order', [])
        data = load_data()
        data['submodules'] = new_order
        with open('input.yaml', 'w') as f: yaml.dump(data, f, default_flow_style=False, sort_keys=False)
        return jsonify({'message': 'Order updated!'}), 200
    except Exception as e: return jsonify({'error': str(e)}), 500
    
@app.route('/add_setting', methods=['POST'])
def add_setting():
    data = load_data()
    role, setting = request.json.get('role'), request.json.get('setting')
    if role and setting and role in data['roles']:
        if setting not in data['roles'][role]:
            data['roles'][role].append(setting)
            save_yaml(data)
            return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/get_settings/<role>', methods=['GET'])
def get_settings(role):
    data = load_data()
    if role in data['roles']: return jsonify({'success': True, 'settings': data['roles'][role]})
    return jsonify({'success': False}), 400

@app.route('/delete_settings', methods=['POST'])
def delete_settings():
    data = load_data()
    role, settings = request.json.get('role'), request.json.get('settings')
    if role and settings and role in data['roles']:
        data['roles'][role] = [s for s in data['roles'][role] if s not in settings]
        save_yaml(data)
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

if __name__ == '__main__':
    initialize_alarm_states()
    
    socketio.start_background_task(read_values_periodically)
    socketio.start_background_task(alarm_processing_engine)
    socketio.start_background_task(emit_alarms)
    
    socketio.run(app, host="0.0.0.0", port=7005, debug=True, allow_unsafe_werkzeug=True)