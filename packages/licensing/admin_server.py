"""
AI Video Flow - SaaS Admin Server & Web Licensing Portal
Provides complete administrative control:
- Generate new license keys with custom wedding credits or unlimited plans
- Search and manage active client licenses
- Reset machine locks (when a videographer switches their laptop/PC)
- Recharge or revoke credits
- Live verification and credit consumption API for the desktop app
- Runs on local port (e.g. 8899) or can be deployed to any VPS/cloud server (Railway, Render, DigitalOcean, cPanel)
"""

import os
import sys
import json
import time
import sqlite3
import hashlib
import secrets
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from typing import Dict, Any, List, Optional

PORT = int(os.environ.get("ADMIN_PORT", 8899))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")  # Can be changed via env
DB_FILE = os.path.join(os.path.expanduser("~/.ai-video-flow"), "admin_licenses.db")


def init_db():
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            client_name TEXT,
            client_email TEXT,
            tier TEXT DEFAULT 'premium',
            tier_display TEXT DEFAULT '👑 AI Smart Premium',
            credits INTEGER DEFAULT 10,
            plan_type TEXT DEFAULT 'per_wedding',
            bound_machine_id TEXT,
            status TEXT DEFAULT 'active',
            created_at INTEGER,
            expires_at INTEGER,
            notes TEXT
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT,
            machine_id TEXT,
            job_name TEXT,
            timestamp INTEGER
        )
    """)
    conn.commit()
    conn.close()


def generate_license_key(prefix: str = "AIFLOW-PREM", credits: int = 10) -> str:
    rnd = secrets.token_hex(4).upper()
    return f"{prefix}-{credits}-{rnd[:4]}-{rnd[4:]}"


class AdminRequestHandler(SimpleHTTPRequestHandler):
    def _json(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(data, indent=2).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/admin"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            html = get_admin_html()
            self.wfile.write(html.encode("utf-8"))
        elif self.path == "/api/admin/licenses":
            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM licenses ORDER BY id DESC")
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._json({"success": True, "licenses": rows})
        else:
            self._json({"error": "Not Found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        # 1. Desktop App Verification Endpoint
        if self.path in ("/v1/license/verify", "/api/v1/license/verify"):
            key = data.get("license_key", "").strip().upper()
            machine_id = data.get("machine_id", "").strip().upper()

            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
            row = c.fetchone()

            if not row:
                conn.close()
                self._json({"success": False, "error": "Invalid License Key."}, 400)
                return

            lic = dict(row)
            if lic["status"] != "active":
                conn.close()
                self._json({"success": False, "error": f"License is currently {lic['status']}."}, 403)
                return

            # Check / Bind machine hardware ID
            bound = lic["bound_machine_id"]
            if not bound:
                c.execute("UPDATE licenses SET bound_machine_id = ? WHERE license_key = ?", (machine_id, key))
                conn.commit()
                bound = machine_id
            elif bound != machine_id:
                conn.close()
                self._json({
                    "success": False,
                    "error": "This license is bound to another PC. Please contact admin to reset machine lock."
                }, 403)
                return

            conn.close()
            self._json({
                "success": True,
                "tier": lic["tier"],
                "tier_display": lic["tier_display"],
                "credits": lic["credits"],
                "plan_type": lic["plan_type"],
                "expires_at": lic["expires_at"]
            })

        # 2. Desktop App Credit Consumption Endpoint
        elif self.path in ("/v1/license/consume", "/api/v1/license/consume"):
            key = data.get("license_key", "").strip().upper()
            machine_id = data.get("machine_id", "")
            job_name = data.get("job_name", "Wedding Edit")

            conn = sqlite3.connect(DB_FILE)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM licenses WHERE license_key = ?", (key,))
            row = c.fetchone()

            if not row:
                conn.close()
                self._json({"success": False, "error": "Invalid license"}, 400)
                return

            lic = dict(row)
            if lic["plan_type"] != "unlimited":
                if lic["credits"] <= 0:
                    conn.close()
                    self._json({"success": False, "error": "0 credits remaining"}, 400)
                    return
                c.execute("UPDATE licenses SET credits = credits - 1 WHERE license_key = ?", (key,))
                rem = lic["credits"] - 1
            else:
                rem = 9999

            c.execute("INSERT INTO usage_logs (license_key, machine_id, job_name, timestamp) VALUES (?, ?, ?, ?)",
                      (key, machine_id, job_name, int(time.time())))
            conn.commit()
            conn.close()

            self._json({"success": True, "remaining": rem})

        # 3. Admin: Generate New License
        elif self.path == "/api/admin/create-license":
            name = data.get("client_name", "Valued Client").strip()
            email = data.get("client_email", "").strip()
            credits = int(data.get("credits", 10))
            plan_type = "unlimited" if credits >= 999 else "per_wedding"
            tier = "premium"
            tier_display = f"👑 AI Smart Premium ({'Unlimited' if credits >= 999 else str(credits) + ' Weddings'})"
            key = generate_license_key(credits=credits)
            now = int(time.time())
            expires = now + (86400 * 365)  # 1 year

            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("""
                INSERT INTO licenses (license_key, client_name, client_email, tier, tier_display, credits, plan_type, status, created_at, expires_at, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """, (key, name, email, tier, tier_display, credits, plan_type, now, expires, data.get("notes", "")))
            conn.commit()
            conn.close()

            self._json({"success": True, "message": "License generated successfully!", "license_key": key})

        # 4. Admin: Add Credits to License
        elif self.path == "/api/admin/add-credits":
            key = data.get("license_key", "").strip().upper()
            add_credits = int(data.get("add_credits", 5))
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("UPDATE licenses SET credits = credits + ? WHERE license_key = ?", (add_credits, key))
            conn.commit()
            conn.close()
            self._json({"success": True, "message": f"Added {add_credits} wedding credits to {key}!"})

        # 5. Admin: Reset Machine Lock
        elif self.path == "/api/admin/reset-machine":
            key = data.get("license_key", "").strip().upper()
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("UPDATE licenses SET bound_machine_id = NULL WHERE license_key = ?", (key,))
            conn.commit()
            conn.close()
            self._json({"success": True, "message": f"Machine hardware lock reset for {key}. Client can now activate on a new PC!"})

        # 6. Admin: Toggle Status (Activate / Revoke)
        elif self.path == "/api/admin/toggle-status":
            key = data.get("license_key", "").strip().upper()
            new_status = data.get("status", "revoked")
            conn = sqlite3.connect(DB_FILE)
            c = conn.cursor()
            c.execute("UPDATE licenses SET status = ? WHERE license_key = ?", (new_status, key))
            conn.commit()
            conn.close()
            self._json({"success": True, "message": f"License {key} is now {new_status}."})

        else:
            self._json({"error": "Unknown API"}, 404)


def get_admin_html() -> str:
    return """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>AI Video Flow – SaaS Admin License Portal</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <style>
    :root {
      --bg: #090d16;
      --card: #131b2e;
      --card-border: #1e293b;
      --accent: #f59e0b;
      --accent-glow: rgba(245, 158, 11, 0.2);
      --text: #f8fafc;
      --muted: #94a3b8;
      --success: #10b981;
      --danger: #ef4444;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font-family: 'Plus Jakarta Sans', sans-serif;
      padding: 30px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 30px;
      padding-bottom: 20px;
      border-bottom: 1px solid var(--card-border);
    }
    .brand h1 { margin: 0; font-size: 24px; color: var(--accent); }
    .brand p { margin: 4px 0 0 0; color: var(--muted); font-size: 13px; }
    .grid { display: grid; grid-template-columns: 360px 1fr; gap: 24px; }
    .card {
      background: var(--card);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 24px;
    }
    .card h2 { margin-top: 0; font-size: 18px; margin-bottom: 16px; border-bottom: 1px solid var(--card-border); padding-bottom: 10px; }
    label { display: block; font-size: 12px; color: var(--muted); margin-bottom: 6px; font-weight: 600; }
    input, select, textarea {
      width: 100%;
      background: #0b1120;
      border: 1px solid #334155;
      color: #fff;
      padding: 10px 12px;
      border-radius: 8px;
      font-family: inherit;
      font-size: 13px;
      margin-bottom: 14px;
    }
    input:focus, select:focus { outline: none; border-color: var(--accent); }
    .btn {
      background: var(--accent);
      color: #000;
      font-weight: 700;
      border: none;
      padding: 12px 18px;
      border-radius: 8px;
      cursor: pointer;
      width: 100%;
      font-size: 14px;
      transition: all 0.2s;
    }
    .btn:hover { background: #d97706; }
    .btn-sm { padding: 4px 10px; font-size: 11px; width: auto; font-weight: 600; }
    .btn-outline { background: transparent; border: 1px solid var(--accent); color: var(--accent); }
    .btn-outline:hover { background: var(--accent-glow); }
    .btn-danger { background: transparent; border: 1px solid var(--danger); color: var(--danger); }
    .btn-danger:hover { background: rgba(239, 68, 68, 0.15); }
    table { width: 100%; border-collapse: collapse; font-size: 13px; margin-top: 10px; }
    th { text-align: left; color: var(--muted); padding: 10px; border-bottom: 1px solid var(--card-border); font-size: 11px; text-transform: uppercase; }
    td { padding: 12px 10px; border-bottom: 1px solid rgba(255,255,255,0.05); }
    .key-badge {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      background: #0b1120;
      padding: 4px 8px;
      border-radius: 6px;
      color: #38bdf8;
      border: 1px solid #1e293b;
      cursor: pointer;
    }
    .status-badge {
      padding: 3px 8px;
      border-radius: 12px;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .status-active { background: rgba(16, 185, 129, 0.2); color: #10b981; border: 1px solid #10b981; }
    .status-revoked { background: rgba(239, 68, 68, 0.2); color: #ef4444; border: 1px solid #ef4444; }
    .stat-pill { background: #1e293b; padding: 6px 14px; border-radius: 20px; font-size: 12px; color: var(--muted); }
  </style>
</head>
<body>
  <div class="header">
    <div class="brand">
      <h1>AI Video Flow – SaaS Admin Portal</h1>
      <p>Issue licenses, manage wedding credits, and unlock machine hardware bindings for client PCs.</p>
    </div>
    <div style="display:flex;gap:12px;">
      <div class="stat-pill">Total Issued: <strong id="total-count" style="color:#fff;">0</strong></div>
      <div class="stat-pill">Portal Port: <strong style="color:var(--accent);">8899</strong></div>
    </div>
  </div>

  <div class="grid">
    <!-- Left: Generate License Form -->
    <div class="card">
      <h2>✨ Issue New License Key</h2>
      <form id="form-create">
        <label>Client Name / Studio Name</label>
        <input type="text" id="inp-name" placeholder="e.g. Rahul Photography" required>

        <label>Client Email</label>
        <input type="email" id="inp-email" placeholder="rahul@example.com">

        <label>Wedding Plan / Credits</label>
        <select id="inp-credits">
          <option value="1">1 Wedding Shoot (Trial / Demo)</option>
          <option value="5">5 Wedding Shoots (₹2,499)</option>
          <option value="10" selected>10 Wedding Shoots (₹4,499 Popular)</option>
          <option value="25">25 Wedding Shoots (₹9,999 Studio)</option>
          <option value="50">50 Wedding Shoots (₹17,999)</option>
          <option value="999">👑 Unlimited Agency (1-Year Pass)</option>
        </select>

        <label>Notes / Order ID (Optional)</label>
        <input type="text" id="inp-notes" placeholder="WooCommerce Order #1042">

        <button type="submit" class="btn">🚀 Generate &amp; Issue Key</button>
      </form>

      <div id="issued-key-box" style="display:none;margin-top:16px;background:#0b1120;border:1px dashed var(--accent);border-radius:10px;padding:14px;text-align:center;">
        <span style="font-size:11px;color:var(--muted);display:block;margin-bottom:4px;">NEW LICENSE GENERATED:</span>
        <code id="issued-key-text" style="font-size:14px;color:var(--accent);font-weight:700;word-break:break-all;">...</code>
        <button class="btn btn-sm btn-outline" style="margin-top:8px;" onclick="copyIssuedKey()">📋 Copy Key</button>
      </div>
    </div>

    <!-- Right: All Licenses Table -->
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:14px;">
        <h2 style="margin:0;border:none;padding:0;">Active Licenses &amp; Client Database</h2>
        <button class="btn btn-sm btn-outline" onclick="loadLicenses()">🔄 Refresh</button>
      </div>

      <table>
        <thead>
          <tr>
            <th>License Key</th>
            <th>Client</th>
            <th>Credits</th>
            <th>Machine ID Lock</th>
            <th>Status</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody id="licenses-tbody">
          <tr><td colspan="6" style="text-align:center;color:var(--muted);padding:30px;">Loading licenses...</td></tr>
        </tbody>
      </table>
    </div>
  </div>

  <script>
    async function loadLicenses() {
      try {
        const res = await fetch('/api/admin/licenses');
        const data = await res.json();
        const tbody = document.getElementById('licenses-tbody');
        document.getElementById('total-count').textContent = data.licenses ? data.licenses.length : 0;
        if (!data.licenses || data.licenses.length === 0) {
          tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;color:#94a3b8;padding:30px;">No licenses generated yet. Generate your first license on the left!</td></tr>';
          return;
        }

        tbody.innerHTML = data.licenses.map(lic => `
          <tr>
            <td>
              <span class="key-badge" onclick="navigator.clipboard.writeText('${lic.license_key}');alert('Copied key: ${lic.license_key}')" title="Click to copy">
                ${lic.license_key}
              </span>
            </td>
            <td>
              <strong>${lic.client_name || 'N/A'}</strong><br>
              <span style="font-size:11px;color:#94a3b8;">${lic.client_email || ''}</span>
            </td>
            <td>
              <strong style="color:${lic.credits > 0 ? '#38bdf8' : '#ef4444'};font-size:14px;">
                ${lic.plan_type === 'unlimited' ? '♾️ Unlimited' : lic.credits}
              </strong>
            </td>
            <td>
              ${lic.bound_machine_id ?
                `<span style="font-family:monospace;font-size:11px;background:#090d16;padding:2px 6px;border-radius:4px;color:#a5f3fc;" title="${lic.bound_machine_id}">🔒 ${lic.bound_machine_id.slice(0, 10)}...</span>` :
                '<span style="font-size:11px;color:#94a3b8;">🔓 Unlocked (Any PC)</span>'
              }
            </td>
            <td>
              <span class="status-badge ${lic.status === 'active' ? 'status-active' : 'status-revoked'}">
                ${lic.status}
              </span>
            </td>
            <td>
              <div style="display:flex;gap:6px;">
                <button class="btn btn-sm btn-outline" onclick="addCredits('${lic.license_key}')" title="Recharge 5 credits">+5 Credits</button>
                ${lic.bound_machine_id ? `<button class="btn btn-sm btn-outline" onclick="resetMachine('${lic.license_key}')" title="Allow user to activate on new PC">Reset PC</button>` : ''}
                <button class="btn btn-sm btn-danger" onclick="toggleStatus('${lic.license_key}', '${lic.status === 'active' ? 'revoked' : 'active'}')">
                  ${lic.status === 'active' ? 'Revoke' : 'Activate'}
                </button>
              </div>
            </td>
          </tr>
        `).join('');
      } catch (e) {
        console.error(e);
      }
    }

    document.getElementById('form-create').addEventListener('submit', async (e) => {
      e.preventDefault();
      const payload = {
        client_name: document.getElementById('inp-name').value,
        client_email: document.getElementById('inp-email').value,
        credits: parseInt(document.getElementById('inp-credits').value),
        notes: document.getElementById('inp-notes').value
      };
      const res = await fetch('/api/admin/create-license', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await res.json();
      if (data.success) {
        document.getElementById('issued-key-box').style.display = 'block';
        document.getElementById('issued-key-text').textContent = data.license_key;
        loadLicenses();
      }
    });

    function copyIssuedKey() {
      const k = document.getElementById('issued-key-text').textContent;
      navigator.clipboard.writeText(k);
      alert('Copied to clipboard: ' + k);
    }

    async function addCredits(key) {
      const num = prompt('Enter number of wedding credits to add to ' + key + ':', '5');
      if (!num) return;
      const res = await fetch('/api/admin/add-credits', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ license_key: key, add_credits: parseInt(num) })
      });
      const data = await res.json();
      alert(data.message);
      loadLicenses();
    }

    async function resetMachine(key) {
      if (!confirm('Reset Machine lock for ' + key + '? The client will be able to activate this key on their new PC.')) return;
      const res = await fetch('/api/admin/reset-machine', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ license_key: key })
      });
      const data = await res.json();
      alert(data.message);
      loadLicenses();
    }

    async function toggleStatus(key, newStatus) {
      if (!confirm('Are you sure you want to change status of ' + key + ' to ' + newStatus + '?')) return;
      const res = await fetch('/api/admin/toggle-status', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ license_key: key, status: newStatus })
      });
      const data = await res.json();
      alert(data.message);
      loadLicenses();
    }

    loadLicenses();
  </script>
</body>
</html>"""


def run_admin_server(port: int = PORT):
    init_db()
    server = ThreadingHTTPServer(("0.0.0.0", port), AdminRequestHandler)
    print(f"================================================================")
    print(f"  AI Video Flow - SaaS Admin License Server Live!")
    print(f"  Open in Browser: http://localhost:{port}/")
    print(f"  Live Verification API: http://localhost:{port}/v1/license/verify")
    print(f"  Database stored in: {DB_FILE}")
    print(f"================================================================")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Admin Server...")
        server.server_close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else PORT
    run_admin_server(p)
