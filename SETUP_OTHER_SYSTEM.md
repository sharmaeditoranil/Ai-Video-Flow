# AI Video Flow - Dusre System Me Setup Karne Ka Guide (Windows & Mac)

Agar aapko ye pura AI Video Flow setup kisi dusre Mac ya Windows PC par chalana hai, toh sirf **4 aasan cheezein** chahiye.

---

## 1. Dusre System Par Kya Kya Software Chahiye (Pre-requisites)

| # | Software | Download Link / Source | Kyu Chahiye |
|---|----------|------------------------|-------------|
| 1 | **DaVinci Resolve** (Studio ya Free 18, 19, ya 20) | [blackmagicdesign.com](https://www.blackmagicdesign.com/products/davinciresolve) | Video editing, coloring aur audio ke liye |
| 2 | **Python 3.10 ya 3.11** | [python.org/downloads](https://www.python.org/downloads/) | AI Scripting aur Server chalane ke liye |
| 3 | **Claude Desktop** | [claude.ai/download](https://claude.ai/download) | AI Assistant jisme aap bolkar ya likhkar command denge |
| 4 | **Node.js (LTS)** *(HyperFrames Motion Graphics ke liye)* | [nodejs.org](https://nodejs.org) | Animated Wedding Titles, Lower Thirds & Overlays render karne ke liye |
| 5 | **FFmpeg** *(Optional but recommended)* | Windows: `winget install Gyan.FFmpeg`<br>Mac: `brew install ffmpeg` | Audio beat & video duration fast probe ke liye |

> ⚠️ **Windows Users ke liye Zaroori Note:**
> Python install karte waqt **"Add Python to PATH"** wale checkbox par tick ✅ zaroor karein!

---

## 2. DaVinci Resolve Me 1 Zaroori Setting (Sirf 1 baar karna hai)

DaVinci Resolve AI ko allow kare timeline banane ke liye, iske liye ye setting on karein:
1. DaVinci Resolve open karein.
2. Top Menu par jayein: **DaVinci Resolve** -> **Preferences** (ya `Ctrl + ,` / `Cmd + ,`).
3. **System** tab par click karein -> **General** par click karein.
4. **"External scripting using"** setting ko **"Local"** (ya "Network") par set karein.
5. **Save** par click karein.

---

## 3. Dusre System Par 1-Click Install Kaise Karein

Aapko is poore `AI Edit Flow` folder ko dusre system me copy kar lena hai (Pendrive ya Google Drive se).

### 🖥️ Agar Dusra System WINDOWS hai:
1. Folder kholein.
2. `installer` folder ke andar **`install_windows.bat`** par double click karein!
3. Wo automatically:
   - Python check karega.
   - Saare AI libraries (`numpy`, `opencv`, `librosa`, `scipy`) install kar dega.
   - Claude Desktop ke andar `ai-video-flow-resolve` connect kar dega.
4. "Installation Complete" aane ke baad Claude Desktop ko restart karein.

### 🍎 Agar Dusra System MAC hai:
1. Terminal kholein aur is folder me jakar run karein:
   ```bash
   bash installer/install_mac.sh
   ```
2. Ye automatically sandbox permission issues solve karke Claude Desktop se connect kar dega.
3. Claude Desktop ko restart karein.

---

## 4. Check Kaise Karein (Testing)

1. DaVinci Resolve open karein aur koi bhi project open karein (ya naya project banayein).
2. Claude Desktop open karein.
3. Chat box me niche 🔌 icon (hammer/tools icon) me aapko **ai-video-flow-resolve** ke 38 tools dikhenge.
4. Bas natural Hindi ya English me likhein:
   - *"DaVinci Resolve status check karo"*
   - *"Footage import karke beat sync highlight timeline ready karo"*
   - *"Timeline me 5-node color grading tree bana do"*

Editor apni screen par timeline aur color review karega, aur jab sab pasand aa jaye tab **manually deliver page se render** kar sakta hai!
