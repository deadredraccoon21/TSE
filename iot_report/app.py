import pyodbc
import yaml
from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash, make_response
from datetime import datetime
# Removed redundant redirect import from werkzeug.utils

# Corrected: Use __name__ for the Flask app constructor
app = Flask(__name__)
app.secret_key = 'xyzsdfg' # For a production app, use a more secure, randomly generated key

# --- Load configuration from YAML at startup ---

try:
    with open('input.yaml', 'r') as f:
        config = yaml.safe_load(f)
        db_config = config['database']
        app_config = config['app_config']
except FileNotFoundError:
    print("FATAL ERROR: input.yaml not found. Please create it in the same directory.")
    exit()
except (KeyError, TypeError) as e:
    print(f"FATAL ERROR: input.yaml is missing a required key or is malformed: {e}")
    exit()

# --- Use loaded config to build the database connection string ---

connection_string = (
    f"DRIVER={{{db_config['driver']}}};"
    f"SERVER={db_config['server']};"
    f"DATABASE={db_config['name']};"
    f"UID={db_config['user']};"
    f"PWD={db_config['password']}"
)

# --- Database Helper Functions ---

def create_connection():
    """Establishes a connection to the database using the configured connection string."""
    try:
        conn = pyodbc.connect(connection_string, autocommit=True) # autocommit can simplify some operations
        return conn
    except pyodbc.Error as e:
        print(f"Error connecting to database: {e}")
        return None

def fetch_column_names(table_name):
    """Fetches all column names for a given table."""
    conn = create_connection()
    if not conn: return []
    try:
        cursor = conn.cursor()
        # Use parameterization for table name in schema query to prevent injection, if driver supports it.
        # For many drivers, this part isn't parameterizable, but it's safe here as table_name comes from our trusted YAML.
        cursor.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = ?", (table_name,))
        column_names = [row.COLUMN_NAME for row in cursor.fetchall()]
    except pyodbc.Error as e:
        print(f"Error fetching column names for table '{table_name}': {e}")
        column_names = []
    finally:
        if conn: conn.close()
    return column_names

def fetch_data(table_name, from_date, to_date, time_difference, columns_to_fetch):
    """Fetches and processes data from a specific table based on user criteria."""
    conn = create_connection()
    if not conn: return []

    try:
        cursor = conn.cursor()
        actual_table_columns = fetch_column_names(table_name)
        valid_cols = [col for col in columns_to_fetch if col in actual_table_columns]

        # Always include date and time for filtering and display if they exist in the table
        if 'date' in actual_table_columns and 'date' not in valid_cols:
            valid_cols.insert(0, 'date')
        if 'time' in actual_table_columns and 'time' not in valid_cols:
            valid_cols.insert(1, 'time')
        
        if not valid_cols:
            return []

        select_clause = ", ".join([f'"{col}"' for col in valid_cols])

        # Parse dates and prepare for query
        from_date_obj = datetime.strptime(from_date, "%Y-%m-%d").date()
        to_date_obj = datetime.strptime(to_date, "%Y-%m-%d").date()

        time_diff_map = {'1 hour': 60, '2 hours': 120, '5 hours': 300, '1 Day': 1440}
        time_difference_minutes = time_diff_map.get(time_difference, 0)

        # Use parameterized queries to prevent SQL injection
        query = f'SELECT {select_clause} FROM "{table_name}" WHERE "date" >= ? AND "date" <= ?'
        params = [from_date_obj, to_date_obj]

        if time_difference_minutes > 0 and 'time' in actual_table_columns:
            # Note: This DATEDIFF syntax is specific to SQL Server.
            # For other databases, this might need adjustment.
            query += " AND DATEDIFF(MINUTE, CAST('00:00:00' AS TIME), CAST(time AS TIME)) % ? = 0"
            params.append(time_difference_minutes)
            
        cursor.execute(query, tuple(params))
        
        column_names = [column[0] for column in cursor.description]
        data = [dict(zip(column_names, row)) for row in cursor.fetchall()]

        # Post-process data for formatting
        for row_dict in data:
            if 'date' in row_dict and isinstance(row_dict.get('date'), datetime):
                 row_dict['date'] = row_dict['date'].strftime("%Y-%m-%d")
            if 'time' in row_dict and row_dict.get('time') is not None:
                # pyodbc can return time as string or datetime.time, handle both
                if isinstance(row_dict.get('time'), datetime):
                    row_dict['time'] = row_dict['time'].strftime("%H:%M:%S")
                else:
                    # Assuming it's already a string-like object, just ensure format if needed
                    row_dict['time'] = str(row_dict['time'])
            for key, value in row_dict.items():
                if isinstance(value, float):
                    row_dict[key] = round(value, 2)
        
        return data
    except pyodbc.Error as e:
        print(f"Database error in fetch_data for table '{table_name}': {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred in fetch_data for table '{table_name}': {e}")
        return []
    finally:
        if conn: conn.close()

# --- Main Application Routes ---

@app.route('/')
def user_login():
    """Renders the user login page, preventing caching."""
    if 'userloggedin' in session:
        return redirect(url_for('reportpage'))
    response = make_response(render_template('userlogin.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/report')
def reportpage():
    """Displays the main report page with department options from the config file."""
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))

    # Department options are now dynamically loaded from the YAML config
    department_options = list(app_config['departments'].keys())

     # Get client name from the loaded config, with a fallback default name
    client_name = app_config.get('client_name', 'Default Client Name')
    
    # Pass the client_name variable to the template
    response = make_response(render_template(
        'report.html', 
        department_options=department_options, 
        client_name=client_name
    ))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response

# --- API/Data Routes ---

@app.route('/getTables', methods=['POST'])
def get_tables():
    """API endpoint to get all available column names for a selected department."""
    department = request.form.get('department')

    # Look up department info from the loaded YAML config
    department_info = app_config['departments'].get(department)
    if not department_info:
        return jsonify({'error': 'Department not configured or invalid'}), 400

    rh_table_name = department_info.get('rh_table')
    temp_table_name = department_info.get('temp_table')

    all_department_columns = set()
    if rh_table_name:
        all_department_columns.update(fetch_column_names(rh_table_name))
    if temp_table_name:
        all_department_columns.update(fetch_column_names(temp_table_name))

    # Filter out internal IDs and sort for a consistent UI
    display_columns = sorted([col for col in all_department_columns if col.lower() not in ['idx', 'id']])
    return jsonify({'columns': display_columns})

@app.route('/data', methods=['POST'])
def get_data_api():
    """API endpoint to fetch the combined RH and Temp data for the report."""
    department = request.form.get('department')
    from_date = request.form.get('from_date')
    to_date = request.form.get('to_date')
    time_difference = request.form.get('time_difference')
    selected_fields_str = request.form.get('selected_fields', '')
    selected_fields = selected_fields_str.split(',') if selected_fields_str else []

    if not all([department, from_date, to_date]):
        return jsonify({'error': 'Missing required parameters: department, from_date, or to_date'}), 400

    # Look up department info from the loaded YAML config
    department_info = app_config['departments'].get(department)
    if not department_info:
        return jsonify({'error': 'Invalid department selected'}), 400

    rh_table = department_info.get('rh_table')
    temp_table = department_info.get('temp_table')

    rh_data = []
    temp_data = []

    if rh_table:
        rh_data = fetch_data(rh_table, from_date, to_date, time_difference, selected_fields)
    if temp_table:
        temp_data = fetch_data(temp_table, from_date, to_date, time_difference, selected_fields)

    # Combine data intelligently for the view
    # This logic assumes we want to merge rows based on date and time.
    # A dictionary is efficient for this merge operation.
    merged_data = {}
    for row in rh_data + temp_data:
        # Ensure date and time exist before creating the key
        row_date = row.get('date')
        row_time = row.get('time')
        if row_date is not None and row_time is not None:
            key = (row_date, row_time)
            if key not in merged_data:
                merged_data[key] = {}
            merged_data[key].update(row)

    final_data = list(merged_data.values())

    # Determine display columns based on the original selection order, ensuring date/time are first.
    all_fetched_columns = set()
    for row in final_data:
        all_fetched_columns.update(row.keys())

    display_columns = []
    if 'date' in all_fetched_columns: display_columns.append('date')
    if 'time' in all_fetched_columns: display_columns.append('time')

    # Add other columns in the order they were originally selected by the user
    other_cols = [col for col in selected_fields if col in all_fetched_columns and col not in ['date', 'time']]
    display_columns.extend(other_cols)

    return jsonify({
        'columns': display_columns,
        'data': final_data,
    })

@app.route('/time-differences')
def get_time_differences():
    """API endpoint to provide time difference options."""
    time_differences = ['10 minutes', '30 minutes', '1 hour', '2 hours', '5 hours', '1 Day']
    return jsonify({'time_differences': time_differences})

# --- User Authentication Routes ---

@app.route('/userlogin', methods=['GET', 'POST'])
def handle_user_login():
    message = ''
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = create_connection()
        if conn:
            try:
                cursor = conn.cursor()
                # Using COLLATE is good for case-sensitive password checks
                cursor.execute("SELECT * FROM users WHERE username = ? COLLATE Latin1_General_CS_AS AND password = ?", (username, password))
                user = cursor.fetchone()
                if user:
                    session['userloggedin'] = True
                    session['userid'] = user.id
                    session['username'] = user.username
                    return redirect(url_for('reportpage'))
                else:
                    message = 'Invalid username or password'
            except pyodbc.Error as e:
                message = "Database error during login."
                print(e)
            finally:
                conn.close()

    return make_response(render_template('userlogin.html', message=message))

@app.route('/userlogout')
def user_logout():
    session.pop('userloggedin', None)
    session.pop('userid', None)
    session.pop('username', None)
    response = make_response(redirect(url_for('user_login')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# --- Admin Section Routes (Login, CRUD for Users & Admins) ---

def execute_query(query, params=(), fetch=None):
    """A helper to safely execute database queries."""
    conn = create_connection()
    if not conn:
        return None if fetch else False
    try:
        cursor = conn.cursor()
        cursor.execute(query, params)
        if fetch == 'all':
            result = cursor.fetchall()
        elif fetch == 'one':
            result = cursor.fetchone()
        else:
            conn.commit()
            result = True
    except pyodbc.Error as e:
        print(f"Query failed: {e}")
        # Rollback on error if it's not an autocommit connection
        # conn.rollback()
        result = None if fetch else False
    finally:
        conn.close()
    return result

@app.route('/admin')
def admin_dashboard():
    if 'loggedin' not in session: return redirect(url_for('admin_login'))
    return make_response(render_template('adminpage.html'))

@app.route('/login', methods=['GET', 'POST'])
def admin_login():
    message = ''
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = execute_query("SELECT * FROM admins WHERE username = ? AND password = ?", (username, password), fetch='one')
        if user:
            session['loggedin'] = True
            session['userid'] = user.id
            session['username'] = user.username
            return redirect(url_for('admin_dashboard'))
        else:
            message = 'Invalid admin credentials'

    response = make_response(render_template('login.html', message=message))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

@app.route('/logout')
def admin_logout():
    session.clear()
    response = make_response(redirect(url_for('admin_login')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response

# Generic CRUD routes for users and admins

def crud_template(template_name, table_name):
    if 'loggedin' not in session: return redirect(url_for('admin_login'))
    records = execute_query(f"SELECT * FROM {table_name}", fetch='all')
    # Passing the fetched records with the key 'records'
    return render_template(template_name, records=records, table_name=table_name)

def crud_insert(table_name, redirect_url):
    if 'loggedin' not in session: return redirect(url_for('admin_login'))
    username = request.form['username']
    password = request.form['password']
    try:
        # Note: Storing passwords in plain text is insecure. Use a hashing library like Werkzeug or Passlib.
        execute_query(f"INSERT INTO {table_name} (username, password) VALUES (?, ?)", (username, password))
        flash("Record Inserted Successfully")
    except pyodbc.IntegrityError:
        flash("Username already exists.", "error")
    return redirect(url_for(redirect_url))

def crud_delete(table_name, redirect_url, record_id):
    if 'loggedin' not in session: return redirect(url_for('admin_login'))
    execute_query(f"DELETE FROM {table_name} WHERE id=?", (record_id,))
    flash("Record Deleted Successfully")
    return redirect(url_for(redirect_url))

def crud_update(table_name, redirect_url, record_id):
    if 'loggedin' not in session: return redirect(url_for('admin_login'))
    username = request.form['username']
    password = request.form['password']
    execute_query(f"UPDATE {table_name} SET username=?, password=? WHERE id=?", (username, password, record_id))
    flash("Record Updated Successfully")
    return redirect(url_for(redirect_url))

@app.route('/users')
def manage_users(): return crud_template('index.html', 'users')

@app.route('/admins')
def manage_admins(): return crud_template('admin.html', 'admins')

@app.route('/insert/users', methods=['POST'])
def insert_user(): return crud_insert('users', 'manage_users')

@app.route('/insert/admins', methods=['POST'])
def insert_admin(): return crud_insert('admins', 'manage_admins')

# Corrected: Added < > around the dynamic parts of the URL
@app.route('/delete/users/<int:record_id>')
def delete_user(record_id): return crud_delete('users', 'manage_users', record_id)

@app.route('/delete/admins/<int:record_id>')
def delete_admin(record_id): return crud_delete('admins', 'manage_admins', record_id)

@app.route('/update/users/<int:record_id>', methods=['POST'])
def update_user(record_id): return crud_update('users', 'manage_users', record_id)

@app.route('/update/admins/<int:record_id>', methods=['POST'])
def update_admin(record_id): return crud_update('admins', 'manage_admins', record_id)

# Corrected: Use __name__ == '__main__'
if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5001, debug=False)