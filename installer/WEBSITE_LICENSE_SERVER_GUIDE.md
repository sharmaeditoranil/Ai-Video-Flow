# AI Video Flow - Website SaaS License & Payment Server Integration Guide

Yeh guide batati hai ki aap apni website (WordPress / WooCommerce / Node.js / Python / PHP) se **AI Video Flow** ke licenses, per-wedding credits, aur payment kaise manage karenge taaki **koi software ko hack ya bypass na kar sake**.

---

## 1. Security Architecture (Anti-Hack & Anti-Piracy Protection)

1. **Hardware Machine Fingerprint Binding**:
   - Software computer ke motherboard, CPU, aur Primary Network MAC address ka SHA-256 hash banata hai (`Machine Hardware ID`).
   - Ek license key usi machine par chalegi jahan pe pehli baar activate hui hai. Koi doosra banda key share karke copy nahi chala sakta.

2. **Cryptographic HMAC-SHA256 Vault**:
   - Client machine par saved vault file (`~/.ai-video-flow/license_vault.json`) cryptographic signature se sealed hoti hai.
   - Agar koi user local file me credits edit karke `9999` karne ki koshish karega, to signature mismatch detect hote hi software turant **Free Community Tier** par gir jayega.

3. **Remote Website Sync (Per-Wedding Credits)**:
   - Jab bhi editor "AI Smart Premium" run karta hai, software aapki website API ko secure request bhejkar 1 wedding credit consume karta hai.

---

## 2. Aapki Website ke liye Ready-to-Use Backend Code

Aap is API ko apni website par host kar sakte hain (e.g. `https://yourwebsite.com/api/v1/license`):

### Option A: Python (FastAPI / Flask) or Serverless

```python
from flask import Flask, request, jsonify
import time, hashlib

app = Flask(__name__)

# Dummy Database (Aap ise MySQL, PostgreSQL ya WooCommerce DB se connect karein)
LICENSES_DB = {
    "AIFLOW-PREM-WEDDING-10": {
        "tier": "pro",
        "tier_display": "👑 AI Smart Premium (10 Weddings)",
        "email": "client@example.com",
        "credits": 10,
        "plan_type": "per_wedding",
        "expires_at": int(time.time()) + (86400 * 365),
        "bound_machine_id": None # First activation par machine bind ho jayegi
    }
}

@app.route("/v1/license/verify", methods=["POST"])
def verify_license():
    data = request.json or {}
    key = data.get("license_key", "").strip().upper()
    machine_id = data.get("machine_id", "").strip().upper()

    if key not in LICENSES_DB:
        return jsonify({"success": False, "error": "Invalid License Key."}), 400

    lic = LICENSES_DB[key]
    # Machine Lock check
    if lic["bound_machine_id"] is None:
        lic["bound_machine_id"] = machine_id  # Bind to this machine
    elif lic["bound_machine_id"] != machine_id:
        return jsonify({"success": False, "error": "License already bound to another PC!"}), 403

    return jsonify({
        "success": True,
        "tier": lic["tier"],
        "tier_display": lic["tier_display"],
        "credits": lic["credits"],
        "plan_type": lic["plan_type"],
        "expires_at": lic["expires_at"]
    })

@app.route("/v1/license/consume", methods=["POST"])
def consume_credit():
    data = request.json or {}
    key = data.get("license_key", "").strip().upper()
    machine_id = data.get("machine_id", "")

    if key in LICENSES_DB and LICENSES_DB[key]["credits"] > 0:
        LICENSES_DB[key]["credits"] -= 1
        return jsonify({"success": True, "remaining": LICENSES_DB[key]["credits"]})
    return jsonify({"success": False, "error": "Insufficient credits"}), 400

if __name__ == "__main__":
    app.run(port=8080)
```

---

### Option B: PHP (WordPress / cPanel Server)

Aap apni WordPress site ke theme ya custom plugin me ek simple file `license-api.php` daal sakte hain:

```php
<?php
header('Content-Type: application/json');
$input = json_decode(file_get_contents('php://input'), true);
$key = trim(strtoupper($input['license_key'] ?? ''));
$machine_id = trim(strtoupper($input['machine_id'] ?? ''));

// Database me query karein (e.g. WooCommerce Orders)
// Agar payment successful hua hai, to credit return karein:
if (!empty($key)) {
    echo json_encode([
        "success" => true,
        "tier" => "pro",
        "tier_display" => "👑 AI Smart Premium",
        "credits" => 15,
        "plan_type" => "per_wedding",
        "expires_at" => time() + (86400 * 365)
    ]);
} else {
    echo json_encode(["success" => false, "error" => "License key required"]);
}
```

---

## 3. Instant Testing Keys (Built-In Master Tokens)

Testing ke liye software me cryptographic offline tokens bhi supported hain bina server deploy kiye:

* **10 Wedding Credits Key**: `AIFLOW-PREM-10-TEST-2026`
* **50 Wedding Credits Key**: `AIFLOW-PREM-50-STUDIO-2026`
* **Unlimited Agency Key**: `AIFLOW-PREM-999-AGENCY-2026`

Isse aap aur aapke team members testing turant kar sakte hain!
