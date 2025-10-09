import eventlet
eventlet.monkey_patch()
from flask import Flask, jsonify, request, render_template, redirect, url_for, session, flash, send_file
from flask_cors import CORS
from opcua import Client, ua
from collections import defaultdict # Add this import at the top of your file
import os
import json
import yaml
from flask_socketio import SocketIO
from opcua import Client
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
from threading import Thread
from flask_socketio import SocketIO, emit
import uuid
import logging


#logging.basicConfig(
#     level=logging.INFO,  # Change to DEBUG for more detail
#     format="%(asctime)s - %(levelname)s - %(message)s",
#     handlers=[
#         #logging.FileHandler("log.txt", encoding="utf-8"),  # Save logs to log.txt
#         logging.StreamHandler(os.devnull)  # Print logs to console
#     ]
# )
# logger = logging.getLogger(__name__)

# #Example log on startup
# logger.info("Flask SCADA application starting up under pythonw mode.")

app = Flask(__name__)

def slugify(value):
    """
    Sanitizes a string to be used as a valid CSS selector/ID.
    - Converts to lowercase.
    - Replaces spaces and other invalid characters with a single underscore.
    - Removes leading/trailing underscores.
    """
    # Replace any character that is not a letter, number, or hyphen with an underscore
    value = re.sub(r'[^\w\-]+', '_', value).strip('_')
    return value.lower()

app.jinja_env.filters['slugify'] = slugify 
CORS(app)
app.secret_key = 'xyzsdfg'
app.permanent_session_lifetime = timedelta(minutes=20)
socketio = SocketIO(app, cors_allowed_origins="*")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

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

with open(os.path.join(BASE_DIR, 'input.yaml'), 'r') as file:
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
    yaml_path = os.path.join(BASE_DIR, "nodeid.yaml")
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
        return data.get("node_ids", {})

# =========================================================================
# ===            FINAL, LOGICALLY CORRECTED ALARMS SECTION            ===
# =========================================================================

# Define the path to the persistent alarm list file
ALARM_LIST_FILE_PATH = os.path.join(BASE_DIR, "alarmList.yaml")

# A single, robust lock for all YAML file access to prevent race conditions.
YAML_ACCESS_LOCK = threading.Lock()

# IN-MEMORY STATE: This dictionary holds the last known trip state (True/False)
# of each alarm code. It's the "single source of truth" during runtime.
LAST_TRIP_STATES = {}


def read_values_periodically():
    global latest_values
    while True:
        try:
            client.connect()
            node_ids = load_node_ids()
            current_batch = {}
            for name, node_id in node_ids.items():
                try:
                    node = client.get_node(node_id)
                    raw_value = node.get_value()
                    if isinstance(raw_value, (int, float)):
                        current_batch[name] = round(raw_value, 1)
                    else:
                        current_batch[name] = raw_value
                except Exception as e:
                    print(f"Error reading node {name}: {e}")
            
            latest_values = current_batch
            socketio.emit('update', latest_values)

            # Use the lock to prevent file access collision with the alarm processor
            with YAML_ACCESS_LOCK:
                with open('nodeValues.yaml', 'w') as yaml_file:
                    yaml.dump(latest_values, yaml_file)

            client.disconnect()
            time.sleep(0.5)
        except Exception as e:
            print(f"OPC-UA connection or read error: {str(e)}")
            time.sleep(2) # Wait longer on connection failure



def initialize_alarm_states():
    """
    Reads the persistent alarm file ONCE at startup to populate the
    in-memory LAST_TRIP_STATES dictionary.
    """
    global LAST_TRIP_STATES
    with YAML_ACCESS_LOCK:
        try:
            if os.path.exists(ALARM_LIST_FILE_PATH):
                with open(ALARM_LIST_FILE_PATH, 'r') as f:
                    data = yaml.safe_load(f) or {}
                    LAST_TRIP_STATES = data.get('lastTripStates', {})
                    print("Successfully initialized alarm states from file.")
        except Exception as e:
            print(f"Could not initialize alarm states from file, starting fresh. Error: {e}")
            LAST_TRIP_STATES = {}

def alarm_processing_engine():
    """
    The core alarm processing thread. It checks for state changes (False -> True)
    and updates the persistent alarm list accordingly.
    """
    global LAST_TRIP_STATES
    while True:
        try:
            current_opc_values = None
            # Step 1: Read the latest node values safely
            with YAML_ACCESS_LOCK:
                if not os.path.exists('nodeValues.yaml'):
                    time.sleep(1) # Wait if the file hasn't been created yet
                    continue
                with open('nodeValues.yaml', 'r') as f:
                    current_opc_values = yaml.safe_load(f)

            if not current_opc_values:
                time.sleep(1)
                continue

            # Step 2: Read alarm rules
            with open("alarms.yaml", "r") as f:
                alarm_rules = yaml.safe_load(f).get("alarms", [])
            
            something_changed = False
            
            # Use the lock for the entire read-modify-write operation on alarmList.yaml
            with YAML_ACCESS_LOCK:
                # Load the current full list of alarms
                full_alarm_data = {'alarms': [], 'lastTripStates': {}}
                if os.path.exists(ALARM_LIST_FILE_PATH):
                    with open(ALARM_LIST_FILE_PATH, 'r') as f:
                        full_alarm_data = yaml.safe_load(f) or full_alarm_data
                
                persistent_alarms = full_alarm_data.get('alarms', [])

                # Step 3: Iterate through rules and check for state changes
                for rule in alarm_rules:
                    code = str(rule['code'])
                    
                    # Determine current trip status from live values
                    current_trip = False
                    for node_name in rule.get('nodeids', []):
                        node_value = current_opc_values.get(node_name)
                        # Check if the node value indicates a trip condition
                        # Handle various representations of True/False
                        if node_value is not None:
                            if isinstance(node_value, bool):
                                if node_value:
                                    current_trip = True
                                    break
                            elif isinstance(node_value, (int, float)):
                                if node_value != 0:  # Non-zero values are considered True
                                    current_trip = True
                                    break
                            elif isinstance(node_value, str):
                                if node_value.lower() in ['true', '1', 'on', 'active']:
                                    current_trip = True
                                    break
                    
                    # Get previous trip status from our fast, in-memory dictionary
                    previous_trip = LAST_TRIP_STATES.get(code, False)

                    # **FIXED LOGIC HERE**
                    # Only create a new alarm on False -> True transition
                    # AND only if there isn't already an active (unresolved) alarm for this code
                    if current_trip and not previous_trip:
                        # Check if there's already an active alarm for this code
                        has_active_alarm = any(
                            str(alarm.get('code')) == code and alarm.get('status') not in ['Resolved', 'Acknowledged'] 
                            for alarm in persistent_alarms
                        )
                        
                        if not has_active_alarm:
                            print(f"NEW ALARM TRIGGERED: {rule['message']} (Code: {code})")
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
                        
                        # Always update the state tracking
                        LAST_TRIP_STATES[code] = True
                        
                    # If an alarm resolves (True -> False), mark it resolved
                    elif not current_trip and previous_trip:
                        print(f"ALARM RESOLVED: {rule['message']} (Code: {code})")
                        # Find the last active alarm with this code and mark it resolved
                        for alarm in reversed(persistent_alarms):
                            if str(alarm.get('code')) == code and alarm.get('status') not in ['Resolved']:
                                alarm['status'] = 'Resolved'
                                alarm['resolved_time'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                                something_changed = True
                                break
                        
                        # Update the state tracking
                        LAST_TRIP_STATES[code] = False

                # Step 4: If any state changed, write the entire updated list back to the file
                if something_changed:
                    temp_file_path = ALARM_LIST_FILE_PATH + ".tmp"
                    final_data = {
                        'alarms': persistent_alarms,
                        'lastTripStates': LAST_TRIP_STATES # Persist the latest states
                    }
                    with open(temp_file_path, 'w') as f:
                        yaml.dump(final_data, f, default_flow_style=False)
                    os.replace(temp_file_path, ALARM_LIST_FILE_PATH)
            
            time.sleep(1) # Wait a second before the next cycle

        except Exception as e:
            import traceback
            print(f"CRITICAL ERROR in alarm_processing_engine: {e}")
            traceback.print_exc()
            time.sleep(2) # Wait a bit longer after an error
            
                        
@app.route("/notifyAlarms")
def get_alarms_notification():
    with YAML_ACCESS_LOCK:
        alarms = load_alarms_from_yaml_list()
    sorted_alarms = sorted(alarms, key=lambda x: x.get('time', '1970-01-01 00:00:00'), reverse=True)
    return jsonify(sorted_alarms[:10])

def load_alarms_from_yaml_list():
    """Reads the alarm list. Must be called from within a YAML_ACCESS_LOCK context."""
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
    except Exception as e:
        print(f"Error reading alarmList.yaml: {e}")
        return []

@app.route("/alarmslist")
def alarmslist():
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))
    return render_template("iot/alarmslist.html")

def emit_alarms():
    """Emits the full alarm list to the dedicated alarm page."""
    while True:
        with YAML_ACCESS_LOCK:
            alarms = load_alarms_from_yaml_list()
        socketio.emit('alarm_update', alarms)
        time.sleep(1)

@app.route("/acknowledge", methods=["POST"])
def acknowledge_alarm():
    with YAML_ACCESS_LOCK:
        try:
            alarm_id = request.json.get("id")
            if not alarm_id:
                return jsonify({"success": False, "error": "Missing alarm ID"}), 400

            data = {}
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
            else:
                return jsonify({"success": False, "error": "Alarm ID not found or already resolved"}), 404
        except Exception as e:
            return jsonify({"success": False, "error": str(e)}), 500

# =========================================================================
# ===                  END OF CORRECTED ALARMS SECTION                  ===
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
    "DeHumidity": "_dh.html",
}

@app.route('/load_template/<parent_submodule>/<setting_option>')
def load_template(parent_submodule, setting_option):
    # Get the correct template suffix (e.g., "_ai.html") from the global mapping
    suffix = setting_suffix_mapping.get(setting_option)
    if not suffix:
        return f"Setting option '{setting_option}' not found", 404

    # Format the department name to match the filename convention
    # e.g., "SPINNING 2" -> "spinning2"
    department_filename_part = parent_submodule.lower().replace(" ", "")

    # Construct the specific template name, e.g., "spinning2_ai.html"
    template_name = f"{department_filename_part}{suffix}"
    
    # Construct the full path to the template within the templates/Settings/ directory
    template_path = f"Settings/{template_name}"
    
    # Prepare data for the template
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()
    }
    
    socketio.emit('update', latest_values)
    
    # Render the specific template. As requested, no prefix is passed.
    # Flask will automatically handle a 404 if the template is not found.
    return render_template(template_path, msg=msg)

# --- END: REPLACEMENT BLOCK ---
@app.route('/')
def home():
    if 'userloggedin' in session:
        role = session.get('role')
        allowed_submodules = load_role_submodules(role)
        departmentss = load_data()
        return render_template('iot/dashboard.html', departmentss=departmentss, allowed_submodules=allowed_submodules)
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
def dashboard():
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))
    role = session.get('role')
    allowed_submodules = load_role_submodules(role)

    # Load the entire config to get the new dashboard_items list
    config_data = load_data()
    dashboard_items = config_data.get('dashboard_items', []) # Default to empty list if not found

    return render_template('iot/dashboard.html', 
                           dashboard_items=dashboard_items, 
                           allowed_submodules=allowed_submodules)

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
    allowed_submodules = []
    if 'userloggedin' in session:
        allowed_submodules = session.get('allowed_submodules', load_role_submodules(session.get('role')))
        # Group submodules by category
    categorized_submodules = defaultdict(list)
    submodules_list = data.get("submodules", [])
    for submodule in submodules_list:
        category = submodule.get("category", "Uncategorized")
        categorized_submodules[category].append(submodule)
    dashboard_name = data.get("Dashboard")
    
    return {
        'submodules': submodules_list,  # Pass the original list for the input page
        'categorized_submodules': categorized_submodules, # Pass the grouped dict for the sidebar
        'client_name': data.get("client_name", "Default Client Name"),
        'allowed_submodules': allowed_submodules,
        'dashboard_name': dashboard_name
    }
# THIS IS THE NEW, CORRECTED FUNCTION
@app.route('/<submodule>')
def render_submodule(submodule):
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))
    
    # CORRECTED LOGIC: Find the submodule in the list of dictionaries
    submodules_list = load_data().get("submodules", [])
    target_submodule = next((s for s in submodules_list if s['name'] == submodule), None)

    # Handle case where the submodule name from the URL is not found
    if not target_submodule:
        return "Page not found", 404

    role = session.get('role')
    allowed_submodules = load_role_submodules(role)
    session['allowed_submodules'] = allowed_submodules

    template_name = target_submodule['template'] # Get template from the found dictionary
    template_path = os.path.join("templates", "iot", template_name)
    
    if os.path.exists(template_path):
        msg = {"payload": latest_values}
        dept_name = submodule
        return render_template(f"iot/{template_name}", msg=msg, allowed_submodules=allowed_submodules, dept_name=dept_name)

    # This part might be reached if the template file is missing, but it's good practice
    return "Template file not found", 404



@app.route('/login', methods=['GET', 'POST'])
def user_login():
    if 'userloggedin' in session:
        return redirect(url_for('dashboard'))
    
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
            session['allowed_submodules'] = load_role_submodules(user_role)
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials, please try again.", 'danger')
    return render_template('User management/userlogin.html')

@app.route('/index')
def index():
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))
    msg = {'payload': 0}
    return render_template('iot/index.html', msg=msg)

@app.route('/sp')
def sp():
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()
    }
    return render_template('Settings/spinning2_sp.html', msg=msg)

@app.route('/ti')
def ti():
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
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
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
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
                # Storing hashed password as string
                cursor.execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (username, hashed_password.decode('utf-8'))) 
                conn.commit()
                flash(f"User added successfully to {table}.", 'success')
            except Exception as e:
                flash(f"Error adding user: {e}", 'danger')
            finally:
                if conn:
                    conn.close()
    return render_template('iot/add_user.html')

@app.route('/user_management')
def user_management():
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
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
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
    username = request.form['username']
    new_password_hashed = bcrypt.hashpw(request.form['password'].encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    new_role = request.form['role']
    
    conn = create_connection()
    cursor = conn.cursor()

    for table in ["Operator", "Manager", "Tse"]:
        cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        conn.commit()

    new_table = new_role
    cursor.execute(f"INSERT INTO {new_table} (username, password) VALUES (?, ?)", (username, new_password_hashed))
    conn.commit()
    conn.close()

    flash("User updated successfully.", "success")
    return redirect(url_for('user_management'))


@app.route('/delete_user', methods=['POST'])
def delete_user():
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
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
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))
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
    if 'userloggedin' not in session:
        return redirect(url_for('user_login'))
    data = load_yaml()
    if request.method == 'POST':
        msg = ""
        if 'client_name' in request.form:
            data['client_name'] = request.form['client_name']
            msg = "Client name updated"
        elif 'opc_ua_url' in request.form:
            data['OPC_UA_URL'] = request.form['opc_ua_url']
            msg = "OPC UA URL updated"
        # CORRECTED LOGIC: Handle adding a categorized submodule to a list
        elif 'submodule_name' in request.form and 'submodule_file' in request.form and 'submodule_category' in request.form:
            new_submodule = {
                'category': request.form['submodule_category'],
                'name': request.form['submodule_name'],
                'template': request.form['submodule_file']
            }
            # Ensure 'submodules' exists as a list and append the new item
            if 'submodules' not in data or not isinstance(data['submodules'], list):
                data['submodules'] = []
            data['submodules'].append(new_submodule)
            msg = "Submodule added"
        save_yaml(data)
        return jsonify(success=True, message=msg)

    return render_template(
        'iot/input.html',
        client_name=data.get('client_name', ''),
        opc_ua_url=data.get('OPC_UA_URL', ''),
        submodules=data.get('submodules', []),  # Pass the list to the template
        roles=data.get('roles', {})
    )

@app.route('/remove_submodule', methods=['POST'])
def remove_submodule():
    data = load_yaml()
    submodule_name = request.form['submodule_name']
    # CORRECTED LOGIC: Use a list comprehension to filter out the item to be deleted
    if 'submodules' in data:
        data['submodules'] = [s for s in data['submodules'] if s.get('name') != submodule_name]
    save_yaml(data)
    return jsonify({'success': True})

@app.route('/edit_submodule', methods=['POST'])
def edit_submodule():
    data = load_yaml()
    old_name = request.form['submodule_name']
    new_name = request.form['new_submodule_name']
    new_file = request.form['submodule_file']
    new_category = request.form['submodule_category']
    
    # CORRECTED LOGIC: Find the dictionary in the list and update its values
    if 'submodules' in data:
        for submodule in data['submodules']:
            if submodule.get('name') == old_name:
                submodule['name'] = new_name
                submodule['template'] = new_file
                submodule['category'] = new_category
                break  # Stop after finding and updating
            
    save_yaml(data)
    return jsonify({'success': True})

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        new_order = request.json.get('new_order', [])
        if not new_order:
            return jsonify({'error': 'No new order provided'}), 400
        
        yaml_file_path = 'input.yaml'
        with open(yaml_file_path, 'r') as yaml_file:
            data = yaml.safe_load(yaml_file) or {}

        # CORRECTED LOGIC: Directly replace the old list with the new ordered list
        data['submodules'] = new_order

        with open(yaml_file_path, 'w') as yaml_file:
            yaml.dump(data, yaml_file, default_flow_style=False, sort_keys=False)
            
        return jsonify({'message': 'Order updated successfully!'}), 200
    except Exception as e:
        # It's good practice to log the actual error for debugging
        print(f"Error in /update-order: {e}") 
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

    # Initialize the in-memory alarm states from the persistent file
    initialize_alarm_states()

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
    
    socketio.run(app, host="0.0.0.0", port=7005, debug=True, allow_unsafe_werkzeug=True)