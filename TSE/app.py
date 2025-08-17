import eventlet
eventlet.monkey_patch()
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, flash, send_file
from opcua import Client, ua
import os
import json
import yaml
from flask_socketio import SocketIO
from opcua import Client
import threading
import time
import plotly
import pyodbc
from werkzeug.utils import redirect
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, ValidationError
import bcrypt
from datetime import datetime, timedelta
from cryptography.fernet import Fernet
from threading import Thread
from flask_socketio import SocketIO, emit
import uuid
import logging


# logging.basicConfig(
#     level=logging.INFO,  # Change to DEBUG for more detail
#     format="%(asctime)s - %(levelname)s - %(message)s",
#     handlers=[
#         #logging.FileHandler("log.txt", encoding="utf-8"),  # Save logs to log.txt
#         logging.StreamHandler(os.devnull)  # Print logs to console
#     ]
# )
# logger = logging.getLogger(__name__)

# Example log on startup
#logger.info("Flask SCADA application starting up under pythonw mode.")

app = Flask(__name__)
app.secret_key = 'xyzsdfg'
app.permanent_session_lifetime = timedelta(minutes=20)
socketio = SocketIO(app)

# Automatic logout after 20 minutes of inactivity
@app.before_request
def before_request_handler():
    session.permanent = True
    # Only check for inactivity if a user is logged in
    if 'userloggedin' in session:
        # Check if last_activity is set
        if 'last_activity' in session:
            try:
                last_activity = datetime.fromisoformat(session['last_activity'])
                time_since_last_activity = datetime.now() - last_activity
                
                # If inactive for more than 20 minutes (1200 seconds)
                if time_since_last_activity.total_seconds() >= 1200:
                    session.pop('userloggedin', None)
                    session.pop('username', None)
                    session.pop('role', None)
                    session.pop('allowed_submodules', None)
                    session.pop('last_activity', None)
                    flash('You were automatically logged out due to inactivity.', 'info')
                    return redirect(url_for('user_login'))
            except (ValueError, TypeError):
                # Handle cases where session data might be corrupted
                session['last_activity'] = datetime.now().isoformat()
        
        # Update last activity time for the current request
        session['last_activity'] = datetime.now().isoformat()


SECRET_KEY = Fernet.generate_key()  # Save this securely, use the same key across app runs
cipher = Fernet(SECRET_KEY)

with open('input.yaml', 'r') as file:
    config = yaml.safe_load(file)

# Extract values from config
DB_SERVER = config.get('DB_SERVER')
DB_DATABASE = config.get('DB_DATABASE')
DB_USER = config.get('DB_USER')
DB_PASSWORD = config.get('DB_PASSWORD')
OPC_UA_URL = config.get('OPC_UA_URL')
# Connection string
connection_string = f'DRIVER={{ODBC Driver 17 for SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USER};PWD={DB_PASSWORD}'


# Function to create a connection
def create_connection():
    while True:
        try:
            conn = pyodbc.connect(connection_string)
            return conn
        except pyodbc.Error as e:
            print(f"Error connecting to database: {e}")
        
        except Exception as e:
            print(f"Unexpected error during connection: {e}")
            return None  # Handle other exceptions as needed

# Connect to the database
def connect_to_db():
    conn_str = 'DRIVER={SQL Server};SERVER=' + DB_SERVER + ';DATABASE=' + DB_DATABASE + ';UID=' + DB_USER + ';PWD=' + DB_PASSWORD
    return pyodbc.connect(conn_str)

# Define allowed submodules per role
ROLE_SUBMODULES = {
    "Operator": ["Set Point"],
    "Manager": ["Set Point", "Digital Input", "Digital Output", "Analog Input", "Analog Output", 
                 "Preset Values", "Timer", "Controllers", "UPSS"],
    "Tse": ["Set Point", "Digital Input", "Digital Output", "Analog Input", "Analog Output", 
             "Preset Values", "Timer", "Controllers", "UPSS", "Pump Min Set"]
}
def validate_user(username, password):
    conn = create_connection()
    cursor = conn.cursor()
    
    for table in ["Operator", "Manager", "Tse"]:
        cursor.execute(f"SELECT * FROM {table} WHERE username = ?", (username,))
        user = cursor.fetchone()
        print(user)
        if user and user[1] == username:  # Ensure username matches case-sensitively
            try:
                if bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
                    session['role'] = table  # Store the role in the session
                    session['username'] = username
                    return table  # Return the role as the table name
            except (ValueError, AttributeError): # Handle potential errors if password field is not a valid hash
                print(f"Error validating password for user '{username}' in table '{table}'. It might not be hashed correctly.")
                continue
        
    return None


# OPC UA Client Initialization
client = Client(OPC_UA_URL)

# Global variable to hold the latest values
latest_values = {}

# Load Node IDs from the YAML file
def load_node_ids():
    yaml_path = os.path.join(os.path.dirname(__file__), "nodeid.yaml")
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
        return data.get("node_ids", {})


def read_values_periodically():
    global latest_values
    while True:
        try:
            client.connect()  # Connect to the OPC UA server

            node_ids = load_node_ids()

            for name, node_id in node_ids.items():
                try:
                    node = client.get_node(node_id)
                    raw_value = node.get_value()  # Read the value
                    if isinstance(raw_value, (int, float)):
                        latest_values[name] = round(raw_value, 1)
                    else:
                        latest_values[name] = raw_value
                except Exception as e:
                    print(f"Error reading node {name}: {e}")

            socketio.emit('update', latest_values)

            # Use the lock to prevent file access collision with the alarm processor
            with YAML_ACCESS_LOCK:
                with open('nodeValues.yaml', 'w') as yaml_file:
                    yaml.dump(latest_values, yaml_file)

            client.disconnect()
            time.sleep(0.5)
        except Exception as e:
            print(f"Error reading values: {str(e)}")
            time.sleep(0.5)


# =========================================================================
# ===                  REVISED AND CORRECTED ALARMS SECTION             ===
# =========================================================================

# Define the path to the persistent alarm list file
ALARM_LIST_FILE_PATH = os.path.join(os.path.dirname(__file__), "alarmList.yaml")

# A single, robust lock for both YAML files
# This ensures that reading node values and writing alarm lists are synchronized
YAML_ACCESS_LOCK = threading.Lock()

def get_trip_status_from_nodeid(nodeid, alarm_values):
    # This function now takes alarm_values as an argument to avoid re-reading the file
    if alarm_values is None:
        return None
    
    node_ids = load_node_ids()
    for name, nid in node_ids.items():
        if nid == nodeid:
            value = alarm_values.get(name, False)
            return bool(value) if value is not None else None
    return None

def process_and_update_alarms():
    """
    This is the new, consolidated alarm processing engine. It handles everything
    from reading OPC-UA values to updating the persistent alarm list in a single,
    locked operation to prevent race conditions.
    """
    with YAML_ACCESS_LOCK: # Lock before accessing any of the shared YAML files
        try:
            # 1. Read the current live values from nodeValues.yaml
            with open('nodeValues.yaml', 'r') as f:
                current_opc_values = yaml.safe_load(f)

            # 2. Read the alarm rules from alarms.yaml
            with open("alarms.yaml", "r") as f:
                data = yaml.safe_load(f)
                alarm_rules = data.get("alarms", [])

            # 3. Determine the current trip status for all alarms based on rules
            for alarm in alarm_rules:
                alarm['trip'] = False
                for node_name in alarm.get('nodeids', []):
                    nodeid = load_node_ids().get(node_name)
                    if nodeid:
                        trip_status = get_trip_status_from_nodeid(nodeid, current_opc_values)
                        if trip_status is True:
                            alarm['trip'] = True
                            break # Move to the next alarm rule
                
                if alarm['trip']:
                    alarm['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            
            # 4. Now, call the function to save/update the persistent alarmList.yaml
            save_alarms_to_list_yaml(alarm_rules)

        except FileNotFoundError:
             # It's okay if nodeValues.yaml doesn't exist on the very first run
             print("Waiting for nodeValues.yaml to be created...")
             return
        except Exception as e:
            print(f"Error during alarm processing: {e}")


def save_alarms_to_list_yaml(current_alarms_from_source):
    # This function assumes it's being called from within a locked context
    try:
        existing_data = {'alarms': [], 'lastTripStates': {}}
        if os.path.exists(ALARM_LIST_FILE_PATH):
            with open(ALARM_LIST_FILE_PATH, 'r') as f:
                content = f.read()
                if content:
                    try:
                        existing_data = yaml.safe_load(content) or {'alarms': [], 'lastTripStates': {}}
                    except yaml.YAMLError as e:
                        print(f"CORRUPTION DETECTED in alarmList.yaml. Resetting file. Error: {e}")
                        # If file is corrupt, reset it to a safe state
                        existing_data = {'alarms': [], 'lastTripStates': {}}


        persistent_alarms = existing_data.get('alarms', [])
        last_trip_states = existing_data.get('lastTripStates', {})
        current_trip_map = {str(a.get('code')): a.get('trip', False) for a in current_alarms_from_source}

        # Check for RESOLUTIONS
        for alarm_in_list in persistent_alarms:
            code = str(alarm_in_list.get('code'))
            is_unresolved = alarm_in_list.get('status') != 'Resolved'
            if is_unresolved and code in current_trip_map and not current_trip_map[code]:
                alarm_in_list['status'] = 'Resolved'
                alarm_in_list['resolved_time'] = datetime.now().strftime("%m/%d/%Y, %I:%M:%S %p")

        # Check for NEWLY TRIGGERED alarms
        for source_alarm in current_alarms_from_source:
            code = str(source_alarm.get('code'))
            if code is None: continue

            current_trip = source_alarm.get('trip', False)
            previous_trip = last_trip_states.get(code, False)

            if current_trip and not previous_trip:
                new_alarm_entry = source_alarm.copy()
                new_alarm_entry['id'] = str(uuid.uuid4())
                new_alarm_entry['status'] = 'Not acknowledged'
                new_alarm_entry['acknowledged'] = False
                dt = datetime.strptime(new_alarm_entry['time'], "%Y-%m-%d %H:%M:%S")
                new_alarm_entry['time'] = dt.strftime("%m/%d/%Y, %I:%M:%S %p")
                persistent_alarms.append(new_alarm_entry)
            
            last_trip_states[code] = current_trip

        updated_data = {
            'alarms': persistent_alarms,
            'lastTripStates': last_trip_states
        }
        with open(ALARM_LIST_FILE_PATH, 'w') as out_file:
            yaml.dump(updated_data, out_file, default_flow_style=False)
    except Exception as e:
        import traceback
        print(f"Error in save_alarms_to_list_yaml: {e}")
        traceback.print_exc()

def alarm_processing_engine():
    """The background thread function that runs the alarm logic."""
    while True:
        process_and_update_alarms()
        time.sleep(1) # Process alarms every 1 second

@app.route("/notifyAlarms")
def get_alarms_notification():
    with YAML_ACCESS_LOCK: # Use the lock to ensure a clean read
        alarms = load_alarms_from_yaml_list()
    last_10_alarms = sorted(alarms, key=lambda x: x.get('time', ''), reverse=True)[:10]
    return jsonify(last_10_alarms)

def load_alarms_from_yaml_list():
    # This function assumes it's being called from within a locked context
    try:
        with open(ALARM_LIST_FILE_PATH, 'r') as f:
            data = yaml.safe_load(f) or {}
            return data.get('alarms', [])
    except FileNotFoundError:
         return []
    except Exception as e:
        print(f"Error reading alarmList.yaml: {e}")
        return []

@app.route("/alarmslist")
def alarmslist():
    return render_template("iot/alarmslist.html")

def emit_alarms():
    """Emits the full alarm list to the dedicated alarm page."""
    while True:
        with YAML_ACCESS_LOCK: # Use the lock to ensure a clean read
            alarms = load_alarms_from_yaml_list()
        socketio.emit('alarm_update', alarms)
        time.sleep(1)

@app.route("/acknowledge", methods=["POST"])
def acknowledge_alarm():
    with YAML_ACCESS_LOCK: # Use the lock
        try:
            alarm_id = request.json.get("id")
            if not alarm_id:
                return jsonify({"success": False, "error": "Missing alarm ID"}), 400

            with open(ALARM_LIST_FILE_PATH, 'r') as f:
                data = yaml.safe_load(f) or {}

            alarms = data.get("alarms", [])
            updated = False
            for alarm in alarms:
                if alarm.get("id") == alarm_id:
                    alarm["acknowledged"] = True
                    alarm["status"] = "Acknowledged"
                    updated = True
                    break
            
            if updated:
                data["alarms"] = alarms
                with open(ALARM_LIST_FILE_PATH, 'w') as f:
                    yaml.dump(data, f, default_flow_style=False)
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "error": "Alarm ID not found"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500
            
# =========================================================================
# ===                  END OF REVISED ALARMS SECTION                    ===
# =========================================================================


@app.route('/write', methods=['POST'])
def write():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    print(f"Received data: {data}")  # Debugging step

    opcua_client = Client(OPC_UA_URL)

    try:
        opcua_client.connect()

        for nodeid, value in data.items():
            node = opcua_client.get_node(nodeid)
            node_data_type = node.get_data_type_as_variant_type()

            if node_data_type == ua.VariantType.Boolean:
                dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
            elif node_data_type == ua.VariantType.Int32:
                dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
            elif node_data_type == ua.VariantType.UInt16:
                dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.UInt16))
            elif node_data_type == ua.VariantType.Float:
                dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
            else:
                raise ValueError(f"Unsupported data type for node {nodeid}: {node_data_type}")

            node.set_value(dv)

        return jsonify({"success": True}), 200

    except Exception as e:
        print(f"Error writing settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        try:
            opcua_client.disconnect()
        except Exception as disconnect_error:
            print(f"Error during client disconnect: {disconnect_error}")


@app.route('/writes', methods=['POST'])
def writes():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    opcua_client = Client(OPC_UA_URL)

    try:
        opcua_client.connect()

        for nodeid, value in data.items():
            if nodeid not in load_node_ids():
                return jsonify({"success": False, "error": f"Invalid node ID: {nodeid}"}), 400
            
            node = opcua_client.get_node(load_node_ids()[nodeid])

            node_data_type = node.get_data_type_as_variant_type()

            if node_data_type == ua.VariantType.Boolean:
                dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
            elif node_data_type == ua.VariantType.Int32:
                dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
            elif node_data_type == ua.VariantType.UInt16:
                dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.UInt16))
            elif node_data_type == ua.VariantType.Float:
                dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
            else:
                raise ValueError(f"Unsupported data type for node {nodeid}: {node_data_type}")

            node.set_value(dv)

        return jsonify({"success": True}), 200

    except Exception as e:
        print(f"Error writing settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        try:
            opcua_client.disconnect()
        except Exception as disconnect_error:
            print(f"Error during client disconnect: {disconnect_error}")



setting_suffix_mapping = {
    "Set Point": "_sp.html",
    "Digital Input": "_di.html",
    "Digital Output": "_do.html",
    "Analog Input": "_ai.html",
    "Analog Output": "_ao.html",
    "Preset Values": "_pv.html",
    "Timer": "_ti.html",
    "Controllers": "_co.html",
    "UPSS": "_up.html",
    "Pump Min Set": "_pm.html",
}

# @app.route('/load_template/<parent_submodule>/<setting_option>')
# def load_template(parent_submodule, setting_option):
#     config_data = load_data() 
#     submodules_map = config_data.get("submodules", {})

#     main_template_file = submodules_map.get(parent_submodule)
#     if not main_template_file:
#         return f"Parent submodule '{parent_submodule}' not found in input.yaml", 404

#     prefix = main_template_file.replace('.html', '')
#     suffix = setting_suffix_mapping.get(setting_option)
#     if not suffix:
#         return f"Setting option '{setting_option}' not found", 404

#     template_path = f"Settings/{prefix}{suffix}"
#     msg = {
#         'payload': latest_values,
#         'node_ids': load_node_ids()
#     }
    
#     socketio.emit('update', latest_values)
#     return render_template(template_path, msg=msg)

@app.route('/load_template/<parent_submodule>/<setting_option>')
def load_template(parent_submodule, setting_option):
    config_data = load_data() 
    
    # NEW: Get the node prefix from the mapping in input.yaml
    node_prefixes_map = config_data.get("node_prefixes", {})
    prefix = node_prefixes_map.get(parent_submodule)

    if not prefix:
        return f"Node prefix for department '{parent_submodule}' not found in input.yaml", 404

    # Get the correct template suffix (e.g., "_sp.html")
    suffix = setting_suffix_mapping.get(setting_option)
    if not suffix:
        return f"Setting option '{setting_option}' not found", 404

    # ALWAYS render the generic template for this setting type
    # Construct a path like "Settings/generic_sp.html"
    template_name = f"generic{suffix}"
    template_path = f"Settings/{template_name}"
    
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()
    }
    
    socketio.emit('update', latest_values)
    
    # Pass the determined 'prefix' to the template
    return render_template(template_path, msg=msg, prefix=prefix)

@app.route('/')
def home():
    role = session.get('role')
    ROLE_SUBMODULES = load_role_submodules(role)
    departmentss = load_data()
    allowed_submodules = ROLE_SUBMODULES
    return render_template('iot/dashboard.html', departmentss=departmentss, allowed_submodules=allowed_submodules)


@app.route('/dashboard')
def dashboard():
    role = session.get('role')
    ROLE_SUBMODULES = load_role_submodules(role)
    allowed_submodules = ROLE_SUBMODULES
    with open('nodeid.yaml', 'r') as file:
        node_ids = yaml.safe_load(file)
    return render_template('iot/dashboard.html', node_ids=node_ids, allowed_submodules=allowed_submodules)

def load_data():
    yaml_path = os.path.join(os.path.dirname(__file__), "input.yaml")
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)
    

def load_role_submodules(role):
    yaml_path = os.path.join(os.path.dirname(__file__), "input.yaml")
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
    roles = data.get("roles", {})
    return roles.get(role, [])


@app.context_processor
def inject_context():
    data = load_data()
    allowed_submodules = session.get('allowed_submodules', [])
    dashboard_name = data.get("Dashboard")
    
    return {
        'submodules': data["submodules"],
        'client_name': data.get("client_name", "Default Client Name"),
        'allowed_submodules': allowed_submodules,
        'dashboard_name': dashboard_name
    }

@app.route('/<submodule>')
def render_submodule(submodule):
    submodules = load_data()["submodules"]
    role = session.get('role')
    ROLE_SUBMODULES = load_role_submodules(role)
    allowed_submodules = ROLE_SUBMODULES
    
    session['allowed_submodules'] = allowed_submodules

    template_name = submodules.get(submodule)
    if template_name:
        template_path = os.path.join("templates", "iot", template_name)
        if os.path.exists(template_path):
            msg = {"payload": latest_values}
            dept_name = submodule
            return render_template(f"iot/{template_name}", msg=msg, allowed_submodules=allowed_submodules, dept_name=dept_name)

    return "Page not found", 404



@app.route('/login', methods=['GET', 'POST'])
def user_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        user_role = validate_user(username, password)
        
        if user_role:
            session['userloggedin'] = True
            session['username'] = username
            session['role'] = user_role
            session['last_activity'] = datetime.now().isoformat()
            session['last_login'] = datetime.now().strftime("%d-%b-%Y %I:%M %p")
            session['allowed_submodules'] = ["Set Point"] if user_role == "Operator" else ROLE_SUBMODULES.get(user_role, [])
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials, please try again.", 'danger')
    return render_template('User management/userlogin.html')

@app.route('/index')
def index():
    msg = {'payload': 0}
    return render_template('iot/index.html', msg=msg)

@app.route('/sp')
def sp():
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()
    }
    return render_template('Settings/spinning2_sp.html', msg=msg)

@app.route('/ti')
def ti():
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()
    }
    return render_template('Settings/spinning2_ti.html', msg=msg)

@socketio.on('connect')
def handle_connect():
    emit('update', latest_values)

@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
        table = None
        if role == 'Operator':
            table = 'Operator'
        elif role == 'Manager':
            table = 'Manager'
        elif role == 'Tse':
            table = 'Tse'

        if table:
            try:
                conn = create_connection()
                cursor = conn.cursor()
                encrypted_password = cipher.encrypt(password.encode('utf-8'))
                cursor.execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (username, hashed_password)) # Storing hashed password
                conn.commit()
                flash(f"User added successfully to {table}.", 'success')
            except Exception as e:
                flash(f"Error adding user: {e}", 'danger')
            finally:
                conn.close()
    return render_template('iot/add_user.html')

@app.route('/user_management')
def user_management():
    conn = create_connection()
    cursor = conn.cursor()
    users = []
    for table in ["Operator", "Manager", "Tse"]:
        cursor.execute(f"SELECT * FROM {table}")
        rows = cursor.fetchall()
        for row in rows:
            users.append({
                "username": row[1],
                "password": "Encrypted",  # Displaying placeholder for security
                "role": table
            })
    conn.close()
    return render_template('iot/user_management.html', users=users)

@app.route('/edit_user', methods=['POST'])
def edit_user():
    username = request.form['username']
    new_password = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt())
    new_role = request.form['role']
    
    conn = create_connection()
    cursor = conn.cursor()

    for table in ["Operator", "Manager", "Tse"]:
        cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        conn.commit()

    new_table = new_role
    cursor.execute(f"INSERT INTO {new_table} (username, password) VALUES (?, ?)", (username, new_password))
    conn.commit()
    conn.close()

    flash("User updated successfully.", "success")
    return redirect(url_for('user_management'))


@app.route('/delete_user', methods=['POST'])
def delete_user():
    username = request.form['username']
    conn = create_connection()
    cursor = conn.cursor()

    for table in ["Operator", "Manager", "Tse"]:
        cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        conn.commit()
    conn.close()

    flash("User deleted successfully.", "success")
    return redirect(url_for('user_management'))
    
@app.route('/logout')
def logout():
    session.clear()
    flash("You have been successfully logged out.", "info")
    return redirect(url_for('user_login'))

@app.route('/trends')
def trends():
    msg = {'payload': 0}
    return render_template('iot/trends.html', msg=msg)

def load_yaml():
    with open("input.yaml", 'r') as file:
        return yaml.safe_load(file)

def save_yaml(data):
    with open("input.yaml", 'w') as file:
        yaml.dump(data, file)

@app.route('/input', methods=['GET', 'POST'])
def input_page():
    data = load_yaml()
    if request.method == 'POST':
        msg = ""
        if 'client_name' in request.form:
            data['client_name'] = request.form['client_name']
            msg = "Client name updated"
        elif 'opc_ua_url' in request.form:
            data['OPC_UA_URL'] = request.form['opc_ua_url']
            msg = "OPC UA URL updated"
        elif 'submodule_name' in request.form and 'submodule_file' in request.form:
            new_submodule = {
                request.form['submodule_name']: request.form['submodule_file']
            }
            data['submodules'].update(new_submodule)
            msg = "Submodule added"
        save_yaml(data)
        return jsonify(success=True, message=msg)

    return render_template(
        'iot/input.html',
        client_name=data['client_name'],
        opc_ua_url=data.get('OPC_UA_URL', ''),
        submodules=data['submodules'],
        roles=data['roles']
    )

@app.route('/remove_submodule', methods=['POST'])
def remove_submodule():
    data = load_yaml()
    submodule_name = request.form['submodule_name']
    del data['submodules'][submodule_name]
    save_yaml(data)
    return jsonify({'success': True})

@app.route('/edit_submodule', methods=['POST'])
def edit_submodule():
    data = load_yaml()
    old_name = request.form['submodule_name']
    new_name = request.form['new_submodule_name']
    new_file = request.form['submodule_file']
    if old_name in data['submodules']:
        del data['submodules'][old_name]
    data['submodules'][new_name] = new_file
    save_yaml(data)
    return jsonify({'success': True})

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        new_order = request.json.get('new_order', [])
        if not new_order:
            return jsonify({'error': 'No new order provided'}), 400
        submodules = {item['name']: item['file'] for item in new_order}
        yaml_file_path = 'input.yaml'
        with open(yaml_file_path, 'r') as yaml_file:
            data = yaml.safe_load(yaml_file) or {}
        data['submodules'] = submodules
        with open(yaml_file_path, 'w') as yaml_file:
            yaml.dump(data, yaml_file, default_flow_style=False, sort_keys=False)
        return jsonify({'message': 'Order updated successfully!'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/add_setting', methods=['POST'])
def add_setting():
    data = load_yaml()
    role = request.json.get('role')
    setting = request.json.get('setting')
    if role and setting:
        if role in data['roles']:
            if setting not in data['roles'][role]:
                data['roles'][role].append(setting)
                save_yaml(data)
                return jsonify({'success': True})
            else:
                return jsonify({'success': False, 'message': 'Setting already exists'}), 400
        else:
            return jsonify({'success': False, 'message': 'Role not found'}), 400
    return jsonify({'success': False, 'message': 'Invalid data'}), 400

@app.route('/get_settings/<role>', methods=['GET'])
def get_settings(role):
    data = load_yaml()
    if role in data['roles']:
        return jsonify({'success': True, 'settings': data['roles'][role]})
    return jsonify({'success': False, 'message': 'Role not found'}), 400

@app.route('/delete_settings', methods=['POST'])
def delete_settings():
    data = load_yaml()
    role = request.json.get('role')
    settings = request.json.get('settings')
    if role and settings:
        if role in data['roles']:
            data['roles'][role] = [s for s in data['roles'][role] if s not in settings]
            save_yaml(data)
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'message': 'Role not found'}), 400
    return jsonify({'success': False, 'message': 'Invalid data'}), 400

if __name__ == '__main__':
    # Start the background thread to read OPC-UA values periodically
    read_thread = threading.Thread(target=read_values_periodically)
    read_thread.daemon = True
    read_thread.start()

    # Start the new consolidated alarm processing engine
    process_alarm_thread = threading.Thread(target=alarm_processing_engine)
    process_alarm_thread.daemon = True
    process_alarm_thread.start()

    # Start the background thread to emit the full alarm list to the /alarmslist page
    emit_alarm_thread = threading.Thread(target=emit_alarms)
    emit_alarm_thread.daemon = True
    emit_alarm_thread.start()
    
    socketio.run(app, host='127.0.0.1', port=7005, debug=True)