import json
import uuid
import os
import copy

# 1. Define the Data (Department Name, Short Name)
departments = [
    ("Preparatory", "WCS_Prep"),
    ("Blowroom", "WCS_BR"),
    ("Card", "WCS_Card"),
    ('Comber', 'WCS_Comber')    
]

# 2. Configuration
SOURCE_FILE = 'spg1.json'
OUTPUT_DIR = 'generated_dashboards'

# Ensure output directory exists
if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

# 3. Load the Template
try:
    with open(SOURCE_FILE, 'r') as f:
        template_data = json.load(f)
except FileNotFoundError:
    print(f"Error: {SOURCE_FILE} not found. Please save your JSON code into this file.")
    exit()

print(f"Generating {len(departments)} dashboards based on {SOURCE_FILE}...\n")

# 4. Process each department
for dept_name, short_name in departments:
    
    # Create a deep copy of the template to avoid modifying the original for the next loop
    dashboard = copy.deepcopy(template_data)
    
    # Helper variables
    # The prompt asks for small letters for the variable/filename
    short_name_lower = short_name.lower() 
    
    # --- MODIFICATION 1: Update Dashboard Metadata ---
    dashboard['title'] = dept_name
    dashboard['uid'] = str(uuid.uuid4()) # Generate a unique ID
    dashboard['id'] = None # Set to None so Grafana creates a new internal ID on import
    
    # --- MODIFICATION 2: Update Templating (Variables) ---
    # We need to find the variable named "spinning2" and rename it
    if 'templating' in dashboard and 'list' in dashboard['templating']:
        for var in dashboard['templating']['list']:
            if var['name'] == 'spinning2':
                # Update variable name
                var['name'] = short_name_lower
                
                # Update the Query and Definition (Replace Spg2 with ShortName)
                # Query: SELECT table_name FROM ... LIKE 'Spg2%';
                if 'query' in var and isinstance(var['query'], str):
                    var['query'] = var['query'].replace('Spg2', short_name)
                
                if 'definition' in var and isinstance(var['definition'], str):
                    var['definition'] = var['definition'].replace('Spg2', short_name)
                    
                # Update the Label (optional, but good for UI)
                var['label'] = dept_name

    # --- MODIFICATION 3: Update Panels ---
    if 'panels' in dashboard:
        for panel in dashboard['panels']:
            
            # Update Panel Title: "spinning2 : $spinning2" -> "prep : $prep"
            if 'title' in panel:
                panel['title'] = panel['title'].replace('spinning2', short_name_lower)
            
            # Update SQL Queries inside Targets
            if 'targets' in panel:
                for target in panel['targets']:
                    if 'rawSql' in target:
                        sql = target['rawSql']
                        
                        # Replace the variable syntax: $spinning2 -> $prep
                        # Note: We replace the variable first
                        sql = sql.replace('$spinning2', f"${short_name_lower}")
                        
                        # Replace the SQL Table/Column prefix: Spg2 -> Prep
                        # Example: 'Spg2_RH' -> 'Prep_RH'
                        sql = sql.replace('Spg2', short_name)
                        
                        target['rawSql'] = sql

    # --- MODIFICATION 4: Save File ---
    filename = f"{short_name_lower}.json"
    file_path = os.path.join(OUTPUT_DIR, filename)
    
    with open(file_path, 'w') as f:
        json.dump(dashboard, f, indent=2)
        
    print(f"Created: {filename} (Title: {dept_name})")

print(f"\nSuccess! All files saved in '{OUTPUT_DIR}' folder.")