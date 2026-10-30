# License server setup — license.aivideoflow.in

Ye server aapke customers ke licenses, payments (Razorpay) aur Claude AI ko sambhalta hai.
App ke saare paid features isi server se chalte hain, isliye app ko crack karke kuch free nahi milta.

## 1. Bundle banana (sirf aapke Mac par)

```bash
python3 packaging/build_server_bundle.py
```

Isse `dist/license-server-bundle.zip` banta hai (index.php, .htaccess, config.php), aur purane domain ke liye `dist/old-domain-forwarder.zip`.
`config.php` me license sign karne wali secret key hai — ye ZIP kisi ko mat bhejiye, git me mat daaliye.
Secret key ki asli copy `~/.ai-video-flow-admin/signing_key.json` me hai. **Iska backup rakhiye** (pen drive / password manager).
Ye kho gayi to naye license sign nahi honge aur app dobara banana padega.

## 2. Upload (cPanel)

1. cPanel → File Manager → `license.aivideoflow.in` ka folder kholiye.
2. Purani files **delete** kariye: `index.php`, `admin.php`, `.htaccess`, `README.txt`.
   `licenses.db` ko delete mat kariye — naya server use apne aap `data/` folder me surakshit jagah le jaata hai.
3. `license-server-bundle.zip` upload karke Extract kariye.
4. cPanel → Select PHP Version: PHP **8.1 ya naya**, extensions me `sodium`, `pdo_sqlite`, `curl` on hone chahiye.

## 3. Pehla login

- Kholiye: `https://license.aivideoflow.in/index.php/admin`
- Pehla password `~/.ai-video-flow-admin/signing_key.json` me `initial_admin_password` hai.
- Login ke turant baad **Settings → Security** me apna naya password set kariye.

## 4. Settings

- **AI · Anthropic Claude**: apni Claude API key (console.anthropic.com) daaliye → Save → Test connection.
  Key server par encrypted rehti hai, customers ko kabhi nahi milti.
- **Payments · Razorpay**: Key ID, Key secret, Webhook secret daaliye aur "Accept online payments" on kariye.
  Razorpay Dashboard → Webhooks me jo URL Settings me dikhta hai wo daaliye (events: `payment.captured`, `order.paid`).
  Customer buy page: `https://license.aivideoflow.in/index.php/buy`
- **AI Audio · ElevenLabs**: AI background music aur voiceover ke liye ElevenLabs API key (elevenlabs.io → Developers → API Keys) daaliye → Save → Test connection.
  Voiceover ki aawaz badalni ho to ElevenLabs → Voices se voice ka ID copy karke "Voiceover voice ID" me daaliye.
  Ye sirf Smart AI packs me chalta hai; "generations per license per day" se kharcha control kariye.
- **App update notice**: naya version release karne ke baad yahan version (jaise `1.1.5`) aur download link daaliye.
  Purane app me "New version available → Download" dikhne lagega. Khali chhodne par app GitHub ka latest release dekhta hai.
- **Plans & pricing**: plans ki keemat, MRP, weddings, badge aur validity yahan badaliye. Free plan ki roz ki limit bhi yahi hai.
  Ye sab installed apps me apne aap dikhta hai, naya app nahi chahiye.

## Domain badalna (flow.aiphotoflow.in → license.aivideoflow.in)

Signing key aur settings key wahi rehti hain, isliye saare license, plans, orders aur API keys naye domain par chalte hain.
Pehle se install kiye gaye app purane domain se baat karte hain, unke liye purane domain par "forwarder" rakha jata hai.

1. cPanel me `license.aivideoflow.in` subdomain banaiye, SSL (AutoSSL / Let's Encrypt) on kariye, PHP 8.1+ (`sodium`, `pdo_sqlite`, `curl`).
2. Naye folder me `license-server-bundle.zip` upload karke Extract kariye. **Abhi naya admin mat kholiye.**
3. Purane domain ke folder me `data-…` naam ka folder hai (licenses database). Use Compress → Download kariye,
   phir naye folder me Upload → Extract kariye. Folder ka naam wahi rehna chahiye. Agar naya admin pehle khul gaya tha
   aur khaali `data-…` folder ban gaya hai, to pehle use delete kariye.
4. Purane domain par sirf `index.php` ko `old-domain-forwarder.zip` wale `index.php` se badliye.
   `config.php` aur `.htaccess` wahi rehne dijiye. Purana `data-…` folder backup ke liye rakh sakte hain.
5. `https://license.aivideoflow.in/index.php/admin` kholiye → Users / Licenses me purane customers dikhne chahiye.
6. Razorpay Dashboard → Webhooks me URL badal kar `https://license.aivideoflow.in/index.php/api/v1/pay/webhook` kariye.
7. Settings → App update notice me naya version daaliye, taaki purane app wale naya app (naye domain wala) le lein.
   Forwarder tab tak chalu rakhiye jab tak sab customers naye version par na aa jayein.

## 5. Roz ka kaam

- **Users → Create user manually**: naam, email, phone daaliye, plan chuniye → license key ban jaati hai, customer ko bhejiye.
- **Licenses**: + Credits, Extend, Reset PC (customer ne naya computer liya), Revoke.
- **Orders**: Razorpay se aaye saare payments, unki license keys ke saath.
- **Usage**: kisne kitne AI weddings aur Claude requests use kiye.

## 6. Naya version release (download link + auto update)

1. Build chalaiye (`packaging/build_mac_app.sh` Mac par, `packaging/build_windows_app.bat` Windows par). Files `dist/` me banti hain.
2. cPanel → File Manager → `license.aivideoflow.in` folder (jahan `index.php` hai) → `downloads` naam ka folder banaiye (pehli baar).
3. Usme ye files upload kariye — **naam bilkul yahi rehna chahiye**, purani file replace ho jayegi:
   - `AI-Video-Flow-Windows-Setup.exe`
   - `AI-Video-Flow-Mac-arm64.dmg` (Apple Silicon M1/M2/M3/M4)
   - `AI-Video-Flow-Mac-x86_64.dmg` (Intel Mac)
   - `AI-Video-Flow-Reels.apk` (Android)
   File Manager badi file (500 MB+) par atak jaye to FTP (FileZilla) se upload kariye.
4. Admin → Settings → **Latest Desktop version** me naya number daaliye (jaise `1.1.15`) → Publish.
   Sabhi chalte apps installer khud download karke "Restart & Update" button dikhayenge.
   SHA-256 server khud nikalta hai; link wale fields khaali chhod dijiye.

**Customers ko bhejne wala link (kabhi nahi badalta, hamesha latest version):**
`https://license.aivideoflow.in/download` — Windows/Android par seedha download, Mac par chip chunne ka page.
Seedha link chahiye to: `/download/windows`, `/download/mac-arm64`, `/download/mac-x86_64`, `/download/android`.

## Zaroori (purane server ke baad)

Purana portal bina password ke database aur API keys de raha tha. Isliye:
1. Google AI Studio me purani **Gemini API key delete** karke nayi banaiye (agar wapas use karni ho).
2. OpenAI key bhi rotate kariye agar daali thi.
3. Purani keys wale customers ko naya app (v1.1) dijiye — purane app ka license check ab kaam nahi karta.
