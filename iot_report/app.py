import pyodbc
import json
from flask import Flask, render_template, jsonify, request, redirect, url_for, session, flash, make_response
from datetime import datetime, timedelta
from werkzeug.utils import redirect
from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, ValidationError
import bcrypt

app = Flask(__name__)
app.secret_key = 'xyzsdfg'

# Database configuration
DB_SERVER = 'DESKTOP-SQ1S6QN'
DB_DATABASE = 'tse_data'
DB_USER = 'tse'
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
            # Handle specific error cases if needed, or retry
            # Example: Handle specific error codes or types of errors
            # Check documentation for pyodbc for specific error handling

        except Exception as e:
            print(f"Unexpected error during connection: {e}")
            return None  # Handle other exceptions as needed

# Connect to the database (redundant, create_connection already does this)
# You can remove this function if create_connection is your primary connection method
def connect_to_db():
    conn_str = f'DRIVER={{SQL Server}};SERVER={DB_SERVER};DATABASE={DB_DATABASE};UID={DB_USER};PWD={DB_PASSWORD}'
    return pyodbc.connect(conn_str)

# Fetch all table names from the database (not directly used in data fetching, but kept for reference)
def fetch_table_names(department):
    conn = create_connection() # Use create_connection
    cursor = conn.cursor()
    cursor.execute("SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_NAME LIKE ?", (department + '%',))
    table_names = [row.TABLE_NAME for row in cursor.fetchall()]
    
    conn.close()
    return table_names
    
# Fetch column names of the selected table
def fetch_column_names(table_name):
    conn = create_connection() # Use create_connection
    cursor = conn.cursor()
    cursor.execute(f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_NAME = '{table_name}'")
    column_names = [row.COLUMN_NAME for row in cursor.fetchall()]
    conn.close()
    return column_names

@app.route('/')
def user_login():
    # Prevent caching of the userlogin.html page
    response = make_response(render_template('userlogin.html'))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


# THIS FUNCTION IS NOW MODIFIED TO ACCEPT SELECTED_COLUMNS
def fetch_data(table_name, from_date, to_date, time_difference, columns_to_fetch):
    conn = None # Initialize conn to None
    cursor = None # Initialize cursor to None
    try:
        conn = create_connection()
        cursor = conn.cursor()
        
        # Ensure 'date' and 'time' are always in the columns to fetch
        # and dynamically build the SELECT clause
        # Filter out 'idx' here if it's not a display column
        # Ensure only columns specific to this table are in the query
        valid_cols = []
        # Get actual columns from the table to filter for the query
        actual_table_columns = fetch_column_names(table_name)
        
        for col in columns_to_fetch:
            if col in actual_table_columns:
                valid_cols.append(col)

        # Always include 'date' and 'time' if they are relevant to this table
        if 'date' not in valid_cols and 'date' in actual_table_columns:
            valid_cols.insert(0, 'date')
        if 'time' not in valid_cols and 'time' in actual_table_columns:
            valid_cols.insert(1, 'time')
            
        # If no valid columns to select (other than date/time), return empty data
        if not valid_cols:
            return []

        select_clause = ", ".join([f'"{col}"' for col in valid_cols]) # Quote column names for safety

        # Parse input dates and adjust format
        from_date = datetime.strptime(from_date, "%Y-%m-%d").strftime("%Y/%m/%d")
        to_date = datetime.strptime(to_date, "%Y-%m-%d").strftime("%Y/%m/%d") + " 23:59:59"  # Set to the end of the selected day

        # Calculate time difference in minutes
        time_diff_map = {
            '10 minutes': 10,
            '30 minutes': 30,
            '1 hour': 60,
            '2 hours': 120,
            '5 hours': 300,
            '1 Day': 1440
        }
        time_difference_minutes = time_diff_map.get(time_difference, 0) # Default to 0 if not found

        # Construct SQL query with proper date and time filtering
        # Using parameterized query for date and time to prevent SQL injection
        query = f"SELECT {select_clause} FROM \"{table_name}\" WHERE date >= ? AND date <= ?"
        params = [from_date, to_date]

        if time_difference_minutes > 0:
            query += " AND DATEDIFF(MINUTE, CAST('00:00:00' AS TIME), CAST(time AS TIME)) % ? = 0"
            params.append(time_difference_minutes)
            
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall()

        data = []
        # Get column names from cursor description
        column_names = [column[0] for column in cursor.description]

        for row in rows:
            row_dict = dict(zip(column_names, row))

            if 'time' in row_dict and isinstance(row_dict['time'], datetime):
                row_dict['time'] = row_dict['time'].strftime("%H:%M:%S")
            elif 'time' in row_dict and isinstance(row_dict['time'], str):
                try:
                    # Attempt to parse string to datetime and then format
                    row_dict['time'] = datetime.strptime(row_dict['time'], "%H:%M:%S").strftime("%H:%M:%S")
                except ValueError:
                    pass # Keep as is if format doesn't match

            for key, value in row_dict.items():
                if isinstance(value, float):
                    row_dict[key] = round(value, 2)

            data.append(row_dict)
        
        return data

    except pyodbc.Error as e:
        print(f"Database error in fetch_data for table {table_name}: {e}")
        return []
    except Exception as e:
        print(f"An unexpected error occurred in fetch_data for table {table_name}: {e}")
        return []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@app.route('/getTables', methods=['POST'])
def get_tables():
    department = request.form.get('department')
    if not department:
        return jsonify({'error': 'Department not provided'}), 400

    table_prefixes = {
        'Preparatory1': ['Prep1_RH', 'Prep1_T'],
        'Preparatory2': ['Prep2_RH', 'Prep2_T'],
        'Spinning1': ['Spg1_RH', 'Spg1_T'],
        'Spinning2': ['Spg2_RH', 'Spg2_T'],
        'LinkConer': ['LC_RH', 'LC_T']
    }
    
    # Get the RH and Temp table names for the selected department
    rh_table_name = table_prefixes.get(department, [None, None])[0]
    temp_table_name = table_prefixes.get(department, [None, None])[1]

    all_department_columns = set()

    if rh_table_name:
        rh_cols = fetch_column_names(rh_table_name)
        all_department_columns.update(rh_cols)
    if temp_table_name:
        temp_cols = fetch_column_names(temp_table_name)
        all_department_columns.update(temp_cols)

    # Convert set to list and sort for consistent order
    sorted_columns = sorted(list(all_department_columns))

    return jsonify({'columns': sorted_columns}) # Return all available columns for the department


@app.route('/report', methods=['GET', 'POST'])
def reportpage():
    # Check if the user is logged in (uncomment to enable login protection)
    # if 'userloggedin' not in session:
    #     return redirect(url_for('user_login'))

    department_options = ['Preparatory1', 'Preparatory2', 'Spinning1', 'Spinning2', 'LinkConer']
    response = make_response(render_template('report.html', department_options=department_options))
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


@app.route('/userlogin', methods=['GET', 'POST'])
def userlogin():
    # Check if the user is already logged in
    if 'userloggedin' in session:
        # If already logged in, redirect to the report page
        return redirect(url_for('reportpage'))

    message = ''
    if request.method == 'POST' and 'username' in request.form and 'password' in request.form:
        username = request.form['username']
        password = request.form['password']
        
        # Establish a database connection
        conn = create_connection()
        cursor = conn.cursor()
        
        # Use a case-sensitive collation for comparison
        cursor.execute("SELECT * FROM users WHERE username = ? COLLATE Latin1_General_CS_AS AND password = ?", (username, password,))
        user = cursor.fetchone()
        
        if user:
            session['userloggedin'] = True
            session['userid'] = user.id  # Assuming 'id' is the column name for the user ID
            session['username'] = user.username  # Assuming 'username' is the column name for the username
            message = 'Logged in successfully!'
            conn.close()  # Close the connection after use
            return redirect(url_for('reportpage'))
        else:
            # Close the connection in case of failure
            conn.close()
            # Display error message only when login attempt fails
            message = 'Invalid username or password'
            return render_template('userlogin.html', message=message)
    else:
        # Clear message variable when rendering login page without any error message
        message = ''
        # Prevent caching of the userlogin page
        response = make_response(render_template('userlogin.html', message=message))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response


@app.route('/data', methods=['POST'])
def get_data():
    department = request.form.get('department')
    if department not in ['Preparatory1', 'Preparatory2', 'Spinning1', 'Spinning2', 'LinkConer']:
        return jsonify({'error': 'Invalid department selected'}), 400

    from_date = request.form.get('from_date')
    to_date = request.form.get('to_date')
    time_difference = request.form.get('time_difference')

    if not from_date or not to_date:
        return jsonify({'error': 'From date and To date are required'})

    selected_fields_str = request.form.get('selected_fields')
    selected_fields = []
    if selected_fields_str:
        selected_fields = selected_fields_str.split(',')
    
    print(f"Selected fields from frontend: {selected_fields}") # Debug print

    table_prefixes = {
        'Preparatory1': {'rh': 'Prep1_RH', 'temp': 'Prep1_T'},
        'Preparatory2': {'rh': 'Prep2_RH', 'temp': 'Prep2_T'},
        'Spinning1': {'rh': 'Spg1_RH', 'temp': 'Spg1_T'},
        'Spinning2': {'rh': 'Spg2_RH', 'temp': 'Spg2_T'},
        'LinkConer' : {'rh': 'LC_RH', 'temp': 'LC_T'}
    }

    rh_table_name = table_prefixes[department]['rh']
    temp_table_name = table_prefixes[department]['temp']

    # --- NEW LOGIC STARTS HERE ---

    # Get the actual columns available in each table
    rh_actual_columns = fetch_column_names(rh_table_name)
    temp_actual_columns = fetch_column_names(temp_table_name)

    # Filter selected fields for each table
    rh_selected_cols_for_query = [col for col in selected_fields if col in rh_actual_columns]
    temp_selected_cols_for_query = [col for col in selected_fields if col in temp_actual_columns]

    print(f"RH table name: {rh_table_name}, Columns for RH query: {rh_selected_cols_for_query}") # Debug print
    print(f"Temp table name: {temp_table_name}, Columns for Temp query: {temp_selected_cols_for_query}") # Debug print

    rh_data = []
    temp_data = []

    # Only fetch if there are relevant columns for that table
    if rh_selected_cols_for_query or ('date' in rh_actual_columns and 'time' in rh_actual_columns):
        rh_data = fetch_data(rh_table_name, from_date, to_date, time_difference, rh_selected_cols_for_query)
    
    if temp_selected_cols_for_query or ('date' in temp_actual_columns and 'time' in temp_actual_columns):
        temp_data = fetch_data(temp_table_name, from_date, to_date, time_difference, temp_selected_cols_for_query)

    # --- END NEW LOGIC ---

    # Combine all unique selected columns (excluding 'idx', 'date', 'time' as they are always present)
    # The columns from the actual fetched data will be the ones to use for the header.
    all_fetched_columns = set()
    if rh_data:
        for key in rh_data[0].keys(): # Assuming all rows have the same keys
            all_fetched_columns.add(key)
    if temp_data:
        for key in temp_data[0].keys(): # Assuming all rows have the same keys
            all_fetched_columns.add(key)

    # Filter out 'idx' as it's typically an internal database ID and not for display
    # Keep the original order of 'selected_fields' as much as possible for display_columns
    display_columns = [col for col in selected_fields if col in all_fetched_columns and col not in ['idx']]
    
    # Ensure 'date' and 'time' are always first if they exist in the fetched data
    # Remove them first to re-insert them at the beginning
    if 'date' in display_columns:
        display_columns.remove('date')
    if 'time' in display_columns:
        display_columns.remove('time')
    
    # Add date and time to the beginning only if they were actually fetched and are relevant
    if 'time' in all_fetched_columns:
        display_columns.insert(0, 'time')
    if 'date' in all_fetched_columns:
        display_columns.insert(0, 'date')


    return jsonify({
        'rh_columns': display_columns, # These are the columns for the main table header
        'rh_data': rh_data,
        'temp_data': temp_data
    })


@app.route('/time-differences')
def get_time_differences():
    time_differences = ['10 minutes', '30 minutes', '1 hour', '2 hours', '5 hours', '1 Day']
    return jsonify({'time_differences': time_differences})


# Route to display users
@app.route('/users')
def index():
    # Check if the admin is logged in
    if 'loggedin' not in session:
        # If not logged in, redirect to the admin login page
        return redirect(url_for('login'))
    else:
        # If logged in, proceed to display users
        connection = create_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM users")
        data = cursor.fetchall()
        cursor.close()
        connection.close()
        # Prevent caching of the /users route
        response = make_response(render_template('index.html', users=data))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    

@app.route('/addadmin')
def addadmin():
    # Check if the admin is logged in
    if 'loggedin' not in session:
        # If not logged in, redirect to the admin login page
        return redirect(url_for('login'))
    else:
        connection = create_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM admins")
        data = cursor.fetchall()
        cursor.close()
        connection.close()
        # Prevent caching of the /addadmin route
        response = make_response(render_template('admin.html', admins=data))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response


@app.route('/admin', methods=['GET', 'POST'])
def admin():
    # Check if the admin is logged in
    # if 'loggedin' not in session:
    #     # If not logged in, redirect to the admin login page
    #     return redirect(url_for('login'))
    # else:
    message = 'Welcome to the admin page!'
    response = make_response(render_template('adminpage.html', message=message))
    # Add cache-control header to prevent caching
    response.headers['Cache-Control'] = 'no-store'
    return response


# Route to insert a new user
@app.route('/insert', methods=['POST'])
def insert():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        try:
            connection = create_connection()
            cursor = connection.cursor()
            cursor.execute("INSERT INTO users (username, password) VALUES (?, ?)", (username, password))
            connection.commit()
            flash("Data Inserted Successfully")
        except pyodbc.IntegrityError:
            flash("Username already exists. Please choose a different username.")
        finally:
            cursor.close()
            connection.close()
        return redirect(url_for('index'))


# Route to delete a user
@app.route('/delete/<string:id_data>', methods=['GET'])
def delete(id_data):
    flash("Record Has Been Deleted Successfully")
    connection = create_connection()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM users WHERE id=?", (id_data,))
    connection.commit()
    cursor.close()
    connection.close()
    return redirect(url_for('index'))

@app.route('/update/<int:id_data>', methods=['POST'])
def update(id_data):
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Check if the new username already exists in the database
        conn = create_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE username = ? AND id != ?", (username, id_data))
        existing_user = cursor.fetchone()
        cursor.close()

        if existing_user:
            flash("Username already exists. Please choose a different username.", "error")
            return redirect(url_for('index'))

        # Perform the update operation
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE users SET username=?, password=?
        WHERE id=?
        """, (username, password, id_data))
        conn.commit()
        flash("Data Updated Successfully")
        conn.close()  # Close the connection after use
        return redirect(url_for('index'))
    
    
# Route to insert a new user
@app.route('/admininsert', methods=['POST'])
def admininsert():
    if request.method == "POST":
        username = request.form['username']
        password = request.form['password']
        try:
            connection = create_connection()
            cursor = connection.cursor()
            cursor.execute("INSERT INTO admins (username, password) VALUES (?, ?)", (username, password))
            connection.commit()
            flash("Data Inserted Successfully")
        except pyodbc.IntegrityError:
            flash("Username already exists. Please choose a different username.")
        finally:
            cursor.close()
            connection.close()
        return redirect(url_for('addadmin'))


# Route to delete a user
@app.route('/admindelete/<string:id_data>', methods=['GET'])
def admindelete(id_data):
    flash("Record Has Been Deleted Successfully")
    connection = create_connection()
    cursor = connection.cursor()
    cursor.execute("DELETE FROM admins WHERE id=?", (id_data,))
    connection.commit()
    cursor.close()
    connection.close()
    return redirect(url_for('addadmin'))


@app.route('/adminupdate/<int:id_data>', methods=['POST'])
def adminupdate(id_data):
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        # Check if the new username already exists in the database
        conn = create_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins WHERE username = ? AND id != ?", (username, id_data))
        existing_user = cursor.fetchone()
        cursor.close()

        if existing_user:
            flash("Username already exists. Please choose a different username.", "error")
            return redirect(url_for('addadmin'))

        # Perform the update operation
        cursor = conn.cursor()
        cursor.execute("""
        UPDATE admins SET username=?, password=?
        WHERE id=?
        """, (username, password, id_data))
        conn.commit()
        flash("Data Updated Successfully")
        conn.close()  # Close the connection after use
        return redirect(url_for('addadmin'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    message = ''
    if request.method == 'POST' and 'username' in request.form and 'password' in request.form:
        username = request.form['username']
        password = request.form['password']
        
        # Establish a database connection
        conn = create_connection()
        cursor = conn.cursor()
        
        # Use a case-sensitive collation for comparison
        cursor.execute("SELECT * FROM admins WHERE username = ? COLLATE Latin1_General_CS_AS AND password = ?", (username, password,))
        user = cursor.fetchone()
        
        if user:
            session['loggedin'] = True
            session['userid'] = user.id  # Assuming 'id' is the column name for the user ID
            session['username'] = user.username  # Assuming 'username' is the column name for the username
            message = 'Logged in successfully!'
            conn.close()  # Close the connection after use
            return redirect(url_for('admin'))
        
        else:
            # Close the connection in case of failure
            conn.close()
            # Display error message only when login attempt fails
            message = 'Invalid username or password'
            return render_template('login.html', message=message)
    else:
        # Clear message variable when rendering login page without any error message
        message = ''
        # Prevent caching of the login page
        response = make_response(render_template('login.html', message=message))
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
        return response
    

@app.route('/logout')
def logout():
    session.pop('loggedin', None)
    session.pop('userid', None)
    session.pop('username', None)
    
    # Create a response to prevent caching of the /admin page after logout
    response = make_response(redirect(url_for('login')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response


@app.route('/userlogout')
def userlogout():
    # Clear user session data
    session.pop('userloggedin', None)
    session.pop('userid', None)
    session.pop('username', None)

    response = make_response(redirect(url_for('userlogin')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

if __name__ == '__main__':
    app.run(host="127.0.0.1", port=5001, debug=True)