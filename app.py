import eventlet
eventlet.monkey_patch()
from eventlet import tpool

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
from datetime import datetime, timedelta, date 
from cryptography.fernet import Fernet
import uuid
import logging
import subprocess
import requests
from sqlalchemy import create_engine, text
import urllib.parse

# ===============================================================================
# === PATH CONFIGURATION                                                      ===
# ===============================================================================
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__, template_folder=os.path.join(BASE_DIR, 'templates'), static_folder=os.path.join(BASE_DIR, 'static'))

app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
app.secret_key = 'xyzsdfg' 
app.permanent_session_lifetime = timedelta(minutes=20)

CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

def slugify(value):
    value = re.sub(r'[^\w\-]+', '_', value).strip('_')
    return value.lower()

app.jinja_env.filters['slugify'] = slugify 

# ===============================================================================
# === GLOBAL RUNTIME STORE                                                    ===
# ===============================================================================
RUNTIME_STORE = {
    "values": {},
    "lock": threading.Lock()
}

# ===============================================================================
# === UTILITIES                                                               ===
# ===============================================================================
def load_data():
    yaml_path = os.path.join(BASE_DIR, "input.yaml")
    try:
        with open(yaml_path, "r") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

def save_yaml(data):
    yaml_path = os.path.join(BASE_DIR, "input.yaml")
    with open(yaml_path, 'w') as file:
        yaml.dump(data, file)

def load_node_ids():
    yaml_path = os.path.join(BASE_DIR, "nodeid.yaml")
    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f)
            return data.get("node_ids", {})
    except Exception:
        return {}

# ===============================================================================
# === GRAFANA API HELPERS                                                     ===
# ===============================================================================
GRAFANA_URL = "http://127.0.0.1:3000"
GRAFANA_AUTH = ('admin', 'admin') 

def import_dashboard_to_grafana(node_prefix, new_title):
    try:
        # Looks for "grafana_dashboards/OutsideConditions.json" when node_prefix is "OutsideConditions"
        filename = f"{node_prefix}.json"
        file_path = os.path.join(BASE_DIR, "grafana_dashboards", filename)
        
        if not os.path.exists(file_path):
            print(f"GRAFANA ERROR: File not found {file_path}")
            return False

        with open(file_path, 'r') as f:
            dashboard_data = json.load(f)
        
        dashboard_data['title'] = new_title
        dashboard_data['id'] = None 
        
        payload = {
            "dashboard": dashboard_data,
            "overwrite": True,
            "folderId": 0
        }

        headers = {'Content-Type': 'application/json'}
        requests.post(
            f"{GRAFANA_URL}/api/dashboards/db", 
            json=payload, 
            auth=GRAFANA_AUTH,
            headers=headers
        )
        print(f"GRAFANA: Imported {new_title}")
        return True

    except Exception as e:
        print(f"GRAFANA EXCEPTION: {e}")
        return False

def delete_dashboard_from_grafana(dashboard_title):
    try:
        search_url = f"{GRAFANA_URL}/api/search?query={urllib.parse.quote(dashboard_title)}"
        search_resp = requests.get(search_url, auth=GRAFANA_AUTH)
        
        if search_resp.status_code != 200:
            return

        dashboards = search_resp.json()
        target_uid = None
        
        for d in dashboards:
            if d.get('title') == dashboard_title:
                target_uid = d.get('uid')
                break
        
        if target_uid:
            requests.delete(f"{GRAFANA_URL}/api/dashboards/uid/{target_uid}", auth=GRAFANA_AUTH)
            print(f"GRAFANA: Deleted {dashboard_title}")

    except Exception as e:
        print(f"GRAFANA DELETE EXCEPTION: {e}")

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
# === NODE ID SYNC HELPER                                                     ===
# ===============================================================================
def sync_nodeids_with_submodules():
    try:
        config = load_data()
        nodeid_path = os.path.join(BASE_DIR, "nodeid.yaml")
        if not os.path.exists(nodeid_path):
            return

        active_prefixes = set()
        if 'submodules' in config:
            for sub in config['submodules']:
                if 'node_prefix' in sub:
                    active_prefixes.add(sub['node_prefix'])
        
        universe_prefixes = set()
        if 'predefined_departments' in config:
            for dept in config['predefined_departments']:
                if 'short_name' in dept:
                    universe_prefixes.add(dept['short_name'])
        
        universe_prefixes.update(active_prefixes)
        sorted_universe = sorted(list(universe_prefixes), key=len, reverse=True)

        with open(nodeid_path, 'r') as f:
            lines = f.readlines()

        new_lines = []
        pattern = re.compile(r'^(\s*)(#?\s*)([A-Za-z0-9_.]+)(\s*:.*)')

        for line in lines:
            match = pattern.match(line)
            if match:
                indent = match.group(1)
                comment_mark = match.group(2)
                key = match.group(3)
                rest = match.group(4)

                matched_prefix = None
                for prefix in sorted_universe:
                    is_standard = key.startswith(prefix + '_')
                    is_di = key.startswith(f"DI_{prefix}_")
                    is_exact = (key == prefix)

                    if is_standard or is_di or is_exact:
                        matched_prefix = prefix
                        break
                
                if matched_prefix:
                    if matched_prefix in active_prefixes:
                        new_lines.append(f"{indent}{key}{rest}\n")
                    else:
                        if '#' not in comment_mark:
                            new_lines.append(f"{indent}# {key}{rest}\n")
                        else:
                            new_lines.append(line) 
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        with open(nodeid_path, 'w') as f:
            f.writelines(new_lines)
            
    except Exception as e:
        print(f"Error syncing nodeids: {e}")

# ===============================================================================
# === DATABASE ENGINE (THREAD OPTIMIZED)                                      ===
# ===============================================================================
initial_config = load_data()
DB_SERVER = initial_config.get('DB_SERVER')
DB_DATABASE = initial_config.get('DB_DATABASE')
DB_USER = initial_config.get('DB_USER')
DB_PASSWORD = initial_config.get('DB_PASSWORD')
OPC_UA_URL = initial_config.get('OPC_UA_URL')

db_engine = None
drivers_to_test = ['ODBC Driver 11 for SQL Server', 'ODBC Driver 17 for SQL Server', 'SQL Server']

for driver in drivers_to_test:
    try:
        print(f"Attempting DB connection using: {driver}...")
        params = urllib.parse.quote_plus(
            f'DRIVER={{{driver}}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USER};PWD={DB_PASSWORD}'
        )
        CONNECTION_STRING = f"mssql+pyodbc:///?odbc_connect={params}"
        engine_candidate = create_engine(CONNECTION_STRING, pool_size=10, max_overflow=20, pool_timeout=30, pool_pre_ping=True, pool_recycle=3600)
        with engine_candidate.connect() as conn: pass 
        db_engine = engine_candidate
        print(f"SUCCESS: Database Engine Initialized with {driver}")
        break 
    except Exception as e:
        print(f"FAILED: Could not connect with {driver}. Error: {e}")

if db_engine is None:
    print("CRITICAL: Failed to initialize DB Engine.")

def run_db_task(func, *args, **kwargs):
    """Executes a database function in a separate thread to avoid blocking Eventlet loop."""
    if db_engine is None:
        return None
    return tpool.execute(func, *args, **kwargs)

# ===============================================================================
# === DYNAMIC DEPARTMENT MAPPING                                              ===
# ===============================================================================
def get_departments_map():
    config = load_data()
    dept_map = {}
    submodules_list = config.get('submodules', [])
    for sub in submodules_list:
        name = sub.get('name')
        prefix = sub.get('node_prefix')
        category = sub.get('category', '').lower()
        if name and prefix:
            display_key = f"{name}"
            if 'wcs' in category:
                dept_map[display_key] = {'rh_table': prefix, 'temp_table': None}
            else:
                dept_map[display_key] = {'rh_table': f"{prefix}_RH", 'temp_table': f"{prefix}_T"}
            
    if config.get('show_outside_conditions', True):
        dept_map['OutsideConditions'] = {'rh_table': 'OutsideConditions_RH', 'temp_table': None}
    return dept_map

# ===============================================================================
# === REPORTING ENGINE HELPERS (THREADED - FIXED)                             ===
# ===============================================================================
def _fetch_column_names_worker(table_name):
    conn = None
    try:
        conn = db_engine.raw_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?", (table_name,))
        return [row.COLUMN_NAME for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error fetching columns: {e}")
        return []
    finally:
        if conn: conn.close()

def fetch_column_names(table_name):
    return run_db_task(_fetch_column_names_worker, table_name) or []

def _fetch_data_worker(table_name, from_date, to_date, time_difference_minutes, columns_to_fetch):
    conn = None
    try:
        conn = db_engine.raw_connection()
        cursor = conn.cursor()
        actual_table_columns = _fetch_column_names_worker(table_name)
        valid_cols = [col for col in columns_to_fetch if col in actual_table_columns]

        if 'date' in actual_table_columns and 'date' not in valid_cols: valid_cols.insert(0, 'date')
        if 'time' in actual_table_columns and 'time' not in valid_cols: valid_cols.insert(1, 'time')
        if not valid_cols: return []

        select_clause = ", ".join([f'"{col}"' for col in valid_cols])
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()

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
            if 'date' in row_dict and isinstance(row_dict['date'], (datetime, date)): 
                row_dict['date'] = str(row_dict['date'])
            if 'time' in row_dict:
                t_val = row_dict['time']
                if isinstance(t_val, datetime): row_dict['time'] = t_val.strftime("%H:%M:%S")
                else: row_dict['time'] = str(t_val)
            for key, value in row_dict.items():
                if isinstance(value, float): row_dict[key] = round(value, 2)
            data.append(row_dict)
        return data
    except Exception as e:
        print(f"Error fetching data: {e}")
        return []
    finally:
        if conn: conn.close()

def fetch_data(table_name, from_date, to_date, time_difference, columns_to_fetch):
    time_diff_map = {'10 minutes': 10, '30 minutes': 30, '1 hour': 60, '2 hours': 120, '5 hours': 300, '1 Day': 1440}
    time_difference_minutes = time_diff_map.get(time_difference, 0)
    return run_db_task(_fetch_data_worker, table_name, from_date, to_date, time_difference_minutes, columns_to_fetch)

# ===============================================================================
# === ADMIN / CRUD HELPERS (THREADED - FIXED)                                 ===
# ===============================================================================
def _execute_query_worker(query, params, fetch):
    conn = None
    try:
        conn = db_engine.raw_connection()
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch == 'all':
            column_names = [column[0] for column in cursor.description]
            return [dict(zip(column_names, row)) for row in cursor.fetchall()]
        elif fetch == 'one':
            return cursor.fetchone()
        else:
            conn.commit()
            return True
    except Exception as e:
        print(f"Query Error: {e}")
        return None if fetch else False
    finally:
        if conn: conn.close()

def execute_query(query, params=(), fetch=None):
    return run_db_task(_execute_query_worker, query, params, fetch)

# ===============================================================================
# === USER AUTH (THREADED - FIXED)                                            ===
# ===============================================================================
def _validate_user_worker(username, password):
    conn = None
    try:
        conn = db_engine.raw_connection()
        cursor = conn.cursor()
        for table in ["Operator", "Manager", "Tse"]:
            cursor.execute(f"SELECT * FROM {table} WHERE username = ?", (username,))
            user = cursor.fetchone()
            if user and user[1] == username:
                try:
                    if bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
                        return table
                except (ValueError, AttributeError):
                    continue
        return None
    except Exception as e:
        print(f"Auth Error: {e}")
        return None
    finally:
        if conn: conn.close()

def validate_user(username, password):
    return run_db_task(_validate_user_worker, username, password)

# =========================================================================
# === ALARMS & OPC UA SECTION (UPDATED FOR JSON)                        ===
# =========================================================================
ALARM_LIST_FILE_PATH = os.path.join(BASE_DIR, "alarmList.json")
ALARMS_CONFIG_PATH = os.path.join(BASE_DIR, "alarms.yaml")
YAML_ACCESS_LOCK = threading.Lock()
LAST_TRIP_STATES = {}

def load_json_data(filepath):
    """Helper to safely load JSON data."""
    if not os.path.exists(filepath):
        return {"alarms": [], "lastTripStates": {}}
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        return {"alarms": [], "lastTripStates": {}}

def save_json_data(filepath, data):
    """Helper to save data as JSON."""
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

def read_values_periodically():
    global RUNTIME_STORE
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

    active_nodes = []
    active_node_names = []
    
    while True:
        try:
            if not active_nodes:
                node_ids_map = load_node_ids()
                temp_nodes = []
                temp_names = []
                for name, node_id in node_ids_map.items():
                    try:
                        node = client.get_node(node_id)
                        temp_nodes.append(node)
                        temp_names.append(name)
                    except Exception: pass
                active_nodes = temp_nodes
                active_node_names = temp_names

            if active_nodes:
                try:
                    values = client.get_values(active_nodes)
                    updates = {}
                    for i, val in enumerate(values):
                        if val is not None and not isinstance(val, ua.StatusCode):
                            if isinstance(val, bool): updates[active_node_names[i]] = val
                            elif isinstance(val, (int, float)): updates[active_node_names[i]] = round(val, 1)
                            else: updates[active_node_names[i]] = val
                                            
                    if updates:
                        with RUNTIME_STORE["lock"]:
                            RUNTIME_STORE["values"].update(updates)
                            full_snapshot = RUNTIME_STORE["values"].copy()
                        socketio.emit('update', full_snapshot) 
                        socketio.emit('opc_status', {'connected': True})

                except Exception as read_err:
                    socketio.emit('opc_status', {'connected': False})
                    active_nodes = [] 
                    try: client.disconnect()
                    except: pass
                    connected = False
            
            socketio.sleep(0.5)
            
            if not connected:
                try:
                    client.connect()
                    connected = True
                    print("OPC UA Reconnected.")
                except:
                    socketio.sleep(2)

        except Exception as e:
            print(f"OPC Loop Error: {e}")
            socketio.sleep(5)

def initialize_alarm_states():
    global LAST_TRIP_STATES
    with YAML_ACCESS_LOCK:
        data = load_json_data(ALARM_LIST_FILE_PATH)
        LAST_TRIP_STATES = data.get('lastTripStates', {})

def alarm_processing_engine():
    global LAST_TRIP_STATES, RUNTIME_STORE
    
    # Load rules from YAML (Config stays YAML)
    alarm_rules = []
    try:
        with open(ALARMS_CONFIG_PATH, "r") as f:
            alarm_rules = yaml.safe_load(f).get("alarms", [])
    except:
        pass

    while True:
        try:
            current_opc_values = {}
            with RUNTIME_STORE["lock"]:
                current_opc_values = RUNTIME_STORE["values"].copy()

            if not current_opc_values:
                socketio.sleep(1)
                continue
            
            trip_states_changed = False
            new_trip_map = LAST_TRIP_STATES.copy()
            
            for rule in alarm_rules:
                code = str(rule['code'])
                current_trip = False
                for node_name in rule.get('nodeids', []):
                    node_value = current_opc_values.get(node_name)
                    if node_value is not None:
                        if isinstance(node_value, bool) and node_value: current_trip = True
                        elif isinstance(node_value, (int, float)) and node_value != 0: current_trip = True
                        elif isinstance(node_value, str) and node_value.lower() in ['true', '1', 'on', 'active']: current_trip = True
                
                prev_trip = new_trip_map.get(code, False)
                if current_trip != prev_trip:
                    trip_states_changed = True
                    new_trip_map[code] = current_trip

            if trip_states_changed:
                with YAML_ACCESS_LOCK:
                    full_alarm_data = load_json_data(ALARM_LIST_FILE_PATH)
                    
                    persistent_alarms = full_alarm_data.get('alarms', [])
                    something_changed = False
                    
                    for rule in alarm_rules:
                        code = str(rule['code'])
                        current_trip = new_trip_map.get(code, False)
                        prev_trip = LAST_TRIP_STATES.get(code, False)
                        
                        if current_trip and not prev_trip:
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
                                
                        elif not current_trip and prev_trip:
                            for alarm in reversed(persistent_alarms):
                                if str(alarm.get('code')) == code and alarm.get('status') != 'Resolved':
                                    alarm['status'] = 'Resolved'
                                    alarm['resolved_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                    something_changed = True
                                    break

                    LAST_TRIP_STATES = new_trip_map
                    
                    if something_changed:
                        final_data = {'alarms': persistent_alarms, 'lastTripStates': LAST_TRIP_STATES}
                        save_json_data(ALARM_LIST_FILE_PATH, final_data)
                        socketio.emit('alarm_update', persistent_alarms)

            socketio.sleep(0.5)
        except Exception as e:
            print(f"Alarm Engine Error: {e}")
            socketio.sleep(2)
                        
@app.route("/notifyAlarms")
def get_alarms_notification():
    with YAML_ACCESS_LOCK:
        alarms = load_alarms_from_json_list()
    return jsonify(alarms)

def load_alarms_from_json_list():
    try:
        data = load_json_data(ALARM_LIST_FILE_PATH)
        alarms = data.get('alarms', [])
        alarms.sort(key=lambda x: x.get('time', ''), reverse=True)

        for alarm in alarms:
            try:
                dt = datetime.strptime(alarm['time'], "%Y-%m-%d %H:%M:%S")
                alarm['time'] = dt.strftime("%m/%d/%Y, %I:%M:%S %p")
            except (ValueError, KeyError): pass
        return alarms
    except Exception: return []

@app.route("/alarmslist")
def alarmslist():
    return render_template("iot/alarmslist.html")

def emit_alarms():
    while True:
        with YAML_ACCESS_LOCK:
            alarms = load_alarms_from_json_list()
        socketio.emit('alarm_update', alarms)
        socketio.sleep(2)

@app.route("/acknowledge", methods=["POST"])
def acknowledge_alarm():
    with YAML_ACCESS_LOCK:
        try:
            alarm_id = request.json.get("id")
            if not alarm_id: return jsonify({"success": False, "error": "Missing alarm ID"}), 400

            data = load_json_data(ALARM_LIST_FILE_PATH)
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
                save_json_data(ALARM_LIST_FILE_PATH, data)
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
    
    def _write_task():
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
            return True
        except Exception as e:
            print(f"Write error: {e}")
            return False
        finally:
            try: opcua_client.disconnect()
            except: pass

    success = run_db_task(_write_task)
    if success: return jsonify({"success": True}), 200
    return jsonify({"success": False, "error": "Write failed"}), 500

@app.route('/writes', methods=['POST'])
def writes():
    if 'userloggedin' not in session: return jsonify({"success": False, "error": "Unauthorized"}), 401
    data = request.get_json()
    if not data: return jsonify({"success": False}), 400
    
    def _writes_task():
        opcua_client = Client(OPC_UA_URL)
        try:
            opcua_client.connect()
            node_ids = load_node_ids()
            for nodeid, value in data.items():
                if nodeid not in node_ids: return False
                node = opcua_client.get_node(node_ids[nodeid])
                node_data_type = node.get_data_type_as_variant_type()
                dv = None
                if node_data_type == ua.VariantType.Boolean: dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
                elif node_data_type == ua.VariantType.Int32: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
                elif node_data_type == ua.VariantType.UInt16: dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.UInt16))
                elif node_data_type == ua.VariantType.Float: dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
                if dv: node.set_value(dv)
            return True
        except Exception as e:
            print(f"Writes error: {e}")
            return False
        finally:
            try: opcua_client.disconnect()
            except: pass

    success = run_db_task(_writes_task)
    if success: return jsonify({"success": True}), 200
    return jsonify({"success": False, "error": "Writes failed"}), 500

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
    if vals: socketio.emit('update', vals)
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
    report_fields = config_data.get('report_fields', [])
    department_categories = {}
    for sub in config_data.get('submodules', []):
        department_categories[sub['name']] = sub['category']
    
    response = make_response(render_template(
        'report.html', 
        department_options=department_options, 
        client_name=client_name,
        department_categories=department_categories,
        report_fields=report_fields
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
    if dept_config.get('rh_table'): 
        cols = fetch_column_names(dept_config['rh_table'])
        if cols: all_columns.update(cols)
    if dept_config.get('temp_table'): 
        cols = fetch_column_names(dept_config['temp_table'])
        if cols: all_columns.update(cols)

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
    data = load_data()
    current_fields = data.get('report_fields', [])
    return make_response(render_template('adminpage.html', current_fields=current_fields))

@app.route('/update_report_settings', methods=['POST'])
def update_report_settings():
    if session.get('role') != 'Tse': return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        selected_fields = request.json.get('fields', [])
        data = load_data()
        data['report_fields'] = selected_fields
        save_yaml(data)
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
            item = {'name': submodule.get('name'), 'category': category, 'sensors': []}
            if 'h-plant' in category:
                item['sensors'].append({'key': f"{node_prefix}_iVa_Act_Temp", 'label': 'Temp', 'unit': '°C', 'color': 'rgba(255, 148, 112)'})
                item['sensors'].append({'key': f"{node_prefix}_iVa_Act_RH", 'label': 'Hum', 'unit': '%RH', 'color': 'rgba(40, 67, 135)'})
            elif 'wcs' in category:
                if 'wcs-1' in category:
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_PDF", 'label': 'PDF', 'unit': 'Pa', 'color': 'rgba(255, 99, 132)'})
                elif 'wcs-2' in category or 'wcs-3' in category:
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_PDF", 'label': 'PDF', 'unit': 'Pa', 'color': 'rgba(255, 99, 132)'})
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_RDF", 'label': 'RDF', 'unit': 'Pa', 'color': 'rgba(54, 162, 235)'})
                elif 'wcs-4' in category or 'wcs-5' in category:
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_PDF1", 'label': 'PDF1', 'unit': 'Pa', 'color': 'rgba(255, 99, 132)'})
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_PDF2", 'label': 'PDF2', 'unit': 'Pa', 'color': 'rgba(255, 206, 86)'})
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_RDF", 'label': 'RDF', 'unit': 'Pa', 'color': 'rgba(54, 162, 235)'})
                else:
                    item['sensors'].append({'key': f"{node_prefix}_iVa_DPT_PDF", 'label': 'PDF', 'unit': 'Pa', 'color': 'rgba(255, 99, 132)'})
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
    
    if os.path.exists(os.path.join(BASE_DIR, "templates", "iot", template_name)):
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
            
            # --- FIX: Restored this line for Last Login Display ---
            session['last_login'] = datetime.now().strftime("%d-%b-%Y %I:%M %p")
            # ------------------------------------------------------
            
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
            # Use threaded execution
            def _add_user_task():
                conn = None
                try:
                    conn = db_engine.raw_connection()
                    cursor = conn.cursor()
                    hashed = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt())
                    cursor.execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (request.form['username'], hashed.decode('utf-8')))
                    conn.commit()
                    return True
                except Exception as e:
                    return str(e)
                finally:
                    if conn: conn.close()
            
            result = run_db_task(_add_user_task)
            if result is True:
                flash(f"User added to {table}.", 'success')
            else:
                flash(f"Error: {result}", 'danger')
    return render_template('iot/add_user.html')

@app.route('/user_management')
def user_management():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    
    def _get_users_task():
        users = []
        conn = None
        try:
            conn = db_engine.raw_connection()
            cursor = conn.cursor()
            for table in ["Operator", "Manager", "Tse"]:
                cursor.execute(f"SELECT * FROM {table}")
                for row in cursor.fetchall():
                    users.append({"username": row[1], "password": "Encrypted", "role": table})
        except: pass
        finally:
            if conn: conn.close()
        return users

    users = run_db_task(_get_users_task)
    return render_template('iot/user_management.html', users=users)

@app.route('/edit_user', methods=['POST'])
def edit_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    username = request.form['username']
    new_role = request.form['role']
    password = request.form['password']
    
    def _edit_user_task():
        conn = None
        try:
            conn = db_engine.raw_connection()
            cursor = conn.cursor()
            for table in ["Operator", "Manager", "Tse"]:
                cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
            hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cursor.execute(f"INSERT INTO {new_role} (username, password) VALUES (?, ?)", (username, hashed))
            conn.commit()
        except: pass
        finally:
            if conn: conn.close()

    run_db_task(_edit_user_task)
    flash("User updated.", "success")
    return redirect(url_for('user_management'))

@app.route('/delete_user', methods=['POST'])
def delete_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    username = request.form['username']
    
    def _delete_user_task():
        conn = None
        try:
            conn = db_engine.raw_connection()
            cursor = conn.cursor()
            for table in ["Operator", "Manager", "Tse"]:
                cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
            conn.commit()
        except: pass
        finally:
            if conn: conn.close()

    run_db_task(_delete_user_task)
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
def input_page():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    data = load_data()
    if request.method == 'POST':
        msg = ""
        if 'client_name' in request.form:
            data['client_name'] = request.form['client_name']
            msg = "Client name updated"
            
        elif 'opc_ua_url' in request.form:
            data['OPC_UA_URL'] = request.form['opc_ua_url']
            save_yaml(data) 
            def restart_app():
                print("--- OPC UA URL Changed. Restarting Application... ---")
                socketio.sleep(1) 
                os.execv(sys.executable, ['python'] + sys.argv)
            socketio.start_background_task(restart_app)
            msg = "OPC UA URL updated. Application is restarting..."
            return jsonify(success=True, message=msg)

        # --- GRAFANA INTEGRATION RESTORED ---
        elif 'toggle_outside_conditions' in request.form:
            vis = request.form['toggle_outside_conditions'] == 'true'
            data['show_outside_conditions'] = vis
            if vis:
                import_dashboard_to_grafana("OutsideConditions", "Outside Conditions")
            else:
                delete_dashboard_from_grafana("Outside Conditions")
            msg = "Outside conditions visibility updated"
        # -------------------------------------
            
        elif 'submodule_name' in request.form:
            new_sub = {
                'category': request.form['submodule_category'],
                'name': request.form['submodule_name'],
                'template': request.form['submodule_file'],
                'node_prefix': request.form['node_prefix']
            }
            if 'submodules' not in data: data['submodules'] = []
            data['submodules'].append(new_sub)
            msg = "Submodule added"
            
            success = import_dashboard_to_grafana(new_sub['node_prefix'], new_sub['name'])
            if not success:
                msg += " (Warning: Grafana dashboard import failed. Check logs.)"
            
        save_yaml(data)
        sync_nodeids_with_submodules()
        
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
    
    sync_nodeids_with_submodules()
    delete_dashboard_from_grafana(sub_name)
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
    
    sync_nodeids_with_submodules()
    
    delete_dashboard_from_grafana(old_name)
    import_dashboard_to_grafana(new_prefix, new_name)
    return jsonify({'success': True})

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        new_order = request.json.get('new_order', [])
        data = load_data()
        data['submodules'] = new_order
        with open(os.path.join(BASE_DIR, 'input.yaml'), 'w') as f: 
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
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

@app.route('/clear_alarms', methods=['POST'])
def clear_alarms():
    if 'userloggedin' not in session: return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        with YAML_ACCESS_LOCK:
            empty_data = {'alarms': [], 'lastTripStates': {}}
            save_json_data(ALARM_LIST_FILE_PATH, empty_data)
            
            global LAST_TRIP_STATES
            LAST_TRIP_STATES = {}
            
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

# =========================================================================
# === NEW ROUTE: RESTART APPLICATION                                    ===
# =========================================================================
@app.route('/restart_server', methods=['POST'])
def restart_server():
    if 'userloggedin' not in session: 
        return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    
    def _restart_task():
        print("--- Manual Restart Initiated ---")
        socketio.sleep(1) 
        os.execv(sys.executable, ['python'] + sys.argv)
    
    socketio.start_background_task(_restart_task)
    return jsonify({'success': True, 'message': 'Application is restarting. The page will reload shortly.'})

# ===============================================================================
# === INSTALLATION HELPER LOGIC                                               ===
# ===============================================================================
def run_installation_tasks(sql_server, opc_url):
    print("--- STARTING POST-INSTALLATION TASKS ---")
    try:
        data = load_data()
        data['DB_SERVER'] = sql_server
        data['OPC_UA_URL'] = opc_url
        save_yaml(data)
        print(f"SUCCESS: Updated input.yaml with Server: {sql_server} and OPC: {opc_url}")
    except Exception as e:
        print(f"ERROR: Could not update input.yaml - {e}")

    try:
        print("--- Running Database Initialization (db.py) ---")
        import db  
        print("SUCCESS: Database initialized.")
    except ImportError: print("WARNING: db.py not found. Skipping DB init.")
    except Exception as e: print(f"ERROR: Database initialization failed - {e}")

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--install-config':
        if len(sys.argv) >= 4:
            run_installation_tasks(sys.argv[2], sys.argv[3])
        else:
            print("Error: Missing arguments for installation configuration.")
        sys.exit(0)

    sync_nodeids_with_submodules()
    initialize_alarm_states()
    socketio.start_background_task(read_values_periodically)
    socketio.start_background_task(alarm_processing_engine)
    socketio.start_background_task(emit_alarms)
    socketio.run(app, host="0.0.0.0", port=7005, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)