import os
import sqlite3
import hashlib
import secrets
from flask import Flask, request, jsonify, session, send_from_directory

app = Flask(__name__, static_folder='.')
# Generate or load a persistent secret key for Flask sessions
DB_FILE = 'admin.db'

def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Create admin table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL
        )
    ''')
    
    # Create configuration table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    ''')
    
    # Create channels table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            logo TEXT,
            cookie TEXT,
            group_name TEXT DEFAULT 'General'
        )
    ''');
    
    # Check if admin user exists, if not create default
    cursor.execute('SELECT * FROM admins WHERE username = ?', ('admin',))
    if not cursor.fetchone():
        # Generate salt and PBKDF2 hash for "adminpass123"
        salt = secrets.token_hex(16)
        password = "adminpass123"
        pw_hash = hashlib.pbkdf2_hmac(
            'sha256', 
            password.encode('utf-8'), 
            salt.encode('utf-8'), 
            100000
        ).hex()
        
        cursor.execute(
            'INSERT INTO admins (username, password_hash, salt) VALUES (?, ?, ?)',
            ('admin', pw_hash, salt)
        )
        print("[DB] Initialized default admin credentials (username: admin, password: adminpass123)")
    
    # Initialize default configuration if not present
    cursor.execute('SELECT * FROM config WHERE key = ?', ('stream_config',))
    if not cursor.fetchone():
        import json
        default_config = {
            "useCredentials": False,
            "corsProxy": "",
            "hlsEngine": "shaka",
            "autoReconnect": True,
            "ch1": {
                "name": "Select a Channel",
                "rawUrl": ""
            }
        }
        cursor.execute(
            'INSERT INTO config (key, value) VALUES (?, ?)',
            ('stream_config', json.dumps(default_config))
        )
        print("[DB] Initialized default stream configuration.")
        
    # Populate channels from ms3 M3U if empty
    cursor.execute('SELECT COUNT(*) FROM channels')
    if cursor.fetchone()[0] == 0:
        if os.path.exists('ms3'):
            print("[DB] Migrating channels from M3U file into SQLite database...")
            with open('ms3', 'r', encoding='utf-8') as f:
                lines = f.readlines()
            current_ch = None
            for line in lines:
                line = line.strip()
                if line.startswith('#EXTINF:'):
                    current_ch = {}
                    import re
                    logo_match = re.search(r'tvg-logo="([^"]+)"', line)
                    current_ch['logo'] = logo_match.group(1) if logo_match else ""
                    
                    group_match = re.search(r'group-title="([^"]+)"', line)
                    current_ch['group_name'] = group_match.group(1) if group_match else "General"
                    
                    comma_idx = line.rfind(',')
                    current_ch['name'] = line[comma_idx + 1:].strip() if comma_idx != -1 else "Unnamed Channel"
                elif line and not line.startswith('#'):
                    if current_ch:
                        parts = line.split('|cookie=')
                        url = parts[0].strip()
                        cookie = parts[1].strip() if len(parts) > 1 else ""
                        cursor.execute(
                            'INSERT INTO channels (name, url, logo, cookie, group_name) VALUES (?, ?, ?, ?, ?)',
                            (current_ch['name'], url, current_ch['logo'], cookie, current_ch['group_name'])
                        )
                        current_ch = None
            print("[DB] Channel migration completed.")

    # Populate WC channels
    wc_channels = [
        ("ENGLISH", "https://fifa-world-cup-live.pages.dev/english.html", "WC"),
        ("CAZE TV", "https://fifa-world-cup-live.pages.dev/caze-tv.html", "WC"),
        ("HINDI", "https://fifa-world-cup-live.pages.dev/hindi.html", "WC"),
        ("TSN", "https://fifa-world-cup-live.pages.dev/tsn.html", "WC"),
        ("FIFA", "https://fifa-world-cup-live.pages.dev/fifa1.html", "WC"),
        ("IOS", "https://fifa-world-cup-live.pages.dev/benin.html", "WC"),
        ("BEIN", "https://fifa-world-cup-live.pages.dev/ios.html", "WC")
    ]
    for name, url, group in wc_channels:
        cursor.execute('SELECT * FROM channels WHERE url = ?', (url,))
        if not cursor.fetchone():
            cursor.execute(
                'INSERT INTO channels (name, url, logo, cookie, group_name) VALUES (?, ?, ?, ?, ?)',
                (name, url, "", "", group)
            )
            print(f"[DB] Initialized new WC channel: {name}")

    # Generate or get session secret key
    cursor.execute('SELECT * FROM config WHERE key = ?', ('secret_key',))
    row = cursor.fetchone()
    if not row:
        secret_key = secrets.token_hex(32)
        cursor.execute('INSERT INTO config (key, value) VALUES (?, ?)', ('secret_key', secret_key))
        app.secret_key = secret_key
    else:
        app.secret_key = row['value']
        
    conn.commit()
    conn.close()

# Configure session cookies for security
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    SESSION_COOKIE_SECURE=False  # Set to True if running HTTPS
)

# Route to serve frontend static files
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/styles.css')
def styles():
    return send_from_directory('.', 'styles.css')

@app.route('/app.js')
def app_js():
    return send_from_directory('.', 'app.js')

@app.route('/ms3')
def playlist():
    return send_from_directory('.', 'ms3')

@app.route('/favicon.ico')
def favicon():
    return '', 204

# Admin Login Endpoint
@app.route('/api/auth/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    username = data.get('username')
    password = data.get('password')
    
    if not username or not password:
        return jsonify({"success": False, "message": "Username and password required"}), 400
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM admins WHERE username = ?', (username,))
    user = cursor.fetchone()
    conn.close()
    
    if not user:
        return jsonify({"success": False, "message": "Invalid credentials"}), 401
        
    # Verify password with stored salt & hash
    salt = user['salt']
    stored_hash = user['password_hash']
    
    calc_hash = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        100000
    ).hex()
    
    if secrets.compare_digest(calc_hash, stored_hash):
        session['logged_in'] = True
        session['username'] = username
        return jsonify({"success": True, "message": "Login successful"})
    else:
        return jsonify({"success": False, "message": "Invalid credentials"}), 401

# Admin Logout Endpoint
@app.route('/api/auth/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({"success": True, "message": "Logged out successfully"})

# Auth Status Check Endpoint
@app.route('/api/auth/status', methods=['GET'])
def auth_status():
    if session.get('logged_in'):
        return jsonify({"authenticated": True, "username": session.get('username')})
    return jsonify({"authenticated": False})

# Admin Change Password Endpoint
@app.route('/api/auth/change-password', methods=['POST'])
def change_password():
    if not session.get('logged_in'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    new_password = data.get('password')
    
    if not new_password or len(new_password) < 6:
        return jsonify({"success": False, "message": "Password must be at least 6 characters long"}), 400
        
    username = session.get('username')
    new_salt = secrets.token_hex(16)
    new_hash = hashlib.pbkdf2_hmac(
        'sha256',
        new_password.encode('utf-8'),
        new_salt.encode('utf-8'),
        100000
    ).hex()
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE admins SET password_hash = ?, salt = ? WHERE username = ?',
        (new_hash, new_salt, username)
    )
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": "Password updated successfully"})

# Get Stream Config Endpoint
@app.route('/api/config', methods=['GET'])
def get_config():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT value FROM config WHERE key = ?', ('stream_config',))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        import json
        return jsonify(json.loads(row['value']))
    return jsonify({"error": "Config not found"}), 404

# Update Stream Config Endpoint
@app.route('/api/config', methods=['POST'])
def update_config():
    if not session.get('logged_in'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    new_config = request.get_json()
    if not new_config:
        return jsonify({"success": False, "message": "Invalid config data"}), 400
        
    # Basic Validation
    if 'ch1' not in new_config or 'name' not in new_config['ch1'] or 'rawUrl' not in new_config['ch1']:
        return jsonify({"success": False, "message": "Invalid channel configuration structure"}), 400
        
    import json
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'UPDATE config SET value = ? WHERE key = ?',
        (json.dumps(new_config), 'stream_config')
    )
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": "Configuration saved successfully"})

# GET all playlist channels
@app.route('/api/channels', methods=['GET'])
def get_channels():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM channels ORDER BY id ASC')
    rows = cursor.fetchall()
    conn.close()
    
    channels = []
    for r in rows:
        # Reconstruct the raw URL with cookie block if present
        raw_url = r['url']
        if r['cookie']:
            raw_url += f"|cookie={r['cookie']}"
        channels.append({
            "id": r['id'],
            "name": r['name'],
            "url": raw_url,
            "logo": r['logo'],
            "group_name": r['group_name'] if 'group_name' in r.keys() else "General"
        })
    return jsonify(channels)

# ADD a new playlist channel (Admin only)
@app.route('/api/channels', methods=['POST'])
def add_channel():
    if not session.get('logged_in'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    data = request.get_json() or {}
    name = data.get('name')
    raw_url = data.get('url')
    logo = data.get('logo', '')
    group_name = data.get('group_name', 'WC')
    
    if not name or not raw_url:
        return jsonify({"success": False, "message": "Channel name and URL are required"}), 400
        
    parts = raw_url.split('|cookie=')
    url = parts[0].strip()
    cookie = parts[1].strip() if len(parts) > 1 else ""
    
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT INTO channels (name, url, logo, cookie, group_name) VALUES (?, ?, ?, ?, ?)',
        (name, url, logo, cookie, group_name)
    )
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": "Channel added successfully"})

# DELETE a playlist channel (Admin only)
@app.route('/api/channels/<int:channel_id>', methods=['DELETE'])
def delete_channel(channel_id):
    if not session.get('logged_in'):
        return jsonify({"success": False, "message": "Unauthorized"}), 401
        
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('DELETE FROM channels WHERE id = ?', (channel_id,))
    conn.commit()
    conn.close()
    
    return jsonify({"success": True, "message": "Channel deleted successfully"})

# Hidden Admin Panel Route
@app.route('/adminnigga')
def adminnigga():
    return send_from_directory('.', 'index.html')

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=8080, debug=True)
