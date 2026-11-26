"""
Quote agent for Instagram.

Each run:
  1. Takes the next few quotes from quotes.txt
  2. Makes a white-background slide for each one (images/ folder)
  3. Pushes the images to GitHub so Instagram can download them
  4. Posts them to Instagram as a carousel with a caption

Settings you can change are right below.
"""

import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
from PIL import Image, ImageDraw, ImageFont

# ---------------- SETTINGS ----------------
SLIDES_PER_POST = 3          # quotes per carousel (1 = single image post)
API_VERSION = "v27.0"

CAPTIONS = [
    "Swipe if you been moving quiet 🤫 Which one hit? 👇🏾",
    "For whoever needed this today 🫶🏾 Send it to somebody who's going through it 📲",
    "Real ones only 💯 Drop a 🙏🏾 if you're still standing",
    "Save this for the days you feel like folding 🔒",
    "Which slide is you right now? 1, 2 or 3? 👇🏾",
    "Tag somebody who's been locked in lately 💪🏾",
]
FOLLOW_LINE = "Follow for daily motivation 💯"
HASHTAG_SETS = [
    "#motivation #grindmindset #hustle #staysolid #motivationalquotes",
    "#dontquit #motivation #keeppushing #mindset #motivationalquotes",
    "#realones #motivation #grind #successmindset #motivationalquotes",
]
# ------------------------------------------

ROOT = os.path.dirname(os.path.abspath(__file__))
QUOTES_FILE = os.path.join(ROOT, "quotes.txt")
STATE_FILE = os.path.join(ROOT, "state.json")
IMAGES_DIR = os.path.join(ROOT, "images")
TEXT_FONT = os.path.join(ROOT, "fonts", "Inter-Regular.otf")
EMOJI_FONT_PATHS = [
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    os.path.join(ROOT, "fonts", "NotoColorEmoji.ttf"),
]

DRY_RUN = os.environ.get("DRY_RUN") == "1"


# ---------------- IMAGE MAKING ----------------
SIZE = 1080
MARGIN = 70
FONT_SIZE = 56

_text_font = ImageFont.truetype(TEXT_FONT, FONT_SIZE)
_emoji_font = None
for p in EMOJI_FONT_PATHS:
    if os.path.exists(p):
        _emoji_font = ImageFont.truetype(p, 109)
        break


def _is_emoji(ch):
    o = ord(ch)
    return (o >= 0x1F000 or 0x2600 <= o <= 0x27BF or 0x2300 <= o <= 0x23FF
            or 0x2B00 <= o <= 0x2BFF or o in (0x200D, 0xFE0F, 0x2640, 0x2642))


def _emoji_image(seq):
    im = Image.new("RGBA", (500, 160), (0, 0, 0, 0))
    ImageDraw.Draw(im).text((0, 0), seq, font=_emoji_font, embedded_color=True)
    box = im.getbbox()
    if not box:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    im = im.crop(box)
    h = int(FONT_SIZE * 1.1)
    w = max(1, int(im.width * h / im.height))
    return im.resize((w, h), Image.LANCZOS)


def _words(text):
    """Split text into words, each word split into text/emoji pieces."""
    out = []
    for word in text.split(" "):
        runs = []
        for ch in word:
            e = _is_emoji(ch) and _emoji_font is not None
            if runs and runs[-1][0] == e:
                runs[-1][1] += ch
            else:
                runs.append([e, ch])
        out.append(runs)
    return out


def _run_width(run):
    is_emoji, txt = run
    return _emoji_image(txt).width if is_emoji else _text_font.getlength(txt)


def make_slide(quote, path):
    space = _text_font.getlength(" ")
    max_w = SIZE - 2 * MARGIN
    lines, cur = [[]], 0
    for word in _words(quote):
        ww = sum(_run_width(r) for r in word)
        if lines[-1] and cur + space + ww > max_w:
            lines.append([])
            cur = 0
        cur += (space if lines[-1] else 0) + ww
        lines[-1].append(word)

    line_h = int(FONT_SIZE * 1.4)
    img = Image.new("RGB", (SIZE, SIZE), "white")
    draw = ImageDraw.Draw(img)
    y = (SIZE - line_h * len(lines)) // 2
    for line in lines:
        x = MARGIN
        for i, word in enumerate(line):
            if i:
                x += space
            for is_emoji, txt in word:
                top = y + int(line_h * 0.12)
                if is_emoji:
                    e = _emoji_image(txt)
                    img.paste(e, (int(x), top), e)
                    x += e.width
                else:
                    draw.text((x, top), txt, font=_text_font, fill=(20, 20, 20))
                    x += _text_font.getlength(txt)
        y += line_h
    img.save(path, "JPEG", quality=95)


# ---------------- QUOTES + STATE ----------------
def load_quotes():
    with open(QUOTES_FILE, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]


def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {"next_quote": 0, "posts": 0}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ---------------- GITHUB ----------------
def git(*args):
    subprocess.run(["git", *args], cwd=ROOT, check=True)


def push_images(paths, message):
    git("config", "user.name", "quote-agent")
    git("config", "user.email", "quote-agent@users.noreply.github.com")
    git("add", *paths)
    git("commit", "-m", message)
    git("push")


def public_url(path):
    repo = os.environ["GITHUB_REPOSITORY"]          # e.g. shawnw110/qoute-agent
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{rel}"


# ---------------- INSTAGRAM ----------------
TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
USER_ID = os.environ.get("IG_USER_ID", "").strip()
HOSTS = ["https://graph.facebook.com", "https://graph.instagram.com"]


class ApiError(Exception):
    pass


def api(host, method, path, **params):
    params["access_token"] = TOKEN
    url = f"{host}/{API_VERSION}/{path}"
    r = requests.request(method, url, data=params if method == "POST" else None,
                         params=params if method == "GET" else None, timeout=60)
    try:
        data = r.json()
    except ValueError:
        raise ApiError(f"{r.status_code}: {r.text[:300]}")
    if r.status_code != 200 or "error" in data:
        err = data.get("error", {})
        raise ApiError(f"{err.get('message', data)} (code {err.get('code')})")
    return data


def pick_host():
    """Find which Instagram API address accepts this token."""
    last = None
    for host in HOSTS:
        try:
            api(host, "GET", USER_ID, fields="id,username")
            return host
        except ApiError as e:
            last = e
            print(f"  {host} didn't accept the token: {e}")
    raise SystemExit(f"Token was rejected. Last error: {last}")


def wait_ready(host, container_id):
    for _ in range(30):
        status = api(host, "GET", container_id, fields="status_code").get("status_code")
        if status == "FINISHED":
            return
        if status in ("ERROR", "EXPIRED"):
            raise ApiError(f"Instagram couldn't process media {container_id}: {status}")
        time.sleep(5)
    raise ApiError("Timed out waiting for Instagram to process the images")


def make_container(host, url, is_child):
    params = {"image_url": url}
    if is_child:
        params["is_carousel_item"] = "true"
    # Retry a few times in case GitHub hasn't finished serving the new image yet
    for attempt in range(6):
        try:
            return api(host, "POST", f"{USER_ID}/media", **params)["id"]
        except ApiError as e:
            if attempt == 5:
                raise
            print(f"  image not ready yet ({e}), retrying in 20s…")
            time.sleep(20)


def publish(urls, caption):
    host = pick_host()
    print(f"Using {host}")
    if len(urls) == 1:
        # single image: retry until GitHub is serving it, caption on the same container
        for attempt in range(6):
            try:
                cid = api(host, "POST", f"{USER_ID}/media",
                          image_url=urls[0], caption=caption)["id"]
                break
            except ApiError as e:
                if attempt == 5:
                    raise
                print(f"  image not ready yet ({e}), retrying in 20s…")
                time.sleep(20)
    else:
        children = [make_container(host, u, is_child=True) for u in urls]
        for c in children:
            wait_ready(host, c)
        cid = api(host, "POST", f"{USER_ID}/media", media_type="CAROUSEL",
                  children=",".join(children), caption=caption)["id"]
    wait_ready(host, cid)
    post = api(host, "POST", f"{USER_ID}/media_publish", creation_id=cid)
    print(f"Posted! Instagram media id: {post['id']}")


# ---------------- MAIN ----------------
def main():
    quotes = load_quotes()
    state = load_state()
    start = state["next_quote"]
    batch = quotes[start:start + SLIDES_PER_POST]
    if len(batch) < SLIDES_PER_POST:
        sys.exit(f"Out of quotes! Used {start} of {len(quotes)}. Add more lines to quotes.txt.")

    os.makedirs(IMAGES_DIR, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    paths = []
    for i, q in enumerate(batch, 1):
        p = os.path.join(IMAGES_DIR, f"{stamp}-{i}.jpg")
        make_slide(q, p)
        paths.append(p)
        print(f"Made slide {i}: {q}")

    n = state["posts"]
    caption = (f"{random.choice(CAPTIONS)}\n\n{FOLLOW_LINE}\n\n"
               f"{HASHTAG_SETS[n % len(HASHTAG_SETS)]}")

    if DRY_RUN:
        print("DRY_RUN is on, not posting. Caption would be:\n" + caption)
        return

    if not TOKEN or not USER_ID:
        sys.exit("Missing IG_ACCESS_TOKEN or IG_USER_ID secret.")

    state["next_quote"] = start + SLIDES_PER_POST
    state["posts"] = n + 1
    save_state(state)
    push_images(paths + [STATE_FILE], f"Post {n + 1}: slides {stamp}")
    time.sleep(15)  # give GitHub a moment to serve the new files

    publish([public_url(p) for p in paths], caption)


if __name__ == "__main__":
    main()
