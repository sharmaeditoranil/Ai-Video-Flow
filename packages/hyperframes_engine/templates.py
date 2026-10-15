"""
HyperFrames HTML/CSS Motion Graphics Templates for AI Video Flow.
Provides broadcast-grade wedding titles, lower-thirds, and overlays.
"""

def get_wedding_title_html(title: str, subtitle: str, date_venue: str, style: str = "Royal Gold", duration_sec: int = 5) -> str:
    """Returns complete standalone HTML composition for a luxury wedding title card."""
    
    # Color palette based on style
    if style == "Romantic Rose":
        accent = "#E0A9AF"
        glow = "rgba(224, 169, 175, 0.4)"
        bg = "radial-gradient(ellipse at center, #2C1820 0%, #10080C 100%)"
    elif style == "Modern Minimal":
        accent = "#E2E8F0"
        glow = "rgba(255, 255, 255, 0.2)"
        bg = "#0A0A0B"
    else: # Royal Gold (default)
        accent = "linear-gradient(135deg, #FFF0D0 0%, #DFBA73 50%, #C49746 100%)"
        glow = "rgba(223, 186, 115, 0.4)"
        bg = "radial-gradient(ellipse at center, #1A150D 0%, #080604 100%)"

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>{title}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@400;600;700&family=Montserrat:wght@300;400;500&display=swap');
    
    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}
    
    body {{
      width: 1920px;
      height: 1080px;
      background: {bg};
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      overflow: hidden;
      font-family: 'Cinzel', serif;
      color: #FFFFFF;
      position: relative;
    }}

    /* Subtle Animated Vignette & Light Pulse */
    .glow-circle {{
      position: absolute;
      width: 800px;
      height: 800px;
      background: radial-gradient(circle, {glow} 0%, transparent 70%);
      border-radius: 50%;
      filter: blur(40px);
      animation: pulse {duration_sec}s ease-in-out infinite alternate;
      z-index: 1;
    }}

    .container {{
      position: relative;
      z-index: 2;
      text-align: center;
      max-width: 1400px;
      padding: 40px;
    }}

    .divider {{
      width: 140px;
      height: 2px;
      margin: 25px auto;
      background: linear-gradient(90deg, transparent, #DFBA73, transparent);
      animation: expandDivider 2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }}

    .subtitle {{
      font-family: 'Montserrat', sans-serif;
      font-size: 24px;
      font-weight: 300;
      letter-spacing: 12px;
      text-transform: uppercase;
      color: #C0C0C0;
      margin-bottom: 20px;
      opacity: 0;
      transform: translateY(20px);
      animation: fadeInUp 1.8s cubic-bezier(0.16, 1, 0.3, 1) 0.3s forwards;
    }}

    .title {{
      font-size: 76px;
      font-weight: 600;
      letter-spacing: 8px;
      text-transform: uppercase;
      background: {accent};
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      text-shadow: 0 10px 30px {glow};
      margin: 0;
      opacity: 0;
      transform: scale(0.92);
      animation: scaleFadeIn 2.2s cubic-bezier(0.16, 1, 0.3, 1) 0.6s forwards;
    }}

    .date-venue {{
      font-family: 'Montserrat', sans-serif;
      font-size: 20px;
      font-weight: 400;
      letter-spacing: 6px;
      text-transform: uppercase;
      color: #A39682;
      margin-top: 15px;
      opacity: 0;
      transform: translateY(-15px);
      animation: fadeInDown 1.8s cubic-bezier(0.16, 1, 0.3, 1) 1.0s forwards;
    }}

    @keyframes scaleFadeIn {{
      0% {{ opacity: 0; transform: scale(0.92); filter: blur(10px); }}
      100% {{ opacity: 1; transform: scale(1.0); filter: blur(0px); }}
    }}

    @keyframes fadeInUp {{
      0% {{ opacity: 0; transform: translateY(20px); }}
      100% {{ opacity: 1; transform: translateY(0); }}
    }}

    @keyframes fadeInDown {{
      0% {{ opacity: 0; transform: translateY(-15px); }}
      100% {{ opacity: 1; transform: translateY(0); }}
    }}

    @keyframes expandDivider {{
      0% {{ width: 0; opacity: 0; }}
      100% {{ width: 140px; opacity: 1; }}
    }}

    @keyframes pulse {{
      0% {{ transform: scale(0.9); opacity: 0.6; }}
      100% {{ transform: scale(1.15); opacity: 0.9; }}
    }}
  </style>
</head>
<body>
  <div class="glow-circle"></div>
  <div class="container">
    <div class="subtitle">{subtitle}</div>
    <h1 class="title">{title}</h1>
    <div class="divider"></div>
    <div class="date-venue">{date_venue}</div>
  </div>
</body>
</html>
"""

def get_lower_third_html(name: str, role: str, style: str = "Glass Gold", duration_sec: int = 5) -> str:
    """Returns standalone HTML composition for an animated lower-third banner (transparent canvas)."""
    
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Lower Third - {name}</title>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Cinzel:wght@600&family=Montserrat:wght@400;500&display=swap');
    
    * {{
      margin: 0;
      padding: 0;
      box-sizing: border-box;
    }}

    body {{
      width: 1920px;
      height: 1080px;
      background: transparent;
      display: flex;
      align-items: flex-end;
      padding: 100px 120px;
      overflow: hidden;
    }}

    .lower-third-card {{
      display: flex;
      align-items: center;
      background: rgba(18, 15, 12, 0.78);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border-left: 6px solid #DFBA73;
      border-top: 1px solid rgba(223, 186, 115, 0.3);
      border-right: 1px solid rgba(223, 186, 115, 0.2);
      border-bottom: 1px solid rgba(223, 186, 115, 0.3);
      padding: 22px 42px;
      border-radius: 4px;
      box-shadow: 0 16px 36px rgba(0, 0, 0, 0.6);
      transform: translateX(-100px);
      opacity: 0;
      animation: slideIn 1.2s cubic-bezier(0.16, 1, 0.3, 1) forwards;
    }}

    .text-group {{
      display: flex;
      flex-direction: column;
      gap: 6px;
    }}

    .name {{
      font-family: 'Cinzel', serif;
      font-size: 36px;
      font-weight: 600;
      letter-spacing: 3px;
      color: #FFF2D6;
      text-transform: uppercase;
    }}

    .role {{
      font-family: 'Montserrat', sans-serif;
      font-size: 16px;
      font-weight: 500;
      letter-spacing: 5px;
      color: #DFBA73;
      text-transform: uppercase;
    }}

    @keyframes slideIn {{
      0% {{ transform: translateX(-100px); opacity: 0; }}
      100% {{ transform: translateX(0); opacity: 1; }}
    }}
  </style>
</head>
<body>
  <div class="lower-third-card">
    <div class="text-group">
      <div class="name">{name}</div>
      <div class="role">{role}</div>
    </div>
  </div>
</body>
</html>
"""
