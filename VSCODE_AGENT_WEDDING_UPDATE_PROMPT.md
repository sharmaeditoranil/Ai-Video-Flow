# VS Code Claude Agent — Wedding Editing Upgrade Prompt

**How to use:** open the `AI Edit Flow` folder in VS Code. In the Claude agent chat, paste
the **MASTER PROMPT** once. Then, for each step, paste the matching **STEP command**
from Part 2. Give one step at a time, and let it finish and test before you send the next one.

---

## PART 1 — MASTER PROMPT (paste this first)

```text
You are working on my existing product "AI Video Flow" (this repository). It is a desktop
app (Python 3.12 + pywebview UI in packages/desktop_app, PyInstaller build, MCP server in
packages/mcp_server, Claude relay via the license server) that edits Indian wedding footage
and sends timelines to DaVinci Resolve, Premiere Pro and EDIUS (Final Cut Pro FCPXML export
is still to be added: ai_engine/fcp_xml.py writes FCP7 XML for Premiere/EDIUS).

CURRENT STATE (desktop v1.1.20, Nov 2026) — build on this, do not redo it
- Smart AI Director already exists in packages/desktop_app/backend.py: DIRECTOR_TOOLS
  (director_project_overview, director_list_clips, director_view_clips, director_view_media,
  director_analyze_audio, director_get_timeline, director_build_timeline) + DIRECTOR_GUIDE.
  cloud_ai_client.assistant_turn accepts tool results with images ({"_content": [...]}),
  runs up to 60 steps at effort "high". The Timeline Re-Editor already runs through it with a
  rule-based fallback (_reedit_rules_job). STEP 11 and STEP 20 extend this, not replace it.
- The project lives in ~/AI-Video-Flow-Output/last_plan.json ("analyzed" clips + "plans").
- Payments: the buy page is https://aivideoflow.in/buy (website/buy.html); the license
  server only creates orders/licenses (CORS for aivideoflow.in). Releases are built by
  GitHub Actions on a v1.x.y tag (.github/workflows/build-apps.yml).
- Known weaknesses found in a code review:
  * shot_type comes from the share of skin-coloured pixels (in_clip_trimmer.py), which
    fails under yellow/orange wedding light and on wide crowd shots.
  * StoryPlanner applies 50% slow motion without checking native_fps, so 25/30 fps clips
    stutter. Fix this in STEP 4 (slow motion only when native_fps >= 50).
  * Rituals come only from folder/file names and prompt keywords.
  * BeatAnalyzer has beats and onset peaks only: no bars, sections or vocal regions, and
    its "drop_times" are just the loudest onsets.
  * Colour: Reinhard LAB transfer only, no skin protection and no log input transforms.
  * Titles: one HTML title + one lower third template.
  * No automated tests except tests/test_mcp_server.py.

GOAL
Make AI Video Flow the best AI editor in the world for WEDDING films only: Teaser (60s),
Instagram Reel (30s, 9:16), Cinematic Highlight (3-5 min), Full Wedding Film, Same-Day Edit,
and Pre-wedding/Save-the-date. Every improvement must serve wedding editing.

MOST IMPORTANT RULES — UPGRADE, DO NOT REBUILD
1. Improve the EXISTING code. Do not create a new app, a new framework or a parallel
   architecture. Extend these existing files and classes wherever possible:
   - packages/ai_engine/in_clip_trimmer.py   (InClipTrimmer — shot quality, golden window)
   - packages/ai_engine/story_planner.py     (StoryPlanner — teaser/highlight/full film)
   - packages/ai_engine/beat_analyzer.py     (BeatAnalyzer — music)
   - packages/ai_engine/multicam_sync.py     (flash/metadata/audio sync)
   - packages/ai_engine/scene_detector.py, broll_placer.py, color_transfer_engine.py
   - packages/ai_engine/cloud_ai_client.py   (Smart AI: review_shots, assistant_turn)
   - packages/ai_engine/ai_audio.py          (ElevenLabs music/voice via server)
   - packages/hyperframes_engine/templates.py (wedding titles)
   - packages/core_resolve, core_premiere, core_edius, ai_engine/fcp_xml.py, otio_exporter.py
   - packages/desktop_app/backend.py, index.html, app.js, style.css (UI)
   - packages/mcp_server/server.py (MCP tools)
   A new file is allowed only when a feature truly has no home. Put it inside the existing
   package (e.g. packages/ai_engine/), and tell me why before creating it.
2. Keep every existing function signature, MCP tool name, API route (/api/...) and export
   format working. Add optional parameters instead of changing existing ones. Existing
   timelines (01_Wedding_Teaser_60s, 02_Cinematic_Highlight_4min, 03_Full_Wedding_Film) must
   still be produced.
3. Do not delete or rewrite non-wedding features (lecture editor, motion ad, reel cloner).
   Leave them untouched and do not spend effort on them.
4. Never modify, move or delete the user's source footage. All work is non-destructive.
5. Licensing, credits, the license server relay and the Ed25519 license check must keep
   working exactly as now. API keys never go into the app.
6. Work in small steps. For every step: first read the relevant files and show me a short
   plan (which files and functions you will change). Wait for my OK, then implement, add or
   update tests in tests/, run them, and report results. Make one git commit per step with
   a clear message. Do not push and do not build the release DMG unless I ask.
7. Reply to me in simple Hinglish. Code, comments and UI strings in English.
8. Every new local feature must work offline. Cloud (Smart AI) features must degrade
   gracefully when there is no premium license or no internet.
9. Never break the PyInstaller build: if you add a Python dependency, add it to
   requirements.txt and the packaging spec, and tell me the size impact. Large AI models
   (interpolation, upscale, stems, embeddings) download on first use with a checksum check
   instead of being bundled in the installer.
10. Every paid cloud feature (generation, upscale, voice isolation, Claude) goes through the
   existing license server relay. It uses the existing credit system and shows the cost
   before running.
11. Use only original or properly licensed assets (SFX, music, fonts, LUTs, models). Do not
   copy any assets, code or designs from other products.
12. Privacy (India DPDP Act 2023): faces, voices and names of couples and guests are personal
   data. Face/voice recognition runs on the device by default, needs a clear opt-in per
   project, and the data can be deleted with one click. Nothing personal leaves the computer
   unless I start a Smart AI / cloud feature that says so.
13. Marketing and website text must be true: no invented reviews, user counts, awards or
   "No.1" claims. Use real numbers and real testimonials (with permission) only.

WEDDING EDITING KNOWLEDGE (use this in every decision)
- Indian wedding order: Getting ready → Haldi → Mehndi → Sangeet → Baraat → Varmala →
  Pheras/Saat Phere → Sindoor & Mangalsutra → Vidaai/Bidaai → Reception. Also: Engagement/
  Ring ceremony, Tilak, Chooda, Jaggo, Nikah, Anand Karaj, South Indian muhurtham — detect
  them where present. Couple shoot, decor, venue and drone are B-roll layers.
- Story arc for the highlight: Anticipation (venue, details, getting ready) → Celebration
  (Haldi, Mehndi, Sangeet, Baraat) → Union (Varmala, Pheras, Sindoor, vows) → Emotion &
  Farewell (Bidaai, couple portrait, final hero shot + title).
- Teaser/Reel: strongest hook in the first 1-2 seconds, fast beat cuts, no idle frames,
  and it ends on the couple's best hero shot.
- Bride and Groom are the heroes. Balance their screen time. Parents and elders appear in
  emotional moments. Avoid random guests, closed eyes, people eating, or someone blocking
  the frame.
- Alternate wide → mid → close. Never put the same person with the same framing back to
  back. Match motion direction between cuts.
- Slow motion only on high-fps clips (50/60/100/120 fps). Otherwise use a gentle speed
  change or skip it.
- Energy sections cut on beats. Emotional sections let shots breathe across full bars. Big
  hero shots land on the song's drop or chorus.
- Vows and speeches play during instrumental parts of the song, with music ducked under them.
- Indian skin tones must stay natural under yellow tent light, LED stage light and Haldi
  yellow. Never make skin grey, orange or pink.

Confirm you understand. Then read the repository (especially the files listed above) and
give me a short report: what each wedding-related module currently does and its biggest
weakness for wedding editing. Do not change any code yet.
```

---

## PART 2 — STEP COMMANDS (paste one at a time, in order)

### STEP 0 — Safety net + quick fixes (do this before everything else)
```text
STEP 0: Before upgrading anything, protect what works today.
(a) Regression tests in tests/: build the teaser / highlight / full film plans from a small
    fake "analyzed" list (no real video needed), check the sequence names stay the same,
    clip in/out stays inside each source clip, total duration is close to the target, and
    the Premiere/EDIUS XML (fcp_xml.py, edius_adapter) and plan JSON are valid. Also test the
    Smart AI Director tools in backend.py (director_build_timeline with editor="none").
(b) Fix: StoryPlanner gives 50% slow motion only when the clip's native_fps >= 50; for
    25/30 fps clips use 100% (or 80% only if I ask for "gentle slow motion").
(c) Fix: in BeatAnalyzer, "drop_times" must be real energy rises (big loudness jumps spread
    across the song), not the first loudest onsets.
(d) Add a GitHub Actions job that runs the tests on every push.
Report what passed, then commit.
```

### STEP 1 — Smarter shot understanding (biggest upgrade)
```text
STEP 1: Upgrade shot understanding inside the existing InClipTrimmer / analysis flow.
Today clips are judged mainly by shake and sharpness, and rituals come from folder/filename
keywords. Add, as extra fields on each analyzed clip/segment (keep the existing fields):
- faces: count, biggest-face size, sharpness on the face, eyes-open, smile score
- shot_type computed from face-to-frame size (extreme wide / wide / mid / close / detail),
  replacing the current guess
- camera_move: static / pan / tilt / push-in / pull-out / orbit / drone / shaky
  (from the optical flow you already compute)
- audio_type: speech / music / crowd-cheer / silence, plus loudness peaks
- moment: wedding ritual label using visual cues (Haldi yellow, mandap fire, garland, stage
  lights, procession) plus clip time gaps. Folder/filename keywords stay as a strong hint.
- a single wedding_score (0-100) that combines quality + emotion + hero presence
Use lightweight on-device models (OpenCV DNN / ONNX Runtime, CoreML on Mac when available).
Cache results per file hash so re-runs are instant. Show progress in the existing UI.
Add tests with small sample frames.
```

### STEP 2 — Bride & Groom recognition
```text
STEP 2: Add face clustering on top of STEP 1. Group face embeddings across all clips into
people. In the desktop UI, show a small "People" gallery (one best face per cluster) where
I can name Bride, Groom, Mother/Father of bride/groom, Siblings, or mark "Guest". Save the
names in the project's analysis cache. After naming, every clip knows who is in it, and
StoryPlanner prefers clips with the Bride and Groom. Keep it optional: if I skip naming,
assume the largest, most frequent faces are the couple.
Face data stays on this computer, needs an opt-in per project (master rule 12) and has a
"Delete face data" button.
```

### STEP 3 — AI tiers & notes (Smart AI)
```text
STEP 3: Upgrade cloud_ai_client.review_shots into a wedding shot reviewer. For each clip
segment, send a small contact sheet (6-9 frames) and get strict JSON back:
{segment_id, tier: "A"|"B"|"C", moment, people, emotion, action, note, hero_frame_time}.
A = hero shot, B = usable, C = reject (blur, blocked, eyes closed, awkward).
Batch the requests, cache by file hash, never re-send a reviewed segment, and show the
credits used. Without premium, fall back to the local wedding_score from STEP 1 to make
the tiers. Feed the tiers into StoryPlanner.
```

### STEP 4 — Story Director rewrite inside StoryPlanner
```text
STEP 4: Improve StoryPlanner (same class, same public methods plan_teaser, plan_highlight,
plan_full_film — add optional params only). Change the selection from "slice the clip
list by index" to a two-stage process:
(a) Edit Plan: the acts and beats for the chosen format (use the wedding story arc from the
    master prompt). Claude builds it when premium is available; otherwise use a built-in
    template.
(b) Assembler: fill each beat with the best A/B tier segments, using the editorial rules
    (hook first, wide-mid-close alternation, no back-to-back same person + same framing,
    bride/groom balance, hero shot at the end, slow motion only on high fps).
Store a short "why chosen" reason on every timeline clip and add it as a marker note in
Resolve/Premiere. Also add two new formats in the same planner: "Instagram Reel 30s 9:16"
(with face-centred reframe) and "Same-Day Edit 2-3 min" (a fast mode with lighter analysis).
Existing output timelines must keep their names.
```

### STEP 5 — Music intelligence
```text
STEP 5: Upgrade BeatAnalyzer: add downbeats/bars, song sections (intro / verse / chorus /
drop / bridge / outro) and vocal vs instrumental regions. Map story acts to song sections:
the hero shot lands on the drop, emotional shots sit on long bars, and the energy part
cuts on beats. Place vows/speech lines (found by audio_type=speech) during instrumental
parts and auto-duck the music under them with smooth fades (reuse the existing ducking
code). Make the cut end exactly with the song's ending or a clean fade.
```

### STEP 6 — Vows & speeches (transcript editing)
```text
STEP 6: Add local transcription (faster-whisper or whisper.cpp, small model, Hindi +
English + Hinglish + Punjabi) for clips where audio_type=speech. In the UI, show the
transcript as text with timestamps. I can tick the best lines, and the planner uses those
lines in the highlight and full film. With premium, Claude suggests the 3-5 most emotional
lines. Export the selected lines as captions (SRT) for reels.
```

### STEP 7 — Wedding color looks
```text
STEP 7: Upgrade color_transfer_engine and the existing 5-node tree. Add skin-tone protection
(keep the skin hue range natural while the look is applied). Detect camera log footage
(S-Log3, C-Log, V-Log, N-Log) from metadata and apply the correct input transform. Add
original wedding looks: Royal Warm, Pastel Airy, Golden Haldi, Mehndi Green, Sangeet Night,
Classic Film, Moody Reception, Monochrome Vidaai. Each look gets an intensity 0-100 and
a before/after preview on my own frames in the UI. Keep the existing reference-photo LUT
and multi-cam match working.
```

### STEP 8 — Titles, branding & finishing
```text
STEP 8: Extend hyperframes_engine/templates.py. Add auto title cards from the project form
(couple names, date, city, venue, hashtag) and 10+ wedding templates: royal gold, floral,
minimal serif, Devanagari calligraphy, Punjabi/Urdu script, save-the-date, and an end card
with the studio logo + phone + Instagram. Add a "Studio Brand Kit" (logo, fonts, colours,
watermark) saved once in settings and reused automatically. Add optional finishing
toggles: letterbox, film grain, light leaks.
```

### STEP 9 — Review & revision loop
```text
STEP 9: Add a simple preview + review loop to the existing desktop UI. During analysis,
generate low-res H.264 proxies. Show a player with a timeline strip (acts, shots, song
sections). I can add timestamped comments like "00:42 yahan bride ka close-up chahiye" or
"ending thoda slow karo". The Smart AI assistant re-edits only that part, marks the
comment done, and saves a new version (v1, v2, v3) with one-click restore. Then I can
"Send again" to Resolve/Premiere/EDIUS as now.
```

### STEP 10 — Learn my studio style
```text
STEP 10: Add "Learn my style": I select 3-5 of my old wedding projects (Resolve .drp export,
Premiere .prproj/XML, EDIUS XML, FCPXML). Read them (read-only) and compute average shot
length per section, cut-on-beat ratio, transition types, slow-motion usage, title timing
and the overall grade. Save a Studio Style Profile and apply it in StoryPlanner for every
new wedding. Show the profile in the UI so I can tweak it.
```

### STEP 11 — Smart AI Director agent
```text
STEP 11: Upgrade cloud_ai_client.assistant_turn and the MCP server into a wedding editing
agent. Give it tools over everything above (analyze footage, list segments by
moment/person/tier, build edit plan, assemble timeline, replace shot, retime section,
apply look, add title, render preview, read review comments, send to NLE). It shows a live
to-do list in the UI, asks me a question when something is missing (e.g. "Bidaai footage
nahi mili, ending couple portrait pe karun?"), checks its own preview before saying done,
and remembers my preferences per studio. Keep all existing MCP tool names working and
add the new ones.
```

### STEP 12 — Premium wedding UI
```text
STEP 12: Redesign the existing desktop UI (index.html, style.css, app.js — same files,
same backend routes) with a "Royal Cinematic" look: deep charcoal background, warm gold
accent used sparingly, ivory text, an elegant serif for headings and a clean sans for the
UI, soft glass panels, smooth transitions, and dark + light themes. Screens:
Projects (wedding cards with couple names, cover frame, progress, output badges) →
New Wedding wizard (couple details → footage per camera → music → outputs) →
Library (filter by moment/person/tier, People gallery) → Story board (acts as columns,
"why chosen", regenerate act) → Preview & Review → Look Studio → Export.
Every long job shows progress, ETA and cancel. Errors explain the fix in simple
Hindi/English. Check that it works at small window sizes.
```

### STEP 13 — Stabilization & smart motion selects
```text
STEP 13: (a) Add stabilization for B-tier handheld segments so they are rescued instead of
rejected. Use the NLE's own stabilizer (Resolve: set the stabilization properties on the
timeline item; Premiere: Warp Stabilizer via the existing CEP bridge) and an offline
ffmpeg vidstab fallback for MP4 renders. Store a "needs_stabilize" flag from the STEP 1
shake score. If the camera wrote gyro data (Sony/GoPro/DJI metadata), use it for a better
shake measure.
(b) Improve camera-move detection into "Motion Selects": score intentional moves (gimbal
walk-in, push-in, orbit, slider, drone reveal, crane) separately from shake. Start with
optical-flow rules. Keep a labelled dataset (I will mark good/bad moves in the Library UI)
so a small on-device classifier can be trained later. Prefer these moves for openings,
transitions between rituals, and the final hero shot.
```

### STEP 14 — Sound design: SFX, room tone, voice cleanup
```text
STEP 14: Add a wedding sound-design layer, extending ai_audio.py and the existing ducking code.
(a) Original SFX library (create or source royalty-free files with a clear licence note):
riser 2s/4s, whoosh short/long, impact/braam, sub-drop, camera shutter, flash pop, crowd
cheer, dhol hit, shehnai sting, bell/chime, reverse swell. Every file gets metadata
{category, length, hit_at, loopable}. The assembler places a riser so its hit lands exactly
on a cut or drop, a whoosh on fast transitions, and a shutter on photo-style freeze frames.
Keep the SFX level sensible, put SFX on its own audio track, and make it optional.
(b) Room tone: when cutting inside vows/speeches, fill gaps with room tone taken from the
same clip's quiet parts so the audio never drops to silence.
(c) Voice isolation and stem separation: separate vocals/music/noise (Demucs-class model
offline where possible, ElevenLabs Audio Isolation via the license server for premium).
Use it to clean vows over DJ/dhol noise, and to remove the vocals from a song when a
speech plays over it.
(d) Loudness: keep -14 LUFS for reels/web and add a -23 LUFS option for broadcast/TV.
```

### STEP 15 — AI finishing: upscale, slow motion, cleanup
```text
STEP 15: Add AI finishing tools, all optional and all non-destructive (new files next to the
originals, then relink):
(a) AI slow motion: frame interpolation (RIFE-class model, ONNX, local) so 25/30 fps hero
shots can become smooth 50% slow motion. StoryPlanner may use this when a shot needs
slow motion but the source is not high fps.
(b) AI upscale/enhance: upscale 1080p to 4K and denoise low-light Sangeet/Reception shots
(local model first; a premium cloud model via the license server if needed).
(c) Blur/hide: blur selected faces, number plates or brand logos with tracking across the
shot (face boxes from STEP 1 + OpenCV tracker).
(d) Clean-up: remove a small unwanted object (light stand, a guest's phone) from a still
frame or a short shot using an inpainting model, and background removal for title/
photo-frame animations.
(e) Generative fill (premium, via the license server, with credits shown before the run):
generate a missing establishing/venue shot or an extended clip end from a frame, or
create a title background. Mark generated clips clearly in the timeline (clip colour +
"AI" label) so they are never mistaken for real footage.
```

### STEP 16 — Full Paper Cut (text-based editing)
```text
STEP 16: Extend STEP 6 into a full Paper Cut editor. Show the transcript of speeches,
vows, family interviews and couple interviews as an editable document: delete words or
lines, drag to reorder, mark ★ favourites, and use "tighten" to remove ums, repeats and long
pauses (Normal / Tight, like the existing pause setting). Every change compiles back to
the timeline with clean audio crossfades. Speakers are labelled by voice as well as by
face (speaker diarization + matching to the People from STEP 2, e.g. "Father of bride").
Add "find the same speaker in other clips". Claude (premium) can build a 60-90 second
emotional speech montage from the document.
```

### STEP 17 — Find similar, smart replace & footage tools
```text
STEP 17: (a) "Find similar": compute an image embedding per segment (CLIP-class model,
local) and add "show me shots like this" plus "replace this shot with a similar better
one" on the Story board and in the AI agent.
(b) Natural-language search in the Library: "bride laughing at Haldi", "drone shot of the
venue at night", "mother crying at Vidaai".
(c) Computer scan: find footage folders, camera cards, old wedding projects (Resolve,
Premiere, EDIUS, FCP) and LUT packs on all drives. Use it in the New Wedding wizard and in
"Learn my style". This is read-only and must not touch the files.
(d) Formats: support Blackmagic RAW and Canon RAW through the NLE or a proxy step. Handle
VFR phone footage (make a CFR proxy) and rotation metadata. Read reference videos from a
YouTube/Instagram link with the existing link_downloader (for pace/style reference only).
```

### STEP 18 — Motion graphics & typography engine
```text
STEP 18: Upgrade hyperframes_engine so the AI can design custom animated graphics for each
wedding, not only fixed templates: animated couple-name reveals, ritual title cards
("Haldi • 12 Feb • Udaipur"), family member name tags, a map/route animation for
destination weddings, photo-frame collages, countdown/save-the-date cards, Instagram
end cards and kinetic lyric/caption text for reels. Add a font library (open-licence fonts,
including Devanagari, Gurmukhi, Urdu and calligraphy styles) with search. Graphics render
to transparent video and are placed on V2+ in the NLE. Claude (premium) can write a new
graphic from a description, previews it, and saves it as a reusable template in the
Studio Brand Kit.
```

### STEP 19 — Client review link, phone review & more exports
```text
STEP 19: (a) Client review link: upload a low-res watermarked render through the license
server and create a password-protected link that expires. The couple opens it on a phone,
watches, and leaves timestamped comments or "Approved". The comments flow back into the
STEP 9 review panel, and the agent addresses them in a new version.
(b) Phone companion: extend the existing mobile_app/Reels app so the studio owner can see
project progress, watch previews and send comments or prompts to the desktop agent.
(c) More exports: FCPXML for Final Cut Pro, CapCut draft (for reel editors) and an After Effects-friendly export
(XML + rendered graphics) in addition to Resolve/Premiere/EDIUS/FCP. Also read CapCut
drafts in "Learn my style".
(d) Ready-to-post renders: a direct MP4 render for Instagram Reel, YouTube and WhatsApp
sizes, with the correct bitrate, loudness and a safe area for captions.
```

### STEP 20 — Smart AI agent power-ups
```text
STEP 20: Extend the STEP 11 agent:
(a) Parallel work: split a job into sub-tasks that run at the same time (teaser, highlight
and film built in parallel by sub-agents), each with its own progress in the to-do list.
(b) Multiple chats per wedding project, with history saved.
(c) Question cards with buttons ("Bidaai missing — end on: Couple portrait / Drone shot /
Title card") and suggestion chips after each answer ("Make it faster", "More emotional",
"Add a family montage").
(d) Self-review: before saying "done", render a low-res preview and a contact sheet, check
for repeated shots, wrong ritual order, closed eyes, bad cuts and audio jumps, fix them,
then report.
(e) Optional web lookups (with my consent) for things like a song's BPM/lyrics timing or
the meaning/order of a regional ritual.
(f) Keep everything exposed through the MCP server so Claude Desktop can drive the same agent.
```

### STEP 21 — Final check
```text
STEP 21: Run the full test suite and a complete end-to-end run on a sample wedding folder:
teaser, reel, highlight and full film → export to Resolve, Premiere XML, EDIUS XML and FCPXML.
Confirm that licensing, credits, free plan limits and the MCP connection still work. Make a
list of anything broken or unfinished. Update README.md with the new wedding features.
Do not build or publish the installers until I say so.
```

---

## PART 3 — INDIA-LEVEL PLATFORM & LAUNCH (product, trust, growth)

These steps turn a good editor into a product that editors all over India can find, trust,
pay for and recommend. Same rules as the master prompt (plan first, small commits, tests).

### STEP 22 — Trusted installers (no security warnings)
```text
STEP 22: Today the Mac app is ad-hoc signed and the Windows installer is unsigned, so users
see "unidentified developer" / SmartScreen warnings. Using my own certificates (I will add
them as GitHub secrets; tell me exactly which ones):
(a) macOS: sign with a Developer ID Application certificate (hardened runtime) and notarize +
    staple the DMG in .github/workflows/build-apps.yml.
(b) Windows: sign the app .exe and the Inno Setup installer (OV/EV certificate or Azure
    Trusted Signing) in the same workflow.
(c) Auto-update: add a "beta" channel in the license server admin so a new version goes to
    a few studios first, then to everyone.
(d) Opt-in crash reports (no footage, no personal data) so I learn about errors early.
Do not change licensing. Show me the cost of each certificate before starting.
```

### STEP 23 — Hindi-first experience & first-run onboarding
```text
STEP 23: (a) Add a language switch in the desktop app: English / हिन्दी (Hinglish where
natural). All UI strings go through one strings file; no hard-coded text in app.js.
(b) First-run wizard: choose editor (Resolve / Premiere / EDIUS), check that it is
connected, then offer a small licensed sample wedding clip pack so a new user gets a
teaser in under 10 minutes without their own footage.
(c) Clear empty states and error messages that say what to do next, in both languages.
(d) Optional notifications when a long job finishes (desktop notification; WhatsApp only
if I enable it on the license server and the user opts in).
```

### STEP 24 — Pricing, payments & growth engine
```text
STEP 24: Extend the existing license server (server/index.php) and website/buy.html:
(a) GST: my GSTIN in settings, a GST invoice PDF for every order (B2B buyers can add their
    GSTIN), emailed with the license key and downloadable from the buy page.
(b) Coupon codes (percent / flat, expiry, max uses) and a "course student" coupon type.
(c) Referral: every license gets a referral link; when a friend buys, both get Smart AI
    wedding credits. Track it in the admin portal. No cash payouts.
(d) Optional auto-renew with Razorpay Subscriptions for monthly/yearly plans, plus
    reminder emails before expiry and a "payment failed, retry" link.
(e) Admin dashboard: signups, downloads, free → paid conversion, active users, churn,
    revenue per plan, top referrers. Numbers come only from real data in the database.
Keep the existing Razorpay flow, license issue and webhook working.
```

### STEP 25 — Website & SEO for all of India
```text
STEP 25: Upgrade the website (website/ folder, same design system):
(a) Hindi + English versions of the main pages with hreflang, and Hindi landing pages.
(b) Landing pages for what editors search: "wedding teaser AI editing", "DaVinci Resolve
    wedding auto edit", "Premiere Pro wedding highlight AI", "EDIUS wedding editing",
    "Instagram wedding reel maker". Each with a real demo video, steps and FAQ.
(c) A showcase gallery of real before/after edits (only with the couple's/studio's
    permission) and real testimonials.
(d) SoftwareApplication, Product (with INR price) and FAQPage schema; sitemap update; fast
    pages (compressed images, lazy video), WhatsApp click-to-chat and a lead form that
    goes to the license server's existing lead API.
(e) A pricing page that matches the admin plans automatically (read /api/v1/plans).
No fake numbers, awards or reviews (master rule 13).
```

### STEP 26 — Marketing launch kit (content, not code)
```text
STEP 26: Create a /marketing folder with ready-to-use content in Hinglish and English:
(a) 30-day launch calendar for Instagram, YouTube and WhatsApp groups of wedding editors.
(b) 10 short reel scripts (15-30s) showing one real feature each (auto teaser in minutes,
    beat-synced cuts, Smart AI re-edit by prompt, Resolve/Premiere/EDIUS in one click).
(c) 5 YouTube tutorial outlines in Hindi (install, first wedding, Smart AI Director,
    colour, export) with chapter timestamps.
(d) A studio/editor partner programme page and outreach messages for wedding studios,
    editing institutes and photography associations.
(e) Email and WhatsApp templates: welcome, first-project tips, trial ending, renewal.
Every claim must match what the app really does (master rule 13).
```

### STEP 27 — Support, feedback & quality loop
```text
STEP 27: (a) In-app Help: searchable how-to articles (Hindi/English) and a "Contact
support" form that attaches the app version and the last log lines (with consent, no
footage). (b) A "Was this edit good?" thumbs up/down after each timeline, with an optional
comment, stored on the license server, so I can see which formats need work. (c) A public
changelog page on the website fed from the release notes. (d) Opt-in, privacy-friendly usage
statistics (which formats are used, how long analysis takes) to guide the next steps.
```

---

## Step order (recommended)
1. STEP 0 first (tests + quick fixes) so nothing breaks while upgrading.
2. Steps 1–5: the core of the edit quality.
3. Steps 22 + 24(a, b): trusted installers, GST invoices and coupons, needed before a big
   India-wide marketing push.
4. Steps 13–15 (stabilization, sound, AI finishing): the biggest visible difference in films.
5. Then 6 + 16 (Paper Cut), 7–8, 9 + 19 (review), 10, 17–18, 11 + 20 (agent), 12 (UI).
6. Steps 23, 24(c-e), 25, 26, 27 alongside, as the product matures.
7. Finally STEP 21 before every big release.

## Tips
- If the agent starts making a "new app" or rewriting a whole file, stop it and say:
  **"Purane code me hi update karo, naya architecture mat banao."**
- After each step, run the app yourself on one real wedding folder before moving on.
- If a step gets too big, tell the agent: **"Is step ko chhote parts me todo."**
