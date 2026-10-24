# License server setup — flow.aiphotoflow.in

Ye server aapke customers ke licenses, payments (Razorpay) aur Claude AI ko sambhalta hai.
App ke saare paid features isi server se chalte hain, isliye app ko crack karke kuch free nahi milta.

## 1. Bundle banana (sirf aapke Mac par)

```bash
python3 packaging/build_server_bundle.py
```

Isse `dist/flow-server-bundle.zip` banta hai (index.php, .htaccess, config.php).
`config.php` me license sign karne wali secret key hai — ye ZIP kisi ko mat bhejiye, git me mat daaliye.
Secret key ki asli copy `~/.ai-video-flow-admin/signing_key.json` me hai. **Iska backup rakhiye** (pen drive / password manager).
Ye kho gayi to naye license sign nahi honge aur app dobara banana padega.

## 2. Upload (cPanel)

1. cPanel → File Manager → `flow.aiphotoflow.in` ka folder kholiye.
2. Purani files **delete** kariye: `index.php`, `admin.php`, `.htaccess`, `README.txt`.
   `licenses.db` ko delete mat kariye — naya server use apne aap `data/` folder me surakshit jagah le jaata hai.
3. `flow-server-bundle.zip` upload karke Extract kariye.
4. cPanel → Select PHP Version: PHP **8.1 ya naya**, extensions me `sodium`, `pdo_sqlite`, `curl` on hone chahiye.

## 3. Pehla login

- Kholiye: `https://flow.aiphotoflow.in/index.php/admin`
- Pehla password `~/.ai-video-flow-admin/signing_key.json` me `initial_admin_password` hai.
- Login ke turant baad **Settings → Security** me apna naya password set kariye.

## 4. Settings

- **AI · Anthropic Claude**: apni Claude API key (console.anthropic.com) daaliye → Save → Test connection.
  Key server par encrypted rehti hai, customers ko kabhi nahi milti.
- **Payments · Razorpay**: Key ID, Key secret, Webhook secret daaliye aur "Accept online payments" on kariye.
  Razorpay Dashboard → Webhooks me jo URL Settings me dikhta hai wo daaliye (events: `payment.captured`, `order.paid`).
  Customer buy page: `https://flow.aiphotoflow.in/index.php/buy`
- **Plans & pricing**: plans ki keemat, credits, validity yahan badaliye.

## 5. Roz ka kaam

- **Users → Create user manually**: naam, email, phone daaliye, plan chuniye → license key ban jaati hai, customer ko bhejiye.
- **Licenses**: + Credits, Extend, Reset PC (customer ne naya computer liya), Revoke.
- **Orders**: Razorpay se aaye saare payments, unki license keys ke saath.
- **Usage**: kisne kitne AI weddings aur Claude requests use kiye.

## Zaroori (purane server ke baad)

Purana portal bina password ke database aur API keys de raha tha. Isliye:
1. Google AI Studio me purani **Gemini API key delete** karke nayi banaiye (agar wapas use karni ho).
2. OpenAI key bhi rotate kariye agar daali thi.
3. Purani keys wale customers ko naya app (v1.1) dijiye — purane app ka license check ab kaam nahi karta.
