from opcua import Client
import os
import yaml

OPC_UA_URL = "opc.tcp://127.0.0.1:4840"  # Replace with your OPC UA server URL
OUTPUT_FILE = "getNodeids.yaml"

def fetch_and_store_node_ids(url=OPC_UA_URL, output_file=OUTPUT_FILE):
    """
    Connects to an OPC UA server, browses its address space to find node IDs,
    and stores them in a YAML file.

    Args:
        url (str): The OPC UA server URL.  Defaults to OPC_UA_URL.
        output_file (str): The name of the YAML file to create.
    """
    opcua_client = Client(url)
    node_ids = {}
    try:
        opcua_client.connect()
        root = opcua_client.get_root_node()

        def browse_recursive(node):
            """Recursively browses the OPC UA address space."""
            for child in node.get_children():
                try:
                    node_name = child.get_browse_name().Name
                    node_id = str(child.nodeid)
                    node_ids[node_name] = node_id
                    browse_recursive(child)  # Traverse deeper
                except Exception as e:
                    print(f"Error browsing node {child}: {e}")

        browse_recursive(root)  # Start browsing from the root

        # Store the fetched node IDs in a YAML file
        yaml_path = os.path.join(os.path.dirname(__file__), output_file)
        data = {"node_ids": node_ids}
        with open(yaml_path, "w") as f:
            yaml.dump(data, f)
        print(f"Successfully fetched and stored {len(node_ids)} node IDs in {output_file}")

    except Exception as e:
        print(f"Error connecting or browsing OPC UA server: {e}")
    finally:
        try:
            opcua_client.disconnect()
        except Exception as disconnect_error:
            print(f"Error during client disconnect: {disconnect_error}")

if __name__ == "__main__":
    fetch_and_store_node_ids()
