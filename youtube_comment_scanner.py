"""
YouTube Harmful Comment Scanner  (no API keys required)
=========================================================
Fetches all comments (including replies) from a YouTube video,
flags derogatory / threatening / physically-harmful ones, and
produces a list of direct mobile-friendly links so you can open
each comment on your phone and take a screenshot.

Requirements
------------
  pip install youtube-comment-downloader

No API keys, no Google Cloud account, nothing else needed.

Usage
-----
  python youtube_comment_scanner.py <youtube_video_url>

  The script writes results to  harmful_comments_<video_id>_<timestamp>.csv
  and also prints every flagged comment directly to the terminal.
"""

import re
import sys
import csv
from datetime import datetime
from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_RECENT, SORT_BY_POPULAR

# ---------------------------------------------------------------------------
# Configuration — edit this list to add / remove phrases to detect
# ---------------------------------------------------------------------------

MAX_DISPLAY_TEXT_LENGTH = 200  # characters shown in terminal output per comment

HARMFUL_KEYWORDS = [
    # ----------------------------------------------------------------
    # English — death threats / physical violence
    # ----------------------------------------------------------------
    "kill", "murder", "shoot", "stab", "beat", "death",
    "hurt you", "harm you", "wish you dead", "hope you die",
    "you should die", "go die", "kys", "kill yourself",
    "end your life", "take your life", "death threat", "bomb threat",
    "i'll find you", "i will find you", "come for you", "hunting you",
    "cut you", "i'll cut", "choke you", "strangle you",
    # English — severe derogatory
    "go to hell", "rot in hell", "burn in hell",
    "son of a bitch", "piece of shit", "motherfucker", "fuck you",
    "you bastard", "you idiot", "you moron", "you retard",
    "go fuck yourself", "get lost", "worthless", "disgusting",

    # ----------------------------------------------------------------
    # Hindi — threats / violence (Devanagari script)
    # ----------------------------------------------------------------
    "मार डालूंगा", "जान से मार", "खून कर दूंगा", "तुझे मार दूंगा",
    "मर जा", "मर जाओ", "मौत", "तेरी मौत",
    "काट दूंगा", "तोड़ दूंगा", "जला दूंगा",
    "ढूंढ लूंगा", "छोड़ूंगा नहीं",

    # Hindi — abuses (Devanagari script)
    "मादरचोद", "भड़वा", "भड़वे", "रंडी", "कमीना", "कमीने",
    "हरामी", "हरामजादा", "हरामजादे", "कुत्ता", "कुत्ते",
    "सुअर", "गधा", "गधे", "बेशर्म", "बेहया",
    "चूतिया", "चूतिये", "साले", "साली", "बकवास",
    "निकल जा", "मुंह बंद कर",

    # ----------------------------------------------------------------
    # Hindi — threats / violence (Roman/transliterated — common on YouTube)
    # ----------------------------------------------------------------
    "maar dunga", "jaan se maar", "khoon kar dunga", "tujhe maar dunga",
    "mar ja", "mar jao", "maut", "teri maut",
    "kaat dunga", "tod dunga", "jala dunga",
    "dhundh lunga", "chhodunga nahi",

    # Hindi — abuses (Roman/transliterated)
    "madarchod", "madarcho", "bhadwa", "bhadwe", "randi",
    "kamina", "kamine", "harami", "haramzada", "haramzade",
    "kutta", "kutte", "suar", "gadha", "gadhe",
    "besharam", "behaya", "chutiya", "chutiye",
    "saale", "saali", "bakwas",
    "nikal ja", "muh band kar", "teri maa", "teri behan",
    "mc", "bc", "bhenchod", "bhencho",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> str:
    """Return the 11-character YouTube video ID from any common URL format."""
    m = re.search(r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})", url)
    if m:
        return m.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def comment_link(video_id: str, comment_id: str) -> str:
    """
    Direct link to a specific comment.
    Opening this on your phone in the YouTube app scrolls straight to the comment.
    """
    return f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"


def is_harmful(text: str) -> tuple[bool, str]:
    """
    Returns (flagged, matched_phrase).
    Checks the comment text against every phrase in HARMFUL_KEYWORDS.
    """
    lower = text.lower()
    for phrase in HARMFUL_KEYWORDS:
        if phrase in lower:
            return True, phrase
    return False, ""

# ---------------------------------------------------------------------------
# Comment fetching (no API key — uses youtube-comment-downloader)
# ---------------------------------------------------------------------------

def _normalise(raw: dict, video_id: str) -> dict:
    """Convert a raw library comment dict to our normalised format."""
    cid = raw.get("cid", "")
    is_reply = raw.get("reply", False)
    parent_id = cid.rsplit(".", 1)[0] if (is_reply and "." in cid) else ""
    return {
        "comment_id": cid,
        "author": raw.get("author", "Unknown"),
        "text": raw.get("text", ""),
        "published_at": raw.get("time", ""),
        "like_count": raw.get("votes", 0),
        "is_reply": is_reply,
        "parent_id": parent_id,
        "link": comment_link(video_id, cid),
    }


def _fetch_pass(downloader: YoutubeCommentDownloader, video_url: str,
                video_id: str, sort_by: int, label: str,
                seen_ids: set) -> list[dict]:
    """
    Run one fetch pass with the given sort order.
    Only returns comments whose IDs have not been seen yet (deduplication).
    """
    results = []
    local_count = 0
    try:
        for raw in downloader.get_comments_from_url(video_url, sort_by=sort_by):
            cid = raw.get("cid", "")
            if not cid or cid in seen_ids:
                continue
            seen_ids.add(cid)
            results.append(_normalise(raw, video_id))
            local_count += 1
            if local_count % 500 == 0:
                print(f"  [{label}] {local_count} new comments so far...", flush=True)
    except Exception as exc:
        print(
            f"\n[{label}] Stopped early: {exc}",
            file=sys.stderr,
        )
        if not results:
            return results
    print(f"  [{label}] pass complete — {local_count} unique comments collected.")
    return results


def fetch_all_comments(video_url: str, video_id: str) -> list[dict]:
    """
    Download comments for the given video URL using two sort passes
    (Most Popular + Most Recent) and deduplicate by comment ID.
    This maximises the number of comments retrieved without an API key.

    Note: YouTube's internal API does not expose every comment on a large
    video through its public web interface. The two-pass approach is the
    best achievable without a YouTube Data API v3 key.
    """
    downloader = YoutubeCommentDownloader()
    seen_ids: set = set()

    print(f"\nFetching comments for video: {video_id}")
    print("Running two passes (Popular + Recent) to maximise coverage...")
    print("(This may take several minutes for large videos.)\n")

    pass1 = _fetch_pass(downloader, video_url, video_id, SORT_BY_POPULAR, "Popular", seen_ids)
    pass2 = _fetch_pass(downloader, video_url, video_id, SORT_BY_RECENT,  "Recent",  seen_ids)

    all_comments = pass1 + pass2
    print(f"\n  Done. Total unique comments fetched: {len(all_comments)}")
    if not all_comments:
        print("ERROR: No comments were fetched. Check the URL or your network.", file=sys.stderr)
        sys.exit(1)
    return all_comments

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_video(video_url: str) -> None:
    video_id = extract_video_id(video_url)
    comments = fetch_all_comments(video_url, video_id)

    print(f"\nAnalyzing {len(comments)} comments for harmful content...")

    flagged = []
    for i, c in enumerate(comments, 1):
        if i % 200 == 0:
            print(f"  Checked {i}/{len(comments)}...", flush=True)

        harmful, keyword_match = is_harmful(c["text"])
        if harmful:
            flagged.append({**c, "keyword_match": keyword_match})

    # -----------------------------------------------------------------------
    # Terminal output
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  RESULTS: {len(flagged)} harmful comment(s) found out of {len(comments)}")
    print(f"{'='*60}\n")

    for idx, c in enumerate(flagged, 1):
        kind = "REPLY" if c["is_reply"] else "COMMENT"
        print(f"[{idx}] {kind} by {c['author']}  ({c['published_at']})")
        print(f"  Text    : {c['text'][:MAX_DISPLAY_TEXT_LENGTH]}")
        print(f"  Matched : \"{c['keyword_match']}\"")
        print(f"  Link    : {c['link']}")
        print()

    # -----------------------------------------------------------------------
    # Save to CSV
    # -----------------------------------------------------------------------
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"harmful_comments_{video_id}_{timestamp}.csv"

    try:
        with open(csv_filename, "w", newline="", encoding="utf-8") as f:
            fieldnames = [
                "comment_id", "author", "published_at", "like_count",
                "is_reply", "parent_id", "keyword_match", "link", "text",
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(flagged)
        print(f"Results saved to: {csv_filename}")
    except OSError as exc:
        print(f"Could not save CSV file: {exc}", file=sys.stderr)
    print(
        "\nTip: Open each link on your phone in the YouTube app — "
        "it will scroll directly to the comment so you can take a screenshot."
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:   python youtube_comment_scanner.py <youtube_video_url>")
        print("Example: python youtube_comment_scanner.py 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'")
        sys.exit(1)

    scan_video(sys.argv[1])
