# AI Video Flow 🎬⚡

> **Next-Generation Autonomous Video Editing & Motion Graphics Automation Platform**  
> Direct Native Bridge between **Claude Desktop (Model Context Protocol - MCP)**, **DaVinci Resolve (Studio & Free)**, **HyperFrames**, and **OpenTimelineIO**.

---

## 🌟 Overview

**AI Video Flow** empowers video editors, wedding studios, and content creators to turn hundreds of gigabytes of raw camera footage, class recordings, and audio tracks into polished, review-ready timelines with a single natural language prompt.

Instead of spending days manually cutting shaky camera movements, syncing multi-cams, and building color nodes, AI Video Flow does the heavy lifting while **leaving creative control in your hands**—timelines and color node trees open directly in DaVinci Resolve for visual review before manual export.

---

## 🚀 Key Features (52 Active Tools)

### 1. 💍 Wedding & Event Story Assembly
* **Optical Flow Camera Shake Trimming**: Analyzes raw clips, trims shaky start/end camera moves, and extracts the 3–4 second "Golden Window" (Laplacian sharpness > 82%).
* **Music Beat-Synchronization**: Uses `librosa` to detect BPM, drops, and transients, cutting clips directly on the beat grid.
* **3 Automated Timelines**:
  1. `01_Teaser_60s` (Fast-paced 9:16 vertical cuts for Reels & Shorts).
  2. `02_Cinematic_Highlight_4min` (3-Act emotional wedding highlight).
  3. `03_Full_Wedding_Film` (Event-wise ritual sequence: Haldi, Mehndi, Varmala, Pheras).

### 2. 📚 1.5 - 2 Hour Class & Lecture Auto-Editor
* **Dead-Air & Silence Cut**: Removes pauses, coughing, and blackboard writing dead air, condensing a 1.5-hour recording into a tight 45-minute lesson.
* **Dynamic Multi-Cam Punch-Ins**: Alternates between 1.0x wide and 1.12x medium close-ups on key sentences to transform single-camera lectures into professional studio setups.
* **B-Roll & Slide Overlays**: Places presentation slides or diagrams onto Video Track 2 (V2).

### 3. 🎨 Color Page — 5-Node Tree & Reference Color Transfer
* **5-Node Pipeline**: Node 1 (Exposure/Balance) ➔ Node 2 (Face Smooth/Skin Glow) ➔ Node 3 (Contrast/Sat) ➔ Node 4 (Look/LUT) ➔ Node 5 (Glow/Vignette).
* **Reference Photo to 3D LUT**: Analyzes any reference image (Bollywood film frame, Pinterest look) and outputs an industry-standard 33x33x33 `.cube` LUT.
* **Multi-Camera Color Match**: Matches Camera B (e.g. Sony) to Camera A (e.g. Canon/Nikon) colors using LAB distribution alignment.
* **Beauty Retouching & Auto White Balance**: Neutralizes harsh yellow wedding tent lighting and softens skin while preserving natural texture.

### 4. ✨ HyperFrames Motion Graphics & Titles
* **Royal Wedding Titles**: Luxury gold shimmer and 3D animated typography rendered to video and automatically placed on Track 2 (V2).
* **Glassmorphism Lower Thirds**: Animated transparent name badges for Bride, Groom, or Speakers.

### 5. 🎙️ Fairlight Audio & Multi-Cam Sync
* **Auto-Ducking (-18 dB)**: Lowers background music beneath speech and smoothly restores volume during pauses.
* **Multi-Cam Waveform Sync**: Automatically syncs multiple camera angles using audio waveform cross-correlation.
* **Broadcast Loudness**: Normalizes audio tracks to -14 LUFS standard.

### 6. 🔄 OpenTimelineIO (OTIO) Cross-NLE Interchange
* Seamlessly exports timelines to **Final Cut Pro XML (`.xml`)**, **Adobe Premiere Pro**, and **CMX 3600 (`.edl`)**.

---

## 🔐 Licensing, payments & AI (server)

Licenses, Razorpay payments and Claude AI run on the license server (`server/`, deployed to flow.aiphotoflow.in).
Licenses are Ed25519-signed by the server; the app only holds the public key, and every paid AI call is checked
and relayed by the server, so the Claude API key never reaches customers. Setup guide: [SERVER_SETUP.md](SERVER_SETUP.md).

---

## 💿 Standalone App (No Python Needed)

Python and all AI libraries are bundled inside the app.

* **macOS:** open `AI-Video-Flow-Mac-arm64.dmg` (Apple Silicon) or `AI-Video-Flow-Mac-x86_64.dmg` (Intel) → drag **AI Video Flow** into **Applications**. First launch: right-click the app → **Open**.
* **Windows:** run `AI-Video-Flow-Windows-Setup.exe` → Desktop & Start Menu shortcuts are created.
* Inside the app press **CONNECT ALL** once to register it with Claude Desktop.

**Building the apps**
* macOS: `bash packaging/build_mac_app.sh`
* Windows (on a Windows PC): double-click `build_windows_exe.bat`
* Both, in the cloud: GitHub → **Actions → Build Apps → Run workflow** (pushing a `v*` tag also publishes a Release).

---

## 📦 1-Click Zero-Touch Installation

### 🖥️ Windows:
1. Double-click **`AI-Video-Flow-Setup.bat`**.
2. It installs all dependencies, configures Claude Desktop, and puts an **"AI Video Flow"** shortcut on your Desktop!

### 🍎 macOS:
1. Double-click **`AI-Video-Flow-Setup-Mac.command`** or run:
   ```bash
   bash installer/install_mac.sh
   ```
2. Double-click **`AI Video Flow.app`** anytime to launch the dashboard.

---

## ⚙️ DaVinci Resolve Setting (One-Time Setup)

To allow AI Video Flow to assemble timelines:
1. Open **DaVinci Resolve**.
2. Go to: **Preferences** (`Ctrl + ,` / `Cmd + ,`) ➔ **System** ➔ **General**.
3. Set **"External scripting using"** to: **"Local"** (or Network).
4. Click **Save**.

---

## 💬 Natural Language Prompting (Claude Desktop)

Once connected, open Claude Desktop and speak or type in English, Hindi, or Hinglish:
* *"DaVinci Resolve status check karo"*
* *"Raw_Clips folder ki footage aur Haldi song lekar 4-minute ki highlight timeline bana do"*
* *"Timeline par Royal Warm 5-node color tree bana do aur white balance theek karo"*
* *"Annu & Pavitra ke liye Royal Gold animated title card banao"*
* *"1.5 ghante ki class recording me se saara dead-air cut karke clean timeline ready karo"*

---

## 📄 License
Apache-2.0 License. Built for professional video editors and creators worldwide.
