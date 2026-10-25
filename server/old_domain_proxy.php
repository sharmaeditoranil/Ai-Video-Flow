<?php
/**
 * AI Video Flow - forwarder for the OLD server domain (flow.aiphotoflow.in).
 *
 * Upload as index.php on the old domain (keep its config.php and .htaccess). Apps installed before the
 * domain change keep working: their license, payment, AI and audio requests are relayed to the new
 * server, so there is only one database. Browsers (admin, buy page) are sent to the new address.
 */
declare(strict_types=1);

const TARGET = 'https://license.aivideoflow.in/index.php';

$uri = $_SERVER['REQUEST_URI'] ?? '/';
$path = parse_url($uri, PHP_URL_PATH) ?: '/';
$query = parse_url($uri, PHP_URL_QUERY);
$scriptDir = rtrim(str_replace('\\', '/', dirname($_SERVER['SCRIPT_NAME'] ?? '/')), '/');
if ($scriptDir !== '' && str_starts_with($path, $scriptDir)) {
    $path = substr($path, strlen($scriptDir));
}
if (str_starts_with($path, '/index.php')) {
    $path = substr($path, strlen('/index.php'));
}
$path = '/' . ltrim($path, '/');
$url = TARGET . ($path === '/' ? '' : $path) . ($query ? "?$query" : '');
$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';

// Web pages (admin, buy page): move the browser to the new domain
if (!str_starts_with($path, '/api/')) {
    header('Location: ' . $url, true, $method === 'GET' ? 301 : 308);
    exit;
}

$config = is_file(__DIR__ . '/config.php') ? require __DIR__ . '/config.php' : [];
$headers = [];
foreach ($_SERVER as $k => $v) {
    if (str_starts_with($k, 'HTTP_') && !in_array($k, ['HTTP_HOST', 'HTTP_CONTENT_LENGTH', 'HTTP_CONNECTION', 'HTTP_COOKIE',
            'HTTP_X_AVF_PROXY', 'HTTP_X_AVF_CLIENT_IP', 'HTTP_ACCEPT_ENCODING'], true)) {
        $headers[] = str_replace('_', '-', substr($k, 5)) . ': ' . $v;
    }
}
if (!isset($_SERVER['HTTP_AUTHORIZATION']) && isset($_SERVER['REDIRECT_HTTP_AUTHORIZATION'])) {
    $headers[] = 'Authorization: ' . $_SERVER['REDIRECT_HTTP_AUTHORIZATION'];
}
if (isset($_SERVER['CONTENT_TYPE'])) {
    $headers[] = 'Content-Type: ' . $_SERVER['CONTENT_TYPE'];
}
if (!empty($config['settings_key'])) {
    $headers[] = 'X-AVF-Proxy: ' . hash_hmac('sha256', 'avf-domain-proxy', (string)$config['settings_key']);
    $headers[] = 'X-AVF-Client-IP: ' . ($_SERVER['REMOTE_ADDR'] ?? '');
}

@set_time_limit(330);
$type = 'application/json';
$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_CUSTOMREQUEST => $method,
    CURLOPT_HTTPHEADER => $headers,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 320,
    CURLOPT_CONNECTTIMEOUT => 15,
    CURLOPT_HEADERFUNCTION => function ($ch, string $line) use (&$type) {
        if (stripos($line, 'Content-Type:') === 0) {
            $type = trim(substr($line, 13));
        }
        return strlen($line);
    },
]);
if (!in_array($method, ['GET', 'HEAD', 'OPTIONS'], true)) {
    curl_setopt($ch, CURLOPT_POSTFIELDS, file_get_contents('php://input'));
}
$body = curl_exec($ch);
$status = (int)curl_getinfo($ch, CURLINFO_RESPONSE_CODE);
if ($body === false || $status === 0) {
    http_response_code(502);
    header('Content-Type: application/json');
    exit(json_encode(['success' => false, 'error' => 'The license server could not be reached. Please try again.']));
}
http_response_code($status);
header('Content-Type: ' . $type);
header('X-Robots-Tag: noindex, nofollow');
echo $body;
