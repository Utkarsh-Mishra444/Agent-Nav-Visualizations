from http.server import SimpleHTTPRequestHandler, HTTPServer
import os
import json
from urllib.parse import parse_qs, urlparse, unquote

# Load .env file if present (optional)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Set the working directory to where server.py lives
os.chdir(os.path.dirname(__file__))

def find_experiment_folders(base_dir='.', max_depth=10):
    """
    Recursively find all experiment folders.
    An experiment folder is one that contains a file starting with 'visited_coordinates_'
    or has openai_calls/gemini_calls directories.
    
    Returns list of paths relative to the base_dir.
    """
    if max_depth <= 0:
        return []
    
    experiments = []
    
    for item in os.listdir(base_dir):
        full_path = os.path.join(base_dir, item)
        rel_path = os.path.relpath(full_path, '.')
        
        if os.path.isdir(full_path):
            # Check if this directory is an experiment by looking for visited_coordinates files
            is_experiment = any(f.startswith('visited_coordinates_') for f in os.listdir(full_path))
            
            # Also check for API call directories
            has_api_calls = (os.path.exists(os.path.join(full_path, 'openai_calls')) or 
                           os.path.exists(os.path.join(full_path, 'gemini_calls')))
            
            if is_experiment or has_api_calls:
                experiments.append(rel_path)
            else:
                # Recursively search subdirectories
                sub_experiments = find_experiment_folders(full_path, max_depth - 1)
                experiments.extend(sub_experiments)
    
    return experiments

def is_experiment_folder(folder_path):
    """
    Check if a folder is an experiment folder.
    """
    if not os.path.isdir(folder_path):
        return False
        
    # Check if directory contains visited_coordinates files
    has_visited_file = any(f.startswith('visited_coordinates_') for f in os.listdir(folder_path))
    
    # Check for API call directories
    has_api_calls = (os.path.exists(os.path.join(folder_path, 'openai_calls')) or 
                    os.path.exists(os.path.join(folder_path, 'gemini_calls')))
    
    return has_visited_file or has_api_calls

def is_successful_experiment(folder_path):
    """
    Check if an experiment was successful by looking for the success message in log files.
    Success is determined by finding "Reached within 50 meters of destination after" in any log file.
    """
    if not os.path.isdir(folder_path):
        return False
    
    # Look for terminal log files (terminal_output_*.log)
    log_files = [f for f in os.listdir(folder_path) if f.startswith('terminal_output_') and f.endswith('.log')]
    
    for log_file in log_files:
        log_path = os.path.join(folder_path, log_file)
        try:
            # Read the last 50 lines of the file to find the success message
            # This is more efficient than reading the entire file
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                # Read all lines but only keep the last 50
                lines = f.readlines()
                last_lines = lines[-50:] if len(lines) > 50 else lines
                
                # Check if the success message is in any of these lines
                if any("Reached within 50 meters of destination after" in line for line in last_lines):
                    return True
        except Exception as e:
            print(f"Error reading log file {log_path}: {e}")
    
    return False

def get_directory_contents(directory='.'):
    """
    Get folders and experiments in a specific directory.
    Returns a dictionary with 'folders', 'experiments', and 'total_experiments' count.
    'total_experiments' includes experiments in the current directory and all subdirectories.
    """
    if not os.path.isdir(directory):
        return {'folders': [], 'experiments': [], 'total_experiments': 0}
    
    contents = {'folders': [], 'experiments': [], 'total_experiments': 0, 'successful_experiments': 0}
    folders_with_success = set()
    
    # First get immediate contents
    for item in os.listdir(directory):
        full_path = os.path.join(directory, item)
        rel_path = os.path.relpath(full_path, '.')
        
        if os.path.isdir(full_path):
            if is_experiment_folder(full_path):
                # Check if this experiment was successful
                is_successful = is_successful_experiment(full_path)
                
                contents['experiments'].append({
                    'path': rel_path,
                    'successful': is_successful
                })
                contents['total_experiments'] += 1
                
                if is_successful:
                    contents['successful_experiments'] += 1
            else:
                # Recursively check subdirectories
                sub_contents = get_directory_contents(full_path)
                
                # Determine if this directory is a meta-run (marker file presence)
                is_meta = os.path.exists(os.path.join(full_path, 'is_meta.txt'))
                folder_info = {
                    'path': rel_path,
                    'has_successful': sub_contents['successful_experiments'] > 0,
                    'is_meta': is_meta
                }
                
                contents['folders'].append(folder_info)
                contents['total_experiments'] += sub_contents['total_experiments']
                contents['successful_experiments'] += sub_contents['successful_experiments']
    
    # Sort alphabetically by path
    contents['folders'].sort(key=lambda x: x['path'])
    contents['experiments'].sort(key=lambda x: x['path'])
    
    return contents

class CustomHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        # Disable caching to reflect file changes immediately
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma', 'no-cache')
        self.send_header('Expires', '0')
        super().end_headers()

    # Accept both /experiments and /experiments/ paths
    def _is_experiments(self, path: str) -> bool:
        return path.rstrip('/') == '/experiments'
    
    def do_GET(self):
        parsed_path = urlparse(self.path)

        # Serve Google Maps API key to the frontend (avoid hardcoding keys in HTML).
        if parsed_path.path == '/config':
            key = (os.environ.get('GOOGLE_MAPS_API_KEY') or '').strip()
            payload = {"googleMapsApiKey": key}
            if not key:
                payload["error"] = "Missing GOOGLE_MAPS_API_KEY in environment"
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())
            return
        
        # Debug endpoint to list directory structure
        if parsed_path.path == '/debug-dirs':
            query = parse_qs(parsed_path.query)
            base_dir = query.get('dir', ['.'])[0]
            
            try:
                debug_info = {}
                if os.path.exists(base_dir):
                    # List all subdirectories recursively up to 3 levels deep
                    for root, dirs, files in os.walk(base_dir):
                        level = root.replace(base_dir, '').count(os.sep)
                        if level < 3:  # Limit depth
                            rel_path = os.path.relpath(root, base_dir)
                            debug_info[rel_path] = {
                                'dirs': dirs[:10],  # First 10 directories
                                'files': [f for f in files if f.endswith(('.json', '.txt'))][:5]  # First 5 relevant files
                            }
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(debug_info, indent=2).encode())
                return
            except Exception as e:
                self.send_response(500)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"Debug error: {str(e)}".encode())
                return
        
        # Endpoint to list all experiment directories recursively (flat list)
        if self._is_experiments(parsed_path.path):
            # List all experiment folders, optionally scoped to a base directory
            query = parse_qs(parsed_path.query)
            base_dir = query.get('dir', ['.'])[0]
            experiments = find_experiment_folders(base_dir)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(experiments).encode())
        
        # New endpoint to get directory contents (folders and experiments)
        elif parsed_path.path == '/directory-contents':
            query = parse_qs(parsed_path.query)
            directory = query.get('dir', ['.'])[0]
            
            # Security check to prevent directory traversal
            normalized_path = os.path.normpath(directory)
            if normalized_path.startswith('..'):
                self.send_response(403)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(b"Access denied: Cannot access parent directories")
                return
                
            contents = get_directory_contents(normalized_path)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(contents).encode())
        
        # Endpoint to list files in experiment directory
        elif parsed_path.path == '/files':
            query = parse_qs(parsed_path.query)
            exp = query.get('exp', [''])[0]
            # URL decode the experiment path to handle spaces and special characters
            exp_dir = unquote(exp)  # Use the experiment directory directly
            # Handle potential double encoding
            if '%' in exp_dir:
                exp_dir = unquote(exp_dir)
            # Handle Windows path separators that may still be encoded
            exp_dir = exp_dir.replace('\\', os.path.sep).replace('%5C', os.path.sep)
            print(f"DEBUG: Files endpoint - Original exp: {exp}")
            print(f"DEBUG: Files endpoint - Decoded exp_dir: {exp_dir}")
            print(f"DEBUG: Files endpoint - Directory exists: {os.path.exists(exp_dir)}")
            
            if os.path.exists(exp_dir):
                # Only include files starting with 'visited_coordinates_'
                files = [f for f in os.listdir(exp_dir) if f.startswith('visited_coordinates')]
                print(f"DEBUG: Files endpoint - Found {len(files)} visited_coordinates files: {files}")
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(files).encode())
            else:
                print(f"DEBUG: Files endpoint - Directory not found: {exp_dir}")
                
                # Try to find similar directories for debugging
                parent_dir = os.path.dirname(exp_dir)
                if os.path.exists(parent_dir):
                    similar_dirs = []
                    for item in os.listdir(parent_dir):
                        item_path = os.path.join(parent_dir, item)
                        if os.path.isdir(item_path):
                            similar_dirs.append(item)
                    print(f"DEBUG: Available directories in {parent_dir}: {similar_dirs[:10]}...")  # Show first 10
                    
                    # Look for directories with similar names
                    exp_basename = os.path.basename(exp_dir).lower()
                    matching_dirs = [d for d in similar_dirs if exp_basename[:20] in d.lower()]
                    if matching_dirs:
                        print(f"DEBUG: Directories with similar names: {matching_dirs}")
                
                self.send_response(404)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"Directory {exp_dir} not found".encode())
        
        # Endpoint to list API call files in an experiment's openai_calls, gemini_calls, or self_position_calls directory
        elif (
            parsed_path.path.endswith('/openai_calls/') or
            parsed_path.path.endswith('/gemini_calls/') or
            parsed_path.path.endswith('/self_position_calls/')
        ):
            try:
                # URL decode the path to handle spaces and special characters
                # Handle potential double encoding by decoding twice if necessary
                decoded_path = unquote(parsed_path.path)
                if '%' in decoded_path:
                    decoded_path = unquote(decoded_path)  # Decode again if still encoded
                print(f"DEBUG: Original path: {parsed_path.path}")
                print(f"DEBUG: Decoded path: {decoded_path}")
                
                # Extract the experiment name and API type from the path
                path_parts = decoded_path.strip('/').split('/')
                if len(path_parts) >= 2:
                    # api_type will be 'openai_calls', 'gemini_calls', or the new 'self_position_calls'
                    api_type = path_parts[-1]
                    exp_path = '/'.join(path_parts[:-1])  # All parts before the last one
                    
                    print(f"DEBUG: Experiment path: {exp_path}")
                    print(f"DEBUG: API type: {api_type}")
                    
                    # Convert forward slashes to OS-specific path separators
                    # Also handle any remaining encoded backslashes from Windows paths
                    exp_path_clean = exp_path.replace('\\', '/').replace('%5C', '/')
                    api_dir = os.path.join(*exp_path_clean.split('/'), api_type)
                    print(f"DEBUG: Looking for directory: {api_dir}")
                    print(f"DEBUG: Directory exists: {os.path.exists(api_dir)}")
                    
                    if os.path.exists(api_dir):
                        # List all JSON files in the directory
                        files = [f for f in os.listdir(api_dir) if f.endswith('.json')]
                        print(f"DEBUG: Found {len(files)} JSON files: {files[:5]}...")  # Show first 5 files
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        self.wfile.write(json.dumps(files).encode())
                    else:
                        print(f"DEBUG: Directory not found: {api_dir}")
                        # Try to list what's actually in the parent directory for debugging
                        parent_dir = os.path.dirname(api_dir)
                        if os.path.exists(parent_dir):
                            available_dirs = [d for d in os.listdir(parent_dir) if os.path.isdir(os.path.join(parent_dir, d))]
                            print(f"DEBUG: Available directories in {parent_dir}: {available_dirs}")
                        
                        self.send_response(404)
                        self.send_header('Content-type', 'text/plain')
                        self.end_headers()
                        self.wfile.write(f"Directory {api_dir} not found".encode())
                else:
                    self.send_response(400)
                    self.send_header('Content-type', 'text/plain')
                    self.end_headers()
                    self.wfile.write(b"Invalid path format")
            except Exception as e:
                print(f"DEBUG: Exception in API calls endpoint: {str(e)}")
                self.send_response(500)
                self.send_header('Content-type', 'text/plain')
                self.end_headers()
                self.wfile.write(f"Error: {str(e)}".encode())
        
        # Serve static files (like index.html) and JSON files directly
        else:
            # Handle file requests with URL decoding for complex paths
            if self.path.endswith('.json'):
                try:
                    # URL decode the entire path
                    decoded_path = unquote(self.path)
                    print(f"DEBUG: Static file request - Original: {self.path}")
                    print(f"DEBUG: Static file request - Decoded: {decoded_path}")
                    
                    # Remove leading slash and convert to OS path
                    file_path = decoded_path.lstrip('/')
                    file_path = file_path.replace('/', os.path.sep).replace('\\', os.path.sep)
                    
                    print(f"DEBUG: Static file request - Final path: {file_path}")
                    print(f"DEBUG: Static file request - Exists: {os.path.exists(file_path)}")
                    
                    if os.path.exists(file_path):
                        self.send_response(200)
                        self.send_header('Content-type', 'application/json')
                        self.end_headers()
                        with open(file_path, 'rb') as f:
                            self.wfile.write(f.read())
                        return
                    else:
                        # Try to list parent directory for debugging
                        parent_dir = os.path.dirname(file_path)
                        if os.path.exists(parent_dir):
                            files_in_parent = [f for f in os.listdir(parent_dir) if f.endswith('.json')]
                            print(f"DEBUG: Parent directory {parent_dir} contains JSON files: {files_in_parent}")
                        
                        print(f"DEBUG: File not found: {file_path}")
                except Exception as e:
                    print(f"DEBUG: Error in static file handling: {e}")
            
            super().do_GET()
    
    def do_HEAD(self):
        parsed_path = urlparse(self.path)
        if self._is_experiments(parsed_path.path):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
        else:
            super().do_HEAD()

# Start the server on port 9000
server = HTTPServer(('localhost', 8000), CustomHandler)
print("Server running at http://localhost:8000")
server.serve_forever()
