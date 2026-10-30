<?php
/**
 * AI Video Flow - License, Payments & AI Relay Server  (license.aivideoflow.in)
 *
 * - Admin portal: users, licenses, plans, orders, usage, settings (login + CSRF protected)
 * - Desktop API: activate / refresh / consume -> Ed25519-signed license tokens
 * - Claude relay: the desktop app's Anthropic SDK talks to /api/v1/ai/v1/messages with its
 *   license key; the real Anthropic API key never leaves this server
 * - Razorpay checkout + webhook: paid orders create the user and license automatically
 *
 * Requires PHP 8.1+ with pdo_sqlite, sodium and curl (or allow_url_fopen).
 */
declare(strict_types=1);

const APP_NAME = 'AI Video Flow';
const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
const ANTHROPIC_VERSION = '2026-10-21';
const RAZORPAY_API = 'https://api.razorpay.com/v1';
const TOKEN_VALID_DAYS = 7;          // offline grace before the app must refresh online
const MAX_RELAY_TOKENS = 16000;
const ALLOWED_BETAS = ['server-side-fallback-2026-07-01'];
const ELEVEN_API = 'https://api.elevenlabs.io/v1';
const DEFAULT_VOICE_ID = 'JBFqnCBsd6RMkjVDRZzb';
const DEFAULT_DOWNLOAD_URL = 'https://github.com/sharmaeditoranil/Ai-Video-Flow/releases/latest';
const AI_MODELS = [
    'claude-opus-5-5' => 'Claude Opus 5.5 (best quality)',
    'claude-sonnet-5-5' => 'Claude Sonnet 5.5 (faster, lower cost)',
    'claude-haiku-5-5' => 'Claude Haiku 5.5 (cheapest)',
];

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
ini_set('display_errors', '0');
header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Referrer-Policy: same-origin');
header('X-Robots-Tag: noindex, nofollow, noarchive');  // admin/licensing site must never appear in search results
header_remove('X-Powered-By');

$REQ_PATH = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
if (str_ends_with($REQ_PATH, '/robots.txt')) {
    header('Content-Type: text/plain; charset=utf-8');
    exit("User-agent: *\nDisallow: /\n");
}

if (!is_file(__DIR__ . '/config.php')) {
    http_response_code(500);
    exit('Server not configured: config.php is missing. Upload the complete server bundle.');
}
$CONFIG = require __DIR__ . '/config.php';

// Data lives in a folder whose name is derived from a secret, so it cannot be guessed even on hosts
// that ignore .htaccess. Moving the database out of the web root happens before anything else.
$DATA_DIR = __DIR__ . '/data-' . substr(hash('sha256', 'avf-data|' . $CONFIG['settings_key']), 0, 20);
if (!is_dir($DATA_DIR)) {
    mkdir($DATA_DIR, 0750, true);
}
foreach (['.htaccess' => "Require all denied\nDeny from all\n", 'index.html' => ''] as $f => $c) {
    if (!is_file("$DATA_DIR/$f")) {
        file_put_contents("$DATA_DIR/$f", $c);
    }
}
// Older versions kept the database in the public web root - move it out of reach
foreach ([__DIR__ . '/licenses.db', __DIR__ . '/data/licenses.db'] as $old) {
    if (is_file($old) && !is_file("$DATA_DIR/licenses.db")) {
        rename($old, "$DATA_DIR/licenses.db");
    } elseif (is_file($old)) {
        rename($old, "$DATA_DIR/licenses-old-" . time() . '.db');
    }
}

if (!function_exists('sodium_crypto_sign_detached')) {
    http_response_code(500);
    exit('The PHP "sodium" extension is required. Enable it in your hosting panel -> PHP settings -> Extensions.');
}

$pdo = new PDO('sqlite:' . $DATA_DIR . '/licenses.db');
$pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
$pdo->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
$pdo->exec('PRAGMA journal_mode = WAL');
migrate($pdo);

$SCRIPT_DIR = rtrim(str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/')), '/');
$BASE = $SCRIPT_DIR . '/index.php';
$PATH = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
if ($SCRIPT_DIR !== '' && str_starts_with($PATH, $SCRIPT_DIR)) {
    $PATH = substr($PATH, strlen($SCRIPT_DIR));
}
if (str_starts_with($PATH, '/index.php')) {
    $PATH = substr($PATH, strlen('/index.php'));
}
$PATH = '/' . trim($PATH, '/');
$METHOD = $_SERVER['REQUEST_METHOD'] ?? 'GET';

// ---------------------------------------------------------------------------
// Schema
// ---------------------------------------------------------------------------
function migrate(PDO $pdo): void
{
    $pdo->exec("
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT,
            phone TEXT,
            studio TEXT,
            notes TEXT,
            source TEXT DEFAULT 'manual',
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS licenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT UNIQUE NOT NULL,
            client_name TEXT,
            client_email TEXT,
            tier TEXT DEFAULT 'premium',
            tier_display TEXT,
            credits INTEGER DEFAULT 10,
            plan_type TEXT DEFAULT 'per_wedding',
            bound_machine_id TEXT,
            status TEXT DEFAULT 'active',
            created_at INTEGER,
            expires_at INTEGER,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            credits INTEGER DEFAULT 10,
            unlimited INTEGER DEFAULT 0,
            price_inr INTEGER DEFAULT 0,
            validity_days INTEGER DEFAULT 365,
            active INTEGER DEFAULT 1,
            sort INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rzp_order_id TEXT UNIQUE,
            rzp_payment_id TEXT,
            plan_id INTEGER,
            amount_paise INTEGER,
            name TEXT,
            email TEXT,
            phone TEXT,
            status TEXT DEFAULT 'created',
            license_key TEXT,
            created_at INTEGER,
            paid_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS usage_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT,
            machine_id TEXT,
            job_name TEXT,
            timestamp INTEGER
        );
        CREATE TABLE IF NOT EXISTS ai_usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            license_key TEXT,
            model TEXT,
            input_tokens INTEGER,
            output_tokens INTEGER,
            status INTEGER,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS rate_limits (bucket TEXT, ts INTEGER);
        CREATE INDEX IF NOT EXISTS idx_rl ON rate_limits(bucket, ts);
        CREATE INDEX IF NOT EXISTS idx_ai_usage ON ai_usage(license_key, created_at);
        CREATE TABLE IF NOT EXISTS free_usage (machine_id TEXT, day TEXT, count INTEGER, PRIMARY KEY (machine_id, day));
    ");

    $ucols = array_column($pdo->query('PRAGMA table_info(users)')->fetchAll(), 'name');
    foreach (['machine_id' => 'TEXT', 'platform' => 'TEXT'] as $col => $type) {
        if (!in_array($col, $ucols, true)) {
            $pdo->exec("ALTER TABLE users ADD COLUMN $col $type");
        }
    }

    $cols = array_column($pdo->query('PRAGMA table_info(licenses)')->fetchAll(), 'name');
    foreach ([
        'user_id' => 'INTEGER', 'activated_at' => 'INTEGER', 'last_seen_at' => 'INTEGER',
        'app_version' => 'TEXT', 'platform' => 'TEXT', 'plan_name' => 'TEXT', 'bound_mobile_id' => 'TEXT',
    ] as $col => $type) {
        if (!in_array($col, $cols, true)) {
            $pdo->exec("ALTER TABLE licenses ADD COLUMN $col $type");
        }
    }

    // The old portal handed these keys to every client; they must be considered leaked
    $pdo->exec("DELETE FROM settings WHERE key IN ('gemini_api_key', 'openai_api_key', 'active_model')");

    // Give licenses from the old portal an owner in the users table
    $orphans = $pdo->query('SELECT id, client_name, client_email, created_at FROM licenses WHERE user_id IS NULL')->fetchAll();
    foreach ($orphans as $lic) {
        $uid = find_or_create_user($pdo, $lic['client_name'] ?: 'Customer', $lic['client_email'] ?: '', '', '', 'legacy');
        $pdo->prepare('UPDATE licenses SET user_id = ? WHERE id = ?')->execute([$uid, $lic['id']]);
    }

    $pcols = array_column($pdo->query('PRAGMA table_info(plans)')->fetchAll(), 'name');
    foreach (['kind' => "TEXT DEFAULT 'ai'", 'mrp_inr' => 'INTEGER DEFAULT 0', 'badge' => "TEXT DEFAULT ''"] as $col => $type) {
        if (!in_array($col, $pcols, true)) {
            $pdo->exec("ALTER TABLE plans ADD COLUMN $col $type");
        }
    }

    // Pricing v2. Replaces the first-version default plans, never plans the admin created or renamed.
    $st = $pdo->query("SELECT value FROM settings WHERE key = 'pricing_version'");
    if ($st->fetchColumn() !== '2') {
        $oldDefaults = ['Trial - 1 Wedding', 'Single - 1 Wedding', 'Starter - 5 Weddings', 'Popular - 10 Weddings',
                        'Studio - 25 Weddings', 'Agency - 50 Weddings', 'Unlimited - 1 Year'];
        $names = $pdo->query('SELECT name FROM plans')->fetchAll(PDO::FETCH_COLUMN);
        if (!array_diff($names, $oldDefaults)) {
            $pdo->exec('DELETE FROM plans');
            $seed = $pdo->prepare('INSERT INTO plans (name, kind, credits, unlimited, price_inr, mrp_inr, validity_days, badge, active, sort) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)');
            foreach ([
                ['AI Editing · Monthly', 'local', 0, 0, 399, 0, 30, '', 1],
                ['AI Editing · Yearly', 'local', 0, 0, 2499, 4788, 365, 'Best value', 2],
                ['Smart AI · 5 Weddings', 'ai', 5, 0, 999, 0, 365, '', 3],
                ['Smart AI · 11 Weddings', 'ai', 11, 0, 1999, 2200, 365, 'Most popular', 4],
                ['Smart AI · 30 Weddings', 'ai', 30, 0, 4999, 6000, 365, 'Best value', 5],
                ['Smart AI Unlimited · Yearly', 'ai', 0, 1, 14999, 29999, 365, '50% off', 6],
                ['Trial · 1 Wedding (manual)', 'ai', 1, 0, 0, 0, 30, '', 9],
            ] as $p) {
                $seed->execute($p);
            }
        }
        $pdo->exec("INSERT INTO settings (key, value) VALUES ('pricing_version', '2') ON CONFLICT(key) DO UPDATE SET value = '2'");
    }

    // Ensure all existing registered users without a license row get a Free Trial license
    $noLicUsers = $pdo->query('SELECT u.id, u.name, u.email, u.machine_id, u.platform, u.source FROM users u WHERE NOT EXISTS (SELECT 1 FROM licenses l WHERE l.user_id = u.id)')->fetchAll();
    foreach ($noLicUsers as $nu) {
        $isMob = str_starts_with((string)$nu['machine_id'], 'F0B11E') || in_array(strtolower((string)$nu['platform']), ['android', 'ios', 'mobile'], true) || ($nu['source'] ?? '') === 'trial_mobile';
        $label = $isMob ? 'Mobile Free Trial (2 Edits/Day)' : 'Free Trial (2 Edits/Day)';
        $tKey = issue_license($pdo, (int)$nu['id'], $label, 0, false, 365, 'Registered profile', 'local');
        if (!empty($nu['machine_id'])) {
            $slotCol = $isMob ? 'bound_mobile_id' : 'bound_machine_id';
            $pdo->prepare("UPDATE licenses SET $slotCol = ?, platform = ?, last_seen_at = ? WHERE license_key = ?")
                ->execute([$nu['machine_id'], $nu['platform'] ?? '', time(), $tKey]);
        }
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function json_out(array $data, int $status = 200): never
{
    http_response_code($status);
    header('Content-Type: application/json; charset=utf-8');
    header('Cache-Control: no-store');
    echo json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function body_json(): array
{
    $raw = file_get_contents('php://input') ?: '';
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function client_ip(): string
{
    // Requests relayed by the old-domain forwarder (old_domain_proxy.php) carry the customer's own IP,
    // vouched for with a secret both servers derive from config.php
    $proof = (string)($_SERVER['HTTP_X_AVF_PROXY'] ?? '');
    $ip = (string)($_SERVER['HTTP_X_AVF_CLIENT_IP'] ?? '');
    if ($proof !== '' && filter_var($ip, FILTER_VALIDATE_IP) && hash_equals(proxy_secret(), $proof)) {
        return $ip;
    }
    return $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0';
}

function proxy_secret(): string
{
    global $CONFIG;
    return hash_hmac('sha256', 'avf-domain-proxy', (string)$CONFIG['settings_key']);
}

function h(?string $s): string
{
    return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8');
}

function b64url(string $bin): string
{
    return rtrim(strtr(base64_encode($bin), '+/', '-_'), '=');
}

function rate_limit(PDO $pdo, string $bucket, int $max, int $window): void
{
    $now = time();
    $pdo->prepare('DELETE FROM rate_limits WHERE ts < ?')->execute([$now - 86400]);
    $st = $pdo->prepare('SELECT COUNT(*) FROM rate_limits WHERE bucket = ? AND ts > ?');
    $st->execute([$bucket, $now - $window]);
    if ((int)$st->fetchColumn() >= $max) {
        json_out(['success' => false, 'error' => 'Too many attempts. Please wait and try again.'], 429);
    }
    $pdo->prepare('INSERT INTO rate_limits (bucket, ts) VALUES (?, ?)')->execute([$bucket, $now]);
}

function settings_key(): string
{
    global $CONFIG;
    return base64_decode($CONFIG['settings_key']);
}

function get_setting(PDO $pdo, string $key, string $default = ''): string
{
    $st = $pdo->prepare('SELECT value FROM settings WHERE key = ?');
    $st->execute([$key]);
    $v = $st->fetchColumn();
    if ($v === false || $v === null) {
        return $default;
    }
    if (str_starts_with($v, 'enc:')) {
        $raw = base64_decode(substr($v, 4));
        $nonce = substr($raw, 0, SODIUM_CRYPTO_SECRETBOX_NONCEBYTES);
        $plain = sodium_crypto_secretbox_open(substr($raw, SODIUM_CRYPTO_SECRETBOX_NONCEBYTES), $nonce, settings_key());
        return $plain === false ? $default : $plain;
    }
    return $v;
}

function set_setting(PDO $pdo, string $key, string $value, bool $secret = false): void
{
    if ($secret && $value !== '') {
        $nonce = random_bytes(SODIUM_CRYPTO_SECRETBOX_NONCEBYTES);
        $value = 'enc:' . base64_encode($nonce . sodium_crypto_secretbox($value, $nonce, settings_key()));
    }
    $pdo->prepare('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
        ->execute([$key, $value]);
}

// Smart AI routes (Claude relay, AI audio): the app sends its license key + machine id
function smart_ai_license(PDO $pdo): array
{
    $key = (string)($_SERVER['HTTP_X_API_KEY'] ?? '');
    $auth = (string)($_SERVER['HTTP_AUTHORIZATION'] ?? $_SERVER['REDIRECT_HTTP_AUTHORIZATION'] ?? '');
    if ($key === '' && str_starts_with($auth, 'Bearer ')) {
        $key = substr($auth, 7);
    }
    $lic = check_license($pdo, $key, strtoupper((string)($_SERVER['HTTP_X_MACHINE_ID'] ?? '')), false);
    if ($lic['plan_type'] === 'local' || ($lic['plan_type'] === 'per_wedding' && (int)$lic['credits'] <= 0)) {
        json_out(['success' => false, 'error' => 'Smart AI needs an active Smart AI pack with credits left.'], 403);
    }
    return $lic;
}

function mask_secret(string $v): string
{
    return $v === '' ? '' : substr($v, 0, 7) . '…' . substr($v, -4);
}

function http_request(string $method, string $url, array $headers, ?string $body, int $timeout = 60, ?string $basicAuth = null): array
{
    if (function_exists('curl_init')) {
        $ch = curl_init($url);
        curl_setopt_array($ch, [
            CURLOPT_CUSTOMREQUEST => $method,
            CURLOPT_RETURNTRANSFER => true,
            CURLOPT_HTTPHEADER => $headers,
            CURLOPT_TIMEOUT => $timeout,
            CURLOPT_CONNECTTIMEOUT => 15,
        ]);
        if ($body !== null) {
            curl_setopt($ch, CURLOPT_POSTFIELDS, $body);
        }
        if ($basicAuth !== null) {
            curl_setopt($ch, CURLOPT_USERPWD, $basicAuth);
        }
        $resp = curl_exec($ch);
        $status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
        $err = curl_error($ch);
        return [$resp === false ? 0 : $status, $resp === false ? json_encode(['error' => ['message' => $err]]) : $resp];
    }
    if ($basicAuth !== null) {
        $headers[] = 'Authorization: Basic ' . base64_encode($basicAuth);
    }
    $ctx = stream_context_create(['http' => [
        'method' => $method, 'header' => implode("\r\n", $headers), 'content' => $body ?? '',
        'timeout' => $timeout, 'ignore_errors' => true,
    ]]);
    $resp = @file_get_contents($url, false, $ctx);
    $status = 0;
    foreach ($http_response_header ?? [] as $line) {
        if (preg_match('#^HTTP/\S+\s+(\d+)#', $line, $m)) {
            $status = (int)$m[1];
        }
    }
    return [$status, $resp === false ? '' : $resp];
}

// ---------------------------------------------------------------------------
// Users & licenses
// ---------------------------------------------------------------------------
function find_or_create_user(PDO $pdo, string $name, string $email, string $phone, string $studio, string $source): int
{
    $email = strtolower(trim($email));
    if ($email !== '') {
        $st = $pdo->prepare('SELECT id FROM users WHERE email = ?');
        $st->execute([$email]);
        if ($id = $st->fetchColumn()) {
            return (int)$id;
        }
    }
    $pdo->prepare('INSERT INTO users (name, email, phone, studio, source, created_at) VALUES (?, ?, ?, ?, ?, ?)')
        ->execute([trim($name) ?: 'Customer', $email, trim($phone), trim($studio), $source, time()]);
    return (int)$pdo->lastInsertId();
}

function new_license_key(): string
{
    $alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
    $parts = [];
    for ($p = 0; $p < 4; $p++) {
        $s = '';
        for ($i = 0; $i < 5; $i++) {
            $s .= $alphabet[random_int(0, strlen($alphabet) - 1)];
        }
        $parts[] = $s;
    }
    return 'AVF-' . implode('-', $parts);
}

/** kind: 'ai' (Smart AI credits / unlimited) or 'local' (unlimited editing, no Smart AI) */
function issue_license(PDO $pdo, int $userId, string $planName, int $credits, bool $unlimited, int $validityDays, string $notes, string $kind = 'ai'): string
{
    $planType = $kind === 'local' ? 'local' : ($unlimited ? 'unlimited' : 'per_wedding');
    $user = $pdo->prepare('SELECT name, email FROM users WHERE id = ?');
    $user->execute([$userId]);
    $u = $user->fetch() ?: ['name' => '', 'email' => ''];
    $key = new_license_key();
    $now = time();
    $pdo->prepare('INSERT INTO licenses (license_key, user_id, client_name, client_email, tier, tier_display, credits, plan_type, plan_name, status, created_at, expires_at, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')
        ->execute([
            $key, $userId, $u['name'], $u['email'], 'premium', $planName,
            $planType === 'per_wedding' ? max(0, $credits) : 0, $planType, $planName,
            'active', $now, $now + 86400 * max(1, $validityDays), $notes,
        ]);
    return $key;
}

function issue_from_plan(PDO $pdo, int $userId, array $p, string $notes): string
{
    return issue_license($pdo, $userId, $p['name'], (int)$p['credits'], (bool)$p['unlimited'], (int)$p['validity_days'], $notes, $p['kind'] ?: 'ai');
}

function upsert_trial_user(PDO $pdo, string $name, string $email, string $phone, string $studio, string $machineId, string $platform): int
{
    $name = trim($name);
    $email = strtolower(trim($email));
    $phone = preg_replace('/[^0-9+]/', '', trim($phone));
    $studio = trim($studio);
    $machineId = strtoupper(trim($machineId));
    $platform = trim($platform);
    $isMobile = str_starts_with($machineId, 'F0B11E') || in_array(strtolower($platform), ['android', 'ios', 'mobile'], true);
    $source = $isMobile ? 'trial_mobile' : 'trial_pc';

    $existingId = 0;
    if ($email !== '') {
        $st = $pdo->prepare('SELECT id FROM users WHERE email = ? LIMIT 1');
        $st->execute([$email]);
        $existingId = (int)$st->fetchColumn();
    }
    if (!$existingId && $phone !== '') {
        $st = $pdo->prepare('SELECT id FROM users WHERE phone = ? LIMIT 1');
        $st->execute([$phone]);
        $existingId = (int)$st->fetchColumn();
    }
    if (!$existingId && $machineId !== '') {
        $st = $pdo->prepare('SELECT id FROM users WHERE machine_id = ? LIMIT 1');
        $st->execute([$machineId]);
        $existingId = (int)$st->fetchColumn();
    }

    if ($existingId) {
        $pdo->prepare('UPDATE users SET name = COALESCE(NULLIF(?, \'\'), name), email = COALESCE(NULLIF(?, \'\'), email), phone = COALESCE(NULLIF(?, \'\'), phone), studio = COALESCE(NULLIF(?, \'\'), studio), machine_id = COALESCE(NULLIF(?, \'\'), machine_id), platform = COALESCE(NULLIF(?, \'\'), platform) WHERE id = ?')
            ->execute([$name, $email, $phone, $studio, $machineId, $platform, $existingId]);
        $uid = $existingId;
    } else {
        $pdo->prepare('INSERT INTO users (name, email, phone, studio, notes, source, machine_id, platform, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)')
            ->execute([$name ?: 'Trial User', $email, $phone, $studio, "Device: $machineId ($platform)", $source, $machineId, $platform, time()]);
        $uid = (int)$pdo->lastInsertId();
    }

    // Auto-create/attach Free Trial license so Admin immediately sees the license under Admin -> Licenses
    $chkLic = $pdo->prepare('SELECT id, license_key, bound_machine_id FROM licenses WHERE user_id = ? ORDER BY id DESC LIMIT 1');
    $chkLic->execute([$uid]);
    $licRow = $chkLic->fetch();
    if (!$licRow) {
        $planLabel = $isMobile ? 'Mobile Free Trial (2 Edits/Day)' : 'Free Trial (2 Edits/Day)';
        $trialKey = issue_license($pdo, $uid, $planLabel, 0, false, 365, "Auto-created on registration ($platform)", 'local');
        if ($machineId !== '') {
            $slotCol = $isMobile ? 'bound_mobile_id' : 'bound_machine_id';
            $pdo->prepare("UPDATE licenses SET $slotCol = ?, platform = ?, last_seen_at = ? WHERE license_key = ?")
                ->execute([$machineId, $platform, time(), $trialKey]);
        }
    } elseif ($machineId !== '' && empty($licRow['bound_machine_id'])) {
        $slotCol = $isMobile ? 'bound_mobile_id' : 'bound_machine_id';
        $pdo->prepare("UPDATE licenses SET $slotCol = ?, platform = ?, last_seen_at = ? WHERE id = ?")
            ->execute([$machineId, $platform, time(), $licRow['id']]);
    }

    return $uid;
}

function load_license(PDO $pdo, string $key): ?array
{
    $st = $pdo->prepare('SELECT * FROM licenses WHERE license_key = ?');
    $st->execute([strtoupper(trim($key))]);
    $row = $st->fetch();
    return $row ?: null;
}

/** Signs the license state so the desktop app can trust it offline (Ed25519, public key embedded in the app). */
function signed_token(array $lic, string $machineId): array
{
    global $CONFIG;
    $now = time();
    $expires = (int)$lic['expires_at'];
    $payload = json_encode([
        'v' => 1,
        'license_key' => $lic['license_key'],
        'machine_id' => $machineId,
        'client_name' => $lic['client_name'],
        'client_email' => $lic['client_email'],
        'plan_name' => $lic['plan_name'] ?: $lic['tier_display'],
        'plan_type' => $lic['plan_type'],
        'credits' => (int)$lic['credits'],
        'status' => $lic['status'],
        'expires_at' => $expires,
        'issued_at' => $now,
        'valid_until' => min($now + 86400 * TOKEN_VALID_DAYS, $expires ?: PHP_INT_MAX),
    ], JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    $sig = sodium_crypto_sign_detached($payload, base64_decode($CONFIG['signing_secret_key']));
    return ['token' => b64url($payload), 'signature' => b64url($sig)];
}

/** Free plan: projects are counted here per computer per day (India time), never on the computer itself. */
function free_day(): string
{
    return (new DateTime('now', new DateTimeZone('Asia/Kolkata')))->format('Y-m-d');
}

function free_machine_id(array $b): string
{
    $mid = strtoupper((string)($b['machine_id'] ?? ''));
    if (!preg_match('/^[0-9A-F]{32}$/', $mid)) {
        json_out(['success' => false, 'error' => 'Invalid computer id.'], 400);
    }
    return $mid;
}

function free_used(PDO $pdo, string $mid): int
{
    $st = $pdo->prepare('SELECT count FROM free_usage WHERE machine_id = ? AND day = ?');
    $st->execute([$mid, free_day()]);
    return (int)$st->fetchColumn();
}

/** Signed permission for one free project on one computer; the app checks it with the public key. */
function free_grant(string $mid, int $used, int $limit): array
{
    global $CONFIG;
    $now = time();
    $payload = json_encode(['v' => 'free', 'machine_id' => $mid, 'day' => free_day(), 'used' => $used, 'limit' => $limit,
        'nonce' => bin2hex(random_bytes(8)), 'issued_at' => $now, 'valid_until' => $now + 3600], JSON_UNESCAPED_SLASHES);
    $sig = sodium_crypto_sign_detached($payload, base64_decode($CONFIG['signing_secret_key']));
    return ['grant' => b64url($payload), 'signature' => b64url($sig)];
}

/** Desktop installer for each platform that the app may download and run by itself */
const UPDATE_ASSETS = ['windows' => 'Windows Setup.exe', 'mac-arm64' => 'Mac Apple Silicon .dmg', 'mac-x86_64' => 'Mac Intel .dmg'];

/** Installers uploaded to the downloads/ folder next to index.php, under these fixed names */
const DOWNLOAD_FILES = ['windows' => 'AI-Video-Flow-Windows-Setup.exe', 'mac-arm64' => 'AI-Video-Flow-Mac-arm64.dmg',
    'mac-x86_64' => 'AI-Video-Flow-Mac-x86_64.dmg', 'android' => 'AI-Video-Flow-Reels.apk'];

/** Installer for a platform: the admin portal's link if set, else the file in downloads/ (SHA-256 cached until it changes). */
function update_asset(PDO $pdo, string $key): array
{
    $url = get_setting($pdo, "app_asset_url_$key");
    if ($url !== '') {
        return ['url' => $url, 'sha256' => get_setting($pdo, "app_asset_sha256_$key")];
    }
    $file = __DIR__ . '/downloads/' . DOWNLOAD_FILES[$key];
    if (!is_file($file)) {
        return [];
    }
    $stamp = filemtime($file) . ':' . filesize($file);
    [$cachedStamp, $sha] = array_pad(explode('|', get_setting($pdo, "download_sha_$key")), 2, '');
    if ($cachedStamp !== $stamp) {
        $sha = hash_file('sha256', $file);
        set_setting($pdo, "download_sha_$key", "$stamp|$sha");
    }
    return ['url' => 'https://' . ($_SERVER['HTTP_HOST'] ?? '') . $GLOBALS['SCRIPT_DIR'] . '/downloads/' . rawurlencode(DOWNLOAD_FILES[$key]), 'sha256' => $sha];
}

/** Auto-update manifest, signed so the app only runs an installer this server chose (even over a bad connection). */
function update_manifest(PDO $pdo, string $version): array
{
    global $CONFIG;
    $assets = [];
    foreach (UPDATE_ASSETS as $key => $_) {
        if ($a = update_asset($pdo, $key)) {
            $assets[$key] = $a;
        }
    }
    if ($version === '' || !$assets) {
        return [];
    }
    $payload = json_encode(['v' => 'update', 'version' => $version, 'assets' => $assets, 'issued_at' => time()], JSON_UNESCAPED_SLASHES);
    $sig = sodium_crypto_sign_detached($payload, base64_decode($CONFIG['signing_secret_key']));
    return ['manifest' => b64url($payload), 'manifest_signature' => b64url($sig)];
}

/** Validates key + machine/mobile for desktop and mobile calls. Each license allows 1 PC/Mac + 1 Mobile App for free. */
function check_license(PDO $pdo, string $key, string $machineId, bool $bind, string $platform = ''): array
{
    if (!preg_match('/^[A-F0-9]{32,64}$/', $machineId)) {
        json_out(['success' => false, 'error' => 'Invalid machine id.'], 400);
    }
    $lic = load_license($pdo, $key);
    if (!$lic) {
        json_out(['success' => false, 'error' => 'License key not found.'], 404);
    }
    if ($lic['status'] !== 'active') {
        json_out(['success' => false, 'error' => 'This license is ' . $lic['status'] . '. Contact support.'], 403);
    }
    if ((int)$lic['expires_at'] && (int)$lic['expires_at'] < time()) {
        json_out(['success' => false, 'error' => 'This license has expired. Please renew.'], 403);
    }

    // Mobile device detection: mobile machine IDs begin with F0B11E or pass platform Android/iOS/Mobile
    $isMobile = str_starts_with($machineId, 'F0B11E')
        || in_array(strtolower(trim($platform)), ['android', 'ios', 'mobile'], true);
    $slotCol = $isMobile ? 'bound_mobile_id' : 'bound_machine_id';
    $label = $isMobile ? 'phone' : 'computer';

    // Also allow if the ID already matches either bound slot
    if (!empty($lic['bound_machine_id']) && hash_equals($lic['bound_machine_id'], $machineId)) {
        $lic['active_device_id'] = $machineId;
        return $lic;
    }
    if (!empty($lic['bound_mobile_id']) && hash_equals($lic['bound_mobile_id'], $machineId)) {
        $lic['active_device_id'] = $machineId;
        return $lic;
    }

    if (empty($lic[$slotCol])) {
        if (!$bind) {
            json_out(['success' => false, 'error' => "License is not activated on this $label."], 403);
        }
        $pdo->prepare("UPDATE licenses SET $slotCol = ?, activated_at = COALESCE(activated_at, ?) WHERE id = ?")
            ->execute([$machineId, time(), $lic['id']]);
        $lic[$slotCol] = $machineId;
        $lic['active_device_id'] = $machineId;
    } elseif (!hash_equals($lic[$slotCol], $machineId)) {
        json_out(['success' => false, 'error' => "This license is already activated on another $label. Ask the admin to reset the device lock."], 403);
    }
    $lic['active_device_id'] = $machineId;
    return $lic;
}

// ---------------------------------------------------------------------------
// Payments (Razorpay)
// ---------------------------------------------------------------------------
function razorpay_creds(PDO $pdo): array
{
    return [get_setting($pdo, 'razorpay_key_id'), get_setting($pdo, 'razorpay_key_secret')];
}

/** Marks an order paid and issues its license exactly once. Returns the license key. */
function fulfill_order(PDO $pdo, array $order, string $paymentId): string
{
    $plan = $pdo->prepare('SELECT * FROM plans WHERE id = ?');
    $plan->execute([$order['plan_id']]);
    $p = $plan->fetch();
    if (!$p) {
        throw new RuntimeException('Plan not found for order');
    }
    // Browser verify and the Razorpay webhook can arrive together: take a write lock so only one issues the license
    $pdo->exec('BEGIN IMMEDIATE');
    try {
        $st = $pdo->prepare('SELECT status, license_key FROM orders WHERE id = ?');
        $st->execute([$order['id']]);
        $cur = $st->fetch();
        if ($cur && $cur['status'] === 'paid' && $cur['license_key']) {
            $pdo->exec('COMMIT');
            return $cur['license_key'];
        }
        $uid = find_or_create_user($pdo, $order['name'], $order['email'], $order['phone'], '', 'razorpay');
        $key = issue_from_plan($pdo, $uid, $p, 'Razorpay ' . $paymentId);
        $pdo->prepare("UPDATE orders SET status = 'paid', rzp_payment_id = ?, license_key = ?, paid_at = ? WHERE id = ?")
            ->execute([$paymentId, $key, time(), $order['id']]);
        $pdo->exec('COMMIT');
    } catch (Throwable $e) {
        $pdo->exec('ROLLBACK');
        throw $e;
    }
    if ($order['email'] && function_exists('mail')) {
        @mail(
            $order['email'],
            APP_NAME . ' - Your license key',
            "Thank you for your purchase!\n\nPlan: {$p['name']}\nLicense key: $key\n\n" .
            "Open AI Video Flow -> Settings -> License, paste the key and press Activate.\n",
            'From: no-reply@' . ($_SERVER['HTTP_HOST'] ?? 'localhost')
        );
    }
    return $key;
}

function capture_if_needed(PDO $pdo, string $paymentId, int $amount): bool
{
    [$keyId, $secret] = razorpay_creds($pdo);
    [$st, $resp] = http_request('GET', RAZORPAY_API . '/payments/' . rawurlencode($paymentId), [], null, 30, "$keyId:$secret");
    $pay = json_decode($resp, true) ?: [];
    if ($st !== 200) {
        return false;
    }
    if (($pay['status'] ?? '') === 'captured') {
        return true;
    }
    if (($pay['status'] ?? '') === 'authorized' && (int)$pay['amount'] === $amount) {
        [$st2, $resp2] = http_request('POST', RAZORPAY_API . '/payments/' . rawurlencode($paymentId) . '/capture',
            ['Content-Type: application/json'], json_encode(['amount' => $amount, 'currency' => 'INR']), 30, "$keyId:$secret");
        return $st2 === 200 && ((json_decode($resp2, true)['status'] ?? '') === 'captured');
    }
    return false;
}

// ---------------------------------------------------------------------------
// Admin session
// ---------------------------------------------------------------------------
function start_admin_session(): void
{
    $secure = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
    session_name('AVF_ADMIN');
    session_set_cookie_params(['lifetime' => 0, 'path' => '/', 'secure' => $secure, 'httponly' => true, 'samesite' => 'Strict']);
    session_start();
    if (empty($_SESSION['csrf'])) {
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
    // Expire idle sessions after 2 hours
    if (!empty($_SESSION['admin']) && time() - ($_SESSION['seen'] ?? 0) > 7200) {
        $_SESSION = [];
        session_regenerate_id(true);
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
    }
    $_SESSION['seen'] = time();
}

function admin_password_ok(PDO $pdo, string $password): bool
{
    global $CONFIG;
    $hash = get_setting($pdo, 'admin_password_hash');
    if ($hash === '') {
        // First login uses the password from config.php, then it is stored as a hash
        if (!hash_equals((string)$CONFIG['initial_admin_password'], $password)) {
            return false;
        }
        set_setting($pdo, 'admin_password_hash', password_hash($password, PASSWORD_DEFAULT));
        return true;
    }
    return password_verify($password, $hash);
}

// ---------------------------------------------------------------------------
// Two-factor authentication (TOTP, RFC 6238 - Google / Microsoft Authenticator)
// ---------------------------------------------------------------------------
function base32_encode(string $bin): string
{
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    $bits = '';
    foreach (str_split($bin) as $c) {
        $bits .= str_pad(decbin(ord($c)), 8, '0', STR_PAD_LEFT);
    }
    $out = '';
    foreach (str_split($bits, 5) as $chunk) {
        $out .= $alphabet[bindec(str_pad($chunk, 5, '0'))];
    }
    return $out;
}

function base32_decode(string $b32): string
{
    $alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
    $bits = '';
    foreach (str_split(strtoupper(preg_replace('/[^A-Za-z2-7]/', '', $b32))) as $c) {
        $bits .= str_pad(decbin(strpos($alphabet, $c)), 5, '0', STR_PAD_LEFT);
    }
    $out = '';
    foreach (str_split($bits, 8) as $byte) {
        if (strlen($byte) === 8) {
            $out .= chr(bindec($byte));
        }
    }
    return $out;
}

function totp_code(string $secretB32, int $counter): string
{
    $hash = hash_hmac('sha1', pack('J', $counter), base32_decode($secretB32), true);
    $offset = ord($hash[19]) & 0x0f;
    $value = ((ord($hash[$offset]) & 0x7f) << 24) | (ord($hash[$offset + 1]) << 16) | (ord($hash[$offset + 2]) << 8) | ord($hash[$offset + 3]);
    return str_pad((string)($value % 1000000), 6, '0', STR_PAD_LEFT);
}

/** Returns the matched time-step (±1 step of clock drift) or null. */
function totp_match(string $secretB32, string $code): ?int
{
    $code = preg_replace('/\D/', '', $code);
    if (strlen($code) !== 6) {
        return null;
    }
    $now = intdiv(time(), 30);
    foreach ([0, -1, 1] as $d) {
        if (hash_equals(totp_code($secretB32, $now + $d), $code)) {
            return $now + $d;
        }
    }
    return null;
}

function twofa_enabled(PDO $pdo): bool
{
    global $CONFIG;
    // Emergency switch: add 'disable_2fa' => true to config.php if the phone and recovery codes are lost
    return empty($CONFIG['disable_2fa']) && get_setting($pdo, 'totp_enabled') === '1';
}

/** Verifies an authenticator code (no reuse of a code) or a one-time recovery code. */
function twofa_verify(PDO $pdo, string $code): bool
{
    $step = totp_match(get_setting($pdo, 'totp_secret'), $code);
    if ($step !== null && $step > (int)get_setting($pdo, 'totp_last_step', '0')) {
        set_setting($pdo, 'totp_last_step', (string)$step);
        return true;
    }
    $norm = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', $code));
    $codes = json_decode(get_setting($pdo, 'totp_recovery', '[]'), true) ?: [];
    foreach ($codes as $i => $hash) {
        if (strlen($norm) === 10 && hash_equals($hash, hash('sha256', $norm))) {
            unset($codes[$i]);
            set_setting($pdo, 'totp_recovery', json_encode(array_values($codes)), true);
            return true;
        }
    }
    return false;
}

function require_admin(string $method): void
{
    if (empty($_SESSION['admin'])) {
        json_out(['success' => false, 'error' => 'Login required'], 401);
    }
    if ($method !== 'GET' && !hash_equals($_SESSION['csrf'], $_SERVER['HTTP_X_CSRF_TOKEN'] ?? '')) {
        json_out(['success' => false, 'error' => 'Security token mismatch. Reload the page.'], 403);
    }
}

// ---------------------------------------------------------------------------
// Desktop app API
// ---------------------------------------------------------------------------
if (str_starts_with($PATH, '/api/v1/')) {
    if ($METHOD === 'OPTIONS') {
        http_response_code(204);
        exit;
    }

    if ($PATH === '/api/v1/plans' && $METHOD === 'GET') {
        $rows = $pdo->query('SELECT id, name, kind, credits, unlimited, price_inr, mrp_inr, validity_days, badge FROM plans WHERE active = 1 AND price_inr > 0 ORDER BY sort, id')->fetchAll();
        json_out(['success' => true, 'plans' => $rows, 'payments_enabled' => get_setting($pdo, 'payments_enabled') === '1',
            'free_daily_projects' => (int)get_setting($pdo, 'free_daily_projects', '2'),
            'buy_url' => 'https://' . ($_SERVER['HTTP_HOST'] ?? '') . $BASE . '/buy']);
    }

    // Update notice: installed desktop & mobile apps compare their version with the one set in the admin portal
    if ($PATH === '/api/v1/app/version' && $METHOD === 'GET') {
        $desktopVer = get_setting($pdo, 'app_latest_version');
        $desktopUrl = get_setting($pdo, 'app_download_url', DEFAULT_DOWNLOAD_URL);
        $desktopNotes = get_setting($pdo, 'app_update_notes');
        $mobileVer = get_setting($pdo, 'mobile_latest_version') ?: $desktopVer;
        $apk = __DIR__ . '/downloads/' . DOWNLOAD_FILES['android'];
        $mobileUrl = get_setting($pdo, 'mobile_apk_url')
            ?: (is_file($apk) ? 'https://' . ($_SERVER['HTTP_HOST'] ?? '') . $SCRIPT_DIR . '/downloads/' . DOWNLOAD_FILES['android'] : $desktopUrl);
        $mobileNotes = get_setting($pdo, 'mobile_update_notes') ?: $desktopNotes;
        json_out([
            'success' => true,
            'latest' => $desktopVer,
            'download_url' => $desktopUrl,
            'notes' => $desktopNotes,
            'mobile_latest' => $mobileVer,
            'mobile_apk_url' => $mobileUrl,
            'mobile_notes' => $mobileNotes,
        ] + update_manifest($pdo, $desktopVer));
    }

    if ($PATH === '/api/v1/user/register' && $METHOD === 'POST') {
        rate_limit($pdo, 'register:' . client_ip(), 30, 3600);
        $b = body_json();
        $mid = free_machine_id($b);
        $name = trim((string)($b['name'] ?? ''));
        $email = strtolower(trim((string)($b['email'] ?? '')));
        $phone = preg_replace('/[^0-9+]/', '', (string)($b['phone'] ?? ''));
        $studio = trim((string)($b['studio'] ?? ''));
        $platform = trim((string)($b['platform'] ?? ''));
        if ($name === '' || strlen($phone) < 10 || !filter_var($email, FILTER_VALIDATE_EMAIL)) {
            json_out(['success' => false, 'error' => 'Please enter your full name, valid 10-digit WhatsApp number, and email address.'], 400);
        }
        $uid = upsert_trial_user($pdo, $name, $email, $phone, $studio, $mid, $platform);
        $stLic = $pdo->prepare('SELECT license_key FROM licenses WHERE user_id = ? ORDER BY id DESC LIMIT 1');
        $stLic->execute([$uid]);
        $licKey = (string)($stLic->fetchColumn() ?: '');
        json_out([
            'success' => true,
            'user_id' => $uid,
            'license_key' => $licKey,
            'user' => ['name' => $name, 'email' => $email, 'phone' => $phone, 'studio' => $studio],
            'message' => 'Profile registered! You now get 2 Free Edits per day.',
        ]);
    }

    if ($PATH === '/api/v1/free/status' && $METHOD === 'POST') {
        rate_limit($pdo, 'freestatus:' . client_ip(), 600, 3600);
        $b = body_json();
        $mid = free_machine_id($b);
        $limit = (int)get_setting($pdo, 'free_daily_projects', '2');
        $used = free_used($pdo, $mid);
        $st = $pdo->prepare('SELECT u.name, u.email, u.phone, u.studio, l.license_key FROM users u LEFT JOIN licenses l ON l.user_id = u.id WHERE u.machine_id = ? ORDER BY l.id DESC LIMIT 1');
        $st->execute([$mid]);
        $u = $st->fetch() ?: null;
        json_out([
            'success' => true,
            'used' => $used,
            'limit' => $limit,
            'left' => max(0, $limit - $used),
            'registered' => (bool)($u && !empty($u['phone']) && !empty($u['email'])),
            'user' => $u ? ['name' => $u['name'], 'email' => $u['email'], 'phone' => $u['phone'], 'studio' => $u['studio']] : null,
            'license_key' => (string)($u['license_key'] ?? ''),
        ]);
    }

    if ($PATH === '/api/v1/free/claim' && $METHOD === 'POST') {
        $b = body_json();
        $mid = free_machine_id($b);
        $limit = (int)get_setting($pdo, 'free_daily_projects', '2');
        if ($limit <= 0) {
            json_out(['success' => false, 'limit' => 0, 'left' => 0, 'error' => 'The free plan is paused. Please choose a plan.'], 403);
        }
        // Check or upsert user registration (mandatory for Free Trial across Mobile, Mac & Windows)
        $prof = is_array($b['user_profile'] ?? null) ? $b['user_profile'] : [];
        $pName = trim((string)($prof['name'] ?? $b['name'] ?? ''));
        $pEmail = strtolower(trim((string)($prof['email'] ?? $b['email'] ?? '')));
        $pPhone = preg_replace('/[^0-9+]/', '', (string)($prof['phone'] ?? $b['phone'] ?? ''));
        $pStudio = trim((string)($prof['studio'] ?? $b['studio'] ?? ''));
        $pPlatform = trim((string)($prof['platform'] ?? $b['platform'] ?? ''));

        if ($pName !== '' && strlen($pPhone) >= 10 && filter_var($pEmail, FILTER_VALIDATE_EMAIL)) {
            upsert_trial_user($pdo, $pName, $pEmail, $pPhone, $pStudio, $mid, $pPlatform);
        } else {
            $chk = $pdo->prepare('SELECT id FROM users WHERE machine_id = ? AND phone != \'\' AND email != \'\' LIMIT 1');
            $chk->execute([$mid]);
            if (!$chk->fetchColumn()) {
                json_out([
                    'success' => false,
                    'needs_registration' => true,
                    'error' => 'Free Trial (2 Edits / Day) ke liye My Profile me apna Name, WhatsApp Number aur Email register karna zaroori hai.',
                ], 403);
            }
        }

        // Many made-up computer ids from one connection are capped too
        rate_limit($pdo, 'freeclaim:' . client_ip(), max(6, $limit * 4), 86400);
        $pdo->exec('BEGIN IMMEDIATE');
        $used = free_used($pdo, $mid);
        if ($used >= $limit) {
            $pdo->exec('ROLLBACK');
            json_out(['success' => false, 'used' => $used, 'limit' => $limit, 'left' => 0,
                'error' => "Today's $limit Free Edits are used. Upgrade to a plan for unlimited edits, or come back tomorrow."], 429);
        }
        $pdo->prepare('DELETE FROM free_usage WHERE day < ?')->execute([date('Y-m-d', time() - 86400 * 40)]);
        $pdo->prepare('INSERT INTO free_usage (machine_id, day, count) VALUES (?, ?, 1) ON CONFLICT(machine_id, day) DO UPDATE SET count = count + 1')
            ->execute([$mid, free_day()]);
        $pdo->prepare('INSERT INTO usage_logs (license_key, machine_id, job_name, timestamp) VALUES (?, ?, ?, ?)')
            ->execute(['FREE', $mid, substr((string)($b['job_name'] ?? 'Free project'), 0, 120), time()]);
        $pdo->exec('COMMIT');
        json_out(['success' => true, 'used' => $used + 1, 'limit' => $limit, 'left' => $limit - $used - 1] + free_grant($mid, $used + 1, $limit));
    }

    if ($PATH === '/api/v1/license/activate' && $METHOD === 'POST') {
        rate_limit($pdo, 'activate:' . client_ip(), 20, 3600);
        $b = body_json();
        $lic = check_license($pdo, (string)($b['license_key'] ?? ''), strtoupper((string)($b['machine_id'] ?? '')), true, (string)($b['platform'] ?? ''));
        $pdo->prepare('UPDATE licenses SET last_seen_at = ?, app_version = ?, platform = ? WHERE id = ?')
            ->execute([time(), substr((string)($b['app_version'] ?? ''), 0, 20), substr((string)($b['platform'] ?? ''), 0, 20), $lic['id']]);
        json_out(['success' => true] + signed_token($lic, $lic['active_device_id'] ?? $lic['bound_machine_id']));
    }

    if ($PATH === '/api/v1/license/refresh' && $METHOD === 'POST') {
        rate_limit($pdo, 'refresh:' . client_ip(), 120, 3600);
        $b = body_json();
        $lic = check_license($pdo, (string)($b['license_key'] ?? ''), strtoupper((string)($b['machine_id'] ?? '')), false, (string)($b['platform'] ?? ''));
        $pdo->prepare('UPDATE licenses SET last_seen_at = ? WHERE id = ?')->execute([time(), $lic['id']]);
        json_out(['success' => true] + signed_token($lic, $lic['active_device_id'] ?? $lic['bound_machine_id']));
    }

    if ($PATH === '/api/v1/license/consume' && $METHOD === 'POST') {
        rate_limit($pdo, 'consume:' . client_ip(), 60, 3600);
        $b = body_json();
        $lic = check_license($pdo, (string)($b['license_key'] ?? ''), strtoupper((string)($b['machine_id'] ?? '')), false, (string)($b['platform'] ?? ''));
        $devId = $lic['active_device_id'] ?? $lic['bound_machine_id'];
        if ($lic['plan_type'] === 'local') {
            json_out(['success' => false, 'error' => 'Smart AI is not part of your AI Editing plan. Add a Smart AI Editing pack.'], 402);
        }
        if ($lic['plan_type'] !== 'unlimited') {
            $st = $pdo->prepare('UPDATE licenses SET credits = credits - 1 WHERE id = ? AND credits > 0');
            $st->execute([$lic['id']]);
            if ($st->rowCount() === 0) {
                json_out(['success' => false, 'error' => 'No wedding credits left. Please recharge.'], 402);
            }
        }
        $pdo->prepare('INSERT INTO usage_logs (license_key, machine_id, job_name, timestamp) VALUES (?, ?, ?, ?)')
            ->execute([$lic['license_key'], $devId, substr((string)($b['job_name'] ?? 'Wedding'), 0, 120), time()]);
        json_out(['success' => true] + signed_token(load_license($pdo, $lic['license_key']), $devId));
    }

    // Smart AI audio (ElevenLabs): background music and voiceover, the API key never leaves the server
    if (($PATH === '/api/v1/audio/music' || $PATH === '/api/v1/audio/voice') && $METHOD === 'POST') {
        @set_time_limit(300);
        $lic = smart_ai_license($pdo);
        rate_limit($pdo, 'audio:' . $lic['license_key'], (int)get_setting($pdo, 'audio_daily_limit', '10'), 86400);
        $apiKey = get_setting($pdo, 'elevenlabs_api_key');
        if ($apiKey === '') {
            json_out(['success' => false, 'error' => 'AI audio is not set up on the server yet. Contact the admin.'], 503);
        }
        $b = body_json();
        if ($PATH === '/api/v1/audio/music') {
            $prompt = trim(substr((string)($b['prompt'] ?? ''), 0, 1500));
            $seconds = max(10, min(300, (int)($b['seconds'] ?? 120)));
            $url = ELEVEN_API . '/music?output_format=mp3_44100_128';
            $req = ['prompt' => $prompt, 'music_length_ms' => $seconds * 1000, 'force_instrumental' => true];
            $kind = 'eleven-music';
            $amount = $seconds;
        } else {
            $prompt = trim(substr((string)($b['text'] ?? ''), 0, 2500));
            $voice = preg_replace('/[^A-Za-z0-9]/', '', get_setting($pdo, 'elevenlabs_voice_id', DEFAULT_VOICE_ID)) ?: DEFAULT_VOICE_ID;
            $url = ELEVEN_API . '/text-to-speech/' . $voice . '?output_format=mp3_44100_128';
            $req = ['text' => $prompt, 'model_id' => 'eleven_multilingual_v2'];
            $kind = 'eleven-voice';
            $amount = function_exists('mb_strlen') ? mb_strlen($prompt) : strlen($prompt);
        }
        if ($prompt === '') {
            json_out(['success' => false, 'error' => 'Nothing to generate.'], 400);
        }
        [$status, $resp] = http_request('POST', $url, ['Content-Type: application/json', 'xi-api-key: ' . $apiKey], json_encode($req), 290);
        $pdo->prepare('INSERT INTO ai_usage (license_key, model, input_tokens, output_tokens, status, created_at) VALUES (?, ?, ?, ?, ?, ?)')
            ->execute([$lic['license_key'], $kind, $amount, 0, $status, time()]);
        if ($status !== 200) {
            $err = json_decode($resp, true)['detail'] ?? null;
            $msg = is_array($err) ? ($err['message'] ?? $err[0]['msg'] ?? '') : (string)$err;
            json_out(['success' => false, 'error' => 'AI audio service error' . ($msg !== '' ? ": $msg" : " (HTTP $status)")], 502);
        }
        header('Content-Type: audio/mpeg');
        header('Content-Length: ' . strlen($resp));
        echo $resp;
        exit;
    }

    // Claude relay: the Anthropic SDK in the desktop app uses base_url = <server>/index.php/api/v1/ai
    if ($PATH === '/api/v1/ai/v1/messages' && $METHOD === 'POST') {
        @set_time_limit(300);
        $lic = smart_ai_license($pdo);
        rate_limit($pdo, 'ai:' . $lic['license_key'], (int)get_setting($pdo, 'ai_daily_limit', '200'), 86400);

        $apiKey = get_setting($pdo, 'anthropic_api_key');
        if ($apiKey === '') {
            json_out(['type' => 'error', 'error' => ['type' => 'api_error', 'message' => 'AI is not configured on the server yet. Contact the admin.']], 503);
        }
        if ((int)($_SERVER['CONTENT_LENGTH'] ?? 0) > 8 * 1024 * 1024) {
            json_out(['type' => 'error', 'error' => ['type' => 'invalid_request_error', 'message' => 'Request too large.']], 413);
        }
        $req = body_json();
        if (!$req || !empty($req['stream'])) {
            json_out(['type' => 'error', 'error' => ['type' => 'invalid_request_error', 'message' => 'Unsupported request.']], 400);
        }
        // Only what the app needs: no server-side tools, containers, MCP servers or speed/priority options
        $req = array_intersect_key($req, array_flip(['model', 'max_tokens', 'messages', 'system', 'tools', 'tool_choice',
            'output_config', 'cache_control', 'thinking', 'fallbacks', 'stop_sequences', 'metadata']));
        if (isset($req['tools'])) {
            $req['tools'] = array_values(array_filter((array)$req['tools'], fn($t) => is_array($t) && isset($t['input_schema']) && !isset($t['type'])));
        }
        // The admin decides the model and the spend ceiling, never the client
        $model = get_setting($pdo, 'anthropic_model', 'claude-opus-5-5');
        $req['model'] = $model;
        $req['max_tokens'] = min((int)($req['max_tokens'] ?? 4096), MAX_RELAY_TOKENS);

        $headers = [
            'Content-Type: application/json',
            'x-api-key: ' . $apiKey,
            'anthropic-version: ' . ANTHROPIC_VERSION,
        ];
        $betas = array_values(array_intersect(
            array_map('trim', explode(',', (string)($_SERVER['HTTP_ANTHROPIC_BETA'] ?? ''))),
            ALLOWED_BETAS
        ));
        if ($betas) {
            $headers[] = 'anthropic-beta: ' . implode(',', $betas);
        } else {
            unset($req['fallbacks']);
        }

        [$status, $resp] = http_request('POST', ANTHROPIC_URL, $headers, json_encode($req), 290);
        $usage = (json_decode($resp, true)['usage'] ?? []);
        $pdo->prepare('INSERT INTO ai_usage (license_key, model, input_tokens, output_tokens, status, created_at) VALUES (?, ?, ?, ?, ?, ?)')
            ->execute([$lic['license_key'], $model, (int)($usage['input_tokens'] ?? 0), (int)($usage['output_tokens'] ?? 0), $status, time()]);

        http_response_code($status ?: 502);
        header('Content-Type: application/json');
        echo $status ? $resp : json_encode(['type' => 'error', 'error' => ['type' => 'api_error', 'message' => 'Could not reach the AI service.']]);
        exit;
    }

    // ---- Payments ----
    if ($PATH === '/api/v1/pay/order' && $METHOD === 'POST') {
        rate_limit($pdo, 'order:' . client_ip(), 15, 3600);
        if (get_setting($pdo, 'payments_enabled') !== '1') {
            json_out(['success' => false, 'error' => 'Online payments are not enabled yet.'], 503);
        }
        $b = body_json();
        $name = trim((string)($b['name'] ?? ''));
        $email = strtolower(trim((string)($b['email'] ?? '')));
        $phone = preg_replace('/[^0-9+]/', '', (string)($b['phone'] ?? ''));
        if ($name === '' || !filter_var($email, FILTER_VALIDATE_EMAIL) || strlen($phone) < 10) {
            json_out(['success' => false, 'error' => 'Please enter your name, a valid email and phone number.'], 400);
        }
        $plan = $pdo->prepare('SELECT * FROM plans WHERE id = ? AND active = 1 AND price_inr > 0');
        $plan->execute([(int)($b['plan_id'] ?? 0)]);
        $p = $plan->fetch();
        if (!$p) {
            json_out(['success' => false, 'error' => 'Please choose a plan.'], 400);
        }
        $amount = (int)$p['price_inr'] * 100;
        [$keyId, $secret] = razorpay_creds($pdo);
        $receipt = 'avf_' . bin2hex(random_bytes(6));
        [$st, $resp] = http_request('POST', RAZORPAY_API . '/orders', ['Content-Type: application/json'],
            json_encode(['amount' => $amount, 'currency' => 'INR', 'receipt' => $receipt, 'notes' => ['plan' => $p['name'], 'email' => $email]]),
            30, "$keyId:$secret");
        $order = json_decode($resp, true) ?: [];
        if ($st !== 200 || empty($order['id'])) {
            json_out(['success' => false, 'error' => 'Payment gateway error. Please try again later.'], 502);
        }
        $pdo->prepare('INSERT INTO orders (rzp_order_id, plan_id, amount_paise, name, email, phone, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)')
            ->execute([$order['id'], $p['id'], $amount, $name, $email, $phone, 'created', time()]);
        json_out(['success' => true, 'key_id' => $keyId, 'order_id' => $order['id'], 'amount' => $amount, 'plan' => $p['name'],
            'name' => $name, 'email' => $email, 'phone' => $phone]);
    }

    if ($PATH === '/api/v1/pay/verify' && $METHOD === 'POST') {
        rate_limit($pdo, 'verify:' . client_ip(), 30, 3600);
        $b = body_json();
        $orderId = (string)($b['razorpay_order_id'] ?? '');
        $paymentId = (string)($b['razorpay_payment_id'] ?? '');
        $sig = (string)($b['razorpay_signature'] ?? '');
        [, $secret] = razorpay_creds($pdo);
        if (!hash_equals(hash_hmac('sha256', "$orderId|$paymentId", $secret), $sig)) {
            json_out(['success' => false, 'error' => 'Payment could not be verified.'], 400);
        }
        $st = $pdo->prepare('SELECT * FROM orders WHERE rzp_order_id = ?');
        $st->execute([$orderId]);
        $order = $st->fetch();
        if (!$order) {
            json_out(['success' => false, 'error' => 'Order not found.'], 404);
        }
        if (!capture_if_needed($pdo, $paymentId, (int)$order['amount_paise'])) {
            json_out(['success' => false, 'error' => 'Payment is not captured yet. Your key will be emailed once it completes.'], 402);
        }
        json_out(['success' => true, 'license_key' => fulfill_order($pdo, $order, $paymentId)]);
    }

    if ($PATH === '/api/v1/pay/webhook' && $METHOD === 'POST') {
        $raw = file_get_contents('php://input') ?: '';
        $secret = get_setting($pdo, 'razorpay_webhook_secret');
        if ($secret === '' || !hash_equals(hash_hmac('sha256', $raw, $secret), $_SERVER['HTTP_X_RAZORPAY_SIGNATURE'] ?? '')) {
            json_out(['ok' => false], 400);
        }
        $ev = json_decode($raw, true) ?: [];
        $payment = $ev['payload']['payment']['entity'] ?? [];
        if (in_array($ev['event'] ?? '', ['payment.captured', 'order.paid'], true) && !empty($payment['order_id'])) {
            $st = $pdo->prepare('SELECT * FROM orders WHERE rzp_order_id = ?');
            $st->execute([$payment['order_id']]);
            if (($order = $st->fetch()) && (int)$payment['amount'] === (int)$order['amount_paise']) {
                fulfill_order($pdo, $order, (string)$payment['id']);
            }
        }
        json_out(['ok' => true]);
    }

    // Old app versions (v1.0) used /verify and /consume and trusted any reply
    if (str_contains($PATH, '/verify') || str_contains($PATH, '/license/consume')) {
        json_out(['success' => false, 'error' => 'Please update AI Video Flow to the latest version.'], 410);
    }
    json_out(['success' => false, 'error' => 'Not found'], 404);
}

// Old v1.0 clients also called /verify and /consume at the root
if (in_array($PATH, ['/verify', '/consume'], true)) {
    json_out(['success' => false, 'error' => 'Please update AI Video Flow to the latest version.'], 410);
}

// ---------------------------------------------------------------------------
// Admin API
// ---------------------------------------------------------------------------
if (str_starts_with($PATH, '/api/admin/')) {
    start_admin_session();
    require_admin($METHOD);
    $b = $METHOD === 'POST' ? body_json() : [];
    $r = substr($PATH, strlen('/api/admin'));

    switch ("$METHOD $r") {
        case 'GET /overview':
            $today = strtotime('today');
            json_out(['success' => true,
                'users' => (int)$pdo->query('SELECT COUNT(*) FROM users')->fetchColumn(),
                'active_licenses' => (int)$pdo->query("SELECT COUNT(*) FROM licenses WHERE status = 'active'")->fetchColumn(),
                'revenue_inr' => (int)$pdo->query("SELECT COALESCE(SUM(amount_paise), 0) FROM orders WHERE status = 'paid'")->fetchColumn() / 100,
                'paid_orders' => (int)$pdo->query("SELECT COUNT(*) FROM orders WHERE status = 'paid'")->fetchColumn(),
                'ai_requests_today' => (int)$pdo->query("SELECT COUNT(*) FROM ai_usage WHERE created_at >= $today")->fetchColumn(),
                'jobs_today' => (int)$pdo->query("SELECT COUNT(*) FROM usage_logs WHERE timestamp >= $today")->fetchColumn(),
                'ai_ready' => get_setting($pdo, 'anthropic_api_key') !== '',
                'payments_ready' => get_setting($pdo, 'payments_enabled') === '1',
            ]);

        case 'GET /users':
            $q = '%' . trim((string)($_GET['q'] ?? '')) . '%';
            $st = $pdo->prepare('SELECT u.*, (SELECT COUNT(*) FROM licenses l WHERE l.user_id = u.id) AS license_count
                                 FROM users u WHERE u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ? OR u.studio LIKE ?
                                 ORDER BY u.id DESC LIMIT 500');
            $st->execute([$q, $q, $q, $q]);
            json_out(['success' => true, 'users' => $st->fetchAll()]);

        case 'POST /users/create':
            $name = trim((string)($b['name'] ?? ''));
            $email = strtolower(trim((string)($b['email'] ?? '')));
            if ($name === '') {
                json_out(['success' => false, 'error' => 'Name is required.'], 400);
            }
            if ($email !== '' && !filter_var($email, FILTER_VALIDATE_EMAIL)) {
                json_out(['success' => false, 'error' => 'Email is not valid.'], 400);
            }
            if ($email !== '') {
                $dup = $pdo->prepare('SELECT id FROM users WHERE email = ?');
                $dup->execute([$email]);
                if ($dup->fetchColumn()) {
                    json_out(['success' => false, 'error' => 'A user with this email already exists.'], 409);
                }
            }
            $pdo->prepare('INSERT INTO users (name, email, phone, studio, notes, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)')
                ->execute([$name, $email, trim((string)($b['phone'] ?? '')), trim((string)($b['studio'] ?? '')), trim((string)($b['notes'] ?? '')), 'manual', time()]);
            $uid = (int)$pdo->lastInsertId();
            $key = null;
            if (!empty($b['plan_id'])) {
                $st = $pdo->prepare('SELECT * FROM plans WHERE id = ?');
                $st->execute([(int)$b['plan_id']]);
                if ($p = $st->fetch()) {
                    $key = issue_from_plan($pdo, $uid, $p, 'Manual');
                }
            }
            json_out(['success' => true, 'user_id' => $uid, 'license_key' => $key]);

        case 'POST /users/update':
            $pdo->prepare('UPDATE users SET name = ?, email = ?, phone = ?, studio = ?, notes = ? WHERE id = ?')
                ->execute([trim((string)$b['name']), strtolower(trim((string)$b['email'])), trim((string)$b['phone']), trim((string)$b['studio']), trim((string)$b['notes']), (int)$b['id']]);
            json_out(['success' => true]);

        case 'POST /users/delete':
            $pdo->prepare("UPDATE licenses SET status = 'revoked' WHERE user_id = ?")->execute([(int)$b['id']]);
            $pdo->prepare('DELETE FROM users WHERE id = ?')->execute([(int)$b['id']]);
            json_out(['success' => true]);

        case 'GET /licenses':
            $uid = (int)($_GET['user_id'] ?? 0);
            $sql = 'SELECT l.*, u.name AS user_name, u.email AS user_email FROM licenses l LEFT JOIN users u ON u.id = l.user_id';
            $st = $uid ? $pdo->prepare("$sql WHERE l.user_id = ? ORDER BY l.id DESC") : $pdo->prepare("$sql ORDER BY l.id DESC LIMIT 1000");
            $st->execute($uid ? [$uid] : []);
            json_out(['success' => true, 'licenses' => $st->fetchAll()]);

        case 'POST /licenses/issue':
            $uid = (int)($b['user_id'] ?? 0);
            if (!$pdo->query("SELECT 1 FROM users WHERE id = $uid")->fetchColumn()) {
                json_out(['success' => false, 'error' => 'Choose a user first.'], 400);
            }
            if (!empty($b['plan_id'])) {
                $st = $pdo->prepare('SELECT * FROM plans WHERE id = ?');
                $st->execute([(int)$b['plan_id']]);
                $p = $st->fetch();
                if (!$p) {
                    json_out(['success' => false, 'error' => 'Plan not found.'], 400);
                }
                $key = issue_from_plan($pdo, $uid, $p, (string)($b['notes'] ?? ''));
            } else {
                $credits = (int)($b['credits'] ?? 0);
                $kind = ($b['kind'] ?? 'ai') === 'local' ? 'local' : 'ai';
                $unlimited = $kind === 'ai' && !empty($b['unlimited']);
                $label = $kind === 'local' ? 'Custom - Pro editing' : ($unlimited ? 'Custom - Smart AI Unlimited' : "Custom - $credits Smart AI weddings");
                $key = issue_license($pdo, $uid, $label, $credits, $unlimited, (int)($b['validity_days'] ?? 365), (string)($b['notes'] ?? ''), $kind);
            }
            json_out(['success' => true, 'license_key' => $key]);

        case 'POST /licenses/add-credits':
            $n = (int)($b['credits'] ?? 0);
            if ($n === 0 || abs($n) > 10000) {
                json_out(['success' => false, 'error' => 'Enter a credit amount.'], 400);
            }
            $pdo->prepare('UPDATE licenses SET credits = MAX(0, credits + ?) WHERE id = ?')->execute([$n, (int)$b['id']]);
            json_out(['success' => true]);

        case 'POST /licenses/extend':
            $days = max(1, min(3650, (int)($b['days'] ?? 365)));
            $pdo->prepare('UPDATE licenses SET expires_at = MAX(COALESCE(expires_at, 0), ?) + ? WHERE id = ?')->execute([time(), 86400 * $days, (int)$b['id']]);
            json_out(['success' => true]);

        case 'POST /licenses/reset-machine':
            $pdo->prepare('UPDATE licenses SET bound_machine_id = NULL, bound_mobile_id = NULL WHERE id = ?')->execute([(int)$b['id']]);
            json_out(['success' => true]);

        case 'POST /licenses/status':
            $status = in_array($b['status'] ?? '', ['active', 'revoked'], true) ? $b['status'] : 'revoked';
            $pdo->prepare('UPDATE licenses SET status = ? WHERE id = ?')->execute([$status, (int)$b['id']]);
            json_out(['success' => true]);

        case 'POST /licenses/delete':
            $pdo->prepare('DELETE FROM licenses WHERE id = ?')->execute([(int)$b['id']]);
            json_out(['success' => true]);

        case 'GET /plans':
            json_out(['success' => true, 'plans' => $pdo->query('SELECT * FROM plans ORDER BY sort, id')->fetchAll()]);

        case 'POST /plans/save':
            $kind = ($b['kind'] ?? 'ai') === 'local' ? 'local' : 'ai';
            $vals = [trim((string)$b['name']), max(0, (int)$b['credits']), (empty($b['unlimited']) || $kind === 'local') ? 0 : 1,
                max(0, (int)$b['price_inr']), max(1, (int)$b['validity_days']), empty($b['active']) ? 0 : 1, (int)($b['sort'] ?? 0),
                $kind, max(0, (int)($b['mrp_inr'] ?? 0)), substr(trim((string)($b['badge'] ?? '')), 0, 24)];
            if ($vals[0] === '') {
                json_out(['success' => false, 'error' => 'Plan name is required.'], 400);
            }
            if (!empty($b['id'])) {
                $pdo->prepare('UPDATE plans SET name = ?, credits = ?, unlimited = ?, price_inr = ?, validity_days = ?, active = ?, sort = ?, kind = ?, mrp_inr = ?, badge = ? WHERE id = ?')
                    ->execute([...$vals, (int)$b['id']]);
            } else {
                $pdo->prepare('INSERT INTO plans (name, credits, unlimited, price_inr, validity_days, active, sort, kind, mrp_inr, badge) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)')->execute($vals);
            }
            json_out(['success' => true]);

        case 'GET /settings/free':
            json_out(['success' => true, 'free_daily_projects' => (int)get_setting($pdo, 'free_daily_projects', '2')]);

        case 'POST /settings/free':
            set_setting($pdo, 'free_daily_projects', (string)max(0, min(50, (int)($b['free_daily_projects'] ?? 2))));
            json_out(['success' => true]);

        case 'POST /plans/delete':
            $pdo->prepare('DELETE FROM plans WHERE id = ?')->execute([(int)$b['id']]);
            json_out(['success' => true]);

        case 'GET /orders':
            json_out(['success' => true, 'orders' => $pdo->query('SELECT o.*, p.name AS plan_name FROM orders o LEFT JOIN plans p ON p.id = o.plan_id ORDER BY o.id DESC LIMIT 500')->fetchAll()]);

        case 'GET /usage':
            json_out(['success' => true,
                'jobs' => $pdo->query('SELECT * FROM usage_logs ORDER BY id DESC LIMIT 200')->fetchAll(),
                'ai' => $pdo->query('SELECT * FROM ai_usage ORDER BY id DESC LIMIT 200')->fetchAll(),
            ]);

        case 'GET /settings':
            $host = $_SERVER['HTTP_HOST'] ?? '';
            json_out(['success' => true,
                'payments_enabled' => get_setting($pdo, 'payments_enabled') === '1',
                'razorpay_key_id' => get_setting($pdo, 'razorpay_key_id'),
                'razorpay_key_secret' => mask_secret(get_setting($pdo, 'razorpay_key_secret')),
                'razorpay_webhook_secret' => mask_secret(get_setting($pdo, 'razorpay_webhook_secret')),
                'webhook_url' => "https://$host" . $GLOBALS['BASE'] . '/api/v1/pay/webhook',
                'buy_url' => "https://$host" . $GLOBALS['BASE'] . '/buy',
                'download_url' => "https://$host" . $GLOBALS['BASE'] . '/download',
                'anthropic_api_key' => mask_secret(get_setting($pdo, 'anthropic_api_key')),
                'anthropic_model' => get_setting($pdo, 'anthropic_model', 'claude-opus-5-5'),
                'ai_daily_limit' => (int)get_setting($pdo, 'ai_daily_limit', '200'),
                'models' => AI_MODELS,
                'elevenlabs_api_key' => mask_secret(get_setting($pdo, 'elevenlabs_api_key')),
                'elevenlabs_voice_id' => get_setting($pdo, 'elevenlabs_voice_id', DEFAULT_VOICE_ID),
                'audio_daily_limit' => (int)get_setting($pdo, 'audio_daily_limit', '10'),
                'app_latest_version' => get_setting($pdo, 'app_latest_version'),
                'app_download_url' => get_setting($pdo, 'app_download_url', DEFAULT_DOWNLOAD_URL),
                'app_update_notes' => get_setting($pdo, 'app_update_notes'),
                'app_assets' => array_map(fn($k) => ['key' => $k, 'label' => UPDATE_ASSETS[$k],
                    'url' => get_setting($pdo, "app_asset_url_$k"), 'sha256' => get_setting($pdo, "app_asset_sha256_$k")], array_keys(UPDATE_ASSETS)),
                'mobile_latest_version' => get_setting($pdo, 'mobile_latest_version'),
                'mobile_apk_url' => get_setting($pdo, 'mobile_apk_url'),
                'mobile_update_notes' => get_setting($pdo, 'mobile_update_notes'),
                'php' => PHP_VERSION,
                'curl' => function_exists('curl_init'),
            ]);

        case 'POST /settings/payment':
            set_setting($pdo, 'payments_enabled', empty($b['payments_enabled']) ? '0' : '1');
            set_setting($pdo, 'razorpay_key_id', trim((string)($b['razorpay_key_id'] ?? '')));
            foreach (['razorpay_key_secret', 'razorpay_webhook_secret'] as $k) {
                if (trim((string)($b[$k] ?? '')) !== '') {
                    set_setting($pdo, $k, trim((string)$b[$k]), true);
                }
            }
            json_out(['success' => true]);

        case 'POST /settings/ai':
            if (trim((string)($b['anthropic_api_key'] ?? '')) !== '') {
                set_setting($pdo, 'anthropic_api_key', trim((string)$b['anthropic_api_key']), true);
            }
            $model = (string)($b['anthropic_model'] ?? 'claude-opus-5-5');
            set_setting($pdo, 'anthropic_model', array_key_exists($model, AI_MODELS) ? $model : 'claude-opus-5-5');
            set_setting($pdo, 'ai_daily_limit', (string)max(1, min(5000, (int)($b['ai_daily_limit'] ?? 200))));
            json_out(['success' => true]);

        case 'POST /settings/ai-test':
            $apiKey = get_setting($pdo, 'anthropic_api_key');
            if ($apiKey === '') {
                json_out(['success' => false, 'error' => 'Save a Claude API key first.'], 400);
            }
            [$st, $resp] = http_request('POST', ANTHROPIC_URL,
                ['Content-Type: application/json', 'x-api-key: ' . $apiKey, 'anthropic-version: ' . ANTHROPIC_VERSION],
                json_encode(['model' => get_setting($pdo, 'anthropic_model', 'claude-opus-5-5'), 'max_tokens' => 64,
                    'messages' => [['role' => 'user', 'content' => 'Reply with the single word: connected']]]), 60);
            $data = json_decode($resp, true) ?: [];
            if ($st === 200) {
                json_out(['success' => true, 'message' => 'Claude is connected (' . ($data['model'] ?? '') . ').']);
            }
            json_out(['success' => false, 'error' => $data['error']['message'] ?? "HTTP $st"], 400);

        case 'POST /settings/audio':
            if (trim((string)($b['elevenlabs_api_key'] ?? '')) !== '') {
                set_setting($pdo, 'elevenlabs_api_key', trim((string)$b['elevenlabs_api_key']), true);
            }
            set_setting($pdo, 'elevenlabs_voice_id', preg_replace('/[^A-Za-z0-9]/', '', (string)($b['elevenlabs_voice_id'] ?? '')) ?: DEFAULT_VOICE_ID);
            set_setting($pdo, 'audio_daily_limit', (string)max(1, min(200, (int)($b['audio_daily_limit'] ?? 10))));
            json_out(['success' => true]);

        case 'POST /settings/audio-test':
            $apiKey = get_setting($pdo, 'elevenlabs_api_key');
            if ($apiKey === '') {
                json_out(['success' => false, 'error' => 'Save an ElevenLabs API key first.'], 400);
            }
            $voice = get_setting($pdo, 'elevenlabs_voice_id', DEFAULT_VOICE_ID);
            [$st, $resp] = http_request('GET', ELEVEN_API . '/voices/' . rawurlencode($voice), ['xi-api-key: ' . $apiKey], null, 30);
            $data = json_decode($resp, true) ?: [];
            if ($st === 200) {
                json_out(['success' => true, 'message' => 'ElevenLabs is connected · voice: ' . ($data['name'] ?? $voice)]);
            }
            $err = $data['detail'] ?? null;
            json_out(['success' => false, 'error' => is_array($err) ? ($err['message'] ?? "HTTP $st") : ($err ?: "HTTP $st")], 400);

        case 'POST /settings/update':
            $ver = trim((string)($b['app_latest_version'] ?? ''));
            if ($ver !== '' && !preg_match('/^\d+(\.\d+){1,3}$/', $ver)) {
                json_out(['success' => false, 'error' => 'Desktop version must look like 1.1.13'], 400);
            }
            $dl = trim((string)($b['app_download_url'] ?? ''));
            if ($dl !== '' && !preg_match('#^https://#', $dl)) {
                json_out(['success' => false, 'error' => 'Desktop download link must start with https://'], 400);
            }
            $mVer = trim((string)($b['mobile_latest_version'] ?? ''));
            if ($mVer !== '' && !preg_match('/^\d+(\.\d+){1,3}$/', $mVer)) {
                json_out(['success' => false, 'error' => 'Mobile version must look like 1.1.13'], 400);
            }
            $mDl = trim((string)($b['mobile_apk_url'] ?? ''));
            if ($mDl !== '' && !preg_match('#^https://#', $mDl)) {
                json_out(['success' => false, 'error' => 'Mobile APK link must start with https://'], 400);
            }
            foreach (UPDATE_ASSETS as $key => $label) {
                $aUrl = trim((string)($b["app_asset_url_$key"] ?? ''));
                $aSha = strtolower(trim((string)($b["app_asset_sha256_$key"] ?? '')));
                if ($aUrl !== '' && !preg_match('#^https://#', $aUrl)) {
                    json_out(['success' => false, 'error' => "$label link must start with https://"], 400);
                }
                if ($aSha !== '' && !preg_match('/^[0-9a-f]{64}$/', $aSha)) {
                    json_out(['success' => false, 'error' => "$label SHA-256 must be 64 hex characters"], 400);
                }
                set_setting($pdo, "app_asset_url_$key", $aUrl);
                set_setting($pdo, "app_asset_sha256_$key", $aSha);
            }
            set_setting($pdo, 'app_latest_version', $ver);
            set_setting($pdo, 'app_download_url', $dl ?: DEFAULT_DOWNLOAD_URL);
            set_setting($pdo, 'app_update_notes', trim(substr((string)($b['app_update_notes'] ?? ''), 0, 300)));
            set_setting($pdo, 'mobile_latest_version', $mVer);
            set_setting($pdo, 'mobile_apk_url', $mDl);
            set_setting($pdo, 'mobile_update_notes', trim(substr((string)($b['mobile_update_notes'] ?? ''), 0, 300)));
            json_out(['success' => true]);

        case 'GET /2fa/status':
            json_out(['success' => true, 'enabled' => twofa_enabled($pdo),
                'recovery_left' => count(json_decode(get_setting($pdo, 'totp_recovery', '[]'), true) ?: [])]);

        case 'POST /2fa/setup':
            $secret = base32_encode(random_bytes(20));
            set_setting($pdo, 'totp_pending', $secret, true);
            $label = rawurlencode(APP_NAME . ' Admin:' . ($_SERVER['HTTP_HOST'] ?? 'admin'));
            json_out(['success' => true, 'secret' => trim(chunk_split($secret, 4, ' ')),
                'uri' => "otpauth://totp/$label?secret=$secret&issuer=" . rawurlencode(APP_NAME) . '&digits=6&period=30']);

        case 'POST /2fa/enable':
            $pending = get_setting($pdo, 'totp_pending');
            $step = $pending !== '' ? totp_match($pending, (string)($b['code'] ?? '')) : null;
            if ($step === null) {
                json_out(['success' => false, 'error' => 'That code is not correct. Check the time on your phone and try again.'], 400);
            }
            $recovery = [];
            $hashes = [];
            for ($i = 0; $i < 8; $i++) {
                $c = strtoupper(substr(base32_encode(random_bytes(7)), 0, 10));
                $recovery[] = substr($c, 0, 5) . '-' . substr($c, 5);
                $hashes[] = hash('sha256', $c);
            }
            set_setting($pdo, 'totp_secret', $pending, true);
            set_setting($pdo, 'totp_last_step', (string)$step);
            set_setting($pdo, 'totp_recovery', json_encode($hashes), true);
            set_setting($pdo, 'totp_pending', '');
            set_setting($pdo, 'totp_enabled', '1');
            json_out(['success' => true, 'recovery_codes' => $recovery]);

        case 'POST /2fa/disable':
            if (!admin_password_ok($pdo, (string)($b['password'] ?? '')) || !twofa_verify($pdo, (string)($b['code'] ?? ''))) {
                json_out(['success' => false, 'error' => 'Password or authenticator code is wrong.'], 400);
            }
            set_setting($pdo, 'totp_enabled', '0');
            set_setting($pdo, 'totp_secret', '');
            set_setting($pdo, 'totp_recovery', '[]');
            json_out(['success' => true]);

        case 'POST /settings/password':
            if (!admin_password_ok($pdo, (string)($b['current'] ?? ''))) {
                json_out(['success' => false, 'error' => 'Current password is wrong.'], 400);
            }
            $new = (string)($b['new'] ?? '');
            if (strlen($new) < 10) {
                json_out(['success' => false, 'error' => 'Use at least 10 characters.'], 400);
            }
            set_setting($pdo, 'admin_password_hash', password_hash($new, PASSWORD_DEFAULT));
            json_out(['success' => true]);
    }
    json_out(['success' => false, 'error' => 'Not found'], 404);
}

// ---------------------------------------------------------------------------
// Permanent download links: /download (picks by device), /download/windows, /download/mac-arm64,
// /download/mac-x86_64, /download/android. They always redirect to the latest installer set in the admin portal.
// ---------------------------------------------------------------------------
if ($PATH === '/download' || str_starts_with($PATH, '/download/')) {
    $fallback = get_setting($pdo, 'app_download_url', DEFAULT_DOWNLOAD_URL);
    $links = [];
    foreach (UPDATE_ASSETS as $key => $_) {
        $links[$key] = update_asset($pdo, $key)['url'] ?? '';
    }
    $apk = __DIR__ . '/downloads/' . DOWNLOAD_FILES['android'];
    $links['android'] = get_setting($pdo, 'mobile_apk_url') ?: (is_file($apk) ? $SCRIPT_DIR . '/downloads/' . DOWNLOAD_FILES['android'] : '');
    $which = substr($PATH, strlen('/download/'));
    if ($PATH === '/download') {
        $ua = $_SERVER['HTTP_USER_AGENT'] ?? '';
        $which = str_contains($ua, 'Android') ? 'android' : (str_contains($ua, 'Windows') ? 'windows' : '');
    }
    header('Cache-Control: no-store');
    if ($which !== '' && array_key_exists($which, $links)) {
        header('Location: ' . ($links[$which] ?: $fallback), true, 302);
        exit;
    }
    if ($which !== '') {
        http_response_code(404);
    }
    // Mac (Apple Silicon and Intel can't be told apart from the browser) and anything else: let the user pick
    $names = ['windows' => 'Windows (64-bit)', 'mac-arm64' => 'Mac — Apple Silicon (M1/M2/M3/M4)', 'mac-x86_64' => 'Mac — Intel', 'android' => 'Android (Reels app)'];
    $ver = get_setting($pdo, 'app_latest_version');
    ?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Download AI Video Flow</title>
<style>
  body { margin: 0; font-family: system-ui, -apple-system, Segoe UI, sans-serif; background: #0f1115; color: #eceef2; display: grid; place-items: center; min-height: 100vh; padding: 16px; box-sizing: border-box; }
  main { width: 100%; max-width: 440px; }
  h1 { font-size: 22px; margin: 0 0 4px; } p { color: #9aa1ad; margin: 0 0 20px; font-size: 14px; }
  a { display: block; padding: 14px 16px; margin-bottom: 10px; border-radius: 10px; background: #1a1d24; border: 1px solid #2a2f3a; color: inherit; text-decoration: none; font-weight: 600; }
  a:hover { border-color: #e8b95f; }
</style>
</head>
<body><main>
  <h1>Download AI Video Flow</h1>
  <p><?= $ver !== '' ? 'Latest version ' . h($ver) . ' · ' : '' ?>Choose your computer or phone. On a Mac, Apple menu → About This Mac shows the chip.</p>
  <?php foreach ($names as $key => $label): ?>
    <a href="<?= h($BASE) ?>/download/<?= h($key) ?>"><?= h($label) ?></a>
  <?php endforeach; ?>
</main></body>
</html>
<?php
    exit;
}

// ---------------------------------------------------------------------------
// Public buy page
// ---------------------------------------------------------------------------
if ($PATH === '/buy') {
    $plans = $pdo->query('SELECT * FROM plans WHERE active = 1 AND price_inr > 0 ORDER BY sort, id')->fetchAll();
    $enabled = get_setting($pdo, 'payments_enabled') === '1';
    header("Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://checkout.razorpay.com; frame-src https://api.razorpay.com https://checkout.razorpay.com; connect-src 'self' https://lumberjack.razorpay.com; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data: https:");
    ?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Buy AI Video Flow</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root { --bg:#0b0d12; --card:#12151d; --line:#232837; --text:#eef0f5; --muted:#8b93a7; --gold:#e8b95f; --accent:#7c5cff; }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font-family:Inter, system-ui, sans-serif; }
  .wrap { max-width:1040px; margin:0 auto; padding:48px 16px 64px; }
  h1 { font-size:32px; margin:0 0 8px; letter-spacing:-0.02em; }
  .sub { color:var(--muted); margin:0 0 32px; }
  .switch { display:inline-flex; gap:4px; padding:4px; background:#0b0d12; border:1px solid var(--line); border-radius:12px; margin:8px 0 18px; }
  .switch button { background:none; color:var(--muted); padding:10px 22px; border-radius:9px; font-weight:700; }
  .switch button.on { background:var(--card); color:var(--gold); box-shadow:0 0 0 1px var(--gold); }
  .group-sub { color:var(--muted); margin:0 0 14px; font-size:14px; }
  .plans { display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:16px; }
  .plan { position:relative; background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px; cursor:pointer; transition:border-color .15s; }
  .plan:hover { border-color:#3a4256; }
  .plan.sel { border-color:var(--gold); box-shadow:0 0 0 1px var(--gold); }
  .plan h3 { margin:0 0 6px; font-size:16px; }
  .badge { position:absolute; top:-10px; right:14px; background:var(--gold); color:#111; font-size:11px; font-weight:700; padding:2px 10px; border-radius:999px; }
  .price { font-size:30px; font-weight:700; margin:8px 0 0; letter-spacing:-.02em; }
  .price s { font-size:15px; color:var(--muted); font-weight:500; margin-left:6px; }
  .unit { color:var(--gold); font-size:13px; font-weight:600; min-height:20px; }
  .meta { color:var(--muted); font-size:13px; }
  .feat { list-style:none; padding:0; margin:14px 0 0; display:grid; gap:6px; font-size:13px; color:#c8cdd8; }
  .feat li::before { content:'✓  '; color:#22c55e; font-weight:700; }
  form { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:24px; margin-top:24px; display:grid; gap:12px; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); align-items:end; }
  label { font-size:12px; color:var(--muted); display:block; margin-bottom:6px; }
  input { width:100%; background:#0b0d12; border:1px solid var(--line); color:var(--text); padding:11px 12px; border-radius:8px; font:inherit; }
  button { background:var(--gold); color:#111; border:0; padding:12px 18px; border-radius:8px; font-weight:700; font:inherit; cursor:pointer; }
  .note { margin-top:20px; padding:16px; border-radius:10px; border:1px solid var(--line); background:var(--card); }
  .key { font-family:ui-monospace, Menlo, monospace; font-size:20px; color:var(--gold); word-break:break-all; }
</style>
</head>
<body>
<div class="wrap">
  <h1><?= h(APP_NAME) ?> plans</h1>
  <p class="sub">Pick a plan, pay with UPI, card or net banking. Your license key appears right here and is emailed to you.</p>
  <?php if (!$enabled || !$plans): ?>
    <div class="note">Online payment is not available right now. Please contact us on WhatsApp to buy a license.</div>
  <?php else: ?>
    <?php
      $groups = [
          'local' => ['AI Editing', 'Automatic trimming, beat sync and silence cutting with no daily limit. The free app allows 1 project per day.'],
          'ai' => ['Smart AI Editing', 'Smart AI picks your best wedding moments and edits on command. Unlimited AI Editing included.'],
      ];
      $first = true;
      $wanted = (int)($_GET['plan'] ?? 0);
      $wantedKind = 'local';
      foreach ($plans as $p) { if ((int)$p['id'] === $wanted) { $wantedKind = $p['kind'] ?: 'ai'; } }
    ?>
      <div class="switch">
        <?php foreach ($groups as $kind => [$title]): ?>
          <button type="button" data-g="<?= h($kind) ?>" class="<?= $kind === $wantedKind ? 'on' : '' ?>"><?= h($title) ?></button>
        <?php endforeach; ?>
      </div>
    <?php
      foreach ($groups as $kind => [$title, $sub]):
          $items = array_values(array_filter($plans, fn($p) => ($p['kind'] ?: 'ai') === $kind));
          if (!$items) { continue; }
    ?>
      <div class="grp" data-g="<?= h($kind) ?>"<?= $kind === $wantedKind ? '' : ' hidden' ?>>
      <p class="group-sub"><?= h($sub) ?></p>
      <div class="plans">
        <?php foreach ($items as $p):
            $price = (int)$p['price_inr'];
            $mrp = (int)$p['mrp_inr'];
            if ($kind === 'local') {
                $unit = (int)$p['validity_days'] >= 300 ? '₹' . number_format($price / 12, 0) . ' / month, billed yearly' : 'per month';
                $what = ['No daily limit — unlimited projects', 'All workflows & editors', (int)$p['validity_days'] . ' days · 1 computer'];
            } elseif ((int)$p['unlimited']) {
                $unit = 'Unlimited Smart AI weddings';
                $what = ['Unlimited Smart AI shot review', 'AI music & voiceover', 'Smart AI Assistant', 'Unlimited editing · 1 year'];
            } else {
                $unit = '₹' . number_format($price / max(1, (int)$p['credits'])) . ' per wedding';
                $what = [(int)$p['credits'] . ' Smart AI weddings', 'AI music & voiceover', 'Smart AI Assistant', 'Unlimited editing · ' . (int)$p['validity_days'] . ' days'];
            }
            $save = $mrp > $price ? round(100 - $price * 100 / $mrp) : 0;
        ?>
          <div class="plan<?= $first ? ' sel' : '' ?>" data-id="<?= (int)$p['id'] ?>">
            <?php if ($p['badge'] || $save): ?><span class="badge"><?= h($p['badge'] ?: "Save $save%") ?></span><?php endif; ?>
            <h3><?= h($p['name']) ?></h3>
            <div class="price">₹<?= number_format($price) ?><?php if ($mrp > $price): ?><s>₹<?= number_format($mrp) ?></s><?php endif; ?></div>
            <div class="unit"><?= h($unit) ?><?= $save && $p['badge'] ? ' · save ' . $save . '%' : '' ?></div>
            <ul class="feat"><?php foreach ($what as $w): ?><li><?= h($w) ?></li><?php endforeach; ?></ul>
          </div>
        <?php $first = false; endforeach; ?>
      </div>
      </div>
    <?php endforeach; ?>
    <form id="buy">
      <div><label>Full name</label><input name="name" required maxlength="80"></div>
      <div><label>Email</label><input name="email" type="email" required maxlength="120"></div>
      <div><label>Phone</label><input name="phone" required maxlength="16"></div>
      <div><button type="submit">Pay securely</button></div>
    </form>
    <div class="note" id="result" hidden></div>
  <?php endif; ?>
</div>
<?php if ($enabled && $plans): ?>
<script src="https://checkout.razorpay.com/v1/checkout.js"></script>
<script>
  const BASE = <?= json_encode($BASE) ?>;
  let planId = <?= (int)$plans[0]['id'] ?>;
  const wanted = Number(new URLSearchParams(location.search).get('plan'));
  document.querySelectorAll('.plan').forEach((el) => {
    if (Number(el.dataset.id) === wanted) {
      document.querySelectorAll('.plan').forEach((p) => p.classList.remove('sel'));
      el.classList.add('sel');
      planId = wanted;
    }
  });
  document.querySelectorAll('.switch button').forEach((b) => b.addEventListener('click', () => {
    document.querySelectorAll('.switch button').forEach((x) => x.classList.toggle('on', x === b));
    document.querySelectorAll('.grp').forEach((g) => g.hidden = g.dataset.g !== b.dataset.g);
    const firstPlan = document.querySelector('.grp:not([hidden]) .plan');
    if (firstPlan) firstPlan.click();
  }));
  document.querySelectorAll('.plan').forEach((el) => el.addEventListener('click', () => {
    document.querySelectorAll('.plan').forEach((p) => p.classList.remove('sel'));
    el.classList.add('sel');
    planId = Number(el.dataset.id);
  }));
  const result = document.getElementById('result');
  function show(html) { result.hidden = false; result.innerHTML = html; }
  function esc(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
  document.getElementById('buy').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = new FormData(e.target);
    const res = await fetch(BASE + '/api/v1/pay/order', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ plan_id: planId, name: f.get('name'), email: f.get('email'), phone: f.get('phone') }) });
    const order = await res.json();
    if (!order.success) return show(esc(order.error || 'Something went wrong.'));
    new Razorpay({
      key: order.key_id, order_id: order.order_id, amount: order.amount, currency: 'INR',
      name: 'AI Video Flow', description: order.plan,
      prefill: { name: order.name, email: order.email, contact: order.phone },
      theme: { color: '#e8b95f' },
      handler: async (resp) => {
        show('Verifying payment…');
        const v = await (await fetch(BASE + '/api/v1/pay/verify', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(resp) })).json();
        if (v.success) {
          show('<div>Payment successful! Your license key:</div><p class="key">' + esc(v.license_key) +
               '</p><div class="meta">Open AI Video Flow → Settings → License, paste this key and press Activate. Keep it safe.</div>');
        } else {
          show(esc(v.error || 'Verification failed. Contact support with your payment id: ' + resp.razorpay_payment_id));
        }
      },
    }).open();
  });
</script>
<?php endif; ?>
</body>
</html>
<?php
    exit;
}

// ---------------------------------------------------------------------------
// Admin portal (login + single page app)
// ---------------------------------------------------------------------------
if (!in_array($PATH, ['/', '/admin', '/logout'], true)) {
    http_response_code(404);
    header('Content-Type: text/plain; charset=utf-8');
    exit('Not found');
}
start_admin_session();
header("Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'");
header('Cache-Control: no-store');

if ($PATH === '/logout') {
    $_SESSION = [];
    session_destroy();
    header('Location: ' . $BASE . '/admin');
    exit;
}

$loginError = '';
$failed = function () use ($pdo): void {
    $pdo->prepare('INSERT INTO rate_limits (bucket, ts) VALUES (?, ?)')->execute(['login:' . client_ip(), time()]);
    usleep(400000);
};
$locked = function () use ($pdo): bool {
    $st = $pdo->prepare('SELECT COUNT(*) FROM rate_limits WHERE bucket = ? AND ts > ?');
    $st->execute(['login:' . client_ip(), time() - 900]);
    return (int)$st->fetchColumn() >= 5;
};
// Step 2: authenticator code (valid for 5 minutes after the password step)
if ($METHOD === 'POST' && isset($_POST['code']) && !empty($_SESSION['pending_2fa'])) {
    if (!hash_equals($_SESSION['csrf'], (string)($_POST['csrf'] ?? '')) || time() - $_SESSION['pending_2fa'] > 300) {
        unset($_SESSION['pending_2fa']);
        $loginError = 'Session expired. Please sign in again.';
    } elseif ($locked()) {
        $loginError = 'Too many failed attempts. Try again in 15 minutes.';
    } elseif (twofa_verify($pdo, (string)$_POST['code'])) {
        session_regenerate_id(true);
        unset($_SESSION['pending_2fa']);
        $_SESSION['admin'] = true;
        $_SESSION['csrf'] = bin2hex(random_bytes(32));
        header('Location: ' . $BASE . '/admin');
        exit;
    } else {
        $failed();
        $loginError = 'Wrong code. Use the 6-digit code from your authenticator app.';
    }
}
if ($METHOD === 'POST' && isset($_POST['password'])) {
    if (!hash_equals($_SESSION['csrf'], (string)($_POST['csrf'] ?? ''))) {
        $loginError = 'Session expired. Please try again.';
    } else {
        $st = $pdo->prepare('SELECT COUNT(*) FROM rate_limits WHERE bucket = ? AND ts > ?');
        $st->execute(['login:' . client_ip(), time() - 900]);
        if ((int)$st->fetchColumn() >= 5) {
            $loginError = 'Too many failed attempts. Try again in 15 minutes.';
        } elseif (admin_password_ok($pdo, (string)$_POST['password'])) {
            session_regenerate_id(true);
            $_SESSION['csrf'] = bin2hex(random_bytes(32));
            if (twofa_enabled($pdo)) {
                $_SESSION['pending_2fa'] = time();
            } else {
                $_SESSION['admin'] = true;
            }
            header('Location: ' . $BASE . '/admin');
            exit;
        } else {
            $failed();
            $loginError = 'Wrong password.';
        }
    }
}
$loggedIn = !empty($_SESSION['admin']);
$needCode = !$loggedIn && !empty($_SESSION['pending_2fa']) && time() - $_SESSION['pending_2fa'] <= 300;
?>
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Admin · <?= h(APP_NAME) ?></title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#0b0d12; --panel:#10131a; --card:#141823; --line:#222838; --line2:#2c3346;
    --text:#eef0f5; --muted:#8b93a7; --gold:#e8b95f; --accent:#7c5cff; --green:#22c55e; --red:#ef4444; --blue:#38bdf8;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 Inter, system-ui, sans-serif; }
  button, input, select, textarea { font:inherit; }
  .login { max-width:380px; margin:12vh auto; padding:0 16px; }
  .card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:22px; }
  .login h1 { font-size:20px; margin:0 0 4px; }
  .muted { color:var(--muted); }
  label { display:block; font-size:12px; color:var(--muted); margin:0 0 6px; font-weight:500; }
  input, select, textarea { width:100%; background:#0b0d12; border:1px solid var(--line2); color:var(--text); padding:10px 12px; border-radius:8px; }
  input:focus, select:focus, textarea:focus { outline:none; border-color:var(--gold); }
  .field { margin-bottom:14px; }
  .btn { display:inline-flex; align-items:center; gap:6px; border:1px solid var(--line2); background:#1a1f2c; color:var(--text); padding:9px 14px; border-radius:8px; cursor:pointer; font-weight:600; white-space:nowrap; }
  .btn:hover { border-color:var(--muted); }
  .btn.primary { background:var(--gold); border-color:var(--gold); color:#111; }
  .btn.danger { color:var(--red); border-color:rgba(239,68,68,.4); background:transparent; }
  .btn.sm { padding:5px 10px; font-size:12px; }
  .btn.block { width:100%; justify-content:center; }
  .err { background:rgba(239,68,68,.12); border:1px solid rgba(239,68,68,.4); color:#fca5a5; padding:10px 12px; border-radius:8px; margin-bottom:14px; font-size:13px; }
  .layout { display:grid; grid-template-columns:232px 1fr; min-height:100vh; }
  aside { background:var(--panel); border-right:1px solid var(--line); padding:20px 14px; display:flex; flex-direction:column; gap:4px; }
  .brand { font-weight:700; font-size:15px; padding:4px 10px 18px; }
  .brand small { display:block; color:var(--muted); font-weight:500; font-size:12px; }
  .nav { text-align:left; background:none; border:0; color:var(--muted); padding:9px 10px; border-radius:8px; cursor:pointer; font-weight:500; }
  .nav:hover { color:var(--text); background:#161a25; }
  .nav.active { color:var(--text); background:#1b2030; box-shadow:inset 2px 0 0 var(--gold); }
  aside .spacer { flex:1; }
  main { padding:28px 32px; min-width:0; }
  .head { display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:22px; flex-wrap:wrap; }
  .head h2 { margin:0; font-size:22px; letter-spacing:-.01em; }
  .stats { display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:14px; margin-bottom:22px; }
  .stat .n { font-size:26px; font-weight:700; margin-top:4px; }
  .grid2 { display:grid; grid-template-columns:minmax(280px, 360px) 1fr; gap:18px; align-items:start; }
  .card h3 { margin:0 0 14px; font-size:15px; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--muted); font-weight:500; font-size:11px; text-transform:uppercase; letter-spacing:.04em; padding:8px 10px; border-bottom:1px solid var(--line); }
  td { padding:10px; border-bottom:1px solid #1a1f2c; vertical-align:middle; }
  .tablewrap { overflow-x:auto; }
  .mono { font-family:'JetBrains Mono', ui-monospace, monospace; font-size:12px; }
  .key { color:var(--blue); cursor:pointer; }
  .pill { display:inline-block; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; border:1px solid; }
  .pill.ok { color:var(--green); border-color:rgba(34,197,94,.4); }
  .pill.bad { color:var(--red); border-color:rgba(239,68,68,.4); }
  .pill.warn { color:var(--gold); border-color:rgba(232,185,95,.4); }
  .actions { display:flex; gap:6px; flex-wrap:wrap; }
  .row { display:flex; gap:10px; }
  .row > * { flex:1; }
  .check { display:flex; align-items:center; gap:8px; margin-bottom:14px; }
  .check input { width:auto; }
  .toast { position:fixed; right:20px; bottom:20px; background:#1b2030; border:1px solid var(--line2); padding:12px 16px; border-radius:10px; max-width:360px; z-index:10; }
  .newkey { margin-top:14px; padding:12px; border:1px dashed var(--gold); border-radius:10px; }
  .newkey .mono { font-size:15px; color:var(--gold); word-break:break-all; }
  .hint { font-size:12px; color:var(--muted); margin-top:-8px; margin-bottom:14px; }
  @media (max-width: 860px) {
    .layout { grid-template-columns:1fr; }
    aside { flex-direction:row; flex-wrap:wrap; }
    aside .spacer, .brand small { display:none; }
    main { padding:20px 16px; }
    .grid2 { grid-template-columns:1fr; }
  }
</style>
</head>
<body>
<?php if (!$loggedIn): ?>
  <div class="login">
    <div class="card">
      <h1><?= h(APP_NAME) ?> Admin</h1>
      <p class="muted" style="margin:0 0 18px">Sign in to manage users, licenses and payments.</p>
      <?php if ($loginError): ?><div class="err"><?= h($loginError) ?></div><?php endif; ?>
      <?php if ($needCode): ?>
      <form method="post" action="<?= h($BASE) ?>/admin">
        <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
        <div class="field"><label>Authenticator code</label><input name="code" required autofocus inputmode="numeric" autocomplete="one-time-code" placeholder="6-digit code or a recovery code" maxlength="16"></div>
        <button class="btn primary block" type="submit">Verify</button>
      </form>
      <p class="muted" style="font-size:12px;margin:12px 0 0">Open Google Authenticator / Microsoft Authenticator and enter the code for AI Video Flow Admin.</p>
      <?php else: ?>
      <form method="post" action="<?= h($BASE) ?>/admin">
        <input type="hidden" name="csrf" value="<?= h($_SESSION['csrf']) ?>">
        <div class="field"><label>Admin password</label><input type="password" name="password" required autofocus autocomplete="current-password"></div>
        <button class="btn primary block" type="submit">Sign in</button>
      </form>
      <?php endif; ?>
    </div>
  </div>
<?php else: ?>
<div class="layout">
  <aside>
    <div class="brand"><?= h(APP_NAME) ?><small>Admin portal</small></div>
    <button class="nav active" data-view="overview">Overview</button>
    <button class="nav" data-view="users">Users</button>
    <button class="nav" data-view="licenses">Licenses</button>
    <button class="nav" data-view="plans">Plans &amp; pricing</button>
    <button class="nav" data-view="orders">Orders</button>
    <button class="nav" data-view="usage">Usage</button>
    <button class="nav" data-view="settings">Settings</button>
    <div class="spacer"></div>
    <a class="nav" href="<?= h($BASE) ?>/logout" style="text-decoration:none">Sign out</a>
  </aside>
  <main id="main"></main>
</div>
<div class="toast" id="toast" hidden></div>
<script>
const BASE = <?= json_encode($BASE) ?>;
const CSRF = <?= json_encode($_SESSION['csrf']) ?>;
const main = document.getElementById('main');
let PLANS = [];

function esc(v) { const d = document.createElement('div'); d.textContent = v == null ? '' : String(v); return d.innerHTML; }
function date(ts) { return ts ? new Date(ts * 1000).toLocaleDateString('en-IN', { day: '2-digit', month: 'short', year: 'numeric' }) : '—'; }
function inr(n) { return '₹' + Number(n || 0).toLocaleString('en-IN'); }
function toast(msg) { const t = document.getElementById('toast'); t.textContent = msg; t.hidden = false; clearTimeout(t._h); t._h = setTimeout(() => t.hidden = true, 3500); }
async function api(path, body) {
  const opts = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': CSRF }, body: JSON.stringify(body) };
  const res = await fetch(BASE + '/api/admin' + path, opts);
  if (res.status === 401) { location.href = BASE + '/admin'; return {}; }
  const data = await res.json().catch(() => ({ success: false, error: 'Server error' }));
  if (!data.success && body !== undefined) toast(data.error || 'Failed');
  return data;
}
async function loadPlans() { PLANS = (await api('/plans')).plans || []; }
function planOptions(withEmpty) {
  return (withEmpty ? '<option value="">No license yet</option>' : '') +
    PLANS.filter((p) => +p.active).map((p) => `<option value="${p.id}">${esc(p.name)} · ${planWhat(p)} · ${inr(p.price_inr)}</option>`).join('');
}
function planWhat(p) { return p.kind === 'local' ? 'Unlimited editing' : (+p.unlimited ? 'Unlimited Smart AI' : p.credits + ' Smart AI weddings'); }
function keyCell(k) { return `<span class="mono key" data-copy="${esc(k)}" title="Click to copy">${esc(k)}</span>`; }
document.addEventListener('click', (e) => {
  const c = e.target.closest('[data-copy]');
  if (c) { navigator.clipboard.writeText(c.dataset.copy); toast('Copied ' + c.dataset.copy); }
});

const views = {
  async overview() {
    const o = await api('/overview');
    main.innerHTML = `
      <div class="head"><h2>Overview</h2></div>
      <div class="stats">
        <div class="card stat"><div class="muted">Users</div><div class="n">${o.users}</div></div>
        <div class="card stat"><div class="muted">Active licenses</div><div class="n">${o.active_licenses}</div></div>
        <div class="card stat"><div class="muted">Revenue (${o.paid_orders} orders)</div><div class="n">${inr(o.revenue_inr)}</div></div>
        <div class="card stat"><div class="muted">AI requests today</div><div class="n">${o.ai_requests_today}</div></div>
        <div class="card stat"><div class="muted">AI weddings today</div><div class="n">${o.jobs_today}</div></div>
      </div>
      <div class="card">
        <h3>Setup checklist</h3>
        <p>${o.ai_ready ? '<span class="pill ok">Done</span>' : '<span class="pill warn">To do</span>'} &nbsp;Claude API key added (Settings → AI)</p>
        <p>${o.payments_ready ? '<span class="pill ok">Done</span>' : '<span class="pill warn">To do</span>'} &nbsp;Razorpay payments enabled (Settings → Payments)</p>
        <p style="margin-bottom:0"><span class="pill warn">Tip</span> &nbsp;Change the admin password from Settings → Security after the first login.</p>
      </div>`;
  },

  async users() {
    await loadPlans();
    main.innerHTML = `
      <div class="head"><h2>Users</h2><input id="q" placeholder="Search name, email, phone…" style="max-width:280px"></div>
      <div class="grid2">
        <div class="card">
          <h3>Create user manually</h3>
          <form id="f-user">
            <div class="field"><label>Name *</label><input name="name" required maxlength="80"></div>
            <div class="field"><label>Studio / business</label><input name="studio" maxlength="80"></div>
            <div class="row"><div class="field"><label>Email</label><input name="email" type="email" maxlength="120"></div>
            <div class="field"><label>Phone</label><input name="phone" maxlength="16"></div></div>
            <div class="field"><label>Notes (order id, payment mode…)</label><input name="notes" maxlength="200"></div>
            <div class="field"><label>Give a license now</label><select name="plan_id">${planOptions(true)}</select></div>
            <button class="btn primary block">Create user</button>
          </form>
          <div id="user-key"></div>
        </div>
        <div class="card"><div class="tablewrap"><table><thead><tr><th>User</th><th>Contact</th><th>Licenses</th><th>Source</th><th>Joined</th><th></th></tr></thead><tbody id="ut"></tbody></table></div></div>
      </div>`;
    const load = async () => {
      const d = await api('/users?q=' + encodeURIComponent(document.getElementById('q').value));
      document.getElementById('ut').innerHTML = (d.users || []).map((u) => {
        const waDigits = String(u.phone || '').replace(/\D/g, '');
        const waNum = waDigits.length === 10 ? '91' + waDigits : waDigits;
        const waLink = waNum.length >= 10 ? `<a href="https://wa.me/${waNum}" target="_blank" rel="noopener" style="color:#22c55e;text-decoration:none;font-weight:600">WhatsApp: ${esc(u.phone)}</a>` : esc(u.phone);
        return `<tr>
        <td><strong>${esc(u.name)}</strong><div class="muted">${esc(u.studio || '')}${u.platform ? ' · ' + esc(u.platform) : ''}</div></td>
        <td>${esc(u.email)}<div class="muted">${waLink}</div></td>
        <td>${u.license_count}</td><td><span class="pill ${u.source === 'razorpay' ? 'ok' : 'warn'}">${esc(u.source)}</span></td>
        <td>${date(u.created_at)}</td>
        <td class="actions"><button class="btn sm" data-lic="${u.id}">Licenses</button><button class="btn sm danger" data-del="${u.id}">Delete</button></td></tr>`;
      }).join('')
        || '<tr><td colspan="6" class="muted">No users yet.</td></tr>';
    };
    document.getElementById('q').addEventListener('input', load);
    document.getElementById('ut').addEventListener('click', async (e) => {
      if (e.target.dataset.lic) { views.licenses(+e.target.dataset.lic); setNav('licenses'); }
      if (e.target.dataset.del && confirm('Delete this user and revoke all their licenses?')) { await api('/users/delete', { id: +e.target.dataset.del }); load(); }
    });
    document.getElementById('f-user').addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(e.target));
      const d = await api('/users/create', f);
      if (!d.success) return;
      e.target.reset();
      document.getElementById('user-key').innerHTML = d.license_key
        ? `<div class="newkey"><div class="muted">License key - send this to the customer</div><div class="mono" data-copy="${esc(d.license_key)}">${esc(d.license_key)}</div></div>`
        : '<div class="newkey muted">User created. Issue a license from the Licenses page.</div>';
      toast('User created');
      load();
    });
    load();
  },

  async licenses(userId) {
    await loadPlans();
    const users = (await api('/users')).users || [];
    main.innerHTML = `
      <div class="head"><h2>Licenses</h2>
        <select id="filter" style="max-width:280px"><option value="">All users</option>${users.map((u) => `<option value="${u.id}" ${u.id == userId ? 'selected' : ''}>${esc(u.name)} ${u.email ? '· ' + esc(u.email) : ''}</option>`).join('')}</select></div>
      <div class="grid2">
        <div class="card">
          <h3>Issue license</h3>
          <form id="f-lic">
            <div class="field"><label>User *</label><select name="user_id" required><option value="">Choose…</option>${users.map((u) => `<option value="${u.id}" ${u.id == userId ? 'selected' : ''}>${esc(u.name)} ${u.email ? '· ' + esc(u.email) : ''}</option>`).join('')}</select></div>
            <div class="field"><label>Plan</label><select name="plan_id">${planOptions(false)}<option value="">Custom…</option></select></div>
            <div id="custom" hidden>
              <div class="field"><label>Type</label><select name="kind"><option value="ai">Smart AI (credits or unlimited)</option><option value="local">Pro editing only (no Smart AI)</option></select></div>
              <div class="row"><div class="field"><label>Smart AI credits</label><input name="credits" type="number" min="0" value="10"></div>
              <div class="field"><label>Valid days</label><input name="validity_days" type="number" min="1" value="365"></div></div>
              <label class="check"><input type="checkbox" name="unlimited"> Unlimited</label>
            </div>
            <div class="field"><label>Notes</label><input name="notes" maxlength="200"></div>
            <button class="btn primary block">Generate key</button>
          </form>
          <div id="lic-key"></div>
        </div>
        <div class="card"><div class="tablewrap"><table><thead><tr><th>Key</th><th>User</th><th>Plan</th><th>Credits</th><th>Computer</th><th>Expires</th><th>Status</th><th></th></tr></thead><tbody id="lt"></tbody></table></div></div>
      </div>`;
    const form = document.getElementById('f-lic');
    form.plan_id.addEventListener('change', () => document.getElementById('custom').hidden = form.plan_id.value !== '');
    const load = async () => {
      const uid = document.getElementById('filter').value;
      const d = await api('/licenses' + (uid ? '?user_id=' + uid : ''));
      document.getElementById('lt').innerHTML = (d.licenses || []).map((l) => {
        const expired = l.expires_at && l.expires_at < Date.now() / 1000;
        return `<tr>
        <td>${keyCell(l.license_key)}</td>
        <td>${esc(l.user_name || l.client_name)}<div class="muted">${esc(l.user_email || l.client_email)}</div></td>
        <td>${esc(l.plan_name || l.tier_display)}</td>
        <td><strong>${l.plan_type === 'local' ? 'Pro' : (l.plan_type === 'unlimited' ? '∞' : l.credits)}</strong></td>
        <td>${l.bound_machine_id ? `<span class="mono" title="${esc(l.bound_machine_id)}">${esc(l.bound_machine_id.slice(0, 8))}…</span><div class="muted">${esc(l.platform || '')} ${esc(l.app_version || '')}</div>` : '<span class="muted">Not activated</span>'}</td>
        <td>${date(l.expires_at)}</td>
        <td><span class="pill ${l.status === 'active' && !expired ? 'ok' : 'bad'}">${expired ? 'expired' : esc(l.status)}</span></td>
        <td class="actions">
          <button class="btn sm" data-a="credits" data-id="${l.id}">+ Credits</button>
          <button class="btn sm" data-a="extend" data-id="${l.id}">Extend</button>
          ${l.bound_machine_id ? `<button class="btn sm" data-a="reset" data-id="${l.id}">Reset PC</button>` : ''}
          <button class="btn sm danger" data-a="status" data-s="${l.status === 'active' ? 'revoked' : 'active'}" data-id="${l.id}">${l.status === 'active' ? 'Revoke' : 'Activate'}</button>
        </td></tr>`;
      }).join('') || '<tr><td colspan="8" class="muted">No licenses.</td></tr>';
    };
    document.getElementById('filter').addEventListener('change', load);
    document.getElementById('lt').addEventListener('click', async (e) => {
      const b = e.target.closest('[data-a]');
      if (!b) return;
      const id = +b.dataset.id;
      if (b.dataset.a === 'credits') { const n = prompt('Credits to add (use a negative number to remove):', '5'); if (!n) return; await api('/licenses/add-credits', { id, credits: +n }); }
      if (b.dataset.a === 'extend') { const n = prompt('Extend validity by how many days?', '365'); if (!n) return; await api('/licenses/extend', { id, days: +n }); }
      if (b.dataset.a === 'reset' && confirm('Reset the computer lock? The customer can then activate on a new computer.')) await api('/licenses/reset-machine', { id });
      if (b.dataset.a === 'status' && confirm('Change status to ' + b.dataset.s + '?')) await api('/licenses/status', { id, status: b.dataset.s });
      toast('Updated');
      load();
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(form));
      f.unlimited = form.unlimited.checked;
      const d = await api('/licenses/issue', f);
      if (!d.success) return;
      document.getElementById('lic-key').innerHTML = `<div class="newkey"><div class="muted">New license key</div><div class="mono" data-copy="${esc(d.license_key)}">${esc(d.license_key)}</div></div>`;
      load();
    });
    load();
  },

  async plans() {
    await loadPlans();
    const FREE_LIMIT = (await api('/settings/free')).free_daily_projects ?? 1;
    main.innerHTML = `
      <div class="head"><h2>Plans &amp; pricing</h2></div>
      <div class="grid2">
        <div class="card">
          <h3 id="pf-title">Add plan</h3>
          <form id="f-plan">
            <input type="hidden" name="id">
            <div class="field"><label>Plan name *</label><input name="name" required maxlength="60"></div>
            <div class="field"><label>Type</label><select name="kind"><option value="ai">Smart AI pack</option><option value="local">Pro — unlimited editing, no Smart AI</option></select></div>
            <div class="row"><div class="field"><label>Price (₹)</label><input name="price_inr" type="number" min="0" value="0"></div>
            <div class="field"><label>MRP (₹, shown crossed out)</label><input name="mrp_inr" type="number" min="0" value="0"></div></div>
            <div class="row"><div class="field"><label>Smart AI weddings</label><input name="credits" type="number" min="0" value="10"></div>
            <div class="field"><label>Badge</label><input name="badge" maxlength="24" placeholder="Most popular"></div></div>
            <div class="row"><div class="field"><label>Valid days</label><input name="validity_days" type="number" min="1" value="365"></div>
            <div class="field"><label>Order</label><input name="sort" type="number" value="0"></div></div>
            <label class="check"><input type="checkbox" name="unlimited"> Unlimited Smart AI</label>
            <label class="check"><input type="checkbox" name="active" checked> Show on buy page</label>
            <div class="row"><button class="btn primary">Save plan</button><button type="button" class="btn" id="pf-reset">Clear</button></div>
          </form>
          <p class="hint" style="margin-top:12px">Plans with price ₹0 are only for manual licenses and never appear on the buy page. Price changes show in every installed app automatically.</p>
          <form id="f-free" style="margin-top:18px">
            <h3>Free plan</h3>
            <div class="field"><label>Projects per day (0 = free plan off)</label><input name="free_daily_projects" type="number" min="0" max="50" value="${FREE_LIMIT}"></div>
            <button class="btn primary">Save free plan</button>
          </form>
        </div>
        <div class="card"><div class="tablewrap"><table><thead><tr><th>Plan</th><th>Price</th><th>Includes</th><th>Days</th><th>Buy page</th><th></th></tr></thead><tbody>
          ${PLANS.map((p) => `<tr><td><strong>${esc(p.name)}</strong>${p.badge ? ` <span class="pill warn">${esc(p.badge)}</span>` : ''}</td><td>${inr(p.price_inr)}${+p.mrp_inr ? `<div class="muted"><s>${inr(p.mrp_inr)}</s></div>` : ''}</td><td>${planWhat(p)}</td><td>${p.validity_days}</td>
            <td><span class="pill ${+p.active && +p.price_inr > 0 ? 'ok' : 'warn'}">${+p.active && +p.price_inr > 0 ? 'Shown' : 'Hidden'}</span></td>
            <td class="actions"><button class="btn sm" data-edit="${p.id}">Edit</button><button class="btn sm danger" data-del="${p.id}">Delete</button></td></tr>`).join('')}
        </tbody></table></div></div>
      </div>`;
    const form = document.getElementById('f-plan');
    const reset = () => { form.reset(); form.id.value = ''; document.getElementById('pf-title').textContent = 'Add plan'; };
    document.getElementById('pf-reset').addEventListener('click', reset);
    main.querySelector('tbody').addEventListener('click', async (e) => {
      if (e.target.dataset.edit) {
        const p = PLANS.find((x) => x.id == e.target.dataset.edit);
        for (const k of ['id', 'name', 'price_inr', 'mrp_inr', 'credits', 'validity_days', 'sort', 'badge', 'kind']) form[k].value = p[k] ?? '';
        form.unlimited.checked = !!+p.unlimited; form.active.checked = !!+p.active;
        document.getElementById('pf-title').textContent = 'Edit plan';
      }
      if (e.target.dataset.del && confirm('Delete this plan? Existing licenses are not affected.')) { await api('/plans/delete', { id: +e.target.dataset.del }); views.plans(); }
    });
    document.getElementById('f-free').addEventListener('submit', async (e) => {
      e.preventDefault();
      if ((await api('/settings/free', Object.fromEntries(new FormData(e.target)))).success) toast('Free plan saved');
    });
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      const f = Object.fromEntries(new FormData(form));
      f.unlimited = form.unlimited.checked; f.active = form.active.checked;
      if ((await api('/plans/save', f)).success) { toast('Plan saved'); views.plans(); }
    });
  },

  async orders() {
    const d = await api('/orders');
    main.innerHTML = `<div class="head"><h2>Orders</h2></div><div class="card"><div class="tablewrap"><table>
      <thead><tr><th>Date</th><th>Customer</th><th>Plan</th><th>Amount</th><th>Status</th><th>Payment</th><th>License</th></tr></thead><tbody>
      ${(d.orders || []).map((o) => `<tr><td>${date(o.created_at)}</td><td>${esc(o.name)}<div class="muted">${esc(o.email)} · ${esc(o.phone)}</div></td>
        <td>${esc(o.plan_name)}</td><td>${inr(o.amount_paise / 100)}</td>
        <td><span class="pill ${o.status === 'paid' ? 'ok' : 'warn'}">${esc(o.status)}</span></td>
        <td class="mono">${esc(o.rzp_payment_id || '—')}</td><td>${o.license_key ? keyCell(o.license_key) : '—'}</td></tr>`).join('') || '<tr><td colspan="7" class="muted">No orders yet.</td></tr>'}
      </tbody></table></div></div>`;
  },

  async usage() {
    const d = await api('/usage');
    main.innerHTML = `<div class="head"><h2>Usage</h2></div>
      <div class="grid2" style="grid-template-columns:1fr 1fr">
        <div class="card"><h3>AI wedding credits used</h3><div class="tablewrap"><table><thead><tr><th>When</th><th>License</th><th>Job</th></tr></thead><tbody>
          ${(d.jobs || []).map((j) => `<tr><td>${new Date(j.timestamp * 1000).toLocaleString('en-IN')}</td><td class="mono">${esc(j.license_key)}</td><td>${esc(j.job_name)}</td></tr>`).join('') || '<tr><td colspan="3" class="muted">Nothing yet.</td></tr>'}
        </tbody></table></div></div>
        <div class="card"><h3>Claude requests</h3><div class="tablewrap"><table><thead><tr><th>When</th><th>License</th><th>Tokens in / out</th><th>Status</th></tr></thead><tbody>
          ${(d.ai || []).map((a) => `<tr><td>${new Date(a.created_at * 1000).toLocaleString('en-IN')}</td><td class="mono">${esc(a.license_key)}</td><td>${a.input_tokens} / ${a.output_tokens}</td><td><span class="pill ${a.status == 200 ? 'ok' : 'bad'}">${a.status}</span></td></tr>`).join('') || '<tr><td colspan="4" class="muted">Nothing yet.</td></tr>'}
        </tbody></table></div></div>
      </div>`;
  },

  async settings() {
    const s = await api('/settings');
    main.innerHTML = `<div class="head"><h2>Settings</h2></div>
      <div class="grid2" style="grid-template-columns:repeat(auto-fit, minmax(320px, 1fr))">
        <div class="card"><h3>Payments · Razorpay</h3>
          <form id="f-pay">
            <label class="check"><input type="checkbox" name="payments_enabled" ${s.payments_enabled ? 'checked' : ''}> Accept online payments</label>
            <div class="field"><label>Key ID</label><input name="razorpay_key_id" value="${esc(s.razorpay_key_id)}" placeholder="rzp_live_…"></div>
            <div class="field"><label>Key secret ${s.razorpay_key_secret ? '· saved ' + esc(s.razorpay_key_secret) : ''}</label><input name="razorpay_key_secret" type="password" placeholder="Leave empty to keep the saved secret" autocomplete="new-password"></div>
            <div class="field"><label>Webhook secret ${s.razorpay_webhook_secret ? '· saved ' + esc(s.razorpay_webhook_secret) : ''}</label><input name="razorpay_webhook_secret" type="password" placeholder="Leave empty to keep the saved secret" autocomplete="new-password"></div>
            <p class="hint">In Razorpay Dashboard → Webhooks add <span class="mono">${esc(s.webhook_url)}</span> with events <b>payment.captured</b> and <b>order.paid</b>.</p>
            <p class="hint">Customer buy page: <a href="${esc(s.buy_url)}" target="_blank" rel="noopener" style="color:var(--gold)">${esc(s.buy_url)}</a></p>
            <button class="btn primary block">Save payment settings</button>
          </form>
        </div>
        <div class="card"><h3>AI · Anthropic Claude</h3>
          <form id="f-ai">
            <div class="field"><label>Claude API key ${s.anthropic_api_key ? '· saved ' + esc(s.anthropic_api_key) : ''}</label><input name="anthropic_api_key" type="password" placeholder="sk-ant-… (leave empty to keep)" autocomplete="new-password"></div>
            <div class="field"><label>Model</label><select name="anthropic_model">${Object.entries(s.models).map(([id, label]) => `<option value="${id}" ${id === s.anthropic_model ? 'selected' : ''}>${esc(label)}</option>`).join('')}</select></div>
            <div class="field"><label>AI requests per license per day</label><input name="ai_daily_limit" type="number" min="1" max="5000" value="${s.ai_daily_limit}"></div>
            <p class="hint">The key stays on this server (encrypted). Customers never see it - their app sends requests here with their license.</p>
            <div class="row"><button class="btn primary">Save AI settings</button><button type="button" class="btn" id="ai-test">Test connection</button></div>
          </form>
        </div>
        <div class="card"><h3>AI Audio · ElevenLabs</h3>
          <form id="f-audio">
            <div class="field"><label>ElevenLabs API key ${s.elevenlabs_api_key ? '· saved ' + esc(s.elevenlabs_api_key) : ''}</label><input name="elevenlabs_api_key" type="password" placeholder="sk_… (leave empty to keep)" autocomplete="new-password"></div>
            <div class="field"><label>Voiceover voice ID</label><input name="elevenlabs_voice_id" value="${esc(s.elevenlabs_voice_id)}" placeholder="From ElevenLabs → Voices → ID"></div>
            <div class="field"><label>Music / voice generations per license per day</label><input name="audio_daily_limit" type="number" min="1" max="200" value="${s.audio_daily_limit}"></div>
            <p class="hint">Smart AI packs get AI background music and Hindi/English voiceover. The key stays on this server (encrypted).</p>
            <div class="row"><button class="btn primary">Save audio settings</button><button type="button" class="btn" id="audio-test">Test connection</button></div>
          </form>
        </div>
        <div class="card"><h3>App &amp; Mobile APK update notice</h3>
          <form id="f-update">
            <div class="field"><label>Latest Desktop version (Win/Mac)</label><input name="app_latest_version" value="${esc(s.app_latest_version)}" placeholder="e.g. 1.1.13 (empty = no notice)"></div>
            <div class="field"><label>Desktop download link</label><input name="app_download_url" value="${esc(s.app_download_url)}" placeholder="https://…"></div>
            <div class="field"><label>Desktop what's new (optional)</label><input name="app_update_notes" value="${esc(s.app_update_notes)}" maxlength="300" placeholder="Short note shown in desktop app"></div>
            <p class="hint">Permanent links to share (always the latest version): <span class="mono">${esc(s.download_url)}</span> — or add <span class="mono">/windows</span>, <span class="mono">/mac-arm64</span>, <span class="mono">/mac-x86_64</span>, <span class="mono">/android</span>.</p>
            <p class="hint">Auto update: upload the installers to the <span class="mono">downloads/</span> folder on this server (same names as the build makes) and they are used automatically. Fill these only to use a file hosted somewhere else.</p>
            ${s.app_assets.map((a) => `<div class="field"><label>${esc(a.label)} link</label><input name="app_asset_url_${a.key}" value="${esc(a.url)}" placeholder="https://…"></div>
            <div class="field"><label>${esc(a.label)} SHA-256</label><input name="app_asset_sha256_${a.key}" value="${esc(a.sha256)}" maxlength="64" class="mono" placeholder="64 hex characters"></div>`).join('')}
            <hr style="border:0;border-top:1px solid var(--line);margin:12px 0">
            <div class="field"><label>Latest Mobile APK version</label><input name="mobile_latest_version" value="${esc(s.mobile_latest_version || '')}" placeholder="e.g. 1.1.14 (leave empty to use desktop version)"></div>
            <div class="field"><label>Mobile .APK direct download link</label><input name="mobile_apk_url" value="${esc(s.mobile_apk_url || '')}" placeholder="https://aivideoflow.in/downloads/AI-Video-Flow-Reels.apk"></div>
            <div class="field"><label>Mobile what's new (optional)</label><input name="mobile_update_notes" value="${esc(s.mobile_update_notes || '')}" maxlength="300" placeholder="Short note shown on user's phone"></div>
            <p class="hint">When you increase the version here, every user with the app installed on their phone or PC immediately gets an in-app "Update Now" banner.</p>
            <button class="btn primary block">Publish update notice</button>
          </form>
        </div>
        <div class="card"><h3>Security</h3>
          <form id="f-pw">
            <div class="field"><label>Current password</label><input name="current" type="password" required autocomplete="current-password"></div>
            <div class="field"><label>New password (min 10 characters)</label><input name="new" type="password" required minlength="10" autocomplete="new-password"></div>
            <button class="btn primary block">Change password</button>
          </form>
          <p class="hint" style="margin-top:14px">Server: PHP ${esc(s.php)} · curl ${s.curl ? 'available' : 'missing'}</p>
        </div>
        <div class="card"><h3>Two-factor authentication</h3><div id="twofa"><span class="muted">Loading…</span></div></div>
      </div>`;
    document.getElementById('f-pay').addEventListener('submit', async (e) => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); f.payments_enabled = e.target.payments_enabled.checked;
      if ((await api('/settings/payment', f)).success) { toast('Payment settings saved'); views.settings(); }
    });
    document.getElementById('f-ai').addEventListener('submit', async (e) => {
      e.preventDefault();
      if ((await api('/settings/ai', Object.fromEntries(new FormData(e.target)))).success) { toast('AI settings saved'); views.settings(); }
    });
    document.getElementById('ai-test').addEventListener('click', async () => { toast('Testing…'); const d = await api('/settings/ai-test', {}); if (d.success) toast(d.message); });
    document.getElementById('f-audio').addEventListener('submit', async (e) => {
      e.preventDefault();
      if ((await api('/settings/audio', Object.fromEntries(new FormData(e.target)))).success) { toast('Audio settings saved'); views.settings(); }
    });
    document.getElementById('audio-test').addEventListener('click', async () => { toast('Testing…'); const d = await api('/settings/audio-test', {}); if (d.success) toast(d.message); });
    document.getElementById('f-update').addEventListener('submit', async (e) => {
      e.preventDefault();
      if ((await api('/settings/update', Object.fromEntries(new FormData(e.target)))).success) { toast('Update notice saved'); views.settings(); }
    });
    document.getElementById('f-pw').addEventListener('submit', async (e) => {
      e.preventDefault(); if ((await api('/settings/password', Object.fromEntries(new FormData(e.target)))).success) { e.target.reset(); toast('Password changed'); }
    });
    renderTwofa();
  },
};

async function renderTwofa() {
  const box = document.getElementById('twofa');
  const st = await api('/2fa/status');
  if (st.enabled) {
    box.innerHTML = `<p><span class="pill ok">On</span> &nbsp;Sign-in asks for a code from your authenticator app. Recovery codes left: <b>${st.recovery_left}</b></p>
      <form id="f-2fa-off"><div class="row"><div class="field"><label>Password</label><input name="password" type="password" required autocomplete="current-password"></div>
      <div class="field"><label>Current code</label><input name="code" required inputmode="numeric" maxlength="16"></div></div>
      <button class="btn danger block">Turn off two-factor</button></form>`;
    document.getElementById('f-2fa-off').addEventListener('submit', async (e) => {
      e.preventDefault();
      if (!confirm('Turn off two-factor authentication?')) return;
      if ((await api('/2fa/disable', Object.fromEntries(new FormData(e.target)))).success) { toast('Two-factor turned off'); renderTwofa(); }
    });
    return;
  }
  box.innerHTML = `<p class="muted" style="margin-top:0">Protect the admin login with a 6-digit code from Google Authenticator or Microsoft Authenticator.</p>
    <button class="btn primary block" id="twofa-start">Set up two-factor</button>`;
  document.getElementById('twofa-start').addEventListener('click', async () => {
    const d = await api('/2fa/setup', {});
    if (!d.success) return;
    box.innerHTML = `<ol style="padding-left:18px;margin:0 0 14px;line-height:1.7">
        <li>Open your authenticator app → <b>+</b> → <b>Enter a setup key</b>.</li>
        <li>Account: <b>AI Video Flow Admin</b>, key: <span class="mono" style="color:var(--gold)">${esc(d.secret)}</span>, type: time based.</li>
        <li>Type the 6-digit code it shows below.</li></ol>
      <form id="f-2fa-on"><div class="field"><label>6-digit code</label><input name="code" required inputmode="numeric" maxlength="6" autocomplete="one-time-code"></div>
      <button class="btn primary block">Turn on</button></form>`;
    document.getElementById('f-2fa-on').addEventListener('submit', async (e) => {
      e.preventDefault();
      const r = await api('/2fa/enable', Object.fromEntries(new FormData(e.target)));
      if (!r.success) return;
      box.innerHTML = `<p><span class="pill ok">On</span> &nbsp;Two-factor is on.</p>
        <p><b>Save these recovery codes now</b> (each works once if you lose your phone). They will not be shown again:</p>
        <div class="mono" style="columns:2;line-height:1.9;color:var(--gold)">${r.recovery_codes.map(esc).join('<br>')}</div>
        <button class="btn block" style="margin-top:12px" id="twofa-done">I saved them</button>`;
      document.getElementById('twofa-done').addEventListener('click', renderTwofa);
    });
  });
}

function setNav(view) { document.querySelectorAll('.nav[data-view]').forEach((n) => n.classList.toggle('active', n.dataset.view === view)); }
document.querySelectorAll('.nav[data-view]').forEach((n) => n.addEventListener('click', () => { setNav(n.dataset.view); views[n.dataset.view](); }));
views.overview();
</script>
<?php endif; ?>
</body>
</html>
