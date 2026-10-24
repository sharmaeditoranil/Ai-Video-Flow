<?php
/**
 * AI Video Flow - SaaS License & Credit Verification Server
 * Domain: video.aiphotoflow.in
 * 
 * Supports:
 * - Desktop App Live API: POST /verify and POST /consume
 * - Admin Web Portal: Browser interface to create keys, add credits, reset PC locks
 * - SQLite Database: licenses.db (auto-created on first run)
 */

session_start();

// Admin Authentication Password (change as needed)
$ADMIN_PASSWORD = getenv('ADMIN_PASSWORD') ?: 'admin123';

$db_file = __DIR__ . '/licenses.db';
$pdo = new PDO("sqlite:" . $db_file);
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);

// Initialize SQLite schema
$pdo->exec("
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
    );
    CREATE TABLE IF NOT EXISTS usage_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        license_key TEXT,
        machine_id TEXT,
        job_name TEXT,
        timestamp INTEGER
    );
");

$request_uri = $_SERVER['REQUEST_URI'] ?? '/';
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

// Handle CORS
if ($method === 'OPTIONS') {
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Headers: *');
    header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    http_response_code(200);
    exit;
}

// Helper: JSON response
function json_response($data, $status = 200) {
    header('Content-Type: application/json; charset=utf-8');
    header('Access-Control-Allow-Origin: *');
    header('Access-Control-Allow-Headers: *');
    http_response_code($status);
    echo json_encode($data, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE);
    exit;
}

// -------------------------------------------------------------
// API Endpoint 1: Verify License (/verify)
// -------------------------------------------------------------
if (strpos($request_uri, '/verify') !== false) {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $key = strtoupper(trim($body['license_key'] ?? ''));
    $machine_id = strtoupper(trim($body['machine_id'] ?? ''));

    if (empty($key)) {
        json_response(["success" => false, "error" => "License key is required"], 400);
    }

    $stmt = $pdo->prepare("SELECT * FROM licenses WHERE license_key = ?");
    $stmt->execute([$key]);
    $lic = $stmt->fetch(PDO::FETCH_ASSOC);

    if (!$lic) {
        json_response(["success" => false, "error" => "Invalid License Key on video.aiphotoflow.in"], 400);
    }

    if ($lic['status'] !== 'active') {
        json_response(["success" => false, "error" => "This license is " . $lic['status']], 403);
    }

    // Machine Hardware ID Lock Binding
    $bound = $lic['bound_machine_id'];
    if (empty($bound)) {
        $update = $pdo->prepare("UPDATE licenses SET bound_machine_id = ? WHERE license_key = ?");
        $update->execute([$machine_id, $key]);
        $bound = $machine_id;
    } elseif ($bound !== $machine_id) {
        json_response([
            "success" => false,
            "error" => "License is locked to another computer. Contact video.aiphotoflow.in admin to reset machine lock."
        ], 403);
    }

    json_response([
        "success" => true,
        "tier" => $lic['tier'],
        "tier_display" => $lic['tier_display'],
        "credits" => (int)$lic['credits'],
        "plan_type" => $lic['plan_type'],
        "expires_at" => (int)$lic['expires_at']
    ]);
}

// -------------------------------------------------------------
// API Endpoint 2: Consume 1 Wedding Credit (/consume)
// -------------------------------------------------------------
if (strpos($request_uri, '/consume') !== false) {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $key = strtoupper(trim($body['license_key'] ?? ''));
    $machine_id = $body['machine_id'] ?? '';
    $job_name = $body['job_name'] ?? 'Wedding Shoot';

    $stmt = $pdo->prepare("SELECT * FROM licenses WHERE license_key = ?");
    $stmt->execute([$key]);
    $lic = $stmt->fetch(PDO::FETCH_ASSOC);

    if (!$lic) {
        json_response(["success" => false, "error" => "Invalid license"], 400);
    }

    $rem = (int)$lic['credits'];
    if ($lic['plan_type'] !== 'unlimited') {
        if ($rem <= 0) {
            json_response(["success" => false, "error" => "0 wedding credits remaining on video.aiphotoflow.in"], 400);
        }
        $rem -= 1;
        $up = $pdo->prepare("UPDATE licenses SET credits = ? WHERE license_key = ?");
        $up->execute([$rem, $key]);
    } else {
        $rem = 9999;
    }

    $log = $pdo->prepare("INSERT INTO usage_logs (license_key, machine_id, job_name, timestamp) VALUES (?, ?, ?, ?)");
    $log->execute([$key, $machine_id, $job_name, time()]);

    json_response(["success" => true, "remaining" => $rem]);
}

// -------------------------------------------------------------
// Admin API Endpoints
// -------------------------------------------------------------
if (strpos($request_uri, '/api/admin/licenses') !== false) {
    $stmt = $pdo->query("SELECT * FROM licenses ORDER BY id DESC");
    $rows = $stmt->fetchAll(PDO::FETCH_ASSOC);
    json_response(["success" => true, "licenses" => $rows]);
}

if (strpos($request_uri, '/api/admin/create-license') !== false && $method === 'POST') {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $name = trim($body['client_name'] ?? 'Valued Client');
    $email = trim($body['client_email'] ?? '');
    $credits = (int)($body['credits'] ?? 10);
    $plan_type = ($credits >= 999) ? 'unlimited' : 'per_wedding';
    $tier = 'premium';
    $tier_display = "👑 AI Smart Premium (" . ($credits >= 999 ? "Unlimited" : "$credits Weddings") . ")";
    
    // Generate randomized key: AIFLOW-PREM-<credits>-<hex4>-<hex4>
    $rnd = strtoupper(bin2hex(random_bytes(4)));
    $key = "AIFLOW-PREM-{$credits}-" . substr($rnd, 0, 4) . "-" . substr($rnd, 4, 4);
    $now = time();
    $expires = $now + (86400 * 365); // 1 year

    $stmt = $pdo->prepare("
        INSERT INTO licenses (license_key, client_name, client_email, tier, tier_display, credits, plan_type, status, created_at, expires_at, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
    ");
    $stmt->execute([$key, $name, $email, $tier, $tier_display, $credits, $plan_type, $now, $expires, $body['notes'] ?? '']);

    json_response(["success" => true, "license_key" => $key, "message" => "License created successfully!"]);
}

if (strpos($request_uri, '/api/admin/add-credits') !== false && $method === 'POST') {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $key = strtoupper(trim($body['license_key'] ?? ''));
    $add = (int)($body['add_credits'] ?? 5);

    $stmt = $pdo->prepare("UPDATE licenses SET credits = credits + ? WHERE license_key = ?");
    $stmt->execute([$add, $key]);
    json_response(["success" => true, "message" => "Added $add wedding credits to $key!"]);
}

if (strpos($request_uri, '/api/admin/reset-machine') !== false && $method === 'POST') {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $key = strtoupper(trim($body['license_key'] ?? ''));

    $stmt = $pdo->prepare("UPDATE licenses SET bound_machine_id = NULL WHERE license_key = ?");
    $stmt->execute([$key]);
    json_response(["success" => true, "message" => "Machine hardware lock reset for $key. Client can now activate on a new PC!"]);
}

if (strpos($request_uri, '/api/admin/toggle-status') !== false && $method === 'POST') {
    $body = json_decode(file_get_contents('php://input'), true) ?? [];
    $key = strtoupper(trim($body['license_key'] ?? ''));
    $status = $body['status'] ?? 'revoked';

    $stmt = $pdo->prepare("UPDATE licenses SET status = ? WHERE license_key = ?");
    $stmt->execute([$status, $key]);
    json_response(["success" => true, "message" => "Status updated to $status for $key"]);
}

// -------------------------------------------------------------
// Admin Portal UI (HTML Rendered in Browser)
// -------------------------------------------------------------
// Check Login Session
$logged_in = isset($_SESSION['admin_auth']) && $_SESSION['admin_auth'] === true;

if (isset($_POST['login_action'])) {
    if (($_POST['admin_pass'] ?? '') === $ADMIN_PASSWORD) {
        $_SESSION['admin_auth'] = true;
        $logged_in = true;
    } else {
        $login_error = "Invalid admin password!";
    }
}

if (isset($_GET['logout'])) {
    session_destroy();
    header("Location: " . strtok($request_uri, '?'));
    exit;
}
?>
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>AI Video Flow – SaaS Admin Portal (video.aiphotoflow.in)</title>
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
<?php if (!$logged_in): ?>
  <!-- Login Dialog -->
  <div style="max-width: 400px; margin: 100px auto;" class="card">
    <div style="text-align: center; margin-bottom: 20px;">
      <h1 style="color: var(--accent); margin: 0 0 6px 0; font-size: 24px;">👑 SaaS Admin Portal</h1>
      <p style="color: var(--muted); font-size: 13px; margin: 0;">video.aiphotoflow.in</p>
    </div>
    <?php if (isset($login_error)): ?>
      <div style="background: rgba(239,68,68,0.2); border: 1px solid var(--danger); color: #fca5a5; padding: 10px; border-radius: 8px; font-size: 12px; margin-bottom: 14px;">
        <?= htmlspecialchars($login_error) ?>
      </div>
    <?php endif; ?>
    <form method="POST">
      <input type="hidden" name="login_action" value="1">
      <label>Admin Master Password</label>
      <input type="password" name="admin_pass" placeholder="Enter password (default: admin123)" required autofocus>
      <button type="submit" class="btn">🔓 Unlock Admin Dashboard</button>
    </form>
  </div>
<?php else: ?>
  <!-- Authenticated Dashboard -->
  <div class="header">
    <div class="brand">
      <h1>AI Video Flow – SaaS Admin Portal</h1>
      <p>Domain: <strong style="color:#38bdf8;">video.aiphotoflow.in</strong> | Client License &amp; Credit Management</p>
    </div>
    <div style="display:flex;gap:12px;align-items:center;">
      <div class="stat-pill">Total Issued: <strong id="total-count" style="color:#fff;">0</strong></div>
      <a href="?logout=1" class="btn btn-sm btn-danger" style="text-decoration:none;display:inline-block;">Log Out</a>
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
        const res = await fetch('?endpoint=licenses', { headers: { 'Accept': 'application/json' } });
        // fallback to api route
        const apiUrl = window.location.pathname.replace(/\/+$/, '') + '/api/admin/licenses';
        let data;
        try {
          const r = await fetch(apiUrl);
          data = await r.json();
        } catch (e) {
          const r2 = await fetch('index.php/api/admin/licenses');
          data = await r2.json();
        }
        
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
      const res = await fetch('index.php/api/admin/create-license', {
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
      const res = await fetch('index.php/api/admin/add-credits', {
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
      const res = await fetch('index.php/api/admin/reset-machine', {
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
      const res = await fetch('index.php/api/admin/toggle-status', {
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
<?php endif; ?>
</body>
</html>
