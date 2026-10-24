========================================================================
  AI VIDEO FLOW - SAAS LICENSING SERVER & ADMIN PORTAL
  DOMAIN: flow.aiphotoflow.in
========================================================================

HOW TO DEPLOY ON flow.aiphotoflow.in (1-MINUTE SETUP):
------------------------------------------------------------------------
1. Go to your Hosting cPanel -> File Manager.
2. Open the directory for "flow.aiphotoflow.in" (e.g. public_html/ or subfolder).
3. Upload and extract this ZIP file.
   Ensure these 3 files are present:
   - index.php
   - admin.php
   - .htaccess

4. Test in your browser:
   Open: https://flow.aiphotoflow.in/admin.php
   (or https://flow.aiphotoflow.in/)

5. Master Admin Login Password:
   admin123
   (You can change this password at line 14 in index.php)

6. Central Cloud AI Setup (One-Click Client Plug-and-Play):
   - In Admin Panel, find the "🔑 Central Cloud AI Setup" card.
   - Enter your Google Gemini API Key.
   - Click "⚡ Test API" to verify connection, then click "💾 Save API Keys".
   - That's it! When clients install the Desktop App and activate their key,
     the app automatically connects to your AI setup. Clients never have to touch
     any API keys!

API ENDPOINTS FOR CLIENT DESKTOP SOFTWARE:
------------------------------------------------------------------------
- Verification API: https://flow.aiphotoflow.in/api/v1/license/verify
- Credit Consumption API: https://flow.aiphotoflow.in/api/v1/license/consume

DATABASE:
------------------------------------------------------------------------
- SQLite database (licenses.db) will automatically be created on first run.
- No MySQL setup required! Everything runs out of the box with zero dependencies.
========================================================================
