import pyodbc
import bcrypt
import yaml
import sys
import os

# --- PATH FIX FOR EXE ---
if getattr(sys, 'frozen', False):
    BASE_DIR = sys._MEIPASS
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

yaml_path = os.path.join(BASE_DIR, "input.yaml")

# Load configuration
try:
    with open(yaml_path, 'r') as file:
        config = yaml.safe_load(file)
except Exception as e:
    print(f"Error loading input.yaml from {yaml_path}: {e}")
    sys.exit(1)

DB_SERVER = config.get('DB_SERVER')
DB_DATABASE = config.get('DB_DATABASE')
DB_USER = config.get('DB_USER')
DB_PASSWORD = config.get('DB_PASSWORD')

# ==============================================================================
# === CONNECTION STRING LOGIC (Test 17, fallback to 11)                      ===
# ==============================================================================
connection_string = None
drivers_to_test = [
    'ODBC Driver 17 for SQL Server', 
    'ODBC Driver 11 for SQL Server'
]

print("--- Initializing Database Setup ---")

for driver in drivers_to_test:
    try:
        # Construct the connection string for the current driver
        temp_conn_string = f'DRIVER={{{driver}}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USER};PWD={DB_PASSWORD}'
        
        # Attempt a test connection
        conn = pyodbc.connect(temp_conn_string, timeout=5)
        conn.close()
        
        # If successful, set the global connection string and break loop
        connection_string = temp_conn_string
        print(f"SUCCESS: Connected using {driver}")
        break
    except Exception as e:
        print(f"FAILED: Could not connect with {driver}. Error: {e}")

if not connection_string:
    print("CRITICAL ERROR: Could not connect to database with either ODBC Driver 17 or 11.")
    sys.exit(1)

# ==============================================================================
# === SQL DEFINITIONS                                                        ===
# ==============================================================================

# SQL commands to create tables with existence checks
create_operator_table_query = '''
IF OBJECT_ID('Operator', 'U') IS NULL
BEGIN
    CREATE TABLE Operator (
       id INT PRIMARY KEY IDENTITY(1,1),
       username VARCHAR(100) NOT NULL,
       password VARCHAR(255) NOT NULL
    );
END
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'unique_Operator_username')
BEGIN
    ALTER TABLE Operator ADD CONSTRAINT unique_Operator_username UNIQUE (username);
END
'''

create_manager_table_query = '''
IF OBJECT_ID('Manager', 'U') IS NULL
BEGIN
    CREATE TABLE Manager (
       id INT PRIMARY KEY IDENTITY(1,1),
       username VARCHAR(100) NOT NULL,
       password VARCHAR(255) NOT NULL
    );
END
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'unique_Manager_username')
BEGIN
    ALTER TABLE Manager ADD CONSTRAINT unique_Manager_username UNIQUE (username);
END
'''

create_tse_table_query = '''
IF OBJECT_ID('Tse', 'U') IS NULL
BEGIN
    CREATE TABLE Tse (
       id INT PRIMARY KEY IDENTITY(1,1),
       username VARCHAR(100) NOT NULL,
       password VARCHAR(255) NOT NULL
    );
END
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'unique_Tse_username')
BEGIN
    ALTER TABLE Tse ADD CONSTRAINT unique_Tse_username UNIQUE (username);
END
'''

create_alarm_history_table_query = '''
IF OBJECT_ID('AlarmHistory', 'U') IS NULL
BEGIN
    CREATE TABLE AlarmHistory (
       id INT PRIMARY KEY IDENTITY(1,1),
       alarm_id VARCHAR(100) NOT NULL,
       time VARCHAR(100),
       message VARCHAR(MAX),
       code VARCHAR(100),
       severity VARCHAR(50),
       status VARCHAR(50),
       acknowledged BIT,
       resolved_time VARCHAR(100)
    );
END
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = 'idx_alarm_time')
BEGIN
    CREATE INDEX idx_alarm_time ON AlarmHistory (time);
END
'''

# ==============================================================================
# === HELPER FUNCTIONS                                                       ===
# ==============================================================================

def execute_query(query, values=None):
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        if values:
            cursor.execute(query, values)
        else:
            cursor.execute(query)
        conn.commit()
        # Only print for table creation usually, reducing noise
        # print("Query executed successfully.") 
    except Exception as e:
        print(f"Error executing query: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def insert_user(table, username, plain_password):
    conn = None
    cursor = None
    try:
        conn = pyodbc.connect(connection_string)
        cursor = conn.cursor()
        # Check if the username already exists
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE username = ?", (username,))
        if cursor.fetchone()[0] > 0:
            print(f"Info: User '{username}' already exists in {table}. Skipping.")
            return
        
        # Hash the password and insert the user
        hashed_password = bcrypt.hashpw(plain_password.encode('utf-8'), bcrypt.gensalt())
        insert_query = f"INSERT INTO {table} (username, password) VALUES (?, ?)"
        cursor.execute(insert_query, (username, hashed_password.decode('utf-8')))
        conn.commit()
        print(f"User '{username}' added successfully to {table}.")
    except Exception as e:
        print(f"Error inserting user into {table}: {str(e)}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ==============================================================================
# === EXECUTION                                                              ===
# ==============================================================================

print("Creating tables if they don't exist...")
execute_query(create_operator_table_query)
execute_query(create_manager_table_query)
execute_query(create_tse_table_query)
execute_query(create_alarm_history_table_query)
print("Table check complete.")

print("Inserting default users...")
insert_user('Operator', 'operator', 'operator@123')
insert_user('Manager', 'manager', 'manager@123')
insert_user('Tse', 'tse', 'tse@123')
print("Setup script finished.")