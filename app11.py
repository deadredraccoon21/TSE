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
import json
import pyodbc
from werkzeug.utils import redirect
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, ValidationError
import bcrypt
from datetime import datetime
from cryptography.fernet import Fernet
from threading import Thread
from flask_socketio import SocketIO, emit
import uuid


app = Flask(__name__)
app.secret_key = 'xyzsdfg'
socketio = SocketIO(app)

SECRET_KEY = Fernet.generate_key()  # Save this securely, use the same key across app runs
cipher = Fernet(SECRET_KEY)

# Database configuration
DB_SERVER = 'localhost\\SQLEXPRESS'
DB_DATABASE = 'tse_database'
DB_USER = 'tse2'
DB_PASSWORD = 'tse@123'
# Connection string
connection_string = f'DRIVER={{ODBC Driver 11 for SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USER};PWD={DB_PASSWORD}'

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
        if user and user[1] == username:  # Ensure username matches case-sensitively
            try:
                if bcrypt.checkpw(password.encode('utf-8'), user[2].encode('utf-8')):
                    session['role'] = table  # Store the role in the session
                    session['username'] = username
                    return table  # Return the role as the table name
            except ValueError as e:
                print(f"Error validating password for user '{username}' in table '{table}': {e}")
                continue
    return None


# OPC UA server URL
OPC_UA_URL = "opc.tcp://127.0.0.1:4840"

# Create an OPC UA client instance
client = Client(OPC_UA_URL)

# Global variable to hold the latest values
latest_values = {}

# Load Node IDs from the YAML file
def load_node_ids():
    yaml_path = os.path.join(os.path.dirname(__file__), "nodeid.yaml")
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
        return data.get("node_ids", {})


import yaml

def read_values_periodically():
    global latest_values
    while True:
        try:
            client.connect()  # Connect to the OPC UA server

            # Load Node IDs from YAML file
            node_ids = load_node_ids()

            for name, node_id in node_ids.items():
                try:
                    node = client.get_node(node_id)
                    raw_value = node.get_value()  # Read the value
                    # Round the value to one decimal place
                    if isinstance(raw_value, (int, float)):
                        latest_values[name] = round(raw_value, 1)
                    else:
                        latest_values[name] = raw_value
                except Exception as e:
                    print(f"Error reading node {name}: {e}")

            # Emit the latest values to all connected clients
            socketio.emit('update', latest_values)

            # Save latest_values to alarmValues.yaml
            with open('alarmValues.yaml', 'w') as yaml_file:
                yaml.dump(latest_values, yaml_file)

            client.disconnect()  # Disconnect from the server
            time.sleep(1)  # Wait for 1 second before the next read
        except Exception as e:
            print(f"Error reading values: {str(e)}")
            time.sleep(1)  # Wait before retrying in case of error


# # Load alarms from YAML
# def load_alarms():
#     yaml_path = os.path.join(os.path.dirname(__file__), "alarms.yaml")
#     with open(yaml_path, "r") as f:
#         data = yaml.safe_load(f)
#         return data.get("alarms", {})

#Alarms 
# Define a relative path to the YAML file
def get_yaml_path():
    return os.path.join(os.path.dirname(__file__), 'alarms.yaml')

# Load Node IDs from the YAML file

# Connect to the OPC UA server and get the trip status

def get_trip_status_from_nodeid(nodeid):
    try:
        with open('alarmValues.yaml', 'r') as yaml_file:
            alarm_values = yaml.safe_load(yaml_file)

        if alarm_values is None:
            print("alarmValues.yaml is empty or could not be parsed.")
            return None

        node_ids = load_node_ids()
        for name, nid in node_ids.items():
            if nid == nodeid:
                value = alarm_values.get(name, False)
                return bool(value) if value is not None else None

        print(f"Node ID {nodeid} not found in configured node IDs.")
        return None
    except Exception as e:
        print(f"Error reading trip status from file for node {nodeid}: {e}")
        return None



# Load alarms from YAML and update trip status with current time if trip is true
def load_alarms():
    try:
        yaml_path = get_yaml_path()  # Get the path dynamically
        with open("alarms.yaml", "r") as f:
            data = yaml.safe_load(f)
            alarms = data.get("alarms", [])

            # Load Node IDs from the file
            node_ids = load_node_ids()

            # Update trip status and time based on OPC UA NodeID for each variable name in nodeids list
            for alarm in alarms:
                alarm['trip'] = False  # Default trip status
                for node_name in alarm.get('nodeids', []):
                    nodeid = node_ids.get(node_name)
                    if nodeid:
                        trip_status = get_trip_status_from_nodeid(nodeid)
                        alarm['trip'] = alarm['trip'] or trip_status  # Set to True if any trip is True

                # If the trip is true, capture the current time
                if alarm['trip']:
                    alarm['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # Filter alarms with trip = True
            alarms_with_trip = [alarm for alarm in alarms ]
            save_alarms_to_list_yaml(alarms_with_trip) 
            return alarms_with_trip
    except Exception as e:
        print(f"Error loading alarms: {e}")
        return []





# Global dictionary to track previous trip states
last_trip_states = {}

try:
        with open(get_yaml_path(), "r") as f:
            data = yaml.safe_load(f)
            alarms = data.get("alarms", [])
            for alarm in alarms:
                code = alarm.get('code')
                if code is not None:
                    last_trip_states[code] = False
        print(last_trip_states)
except Exception as e:
    print(f"Failed to initialize last_trip_states: {e}")

def save_alarms_to_list_yaml(current_alarms):
    try:
        existing_data = {'alarms': [], 'lastTripStates': {}}

        if os.path.exists(ALARM_LIST_FILE_PATH):
            with open(ALARM_LIST_FILE_PATH, 'r') as f:
                existing_data = yaml.safe_load(f) or {'alarms': [], 'lastTripStates': {}}

        alarms_list = existing_data.get('alarms', [])
        last_trip_states = existing_data.get('lastTripStates', {})

        for alarm in current_alarms:
            code = str(alarm.get('code'))
            if code is None:
                continue  # Skip if code is missing
            
            if 'trip' in alarm and alarm['trip'] is not None:
                current_trip = alarm['trip']
            else:
                continue  # or skip, or set a default if needed

            previous_trip = last_trip_states.get(code, False)

            

            # If trip state changed from False to True, add to alarm list
            if current_trip and not previous_trip:
                alarm['id'] = str(uuid.uuid4())
                alarm['status'] = 'Not acknowledged'
                alarm['acknowledged'] = False

                if 'time' in alarm:
                    try:
                        dt = datetime.strptime(alarm['time'], "%Y-%m-%d %H:%M:%S")
                        alarm['time'] = dt.strftime("%-m/%-d/%Y, %-I:%M:%S %p")
                    except Exception:
                        pass  # Keep original time format

                alarms_list.append(alarm)

            # Update the last trip state
            last_trip_states[code] = current_trip

        # Save updated data
        updated_data = {
            'alarms': alarms_list,
            'lastTripStates': last_trip_states
        }

        with open(ALARM_LIST_FILE_PATH, 'w') as out_file:
            yaml.dump(updated_data, out_file, default_flow_style=False)

        print("Alarms saved and trip states updated.")

    except Exception as e:
        print(f"Error saving alarmList.yaml: {e}")



@app.route("/alarms")
def get_alarms():
    alarms = load_alarms()
    return jsonify(alarms)

@app.route("/notifyAlarms")
def get_alarms_notification():
    alarms = load_alarms_from_yaml_list()
    return jsonify(alarms)


def load_alarms_from_yaml_list():
    try:
        with open(ALARM_LIST_FILE_PATH, 'r') as f:
            data = yaml.safe_load(f) or {}
            return data.get('alarms', [])
    except Exception as e:
        print(f"Error reading alarmList.yaml: {e}")
        return []


@app.route("/alarmslist")
def alarmslist():
    return render_template("iot/alarmslist.html")

# Emit alarms to all connected clients in real time
def emit_alarms():
    while True:
        alarms = load_alarms_from_yaml_list()
        socketio.emit('alarm_update', alarms)
        time.sleep(1)  # Emit every second to ensure real-time updates


from flask import request, jsonify

@app.route("/acknowledge", methods=["POST"])
def acknowledge_alarm():
    try:
        alarm_id = request.json.get("id")
        if not alarm_id:
            return jsonify({"success": False, "error": "Missing alarm ID"}), 400

        # Load the full YAML file
        with open(ALARM_LIST_FILE_PATH, 'r') as f:
            data = yaml.safe_load(f) or {}

        alarms = data.get("alarms", [])
        updated = False

        # Modify only the matched alarm
        for alarm in alarms:
            if alarm.get("id") == alarm_id:
                alarm["acknowledged"] = True
                alarm["status"] = "Acknowledged"
                updated = True
                break

        if updated:
            data["alarms"] = alarms  # Reassign in case it's a view
            with open(ALARM_LIST_FILE_PATH, 'w') as f:
                yaml.dump(data, f, default_flow_style=False)  # ✅ keeps rest of file intact
            return jsonify({"success": True})
        else:
            return jsonify({"success": False, "error": "Alarm ID not found"}), 404

    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

# @app.route("/acknowledge", methods=["POST"])
# def acknowledge_alarm():
#     data = request.json
#     alarm_code = data.get("code")

#     try:
#         yaml_path = get_yaml_path()
#         with open(yaml_path, "r") as f:
#             alarms_data = yaml.safe_load(f)
#             all_alarms = alarms_data.get("alarms", [])
        
#         # Update the specific alarm's status
#         for alarm in all_alarms:
#             if alarm["code"] == alarm_code:
#                 alarm["status"] = "Acknowledged"
#                 break

#         # Save the updated alarms back to the YAML file
#         with open(yaml_path, "w") as f:
#             yaml.dump({"alarms": all_alarms}, f)

#         return jsonify({"success": True})

#     except Exception as e:
#         print(f"Error updating YAML file: {e}")
#         return jsonify({"success": False, "error": str(e)})

@app.route("/debug")
def debug_alarms():
    alarms = load_alarms()
    return jsonify(alarms)



# def read_values_periodically():
#     global latest_values
#     while True:
#         try:
#             client.connect()  # Connect to the OPC UA server
            
#             # Load Node IDs from YAML file
#             node_ids = load_node_ids()
            
#             for name, node_id in node_ids.items():
#                 try:
#                     node = client.get_node(node_id)
#                     latest_values[name] = node.get_value()  # Read the value
#                 except Exception as e:
#                     print(f"Error reading node {name}: {e}")
            
#             # Emit the latest values to all connected clients
#             socketio.emit('update', {
#                 'temperature': latest_values.get('Temperature', 0),
#                 'humidity': latest_values.get('Humidity', 0)
#             })
            
#             socketio.emit('gauge_update', {
#                 'temperature': latest_values.get('Temperature', 0),
#                 'humidity': latest_values.get('Humidity', 0)
#             })
            
#             client.disconnect()  # Disconnect from the server
#             time.sleep(1)  # Wait for 1 second before the next read
#         except Exception as e:
#             print(f"Error reading values: {str(e)}")
#             time.sleep(1)  # Wait before retrying in case of error



# @app.route('/write', methods=['POST'])
# def write():
#     data = request.get_json()
#     if not data:
#         return jsonify({"success": False, "error": "No data provided"}), 400

#     opcua_client = Client("opc.tcp://127.0.0.1:4840")  # Replace with your OPC UA server URL

#     try:
#         opcua_client.connect()  # Ensure connection is established

#         for nodeid, value in data.items():
#             node = opcua_client.get_node(nodeid)
#             node_data_type = node.get_data_type_as_variant_type()

#             if node_data_type == ua.VariantType.Boolean:
#                 dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
#             elif node_data_type == ua.VariantType.Int32:
#                 dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
#             elif node_data_type == ua.VariantType.Float:
#                 dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
#             else:
#                 raise ValueError(f"Unsupported data type for node {nodeid}: {node_data_type}")

#             node.set_value(dv)

#         return jsonify({"success": True}), 200

#     except Exception as e:
#         print(f"Error writing settings: {e}")
#         return jsonify({"success": False, "error": str(e)}), 500

#     finally:
#         try:
#             opcua_client.disconnect()  # Disconnect after operation
#         except Exception as disconnect_error:
#             print(f"Error during client disconnect: {disconnect_error}")


# @app.route('/write', methods=['POST'])
# def write():
#     data = request.get_json()
#     if not data:
#         return jsonify({"success": False, "error": "No data provided"}), 400

#     node_ids = load_node_ids()  # Load Node IDs from the YAML file
#     opcua_client = Client(OPC_UA_URL)

#     try:
#         opcua_client.connect()  # Connect to OPC UA server

#         for parameter, value in data.items():
#             node_id = node_ids.get(parameter)
#             if not node_id:
#                 raise ValueError(f"Node ID for parameter '{parameter}' not found")

#             # Retrieve the node and determine its data type
#             node = opcua_client.get_node(node_id)
#             node_data_type = node.get_data_type_as_variant_type()

#             if node_data_type == ua.VariantType.Boolean:
#                 dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
#             else:
#                 raise ValueError(f"Unsupported data type for node {node_id}: {node_data_type}")

#             node.set_value(dv)  # Write the value to the node

#         return jsonify({"success": True}), 200

#     except Exception as e:
#         print(f"Error writing settings: {e}")
#         return jsonify({"success": False, "error": str(e)}), 500

#     finally:
#         try:
#             opcua_client.disconnect()  # Disconnect from OPC UA server
#         except Exception as disconnect_error:
#             print(f"Error during client disconnect: {disconnect_error}")


# @app.route('/write', methods=['POST'])
# def write():
#     data = request.get_json()
#     if not data:
#         return jsonify({"success": False, "error": "No data provided"}), 400

#     opcua_client = Client("opc.tcp://127.0.0.1:4840")  # Replace with your OPC UA server URL

#     try:
#         opcua_client.connect()  # Ensure connection is established

#         for nodeid, value in data.items():
#             node = opcua_client.get_node(nodeid)
#             node_data_type = node.get_data_type_as_variant_type()

#             if node_data_type == ua.VariantType.Boolean:
#                 dv = ua.DataValue(ua.Variant(bool(value), ua.VariantType.Boolean))
#             elif node_data_type == ua.VariantType.Int32:
#                 dv = ua.DataValue(ua.Variant(int(value), ua.VariantType.Int32))
#             elif node_data_type == ua.VariantType.Float:
#                 dv = ua.DataValue(ua.Variant(float(value), ua.VariantType.Float))
#             else:
#                 raise ValueError(f"Unsupported data type for node {nodeid}: {node_data_type}")

#             node.set_value(dv)

#         return jsonify({"success": True}), 200

#     except Exception as e:
#         print(f"Error writing settings: {e}")
#         return jsonify({"success": False, "error": str(e)}), 500

#     finally:
#         try:
#             opcua_client.disconnect()  # Disconnect after operation
#         except Exception as disconnect_error:
#             print(f"Error during client disconnect: {disconnect_error}")

@app.route('/write', methods=['POST'])
def write():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    print(f"Received data: {data}")  # Debugging step

    opcua_client = Client("opc.tcp://127.0.0.1:4840")  # Replace with your OPC UA server URL

    try:
        opcua_client.connect()  # Ensure connection is established

        for nodeid, value in data.items():
            node = opcua_client.get_node(nodeid)
            node_data_type = node.get_data_type_as_variant_type()

            # Handle different data types (Boolean, Int32, Float, etc.)
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
            opcua_client.disconnect()  # Disconnect after operation
        except Exception as disconnect_error:
            print(f"Error during client disconnect: {disconnect_error}")


@app.route('/writes', methods=['POST'])
def writes():
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "No data provided"}), 400

    opcua_client = Client("opc.tcp://127.0.0.1:4840")  # Replace with your OPC UA server URL

    try:
        opcua_client.connect()  # Ensure connection is established

        for nodeid, value in data.items():
            # Get the node ID from the YAML configuration
            if nodeid not in load_node_ids():
                return jsonify({"success": False, "error": f"Invalid node ID: {nodeid}"}), 400
            
            # Ensure correct parsing of node_id
            node = opcua_client.get_node(load_node_ids()[nodeid])  # Get the correct node based on YAML config

            node_data_type = node.get_data_type_as_variant_type()

            # Handle different data types (Boolean, Int32, Float, etc.)
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

            node.set_value(dv)  # Write the value to the node

        return jsonify({"success": True}), 200

    except Exception as e:
        print(f"Error writing settings: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

    finally:
        try:
            opcua_client.disconnect()  # Disconnect after operation
        except Exception as disconnect_error:
            print(f"Error during client disconnect: {disconnect_error}")



template_mapping = {
    "Set Point": "Settings/spinning2_sp.html",
    "Digital Input": "Settings/spinning2_di.html",
    "Digital Output": "Settings/spinning2_do.html",
    "Analog Input": "Settings/spinning2_ai.html",
    "Analog Output": "Settings/spinning2_ao.html",
    "Preset Values": "Settings/spinning2_pv.html",
    "Timer": "Settings/spinning2_ti.html",
    "Controllers": "Settings/spinning2_co.html",
    "UPSS": "Settings/spinning2_up.html",
    "Pump Min Set": "Settings/spinning2_pm.html",
}

# @app.route('/load_template/<submodule_option>')
# def load_template(submodule_option):
#     if submodule_option in template_mapping:
#         template_path = template_mapping[submodule_option]
#         msg = {
#             'payload': latest_values,
#             'node_ids': load_node_ids()  # Include node_ids from the YAML file
#         }
#         # Include node IDs as part of the context
#         return render_template(template_path, msg=msg)
#     return "Template not found", 404

@app.route('/load_template/<submodule_option>')
def load_template(submodule_option):
    if submodule_option in template_mapping:
        template_path = template_mapping[submodule_option]
        msg = {
            'payload': latest_values,
            'node_ids': load_node_ids()  # Include node_ids from the YAML file
        }
        # Emit the latest values before rendering the template
        socketio.emit('update', latest_values)
        # Include node IDs as part of the context
        return render_template(template_path, msg=msg)
    return "Template not found", 404


# @app.route('/load_template/<submodule_option>')
# def load_template(submodule_option):
#     # Ensure the submodule_option exists in the mapping
#     if submodule_option in template_mapping:
#         template_path = template_mapping[submodule_option]
#         return render_template(
#             template_path, 
#             msg={"payload": latest_values, "node_ids": load_node_ids()}
#         )
#     return "Template not found", 404



# # Load alarms and node mappings
# with open("alarms.yaml") as f:
#     alarms_data = yaml.safe_load(f)["alarms"]

# # Mock database to store active alarms and acknowledgment status
# active_alarms = []

# @app.route("/alarms")
# def get_alarms():
#     """Fetch active alarms."""
#     global active_alarms
#     # Simulate active alarms with acknowledgment status
#     active_alarms = [
#         {
#             "alarm": node,
#             "name": alarms_data[node]["name"],
#             "message": alarms_data[node]["message"],
#             "code": alarms_data[node]["code"],
#             "severity": alarms_data[node]["severity"],
#             "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
#             "acknowledged": alarms_data[node].get("acknowledged", False)
#         }
#         for node in alarms_data if node.endswith("TRIP")
#     ]
#     return jsonify(active_alarms)



# @app.route("/acknowledge", methods=["POST"])
# def acknowledge_alarm():
#     """Acknowledge an alarm."""
#     global alarms_data
#     data = request.json
#     alarm_id = data.get("alarm")

#     if alarm_id in alarms_data:
#         alarms_data[alarm_id]["acknowledged"] = True
#         return jsonify({"success": True, "message": "Alarm acknowledged"})
#     return jsonify({"success": False, "message": "Alarm not found"}), 404

# @app.route('/write/<node_id>/<int:value>', methods=['POST'])
# def write_value(node_id, value):
#     try:
#         node = client.get_node(node_id)
#         node.set_value(value)
#         return jsonify({"success": True})
#     except Exception as e:
#         return jsonify({"success": False, "error": str(e)})


# @app.route('/alarmslist')
# def alarmslist():
#     return render_template('iot/alarmslist.html')  # Render the HTML template


# @app.route('/')
# def home():
#     departmentss = load_data()
#     seen = set()
#     allowed_submodules = [
#         submodule for modules in ROLE_SUBMODULES.values() for submodule in modules
#         if not (submodule in seen or seen.add(submodule))
#     ]
#     return render_template('iot/dashboard.html', departmentss=departmentss,allowed_submodules=allowed_submodules)  # Render the HTML template

# Dynamically fetch roles and submodules
@app.route('/')
def home():
    ROLE_SUBMODULES = load_role_submodules()  # Load roles dynamically
    departmentss = load_data()
    seen = set()
    allowed_submodules = [
        submodule for modules in ROLE_SUBMODULES.values() for submodule in modules
        if not (submodule in seen or seen.add(submodule))
    ]
    return render_template('iot/dashboard.html', departmentss=departmentss, allowed_submodules=allowed_submodules)

# @app.route('/trends')
# def trends():
#     # Remove duplicates while preserving order
#     seen = set()
#     allowed_submodules = [
#         submodule for modules in ROLE_SUBMODULES.values() for submodule in modules
#         if not (submodule in seen or seen.add(submodule))
#     ]
#     # Load node_ids from the YAML file
#     with open('nodeid.yaml', 'r') as file:
#         node_ids = yaml.safe_load(file)
#     return render_template('iot/trends.html', node_ids=node_ids, allowed_submodules=allowed_submodules)

# @app.route('/dashboard')
# def dashboard():
#     # Remove duplicates while preserving order
#     seen = set()
#     allowed_submodules = [
#         submodule for modules in ROLE_SUBMODULES.values() for submodule in modules
#         if not (submodule in seen or seen.add(submodule))
#     ]
#     # Load node_ids from the YAML file
#     with open('nodeid.yaml', 'r') as file:
#         node_ids = yaml.safe_load(file)
#     return render_template('iot/dashboard.html', node_ids=node_ids, allowed_submodules=allowed_submodules)

@app.route('/dashboard')
def dashboard():
    ROLE_SUBMODULES = load_role_submodules()  # Load roles dynamically
    seen = set()
    allowed_submodules = [
        submodule for modules in ROLE_SUBMODULES.values() for submodule in modules
        if not (submodule in seen or seen.add(submodule))
    ]
    with open('nodeid.yaml', 'r') as file:
        node_ids = yaml.safe_load(file)
    return render_template('iot/dashboard.html', node_ids=node_ids, allowed_submodules=allowed_submodules)

# Function to load the data from the YAML file
def load_data():
    yaml_path = os.path.join(os.path.dirname(__file__), "input.yaml")
    with open(yaml_path, "r") as f:
        return yaml.safe_load(f)
    

# Function to load allowed submodules per role
def load_role_submodules():
    yaml_path = os.path.join(os.path.dirname(__file__), "input.yaml")
    with open(yaml_path, "r") as f:
        data = yaml.safe_load(f)
    return data.get("roles", {})

# Inject latest_values globally into all templates
# @app.context_processor
# def inject_latest_values():
#     return {'latest_values': latest_values}
    

# Inject submodules and client name into templates for consistent access
@app.context_processor
def inject_context():
    data = load_data()
    allowed_submodules = session.get('allowed_submodules', [])
    
    # Only pass the dashboard_name if 'Dashboard' is present in the YAML file
    dashboard_name = data.get("Dashboard")  # This will be None if 'Dashboard' is not in the YAML
    
    return {
        'submodules': data["submodules"],
        'client_name': data.get("client_name", "Default Client Name"),  # Fallback to default
        'allowed_submodules': allowed_submodules,
        'dashboard_name': dashboard_name  # Pass None or actual value
    }

@app.route('/<submodule>')
def render_submodule(submodule):
    # Load the submodule to template mapping
    submodules = load_data()["submodules"]

    seen = set()
    ROLE_SUBMODULES = load_role_submodules()  # Load roles dynamically
    allowed_submodules = [
        submodule for modules in ROLE_SUBMODULES.values() for submodule in modules
        if not (submodule in seen or seen.add(submodule))
    ]

    session['allowed_submodules'] = allowed_submodules

    # Find the template associated with the submodule
    template_name = submodules.get(submodule)
    if template_name:
        template_path = os.path.join("templates", "iot", template_name)
        if os.path.exists(template_path):
            # Pass the latest values from the OPC UA server (or other data)
            msg = {"payload": latest_values}  # Replace latest_values with your data
            return render_template(f"iot/{template_name}", msg=msg, allowed_submodules=allowed_submodules)

    return "Page not found", 404


# @app.route('/login', methods=['GET', 'POST'])
# def user_login():
#     if request.method == 'POST':
#         username = request.form['username']
#         password = request.form['password']
        
#         user_role = validate_user(username, password)
#         if user_role:
#             session['userloggedin'] = True
#             session['username'] = username
#             session['role'] = user_role
#             session['allowed_submodules'] = ROLE_SUBMODULES[user_role]
#             session['last_login'] = datetime.now().strftime('%d/%m/%Y %H:%M:%S')  # Store current timestamp
#             return redirect(url_for('dashboard'))
#         else:
#             flash("Invalid credentials, please try again.", 'danger')
#     return render_template('User management/userlogin.html')

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
            # Ensure only "Set Point" is assigned to Operators
            session['allowed_submodules'] = ["Set Point"] if user_role == "Operator" else ROLE_SUBMODULES.get(user_role, [])
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials, please try again.", 'danger')
    return render_template('User management/userlogin.html')

@app.route('/index')
def index():
    msg = {'payload': 0}
    return render_template('iot/index.html', msg=msg)  # Render the HTML template

# @app.route('/setpoint')
# def setpoint():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_setpoint.html', msg=msg)  # Render the HTML template

# @app.route('/di')
# def di():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_di.html', msg=msg)  # Render the HTML template


@app.route('/sp')
def sp():
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()  # Include node_ids from the YAML file
    }
    return render_template('Settings/spinning2_sp.html', msg=msg)  # Render the HTML template

@app.route('/ti')
def ti():
    msg = {
        'payload': latest_values,
        'node_ids': load_node_ids()  # Include node_ids from the YAML file
    }
    return render_template('Settings/spinning2_ti.html', msg=msg)  # Render the HTML template

@socketio.on('connect')
def handle_connect():
    # Send the latest values immediately upon connection
    emit('update', latest_values)


# @app.route('/ai')
# def ai():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_ai.html', msg=msg)  # Render the HTML template

# @app.route('/ao')
# def ao():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_ao.html', msg=msg)  # Render the HTML template

# @app.route('/pv')
# def pv():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_pv.html', msg=msg)  # Render the HTML template

# @app.route('/ti')
# def ti():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_ti.html', msg=msg)  # Render the HTML template

# @app.route('/co')
# def co():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_co.html', msg=msg)  # Render the HTML template

# @app.route('/up')
# def up():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_up.html', msg=msg)  # Render the HTML template

# @app.route('/pu')
# def pu():
#     msg = {"payload": latest_values}
#     return render_template('Settings/spinning2_pu.html', msg=msg)  # Render the HTML template


@app.route('/add_user', methods=['GET', 'POST'])
def add_user():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']

        # Hash the password
        hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())

        # Determine table based on the role
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
                # Encrypt the password before storing it
                encrypted_password = cipher.encrypt(password.encode('utf-8'))
                # cursor.execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (username, hashed_password))
                cursor.execute(f"INSERT INTO {table} (username, password) VALUES (?, ?)", (username, encrypted_password))
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
            try:
                decrypted_password = cipher.decrypt(row[2].encode('utf-8')).decode('utf-8')
            except Exception as e:
                decrypted_password = "Error decrypting"  # Handle decryption errors
                
            users.append({
                "username": row[1],
                "password": decrypted_password,  # Decrypted password
                "role": table[:]  # Remove the trailing "r" for display
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

    # Delete user from old table
    for table in ["Operator", "Manager", "Tse"]:
        cursor.execute(f"DELETE FROM {table} WHERE username = ?", (username,))
        conn.commit()

    # Insert user into the new role table
    # new_table = new_role + "r"
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
    # Clear user session data
    session.pop('userloggedin', None)
    session.pop('username', None)
    session.pop('role', None)
    session.pop('allowed_submodules', None)
    return redirect(url_for('home'))  # Redirect to login page after logout

# Render the Trends HTML template
@app.route('/trends')
def trends():
    msg = {'payload': 0}
    return render_template('iot/trends.html', msg=msg)

# configuring input.yaml

# Load the YAML file
def load_yaml():
    with open("input.yaml", 'r') as file:
        return yaml.safe_load(file)

# Save changes to the YAML file
def save_yaml(data):
    with open("input.yaml", 'w') as file:
        yaml.dump(data, file)
        
# @app.route('/input', methods=['GET', 'POST'])
# def input_page():
#     data = load_yaml()

#     if request.method == 'POST':
#         if 'client_name' in request.form:
#             data['client_name'] = request.form['client_name']
#         elif 'submodule_name' in request.form and 'submodule_file' in request.form:
#             new_submodule = {
#                 request.form['submodule_name']: request.form['submodule_file']
#             }
#             data['submodules'].update(new_submodule)
#         save_yaml(data)

#     return render_template('iot/input.html', client_name=data['client_name'], submodules=data['submodules'])


@app.route('/input', methods=['GET', 'POST'])
def input_page():
    data = load_yaml()

    if request.method == 'POST':
        if 'client_name' in request.form:
            data['client_name'] = request.form['client_name']
        elif 'submodule_name' in request.form and 'submodule_file' in request.form:
            new_submodule = {
                request.form['submodule_name']: request.form['submodule_file']
            }
            data['submodules'].update(new_submodule)
        save_yaml(data)

    return render_template('iot/input.html', client_name=data['client_name'], submodules=data['submodules'], roles=data['roles'])


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
    
    # Remove the old submodule if it exists
    if old_name in data['submodules']:
        del data['submodules'][old_name]
    
    # Add the new submodule with the new name
    data['submodules'][new_name] = new_file
    
    # Save the updated data to the YAML file
    save_yaml(data)
    
    return jsonify({'success': True})


from flask import request, jsonify
import yaml

@app.route('/update-order', methods=['POST'])
def update_order():
    try:
        # Fetch the new order from the request
        new_order = request.json.get('new_order', [])
        if not new_order:
            return jsonify({'error': 'No new order provided'}), 400

        # Convert the new order to the expected dictionary format
        submodules = {item['name']: item['file'] for item in new_order}

        # Path to your YAML file
        yaml_file_path = 'input.yaml'

        # Read the existing YAML file
        with open(yaml_file_path, 'r') as yaml_file:
            data = yaml.safe_load(yaml_file) or {}

        # Update only the 'submodules' key in the data
        data['submodules'] = submodules

        # Write the updated content back to the YAML file
        with open(yaml_file_path, 'w') as yaml_file:
            yaml.dump(data, yaml_file, default_flow_style=False, sort_keys=False)

        return jsonify({'message': 'Order updated successfully!'}), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
# Add a setting for a specific role
@app.route('/add_setting', methods=['POST'])
def add_setting():
    data = load_yaml()
    role = request.json.get('role')
    setting = request.json.get('setting')
    
    if role and setting:
        # Check if the role exists and add the new setting
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

# Fetch settings for a specific role
@app.route('/get_settings/<role>', methods=['GET'])
def get_settings(role):
    data = load_yaml()
    if role in data['roles']:
        return jsonify({'success': True, 'settings': data['roles'][role]})
    return jsonify({'success': False, 'message': 'Role not found'}), 400

# Delete multiple settings from a specific role
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



    
    #-----------------------------------

   # Define the path to the YAML file - use absolute path for reliability
ALARM_LIST_FILE_PATH = os.path.join(os.path.dirname(__file__), "alarmList.yaml")

# Function to load alarms from YAML
# def load_alarms_from_yaml():
#     try:
#         if os.path.exists(ALARM_LIST_FILE_PATH):
#             with open(ALARM_LIST_FILE_PATH, 'r') as file:
#                 data = yaml.safe_load(file)
#                 if data is None:
#                     print(f"File exists but is empty or invalid: {ALARM_LIST_FILE_PATH}")
#                     return {"alarms": [], "lastTripStates": {}}
#                 print(f"Successfully loaded data from {ALARM_LIST_FILE_PATH}")
#                 return data
#         else:
#             print(f"File not found: {ALARM_LIST_FILE_PATH}. Creating empty file.")
#             # If file doesn't exist, create it with empty data
#             empty_data = {"alarms": [], "lastTripStates": {}}
#             with open(ALARM_LIST_FILE_PATH, 'w') as file:
#                 yaml.dump(empty_data, file, default_flow_style=False)
#             return empty_data
#     except Exception as e:
#         print(f"Error loading alarms from YAML: {e}")
#         return {"alarms": [], "lastTripStates": {}}

# Function to save alarms to YAML
# def save_alarms_to_yaml(data):
#     try:
#         with open(ALARM_LIST_FILE_PATH, 'w') as file:
#             yaml.dump(data, file, default_flow_style=False)
#         print(f"Successfully saved data to {ALARM_LIST_FILE_PATH}")
#         return True
#     except Exception as e:
#         print(f"Error saving alarms to YAML: {e}")
#         return False

#--------------------------------------------------------------------------------------------------------- Route to load alarms
# @app.route('/alarms/load', methods=['GET'])
# def load_alarms_endpoint():  # Renamed to avoid conflict with existing load_alarms function
#     try:
#         data = load_alarms_from_yaml()
#         print(f"Loaded {len(data.get('alarms', []))} alarms from {ALARM_LIST_FILE_PATH}")
#         return jsonify({
#             "success": True,
#             "alarms": data.get("alarms", []),
#             "lastTripStates": data.get("lastTripStates", {})
#         })
#     except Exception as e:
#         print(f"Error in /alarms/load endpoint: {e}")
#         return jsonify({
#             "success": False,
#             "error": str(e)
#         })

# =----------------------------------------------------------------------------------------------------------------------Route to save alarms
# @app.route('/alarms/save', methods=['POST'])
# def save_alarms_endpoint():  # Renamed to avoid conflict with existing save_alarms function
#     try:
#         data = request.json
#         if not data:
#             print("No data provided in request")
#             return jsonify({"success": False, "error": "No data provided"})
            
#         print(f"Saving  alarms to {ALARM_LIST_FILE_PATH}")
#         success = true
#         # success = save_alarms_to_yaml({
#         #     "alarms": data.get("alarms", []),
#         #     "lastTripStates": data.get("lastTripStates", {})
#         # })

#         return jsonify({
#             "success": success,
#             "message": "Alarms saved successfully" if success else "Failed to save alarms"
#         })
#     except Exception as e:
#         print(f"Error in /alarms/save endpoint: {e}")
#         return jsonify({
#             "success": False,
#             "error": str(e)
#         })

if __name__ == '__main__':
    # Start the background thread to read values periodically
    thread = threading.Thread(target=read_values_periodically)
    thread.daemon = True  # Daemonize thread
    thread.start()

    # Start the background thread to emit alarms
    alarm_thread = threading.Thread(target=emit_alarms)
    alarm_thread.daemon = True
    alarm_thread.start()
    
    # app.run(host="127.0.0.1", port=7005)
    socketio.run(app, host='127.0.0.1', port=7005, debug=True)