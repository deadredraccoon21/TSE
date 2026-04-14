from gevent import monkey
monkey.patch_all()
from gevent.threadpool import ThreadPool

from flask import Flask, jsonify, request, render_template, redirect, url_for, session, flash, send_file, make_response
from flask_cors import CORS
from opcua import Client, ua
from collections import defaultdict
import socket
import shift_report
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
import glob

# Create a threadpool for native C-blocking DB operations (like pyodbc)
tpool = ThreadPool(10)

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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

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
def load_predefined_departments():
    yaml_path = os.path.join(BASE_DIR, "predefined_departments.yml")
    try:
        with open(yaml_path, "r") as f:
            data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                return data.get('predefined_departments', [])
            elif isinstance(data, list):
                return data
            return []
    except Exception as e:
        return []
    
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
        filename = f"{node_prefix}.json"
        file_path = os.path.join(BASE_DIR, "grafana_dashboards", filename)
        if not os.path.exists(file_path): return False

        with open(file_path, 'r') as f:
            dashboard_data = json.load(f)
        
        dashboard_data['title'] = new_title
        dashboard_data['id'] = None 
        
        payload = {"dashboard": dashboard_data, "overwrite": True, "folderId": 0}
        headers = {'Content-Type': 'application/json'}
        requests.post(f"{GRAFANA_URL}/api/dashboards/db", json=payload, auth=GRAFANA_AUTH, headers=headers)
        return True
    except Exception as e: return False

def delete_dashboard_from_grafana(dashboard_title):
    try:
        search_url = f"{GRAFANA_URL}/api/search?query={urllib.parse.quote(dashboard_title)}"
        search_resp = requests.get(search_url, auth=GRAFANA_AUTH)
        if search_resp.status_code != 200: return

        dashboards = search_resp.json()
        target_uid = next((d.get('uid') for d in dashboards if d.get('title') == dashboard_title), None)
        if target_uid: requests.delete(f"{GRAFANA_URL}/api/dashboards/uid/{target_uid}", auth=GRAFANA_AUTH)
    except Exception as e: pass

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
                if (datetime.now() - last_activity).total_seconds() >= 1200:
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
        if not os.path.exists(nodeid_path): return

        active_prefixes = set(sub['node_prefix'] for sub in config.get('submodules', []) if 'node_prefix' in sub)
        universe_prefixes = set(dept['short_name'] for dept in load_predefined_departments() if 'short_name' in dept)
        universe_prefixes.update(active_prefixes)
        sorted_universe = sorted(list(universe_prefixes), key=len, reverse=True)

        with open(nodeid_path, 'r') as f: lines = f.readlines()

        new_lines = []
        pattern = re.compile(r'^(\s*)(#?\s*)([A-Za-z0-9_.]+)(\s*:.*)')

        for line in lines:
            match = pattern.match(line)
            if match:
                indent, comment_mark, key, rest = match.groups()
                matched_prefix = next((p for p in sorted_universe if key.startswith(p + '_') or key.startswith(f"DI_{p}_") or key == p), None)
                if matched_prefix:
                    if matched_prefix in active_prefixes: new_lines.append(f"{indent}{key}{rest}\n")
                    else: new_lines.append(f"{indent}# {key}{rest}\n" if '#' not in comment_mark else line)
                else: new_lines.append(line)
            else: new_lines.append(line)

        with open(nodeid_path, 'w') as f: f.writelines(new_lines)
    except Exception as e: pass

# ===============================================================================
# === DATABASE ENGINE                                                         ===
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
        params = urllib.parse.quote_plus(f'DRIVER={{{driver}}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USER};PWD={DB_PASSWORD}')
        engine_candidate = create_engine(f"mssql+pyodbc:///?odbc_connect={params}", pool_size=10, max_overflow=20, pool_timeout=30, pool_pre_ping=True, pool_recycle=3600)
        with engine_candidate.connect() as conn: pass 
        db_engine = engine_candidate
        print(f"Database Engine Initialized with {driver}")
        break 
    except Exception as e: pass

def run_db_task(func, *args, **kwargs):
    if db_engine is None: return None
    return tpool.spawn(func, *args, **kwargs).get()

def get_departments_map():
    config = load_data()
    dept_map = {}
    for sub in config.get('submodules', []):
        if sub.get('name') and sub.get('node_prefix'):
            # Detect whether this belongs to WCS or H-PLANT
            cat_str = sub.get('category', '').upper()
            main_cat = "WCS" if "WCS" in cat_str else "H-PLANT"
            
            if main_cat == "WCS":
                dept_map[sub['name']] = {'rh_table': sub['node_prefix'], 'temp_table': None, 'category': main_cat}
            else:
                dept_map[sub['name']] = {'rh_table': f"{sub['node_prefix']}_RH", 'temp_table': f"{sub['node_prefix']}_T", 'category': main_cat}
                
    if config.get('show_outside_conditions', True):
        dept_map['OutsideConditions'] = {'rh_table': 'OutsideConditions_RH', 'temp_table': None, 'category': 'H-PLANT'}
    return dept_map

# ===============================================================================
# === REPORTING ENGINE HELPERS (UI DATA)                                      ===
# ===============================================================================
def _fetch_column_names_worker(table_name):
    conn = None
    try:
        conn = db_engine.raw_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?", (table_name,))
        return [row.COLUMN_NAME for row in cursor.fetchall()]
    except Exception as e: return []
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
        query = f'SELECT {select_clause} FROM "{table_name}" WHERE "date" >= ? AND "date" <= ?'
        params = [datetime.strptime(from_date, "%Y-%m-%d").date(), datetime.strptime(to_date, "%Y-%m-%d").date()]

        if time_difference_minutes > 0 and 'time' in actual_table_columns:
            query += " AND DATEDIFF(MINUTE, CAST('00:00:00' AS TIME), CAST(time AS TIME)) % ? = 0"
            params.append(time_difference_minutes)
            
        cursor.execute(query, tuple(params))
        column_names = [column[0] for column in cursor.description]
        
        data = []
        for row in cursor.fetchall():
            row_dict = dict(zip(column_names, row))
            if 'date' in row_dict and isinstance(row_dict['date'], (datetime, date)): row_dict['date'] = str(row_dict['date'])
            if 'time' in row_dict:
                t_val = row_dict['time']
                if isinstance(t_val, datetime): row_dict['time'] = t_val.strftime("%H:%M:%S")
                else: row_dict['time'] = str(t_val)
            for key, value in row_dict.items():
                if isinstance(value, float): row_dict[key] = round(value, 2)
            data.append(row_dict)
        return data
    except Exception as e: return []
    finally:
        if conn: conn.close()

def fetch_data(table_name, from_date, to_date, time_difference, columns_to_fetch):
    time_diff_map = {'10 minutes': 10, '30 minutes': 30, '1 hour': 60, '2 hours': 120, '5 hours': 300, '1 Day': 1440}
    return run_db_task(_fetch_data_worker, table_name, from_date, to_date, time_diff_map.get(time_difference, 0), columns_to_fetch)

# ===============================================================================
# === ADMIN / CRUD HELPERS & USER AUTH                                        ===
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
        elif fetch == 'one': return cursor.fetchone()
        else:
            conn.commit()
            return True
    except Exception as e: return None if fetch else False
    finally:
        if conn: conn.close()

def execute_query(query, params=(), fetch=None):
    return run_db_task(_execute_query_worker, query, params, fetch)

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
                    if bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')): return table
                except (ValueError, AttributeError): continue
        return None
    except Exception as e: return None
    finally:
        if conn: conn.close()

def validate_user(username, password):
    return run_db_task(_validate_user_worker, username, password)

# =========================================================================
# === ALARMS & OPC UA SECTION                                           ===
# =========================================================================
ALARM_LIST_FILE_PATH = os.path.join(BASE_DIR, "alarmList.json")
ALARMS_CONFIG_PATH = os.path.join(BASE_DIR, "alarms.yaml")
YAML_ACCESS_LOCK = threading.Lock()
LAST_TRIP_STATES = {}

def load_json_data(filepath):
    if not os.path.exists(filepath): return {"alarms": [], "lastTripStates": {}}
    try:
        with open(filepath, 'r') as f: return json.load(f)
    except (json.JSONDecodeError, ValueError): return {"alarms": [], "lastTripStates": {}}

def save_json_data(filepath, data):
    with open(filepath, 'w') as f: json.dump(data, f, indent=4)

def update_opc_connection_alarm(is_disconnected):
    """Creates a system alarm when OPC UA disconnects, and resolves it when reconnected."""
    with YAML_ACCESS_LOCK:
        full_alarm_data = load_json_data(ALARM_LIST_FILE_PATH)
        persistent_alarms = full_alarm_data.get('alarms', [])
        something_changed = False
        code = "SYS_OPC_DISCONNECT"
        
        if is_disconnected:
            # Check if there's already an active disconnect alarm so we don't spam
            has_active = any(str(a.get('code')) == code and a.get('status') not in ['Resolved', 'Acknowledged'] for a in persistent_alarms)
            if not has_active:
                new_alarm = {
                    'id': str(uuid.uuid4()), 
                    'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    'message': "Controller / OPC UA Server Disconnected", 
                    'code': code, 
                    'severity': "Critical",
                    'status': 'Not acknowledged', 
                    'acknowledged': False
                }
                persistent_alarms.append(new_alarm)
                something_changed = True
        else:
            # If reconnected, resolve the active disconnect alarm
            for alarm in reversed(persistent_alarms):
                if str(alarm.get('code')) == code and alarm.get('status') != 'Resolved':
                    alarm['status'] = 'Resolved'
                    alarm['resolved_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    something_changed = True
                    break
                    
        if something_changed:
            save_json_data(ALARM_LIST_FILE_PATH, {'alarms': persistent_alarms, 'lastTripStates': full_alarm_data.get('lastTripStates', {})})
            socketio.emit('alarm_update', persistent_alarms)

def read_values_periodically():
    global RUNTIME_STORE
    client = Client(OPC_UA_URL)
    connected = False
    first_fail_printed = False 
    
    while not connected:
        try:
            client.connect()
            connected = True
            print("OPC UA Client Connected successfully.")
            update_opc_connection_alarm(False) # Clear any previous alarms on startup
        except Exception as e:
            if not first_fail_printed:
                print("OPC UA Connection offline. Retrying silently in background...")
                update_opc_connection_alarm(True) # Trigger alarm if initial connection fails
                first_fail_printed = True
            socketio.sleep(5)

    active_nodes = []
    active_node_names = []
    
    while True:
        try:
            if not active_nodes:
                node_ids_map = load_node_ids()
                temp_nodes, temp_names = [], []
                for name, node_id in node_ids_map.items():
                    try:
                        node = client.get_node(node_id)
                        temp_nodes.append(node)
                        temp_names.append(name)
                    except Exception: pass
                active_nodes, active_node_names = temp_nodes, temp_names

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
                    
                    if connected: # Only trigger the alarm if we just lost a live connection
                        print(f"OPC Loop Error: {read_err}")
                        update_opc_connection_alarm(True) 
                        
                    connected = False
            
            socketio.sleep(0.5)
            
            if not connected:
                try:
                    client.connect()
                    connected = True
                    print("OPC UA Reconnected.")
                    update_opc_connection_alarm(False) # Auto-resolve the alarm
                except: socketio.sleep(2)

        except Exception as e:
            if connected: print(f"OPC Loop Error: {e}")
            socketio.sleep(5)

def initialize_alarm_states():
    global LAST_TRIP_STATES
    with YAML_ACCESS_LOCK:
        LAST_TRIP_STATES = load_json_data(ALARM_LIST_FILE_PATH).get('lastTripStates', {})

def alarm_processing_engine():
    global LAST_TRIP_STATES, RUNTIME_STORE
    alarm_rules = []
    try:
        with open(ALARMS_CONFIG_PATH, "r") as f: alarm_rules = yaml.safe_load(f).get("alarms", [])
    except: pass

    while True:
        try:
            current_opc_values = {}
            with RUNTIME_STORE["lock"]: current_opc_values = RUNTIME_STORE["values"].copy()
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
                                    'id': str(uuid.uuid4()), 'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                                    'message': rule['message'], 'code': code, 'severity': rule['severity'],
                                    'status': 'Not acknowledged', 'acknowledged': False
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
                        save_json_data(ALARM_LIST_FILE_PATH, {'alarms': persistent_alarms, 'lastTripStates': LAST_TRIP_STATES})
                        socketio.emit('alarm_update', persistent_alarms)
            socketio.sleep(0.5)
        except Exception as e: socketio.sleep(2)
                        
@app.route("/notifyAlarms")
def get_alarms_notification():
    with YAML_ACCESS_LOCK: alarms = load_alarms_from_json_list()
    return jsonify(alarms)

def load_alarms_from_json_list():
    try:
        alarms = load_json_data(ALARM_LIST_FILE_PATH).get('alarms', [])
        priority_map = {'Not acknowledged': 0, 'Acknowledged': 1, 'Resolved': 2}
        alarms.sort(key=lambda x: x.get('time', ''), reverse=True)
        alarms.sort(key=lambda x: priority_map.get(x.get('status', 'Not acknowledged'), 0))
        for alarm in alarms:
            try:
                if '-' in alarm['time']: alarm['time'] = datetime.strptime(alarm['time'], "%Y-%m-%d %H:%M:%S").strftime("%d/%m/%Y, %I:%M:%S %p")
            except (ValueError, KeyError): pass
        return alarms
    except Exception as e: return []

@app.route("/alarmslist")
def alarmslist(): return render_template("iot/alarmslist.html")

def emit_alarms():
    while True:
        with YAML_ACCESS_LOCK: alarms = load_alarms_from_json_list()
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
        except Exception as e: return jsonify({"success": False, "error": str(e)}), 500

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
        except Exception as e: return False
        finally:
            try: opcua_client.disconnect()
            except: pass

    if run_db_task(_write_task): return jsonify({"success": True}), 200
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
        except Exception as e: return False
        finally:
            try: opcua_client.disconnect()
            except: pass

    if run_db_task(_writes_task): return jsonify({"success": True}), 200
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
    target_dept = next((s for s in config.get("submodules", []) if s['name'] == parent_submodule), None)
    if not target_dept: return "Not found", 404

    category = target_dept.get('category')
    prefix = target_dept.get('node_prefix')
    template_name = f"{slugify(category)}{setting_suffix_mapping.get(setting_option)}"
    
    vals = {}
    with RUNTIME_STORE["lock"]: vals = RUNTIME_STORE["values"].copy()
    msg = {'payload': vals, 'node_ids': load_node_ids()}
    if vals: socketio.emit('update', vals)
    return render_template(f"Settings/{template_name}", msg=msg, prefix=prefix)

# =========================================================================
# === REPORTING ROUTES (WEB TRENDS UI)                                  ===
# =========================================================================
@app.route('/report')
def reportpage():
    dept_map = get_departments_map()
    config_data = load_data()
    department_categories = {sub['name']: sub['category'] for sub in config_data.get('submodules', [])}
    
    response = make_response(render_template(
        'report.html', 
        department_options=sorted(list(dept_map.keys())), 
        client_name=config_data.get('client_name', 'Default Client'),
        department_categories=department_categories,
        report_fields=config_data.get('report_fields', [])
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

@app.route('/getTables', methods=['POST'])
def get_tables():
    dept_config = get_departments_map().get(request.form.get('department'))
    if not dept_config: return jsonify({'error': 'Department not found'}), 400

    all_columns = set()
    if dept_config.get('rh_table'): all_columns.update(fetch_column_names(dept_config['rh_table']))
    if dept_config.get('temp_table'): all_columns.update(fetch_column_names(dept_config['temp_table']))

    display_columns = sorted([col for col in all_columns if col.lower() not in ['idx', 'id']])
    return jsonify({'columns': display_columns})

@app.route('/data', methods=['POST'])
def get_data_api():
    dept_config = get_departments_map().get(request.form.get('department'))
    if not dept_config: return jsonify({'error': 'Invalid department'}), 400

    from_date, to_date = request.form.get('from_date'), request.form.get('to_date')
    time_diff = request.form.get('time_difference')
    selected_fields = request.form.get('selected_fields', '').split(',') if request.form.get('selected_fields', '') else []

    rh_data = fetch_data(dept_config.get('rh_table'), from_date, to_date, time_diff, selected_fields) if dept_config.get('rh_table') else []
    temp_data = fetch_data(dept_config.get('temp_table'), from_date, to_date, time_diff, selected_fields) if dept_config.get('temp_table') else []

    merged_data = {}
    for row in rh_data + temp_data:
        key = (row.get('date'), row.get('time'))
        if key[0] and key[1]:
            if key not in merged_data: merged_data[key] = {}
            merged_data[key].update(row)

    final_data = list(merged_data.values())
    all_fetched_columns = set().union(*(row.keys() for row in final_data))
    
    display_columns = [col for col in ['date', 'time'] if col in all_fetched_columns]
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
    return make_response(render_template('adminpage.html', current_fields=load_data().get('report_fields', [])))

@app.route('/update_report_settings', methods=['POST'])
def update_report_settings():
    if session.get('role') != 'Tse': return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        data = load_data()
        data['report_fields'] = request.json.get('fields', [])
        save_yaml(data)
        return jsonify({'success': True})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/admin_logout')
def admin_logout(): return redirect(url_for('logout'))

@app.route('/userlogout')
def user_logout(): return redirect(url_for('logout'))

@app.route('/users')
def manage_users(): 
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    return render_template('index.html', records=execute_query("SELECT * FROM users", fetch='all'), table_name='users')

@app.route('/admins')
def manage_admins(): 
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    return render_template('admin.html', records=execute_query("SELECT * FROM admins", fetch='all'), table_name='admins')

@app.route('/insert/<table_name>', methods=['POST'])
def crud_insert(table_name):
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    if table_name not in ['users', 'admins']: return "Invalid table", 400
    execute_query(f"INSERT INTO {table_name} (username, password) VALUES (?, ?)", (request.form['username'], request.form['password']))
    return redirect(url_for('manage_users') if table_name == 'users' else url_for('manage_admins'))

@app.route('/delete/<table_name>/<int:record_id>')
def crud_delete(table_name, record_id):
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    if table_name not in ['users', 'admins']: return "Invalid table", 400
    execute_query(f"DELETE FROM {table_name} WHERE id=?", (record_id,))
    return redirect(url_for('manage_users') if table_name == 'users' else url_for('manage_admins'))

@app.route('/update/<table_name>/<int:record_id>', methods=['POST'])
def crud_update(table_name, record_id):
    if session.get('role') != 'Tse': return redirect(url_for('user_login'))
    if table_name not in ['users', 'admins']: return "Invalid table", 400
    execute_query(f"UPDATE {table_name} SET username=?, password=? WHERE id=?", (request.form['username'], request.form['password'], record_id))
    return redirect(url_for('manage_users') if table_name == 'users' else url_for('manage_admins'))

# =========================================================================
# === MAIN APP ROUTES                                                   ===
# =========================================================================
@app.route('/')
def home(): return redirect(url_for('dashboard'))

def load_role_submodules(role):
    return load_data().get("roles", {}).get(role, [])

@app.route('/dashboard')
def dashboard():
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
    return render_template('iot/dashboard.html', dashboard_items=dashboard_items, allowed_submodules=load_role_submodules(session.get('role')))

@app.context_processor
def inject_context():
    data = load_data()
    categorized_submodules = defaultdict(list)
    for submodule in data.get("submodules", []): categorized_submodules[submodule.get("category", "Uncategorized")].append(submodule)
    return {
        'submodules': data.get("submodules", []),
        'categorized_submodules': categorized_submodules,
        'client_name': data.get("client_name", "Default Client Name"),
        'dashboard_name': data.get("Dashboard"),
        'show_outside_conditions': data.get("show_outside_conditions", True) 
    }

@app.route('/<submodule>')
def render_submodule(submodule):
    if submodule in ['login', 'report', 'dashboard', 'user_management', 'admin', 'users', 'admins', 'send_report_page']: return "Not found", 404
    config_data = load_data()
    target_submodule = next((s for s in config_data.get("submodules", []) if s['name'] == submodule), None)
    if not target_submodule: return "Page not found", 404

    category = target_submodule.get('category', '').lower()
    cat_settings = config_data.get('H-plant', []) if category.startswith('h-plant') else config_data.get('WCS', [])
    final_visible_settings = [s for s in load_role_submodules(session.get('role')) if s in cat_settings]
    
    template_name = target_submodule['template']
    if os.path.exists(os.path.join(BASE_DIR, "templates", "iot", "departments", template_name)):
        vals = {}
        with RUNTIME_STORE["lock"]: vals = RUNTIME_STORE["values"].copy()
        return render_template(f"iot/departments/{template_name}", msg={"payload": vals}, allowed_submodules=final_visible_settings, dept_name=submodule)
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
def index(): return render_template('iot/index.html', msg={'payload': 0})

@socketio.on('connect')
def handle_connect():
    with RUNTIME_STORE["lock"]:
        if RUNTIME_STORE["values"]: emit('update', RUNTIME_STORE["values"])

# =========================================================================
# === USER MANAGEMENT ROUTES                                            ===
# =========================================================================
@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    if request.method == 'POST':
        table = request.form['role']
        if table in ['Operator', 'Manager', 'Tse']:
            def _add_user_task(u_name, u_pass):
                conn = None
                try:
                    conn = db_engine.raw_connection()
                    cursor = conn.cursor()
                    hashed = bcrypt.hashpw(u_pass.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                    cursor.execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (u_name, hashed))
                    conn.commit()
                    return True
                except Exception as e: 
                    error_msg = str(e)
                    # Intercept the SQL Server duplicate key error (Code 2627)
                    if '2627' in error_msg or 'UNIQUE KEY' in error_msg:
                        return "Username already exists. Please choose a different username."
                    return f"Database error: {error_msg}"
                finally:
                    if conn: conn.close()
                    
            result = run_db_task(_add_user_task, request.form['username'], request.form['password'])
            if result is True: 
                flash(f"User added successfully to {table}.", 'success')
            else: 
                flash(result, 'danger')
                
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
                for row in cursor.fetchall(): users.append({"username": row[1], "password": "Encrypted", "role": table})
        except: pass
        finally:
            if conn: conn.close()
        return users
    return render_template('iot/user_management.html', users=run_db_task(_get_users_task))

@app.route('/edit_user', methods=['POST'])
def edit_user():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    
    # 1. EXTRACT DATA HERE (Inside the active web request)
    form_username = request.form.get('username')
    form_password = request.form.get('password')
    form_role = request.form.get('role', '').capitalize()
    
    # 2. Pass those extracted variables into the background task
    def _edit_user_task(u_name, u_pass, u_role):
        if u_role not in ["Operator", "Manager", "Tse"]:
            return "Invalid role selected."
            
        if not u_pass:
            return "Password cannot be empty."

        conn = None
        try:
            conn = db_engine.raw_connection()
            cursor = conn.cursor()
            
            # Safely remove the old user from all possible tables
            for table in ["Operator", "Manager", "Tse"]: 
                cursor.execute(f"DELETE FROM {table} WHERE username = ?", (u_name,))
            
            # Hash the new password and insert the updated user
            hashed = bcrypt.hashpw(u_pass.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
            cursor.execute(f"INSERT INTO {u_role} (username, password) VALUES (?, ?)", (u_name, hashed))
            
            conn.commit()
            return True
            
        except Exception as e: 
            error_msg = str(e)
            if '2627' in error_msg or 'UNIQUE KEY' in error_msg:
                return "Username already exists."
            return f"Database Error: {error_msg}"
        finally:
            if conn: conn.close()
            
    # 3. Run the task and hand it the variables
    result = run_db_task(_edit_user_task, form_username, form_password, form_role)
    
    if result is True:
        flash("User updated successfully.", "success")
    else:
        flash(result, "danger")
        
    return redirect(url_for('user_management'))

@app.route('/delete_user', methods=['POST'])
def delete_user():
    # 1. Security Check
    if 'userloggedin' not in session: 
        return redirect(url_for('user_login'))

    username = request.form.get('username')
    
    # 2. Database Operation
    conn = db_engine.raw_connection()
    try:
        cursor = conn.cursor()
        for table in ["Operator", "Manager", "Tse"]:
            cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        conn.commit()
        flash(f"User {username} deleted.", "success")
    except Exception:
        flash("Error deleting user.", "danger")
    finally:
        conn.close()

    return redirect(url_for('user_management'))
    
@app.route('/logout')
def logout():
    session.clear()
    flash("Logged out.", "info")
    return redirect(url_for('user_login'))

@app.route('/trends')
def trends(): return render_template('iot/trends.html', msg={'payload': 0})

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
                socketio.sleep(1) 
                os.execv(sys.executable, ['python'] + sys.argv)
            socketio.start_background_task(restart_app)
            return jsonify(success=True, message="OPC UA URL updated. Application is restarting...")

        elif 'toggle_outside_conditions' in request.form:
            vis = request.form['toggle_outside_conditions'] == 'true'
            data['show_outside_conditions'] = vis
            import_dashboard_to_grafana("OutsideConditions", "Outside Conditions") if vis else delete_dashboard_from_grafana("Outside Conditions")
            msg = "Outside conditions visibility updated"
        
        elif 'update_sender_config' in request.form:
            data['SMTP_SERVER'] = request.form.get('smtp_server')
            data['SMTP_PORT'] = int(request.form.get('smtp_port', 587))
            data['SENDER_EMAIL'] = request.form.get('sender_email')
            data['SENDER_PASSWORD'] = request.form.get('sender_password')
            data['TELEGRAM_BOT_TOKEN'] = request.form.get('telegram_bot_token')
            save_yaml(data)
            return jsonify(success=True, message="Sender credentials updated successfully!")
        # ------------------------
            
        elif 'submodule_name' in request.form:
            new_sub = {'category': request.form['submodule_category'], 'name': request.form['submodule_name'], 'template': request.form['submodule_file'], 'node_prefix': request.form['node_prefix']}
            if 'submodules' not in data: data['submodules'] = []
            data['submodules'].append(new_sub)
            msg = "Submodule added"
            if not import_dashboard_to_grafana(new_sub['node_prefix'], new_sub['name']): msg += " (Warning: Grafana import failed.)"
            
        save_yaml(data)
        sync_nodeids_with_submodules()
        return jsonify(success=True, message=msg)
    
    return render_template('iot/input.html', client_name=data.get('client_name', ''), opc_ua_url=data.get('OPC_UA_URL', ''), show_outside_conditions=data.get('show_outside_conditions', True), submodules=data.get('submodules', []), roles=data.get('roles', {}), predefined_departments=load_predefined_departments())

@app.route('/remove_submodule', methods=['POST'])
def remove_submodule():
    data = load_data()
    sub_name = request.form['submodule_name']
    if 'submodules' in data: data['submodules'] = [s for s in data['submodules'] if s.get('name') != sub_name]
    save_yaml(data)
    sync_nodeids_with_submodules()
    delete_dashboard_from_grafana(sub_name)
    return jsonify({'success': True})

@app.route('/edit_submodule', methods=['POST'])
def edit_submodule():
    data = load_data()
    old_name = request.form['submodule_name']
    for sub in data.get('submodules', []):
        if sub.get('name') == old_name:
            sub.update({'name': request.form['new_submodule_name'], 'template': request.form['submodule_file'], 'category': request.form['submodule_category'], 'node_prefix': request.form['node_prefix']})
            break
    save_yaml(data)
    sync_nodeids_with_submodules()
    delete_dashboard_from_grafana(old_name)
    import_dashboard_to_grafana(request.form['node_prefix'], request.form['new_submodule_name'])
    return jsonify({'success': True})

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        data = load_data()
        data['submodules'] = request.json.get('new_order', [])
        save_yaml(data)
        return jsonify({'message': 'Order updated!'}), 200
    except Exception as e: return jsonify({'error': str(e)}), 500
    
@app.route('/add_setting', methods=['POST'])
def add_setting():
    data = load_data()
    role, setting = request.json.get('role'), request.json.get('setting')
    if role and setting and role in data.get('roles', {}):
        if setting not in data['roles'][role]:
            data['roles'][role].append(setting)
            save_yaml(data)
            return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/get_settings/<role>', methods=['GET'])
def get_settings(role):
    data = load_data()
    if role in data.get('roles', {}): return jsonify({'success': True, 'settings': data['roles'][role]})
    return jsonify({'success': False}), 400

@app.route('/delete_settings', methods=['POST'])
def delete_settings():
    data = load_data()
    role, settings = request.json.get('role'), request.json.get('settings')
    if role and settings and role in data.get('roles', {}):
        data['roles'][role] = [s for s in data['roles'][role] if s not in settings]
        save_yaml(data)
        return jsonify({'success': True})
    return jsonify({'success': False}), 400

@app.route('/clear_alarms', methods=['POST'])
def clear_alarms():
    if 'userloggedin' not in session: return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    try:
        with YAML_ACCESS_LOCK:
            save_json_data(ALARM_LIST_FILE_PATH, {'alarms': [], 'lastTripStates': {}})
            global LAST_TRIP_STATES
            LAST_TRIP_STATES = {}
        return jsonify({'success': True})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/restart_server', methods=['POST'])
def restart_server():
    if 'userloggedin' not in session: return jsonify({'success': False, 'error': 'Unauthorized'}), 401
    def _restart_task():
        socketio.sleep(1) 
        os.execv(sys.executable, ['python'] + sys.argv)
    socketio.start_background_task(_restart_task)
    return jsonify({'success': True, 'message': 'Application is restarting. The page will reload shortly.'})


# =========================================================================
# === NEW SHIFT REPORTING LOGIC (GLOBAL ENGINE)                         ===
# =========================================================================
def save_shift_data_helper(data):
    with open(os.path.join(BASE_DIR, 'shift_data.json'), 'w') as f: json.dump(data, f, indent=4)

@app.route('/send_report_page')
def send_report_page():
    if 'userloggedin' not in session: return redirect(url_for('user_login'))
    
    app_config = load_data()
    tg_group_id = app_config.get("TELEGRAM_GROUP_ID", "")
    
    return render_template('iot/send_report.html', shift_data=shift_report.load_shift_data(BASE_DIR), telegram_group_id=tg_group_id)
    
@app.route('/api/update_global_config', methods=['POST'])
def api_update_global_config():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    # 1. Save normal shift data
    data = shift_report.load_shift_data(BASE_DIR)
    data['global_config'] = {'send_time': request.json.get('send_time', '22:00'), 'fields': request.json.get('fields', [])}
    save_shift_data_helper(data)
    
    # 2. Save Telegram Group ID to input.yaml
    app_config = load_data()
    app_config['TELEGRAM_GROUP_ID'] = request.json.get('telegram_group_id', '')
    save_yaml(app_config)
    
    return jsonify({"success": True, "global_config": data['global_config']})

@app.route('/api/add_shift', methods=['POST'])
def api_add_shift():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = shift_report.load_shift_data(BASE_DIR)
    if 'shifts' not in data: data['shifts'] = {}
    data['shifts'][request.json.get('name')] = {"from": request.json.get('from_time'), "to": request.json.get('to_time'), "members": []}
    save_shift_data_helper(data)
    return jsonify({"success": True, "shifts": data['shifts']})

@app.route('/api/edit_shift', methods=['POST'])
def api_edit_shift():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = shift_report.load_shift_data(BASE_DIR)
    shifts = data.get('shifts', {})
    if request.json.get('old_name') in shifts:
        shift_obj = shifts.pop(request.json.get('old_name'))
        if not isinstance(shift_obj, dict): shift_obj = {"members": shift_obj}
        shift_obj['from'], shift_obj['to'] = request.json.get('from_time'), request.json.get('to_time')
        shifts[request.json.get('name')] = shift_obj
        save_shift_data_helper(data)
        return jsonify({"success": True, "shifts": shifts})
    return jsonify({"success": False})

@app.route('/api/delete_shift', methods=['POST'])
def api_delete_shift():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = shift_report.load_shift_data(BASE_DIR)
    if request.json.get('name') in data.get('shifts', {}):
        del data['shifts'][request.json.get('name')]
        save_shift_data_helper(data)
        return jsonify({"success": True, "shifts": data['shifts']})
    return jsonify({"success": False})

@app.route('/api/add_shift_member', methods=['POST'])
def api_add_shift_member():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = shift_report.load_shift_data(BASE_DIR)
    shift_obj = data.get('shifts', {}).get(request.json.get('shift'))
    new_mem = {"name": request.json.get('name'), "email": request.json.get('email'), "mobile": request.json.get('mobile'), "telegram": request.json.get('telegram')}
    
    if isinstance(shift_obj, dict):
        if 'members' not in shift_obj: shift_obj['members'] = []
        shift_obj['members'].append(new_mem)
    elif isinstance(shift_obj, list): 
        data['shifts'][request.json.get('shift')] = {"from": "00:00", "to": "23:59", "members": shift_obj + [new_mem]}
    save_shift_data_helper(data)
    return jsonify({"success": True, "shifts": data['shifts']})

@app.route('/api/edit_shift_member', methods=['POST'])
def api_edit_shift_member():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = shift_report.load_shift_data(BASE_DIR)
    shift_obj = data.get('shifts', {}).get(request.json.get('shift'))
    members = shift_obj.get('members', []) if isinstance(shift_obj, dict) else shift_obj
    idx = request.json.get('index')
    
    if 0 <= idx < len(members):
        members[idx] = {"name": request.json.get('name'), "email": request.json.get('email'), "mobile": request.json.get('mobile'), "telegram": request.json.get('telegram')}
        if isinstance(shift_obj, dict): shift_obj['members'] = members
        else: data['shifts'][request.json.get('shift')] = {"from": "00:00", "to": "23:59", "members": members}
        save_shift_data_helper(data)
        return jsonify({"success": True, "shifts": data['shifts']})
    return jsonify({"success": False})

@app.route('/api/delete_shift_member', methods=['POST'])
def api_delete_shift_member():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    data = shift_report.load_shift_data(BASE_DIR)
    shift_obj = data.get('shifts', {}).get(request.json.get('shift'))
    members = shift_obj.get('members', []) if isinstance(shift_obj, dict) else shift_obj
    idx = request.json.get('index')
    
    if 0 <= idx < len(members):
        members.pop(idx)
        if isinstance(shift_obj, dict): shift_obj['members'] = members
        else: data['shifts'][request.json.get('shift')] = {"from": "00:00", "to": "23:59", "members": members}
        save_shift_data_helper(data)
        return jsonify({"success": True, "shifts": data['shifts']})
    return jsonify({"success": False})

@app.route('/api/generate_daily_report', methods=['POST'])
def api_generate_daily_report():
    if 'userloggedin' not in session: return jsonify({'error': 'Unauthorized'}), 401
    
    today_date = datetime.now().strftime("%d-%m-%Y")
    yesterday_date = (datetime.now() - timedelta(days=1)).strftime("%d-%m-%Y")
    
    shift_data = shift_report.load_shift_data(BASE_DIR)
    shifts = shift_data.get('shifts', {})
    fields = shift_data.get('global_config', {}).get('fields', [])
    
    if not shifts: return jsonify({"success": False, "error": "No shifts configured."})
    if not fields: return jsonify({"success": False, "error": "No data fields selected in Global Settings."})

    multi_shift_data = []
    # Removed Telegram from the individual user loop
    emails_to_send, mobiles_to_send = set(), set()
    dept_map = get_departments_map()

    for s_name, s_info in shifts.items():
        if not isinstance(s_info, dict): continue
        s_from, s_to = s_info.get("from", "00:00"), s_info.get("to", "23:59")
        
        shift_from_date = yesterday_date
        shift_to_date = today_date if s_from > s_to else yesterday_date
        
        multi_shift_data.append({
            "shift_name": s_name, 
            "from_time": s_from,       # Kept 24-hour format
            "to_time": s_to,           # Kept 24-hour format
            "from_date": shift_from_date,  
            "to_date": shift_to_date,      
            "report_data": shift_report.fetch_aggregated_department_data(db_engine, run_db_task, dept_map, s_from, s_to, fields),
            "alarms": shift_report.get_shift_alarms(BASE_DIR, s_from, s_to)
        })
        
        for m in s_info.get("members", []):
            if m.get('email'): emails_to_send.add(m.get('email'))
            if m.get('mobile'): mobiles_to_send.add(m.get('mobile'))

    pdf_bytes = shift_report.generate_daily_report_pdf(load_data().get('client_name', 'Customer'), yesterday_date, multi_shift_data, fields)
    if not pdf_bytes: return jsonify({"success": False, "error": "Failed to generate PDF."})

    status = {'Email': {'success': 0, 'fail': 0}, 'WhatsApp': {'success': 0, 'fail': 0}, 'Telegram': {'success': 0, 'fail': 0}}
    
    for email in emails_to_send:
        if shift_report.send_email_report(email, pdf_bytes, yesterday_date, "Daily Report")[0]: status['Email']['success'] += 1
        else: status['Email']['fail'] += 1
    for mobile in mobiles_to_send:
        if shift_report.send_whatsapp_report(mobile, pdf_bytes, yesterday_date, "Daily Report")[0]: status['WhatsApp']['success'] += 1
        else: status['WhatsApp']['fail'] += 1
        
    # --- SEND TO TELEGRAM GROUP ONCE ---
    if shift_report.send_telegram_report(pdf_bytes, yesterday_date, "Daily Report")[0]:
        status['Telegram']['success'] += 1
    else:
        status['Telegram']['fail'] += 1

    results = [f"{method}: ({counts['success']}/{counts['success'] + counts['fail']} sent)" if counts['success'] > 0 and counts['fail'] > 0 else f"{method}: {'Success' if counts['fail'] == 0 else 'Failed'}" for method, counts in status.items() if (counts['success'] + counts['fail']) > 0]
    return jsonify({"success": True, "message": f"Complete: {' | '.join(results)}"})

# --- INTERNET CHECK & QUEUE ---
PENDING_REPORTS = []

def check_internet_connection():
    """Rapidly checks if the PC has active internet by pinging Google's Public DNS via TCP."""
    try:
        socket.create_connection(("8.8.8.8", 53), timeout=3)
        return True
    except OSError:
        return False

# --- AUTO SHIFT REPORT ENGINE ---
TRACKED_AUTO_REPORTS = {}

# --- AUTO SHIFT REPORT ENGINE ---
def auto_shift_report_engine():
    global PENDING_REPORTS
    print("Automated Report Engine initialized and waiting for schedule...")

    while True:
        try:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            today_date = now.strftime("%d-%m-%Y")
            yesterday_date = (now - timedelta(days=1)).strftime("%d-%m-%Y")
            
            # ==========================================
            # 1. PROCESS QUEUED REPORTS (IF INTERNET IS BACK)
            # ==========================================
            if PENDING_REPORTS and check_internet_connection():
                print(" Internet connection restored! Processing pending reports...")
                
                queue_to_process = PENDING_REPORTS[:]
                PENDING_REPORTS.clear()
                
                for pending in queue_to_process:
                    print(f"Attempting to send queued report for {pending['date']}...")
                    for email in pending['emails']: shift_report.send_email_report(email, pending['pdf'], pending['date'], "Daily Report")
                    for mobile in pending['mobiles']: shift_report.send_whatsapp_report(mobile, pending['pdf'], pending['date'], "Daily Report")
                    shift_report.send_telegram_report(pending['pdf'], pending['date'], "Daily Report")
                    print(f" Successfully sent queued report for {pending['date']}.")

            # ==========================================
            # 2. NORMAL SCHEDULED & CATCH-UP REPORT GENERATION
            # ==========================================
            shift_data = shift_report.load_shift_data(BASE_DIR)
            fields = shift_data.get("global_config", {}).get("fields", [])
            configured_time = shift_data.get("global_config", {}).get("send_time")
            
            if configured_time and fields:
                # 1. Check if the current time is PAST or EQUAL TO the scheduled time
                if current_time >= configured_time:
                    
                    # 2. Load input.yaml to see if we already sent it today
                    app_config = load_data()
                    track_key = f"DAILY_{today_date}_{configured_time}"
                    last_sent_key = app_config.get("last_report_sent", "")
                    
                    # 3. If the key doesn't match, it means we missed it! Send it now.
                    if last_sent_key != track_key:
                        print(f" Catch-up / Scheduled time reached. Generating daily report for {yesterday_date}...")
                        
                        # Instantly write to input.yaml so it locks out duplicates
                        app_config["last_report_sent"] = track_key
                        save_yaml(app_config)
                        
                        with app.app_context():
                            shifts = shift_data.get("shifts", {})
                            if not shifts: continue
                            
                            multi_shift_data = []
                            emails_to_send, mobiles_to_send = set(), set()
                            dept_map = get_departments_map()
                            
                            for s_name, s_info in shifts.items():
                                if not isinstance(s_info, dict): continue
                                s_from, s_to = s_info.get("from", "00:00"), s_info.get("to", "23:59")
                                
                                shift_from_date = yesterday_date
                                shift_to_date = today_date if s_from > s_to else yesterday_date
                                
                                multi_shift_data.append({
                                    "shift_name": s_name, 
                                    "from_time": s_from, 
                                    "to_time": s_to, 
                                    "from_date": shift_from_date,  
                                    "to_date": shift_to_date,      
                                    "report_data": shift_report.fetch_aggregated_department_data(db_engine, run_db_task, dept_map, s_from, s_to, fields),
                                    "alarms": shift_report.get_shift_alarms(BASE_DIR, s_from, s_to)
                                })
                                for m in s_info.get("members", []):
                                    if m.get('email'): emails_to_send.add(m.get('email'))
                                    if m.get('mobile'): mobiles_to_send.add(m.get('mobile'))
                                    
                            print("Compiling PDF...")
                            pdf_bytes = shift_report.generate_daily_report_pdf(app_config.get('client_name', 'Customer'), yesterday_date, multi_shift_data, fields)
                            
                            if pdf_bytes:
                                if check_internet_connection():
                                    print("Internet connection is OK. Dispatching reports...")
                                    for email in emails_to_send: shift_report.send_email_report(email, pdf_bytes, yesterday_date, "Daily Report")
                                    for mobile in mobiles_to_send: shift_report.send_whatsapp_report(mobile, pdf_bytes, yesterday_date, "Daily Report")
                                    shift_report.send_telegram_report(pdf_bytes, yesterday_date, "Daily Report")
                                    print(f" Report for {yesterday_date} dispatched successfully.")
                                else:
                                    print(f" NO INTERNET CONNECTION DETECTED. Queuing report for {yesterday_date} to be sent later.")
                                    PENDING_REPORTS.append({
                                        'date': yesterday_date,
                                        'pdf': pdf_bytes,
                                        'emails': emails_to_send,
                                        'mobiles': mobiles_to_send
                                    })
                            else:
                                print(f"Failed to generate PDF document for {yesterday_date}.")

        except Exception as e: 
            print(f"Engine Loop Crash: {str(e)}")
            
        socketio.sleep(30)

# --- LOG CLEANUP ENGINE ---
def auto_log_cleanup_engine():
    while True:
        try:
            exe_dir = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
            log_dir = os.path.join(exe_dir, "logs")
            if os.path.exists(log_dir):
                logs = sorted(glob.glob(os.path.join(log_dir, "service-*.log")), key=os.path.getmtime)
                if len(logs) > 3:
                    for old_log in logs[:-3]:
                        try: os.remove(old_log)
                        except Exception: pass
        except Exception: pass
        socketio.sleep(43200)

def run_installation_tasks(sql_server, opc_url):
    print("--- STARTING POST-INSTALLATION TASKS ---")
    try:
        data = load_data()
        data['DB_SERVER'] = sql_server
        data['OPC_UA_URL'] = opc_url
        save_yaml(data)
    except Exception: pass
    try: import db  
    except Exception: pass

if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--install-config':
        if len(sys.argv) >= 4: run_installation_tasks(sys.argv[2], sys.argv[3])
        sys.exit(0)

    sync_nodeids_with_submodules()
    initialize_alarm_states()
    socketio.start_background_task(read_values_periodically)
    socketio.start_background_task(alarm_processing_engine)
    socketio.start_background_task(emit_alarms)
    socketio.start_background_task(auto_shift_report_engine)
    socketio.start_background_task(auto_log_cleanup_engine) 
    
    socketio.run(app, host="0.0.0.0", port=7005, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)