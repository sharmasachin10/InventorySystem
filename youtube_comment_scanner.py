"""
YouTube Harmful Comment Scanner
================================
Fetches all comments (including replies) from a YouTube video,
flags derogatory / threatening / physically-harmful ones, and
produces a list of direct mobile-friendly links so you can open
each comment on your phone and take a screenshot.

Requirements
------------
  pip install google-api-python-client requests

APIs needed
-----------
  - YouTube Data API v3 key  (YOUTUBE_API_KEY)
  - Google Perspective API key (PERSPECTIVE_API_KEY)
    Get one at: https://developers.perspectiveapi.com/s/docs-get-started

Usage
-----
  python youtube_comment_scanner.py <youtube_video_url>

  The script writes results to  harmful_comments_<video_id>_<timestamp>.csv
  and also prints every flagged comment directly to the terminal.
"""

import os
import re
import sys
import csv
import time
import requests
from datetime import datetime
from googleapiclient.discovery import build

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
PERSPECTIVE_API_KEY = os.environ.get("PERSPECTIVE_API_KEY", "")

# Perspective API attributes and minimum score threshold (0-1) to flag a comment
PERSPECTIVE_ATTRIBUTES = ["TOXICITY", "SEVERE_TOXICITY", "THREAT", "INSULT", "IDENTITY_ATTACK"]
PERSPECTIVE_THRESHOLD = 0.75   # comments scoring above this on any attribute are flagged
MAX_DISPLAY_TEXT_LENGTH = 200  # characters shown in terminal output per comment

# Fallback keyword list used when Perspective API is not configured
HARMFUL_KEYWORDS = [
    # threats / violence
    "kill you", "i'll kill", "i will kill", "gonna kill", "going to kill",
    "murder you", "i'll murder", "shoot you", "i will shoot",
    "stab you", "i'll stab", "beat you up", "beat you to death",
    "hurt you", "harm you", "wish you dead", "hope you die",
    "you should die", "go die", "kys", "kill yourself",
    "end your life", "take your life",
    # severe derogatory slurs (abbreviated to avoid embedding full slurs)
    "death threat", "bomb threat",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def extract_video_id(url: str) -> str:
    """Return the 11-character YouTube video ID from any common URL format."""
    patterns = [
        r"(?:v=|youtu\.be/|embed/|shorts/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    raise ValueError(f"Could not extract video ID from URL: {url}")


def comment_link(video_id: str, comment_id: str) -> str:
    """
    Return a direct link to the comment.
    On mobile the YouTube app intercepts this URL and scrolls to the comment.
    """
    return f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"


def is_harmful_by_keywords(text: str) -> tuple[bool, str]:
    """Simple keyword-based fallback detector. Returns (flagged, matched_phrase)."""
    lower = text.lower()
    for phrase in HARMFUL_KEYWORDS:
        if phrase in lower:
            return True, phrase
    return False, ""


def analyze_with_perspective(text: str) -> tuple[bool, dict]:
    """
    Call the Perspective API.
    Returns (flagged, {attribute: score, ...}).
    Returns (False, {}) if the API key is missing or the call fails.
    """
    if not PERSPECTIVE_API_KEY:
        return False, {}

    url = (
        "https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze"
        f"?key={PERSPECTIVE_API_KEY}"
    )
    payload = {
        "comment": {"text": text},
        "requestedAttributes": {attr: {} for attr in PERSPECTIVE_ATTRIBUTES},
        "languages": ["en"],
        "doNotStore": True,
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 429:
            # Rate limited — back off and retry once
            time.sleep(2)
            resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        scores = {
            attr: data["attributeScores"][attr]["summaryScore"]["value"]
            for attr in PERSPECTIVE_ATTRIBUTES
            if attr in data.get("attributeScores", {})
        }
        flagged = any(s >= PERSPECTIVE_THRESHOLD for s in scores.values())
        return flagged, scores
    except requests.RequestException as exc:
        print(f"  [Perspective API error] {exc}", file=sys.stderr)
        return False, {}


def is_harmful(text: str) -> tuple[bool, str, dict]:
    """
    Returns (flagged, keyword_match_or_empty, perspective_scores).
    Perspective API is tried first when a key is available;
    keyword matching is always applied as a safety net.
    """
    perspective_flagged, scores = analyze_with_perspective(text)
    keyword_flagged, keyword_match = is_harmful_by_keywords(text)
    flagged = perspective_flagged or keyword_flagged
    return flagged, keyword_match, scores

# ---------------------------------------------------------------------------
# YouTube comment fetching
# ---------------------------------------------------------------------------

def fetch_replies(youtube, parent_id: str, video_id: str) -> list[dict]:
    """Fetch all replies to a top-level comment thread."""
    replies = []
    page_token = None
    while True:
        response = youtube.comments().list(
            part="snippet",
            parentId=parent_id,
            maxResults=100,
            pageToken=page_token,
            textFormat="plainText",
        ).execute()

        for item in response.get("items", []):
            snippet = item["snippet"]
            replies.append({
                "comment_id": item["id"],
                "author": snippet.get("authorDisplayName", "Unknown"),
                "text": snippet.get("textDisplay", ""),
                "published_at": snippet.get("publishedAt", ""),
                "like_count": snippet.get("likeCount", 0),
                "is_reply": True,
                "parent_id": parent_id,
                "link": comment_link(video_id, item["id"]),
            })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return replies


def fetch_all_comments(youtube, video_id: str) -> list[dict]:
    """
    Fetch every top-level comment and all replies for the given video.
    Handles pagination automatically.
    """
    all_comments = []
    page_token = None
    page_num = 0

    print(f"\nFetching comments for video: {video_id}")

    while True:
        page_num += 1
        print(f"  Fetching comment page {page_num}...", end="", flush=True)

        response = youtube.commentThreads().list(
            part="snippet,replies",
            videoId=video_id,
            maxResults=100,
            pageToken=page_token,
            textFormat="plainText",
            order="time",
        ).execute()

        for item in response.get("items", []):
            thread_id = item["id"]
            top = item["snippet"]["topLevelComment"]
            top_snippet = top["snippet"]

            all_comments.append({
                "comment_id": top["id"],
                "author": top_snippet.get("authorDisplayName", "Unknown"),
                "text": top_snippet.get("textDisplay", ""),
                "published_at": top_snippet.get("publishedAt", ""),
                "like_count": top_snippet.get("likeCount", 0),
                "is_reply": False,
                "parent_id": "",
                "link": comment_link(video_id, top["id"]),
            })

            # Collect replies — YouTube returns up to 5 inline; fetch the rest
            reply_count = item["snippet"].get("totalReplyCount", 0)
            inline_replies = item.get("replies", {}).get("comments", [])

            if reply_count > len(inline_replies):
                # More replies exist — fetch them all
                full_replies = fetch_replies(youtube, thread_id, video_id)
                all_comments.extend(full_replies)
            else:
                for r in inline_replies:
                    r_snippet = r["snippet"]
                    all_comments.append({
                        "comment_id": r["id"],
                        "author": r_snippet.get("authorDisplayName", "Unknown"),
                        "text": r_snippet.get("textDisplay", ""),
                        "published_at": r_snippet.get("publishedAt", ""),
                        "like_count": r_snippet.get("likeCount", 0),
                        "is_reply": True,
                        "parent_id": thread_id,
                        "link": comment_link(video_id, r["id"]),
                    })

        print(f" {len(all_comments)} total so far.")
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print(f"  Done. Total comments fetched: {len(all_comments)}")
    return all_comments

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_video(video_url: str):
    if not YOUTUBE_API_KEY:
        print(
            "ERROR: YOUTUBE_API_KEY environment variable is not set.\n"
            "Export it before running:\n"
            "  export YOUTUBE_API_KEY='your_key_here'\n"
            "Get a key at https://console.cloud.google.com/ → YouTube Data API v3",
            file=sys.stderr,
        )
        sys.exit(1)

    if not PERSPECTIVE_API_KEY:
        print(
            "WARNING: PERSPECTIVE_API_KEY is not set. "
            "Falling back to keyword-based detection only.\n"
            "For more accurate results, set PERSPECTIVE_API_KEY.\n"
            "Get a key at https://developers.perspectiveapi.com/\n"
        )

    video_id = extract_video_id(video_url)
    youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

    comments = fetch_all_comments(youtube, video_id)

    print(f"\nAnalyzing {len(comments)} comments for harmful content...")

    flagged = []
    for i, c in enumerate(comments, 1):
        if i % 50 == 0:
            print(f"  Checked {i}/{len(comments)}...", flush=True)

        harmful, keyword_match, scores = is_harmful(c["text"])
        if harmful:
            flagged.append({
                **c,
                "keyword_match": keyword_match,
                "perspective_scores": "; ".join(
                    f"{k}={v:.2f}" for k, v in scores.items()
                ),
            })

    # -----------------------------------------------------------------------
    # Output
    # -----------------------------------------------------------------------
    print(f"\n{'='*60}")
    print(f"  RESULTS: {len(flagged)} harmful comment(s) found out of {len(comments)}")
    print(f"{'='*60}\n")

    for idx, c in enumerate(flagged, 1):
        kind = "REPLY" if c["is_reply"] else "COMMENT"
        print(f"[{idx}] {kind} by {c['author']}  ({c['published_at']})")
        print(f"  Text    : {c['text'][:MAX_DISPLAY_TEXT_LENGTH]}")
        if c["keyword_match"]:
            print(f"  Matched : \"{c['keyword_match']}\"")
        if c["perspective_scores"]:
            print(f"  Scores  : {c['perspective_scores']}")
        print(f"  Link    : {c['link']}")
        print()

    # Save to CSV
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_filename = f"harmful_comments_{video_id}_{timestamp}.csv"

    with open(csv_filename, "w", newline="", encoding="utf-8") as f:
        fieldnames = [
            "comment_id", "author", "published_at", "like_count",
            "is_reply", "parent_id", "keyword_match", "perspective_scores",
            "link", "text",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flagged)

    print(f"Results saved to: {csv_filename}")
    print(
        "\nTip: Open each link on your phone in the YouTube app — "
        "it will scroll directly to the comment so you can screenshot it."
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python youtube_comment_scanner.py <youtube_video_url>")
        print("Example: python youtube_comment_scanner.py 'https://www.youtube.com/watch?v=dQw4w9WgXcQ'")
        sys.exit(1)

    scan_video(sys.argv[1])
