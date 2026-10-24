# 🌐 SaaS License & Admin Portal Setup for `video.aiphotoflow.in`

Ye guide aapko batayegi ki aap apne domain **`video.aiphotoflow.in`** par **AI Video Flow** ka SaaS Licensing Server aur Admin Portal kaise live karenge aur clients ke licenses & wedding credits kaise manage karenge.

---

## 📌 Domain & Endpoint Details

- **SaaS Main Domain**: `https://video.aiphotoflow.in`
- **Online Verification API**: `https://video.aiphotoflow.in/api/v1/license/verify`
- **Credit Consumption API**: `https://video.aiphotoflow.in/api/v1/license/consume`
- **Web Admin Portal**: `https://video.aiphotoflow.in/admin.php` (ya `https://video.aiphotoflow.in/`)
- **Master Admin Password**: `admin123` *(index.php ke line 14 par aap ise change kar sakte hain)*

---

## 🚀 Option 1: cPanel / Apache / PHP Hosting (Sabse Aasan - 2 Minute Setup)

Agar aapka domain **video.aiphotoflow.in** kisi cPanel, Hostinger, GoDaddy ya WordPress hosting par chal raha hai:

### Step 1: Files Upload Karein
Project folder me `installer/server_deploy/php/` me ye 3 files hain:
1. `index.php` *(API + Admin Portal)*
2. `admin.php` *(Admin portal direct shortcut)*
3. `.htaccess` *(URL rewrite rules)*

### Step 2: Upload Location
Apne cPanel ke **File Manager** me jayein:
- **Case A (Subdomain Root)**: `video.aiphotoflow.in` ke root folder (jaise `public_html/` ya `video/`) me ye teeno files upload kar dein.
- **Case B (Subfolder)**: Agar aap chahe to `public_html/api/v1/license/` folder banakar waha bhi upload kar sakte hain. *(Hamare desktop software me dono paths ka auto-fallback laga hua hai!)*

### Step 3: Test Karein
Browser me open karein:
👉 `https://video.aiphotoflow.in/admin.php`

Aapke samne **👑 SaaS Admin Portal** ka login page aayega.
- Password dalein: `admin123`
- Login hote hi aapka pura Admin Dashboard live ho jayega!

---

## ⚡ Option 2: VPS / Cloud Server (Python Systemd)

Agar aap Ubuntu / Debian VPS par chalana chahte hain:

1. `installer/server_deploy/python/admin_server.py` ko server par `/var/www/aivideoflow/` me copy karein.
2. Systemd service banayein:
```ini
[Unit]
Description=AI Video Flow License Server
After=network.target

[Service]
User=www-data
WorkingDirectory=/var/www/aivideoflow
ExecStart=/usr/bin/python3 admin_server.py 8899
Restart=always

[Install]
WantedBy=multi-user.target
```
3. Nginx Reverse Proxy config me add karein:
```nginx
server {
    server_name video.aiphotoflow.in;

    location / {
        proxy_pass http://127.0.0.1:8899;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

## 👑 Admin Portal Features & How to Use

### 1. New Client ke liye License Key Generate Karna
1. Admin Portal me left side **✨ Issue New License Key** form me:
   - **Client Name**: e.g., *Rahul Photography*
   - **Client Email**: *rahul@gmail.com*
   - **Wedding Plan**:
     - 1 Wedding (Trial)
     - 5 Weddings (₹2,499)
     - 10 Weddings (₹4,499 Popular)
     - 25 Weddings (₹9,999 Studio)
     - 50 Weddings (₹17,999 Agency)
     - 👑 Unlimited Agency (1-Year Pass)
2. **🚀 Generate & Issue Key** par click karein.
3. License Key screen par aa jayegi (jaise: `AIFLOW-PREM-10-8F2B-9C14`). **Copy Key** karke client ko WhatsApp/Email par bhej dein!

### 2. Client Computer Hack / Sharing Protection (Machine Hardware Lock)
- Jab client pehli baar apne computer par key activate karega, software automatically us PC ka **Motherboard UUID + CPU + MAC Hash** server par lock kar dega.
- Agar wo key kisi doosre videographer ko dega, to doosre PC par error aayega:
  > *"License is locked to another computer. Contact video.aiphotoflow.in admin."*

### 3. Agar Client Naya Laptop / PC Kharide to "Reset PC" Kaise Karein?
- Client aapse request karega ki usne naya PC liya hai.
- Aap Admin Portal me uske naam ke aage **Reset PC** button par 1 click karenge.
- Machine hardware lock unlock ho jayega, aur client turant apne naye PC par activate kar sakega!

### 4. Credits Recharge Karna (+5 Credits)
- Agar client ke 10 credits khatam ho gaye aur usne aapse recharge karwaya:
- Uske row me **+5 Credits** button dabayein ya custom number enter karein.
- Bina nayi key diye, uske software me credits automatically add ho jayenge!

### 5. Suspicious User ko Revoke Karna
- Agar kisi client ka payment cancel hua ya refund manga:
- **Revoke** button dabate hi uska access freeze ho jayega.

---

## 🛡️ Offline Emergency Tokens (Bina Internet ke bhi chalega)
Agar kisi client ke studio me internet band ho jaye ya server me maintenance ho:
- Client ka software **Cryptographic Offline Vault** se seamlessly chalta rahega.
- Master Key Format: `AIFLOW-PREM-50-STUDIO-2026` offline bhi verify hoti hai!
