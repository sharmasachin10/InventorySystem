"""
YouTube Harmful Comment Scanner — YouTube Data API v3
=======================================================
Fetches ALL comments (top-level + every reply) from a YouTube video using
the official YouTube Data API v3, flags derogatory / threatening comments,
and writes results to a CSV file.

Requirements
------------
  pip install google-api-python-client

You need a YouTube Data API v3 key:
  1. Go to https://console.cloud.google.com/
  2. Create a project → Enable "YouTube Data API v3"
  3. Create an API key under "Credentials"

Usage
-----
  # Pass the key via environment variable (recommended):
  export YOUTUBE_API_KEY="AIza..."
  python youtube_comment_scanner_v3.py <youtube_video_url>

  # Or pass the key directly:
  python youtube_comment_scanner_v3.py <youtube_video_url> --api-key AIza...

The script writes flagged results to:
  harmful_comments_<video_id>_<timestamp>.csv

API quota notes
---------------
  • Each commentThreads.list or comments.list call costs 1 unit.
  • A video with 10 000 comments ≈ 100–200 API calls (well within the free
    10 000-unit daily quota).
  • For very large videos (100 k+ comments) you may need to request a quota
    increase in the Google Cloud Console.
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

# ---------------------------------------------------------------------------
# Configuration — identical to the no-API-key scanner for consistency
# ---------------------------------------------------------------------------

MAX_DISPLAY_TEXT_LENGTH = 200  # characters shown per comment in terminal output
MAX_RETRIES = 5                # retries on transient HTTP errors
RETRY_BACKOFF = 2.0            # seconds; doubles on each retry

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
    """Direct link to a specific comment (scrolls to it in the YouTube app)."""
    return f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"


def is_harmful(text: str) -> tuple[bool, str]:
    """Returns (flagged, matched_phrase) by checking against HARMFUL_KEYWORDS."""
    lower = text.lower()
    for phrase in HARMFUL_KEYWORDS:
        if phrase in lower:
            return True, phrase
    return False, ""


def _api_call_with_retry(request):
    """Execute a googleapiclient request with exponential-backoff retry."""
    delay = RETRY_BACKOFF
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return request.execute()
        except HttpError as exc:
            status = exc.resp.status
            # Retry on quota exceeded (429) or server errors (5xx)
            if status in (429, 500, 503) and attempt < MAX_RETRIES:
                print(
                    f"  HTTP {status} on attempt {attempt}/{MAX_RETRIES}. "
                    f"Retrying in {delay:.0f}s...",
                    file=sys.stderr,
                )
                time.sleep(delay)
                delay *= 2
            else:
                raise
    return None  # unreachable, but satisfies type checkers

# ---------------------------------------------------------------------------
# Comment fetching via YouTube Data API v3
# ---------------------------------------------------------------------------

def fetch_replies(youtube, parent_id: str, video_id: str) -> list[dict]:
    """
    Fetch all replies for a comment thread that has more than 5 replies.
    Uses the comments.list endpoint with pagination.
    """
    replies = []
    page_token = None

    while True:
        request = youtube.comments().list(
            part="snippet",
            parentId=parent_id,
            maxResults=100,
            pageToken=page_token,
            textFormat="plainText",
        )
        response = _api_call_with_retry(request)

        for item in response.get("items", []):
            snippet = item["snippet"]
            cid = item["id"]
            replies.append({
                "comment_id": cid,
                "author": snippet.get("authorDisplayName", "Unknown"),
                "text": snippet.get("textDisplay", ""),
                "published_at": snippet.get("publishedAt", ""),
                "like_count": snippet.get("likeCount", 0),
                "is_reply": True,
                "parent_id": parent_id,
                "link": comment_link(video_id, cid),
            })

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return replies


def fetch_all_comments(youtube, video_id: str) -> list[dict]:
    """
    Download every top-level comment and all replies for the given video.

    Strategy
    --------
    1. Paginate through commentThreads.list (up to 100 threads per page).
       Each thread includes the top comment and up to 5 inline replies.
    2. For threads with totalReplyCount > 5, make an additional comments.list
       call to retrieve all replies (also paginated).

    This guarantees complete coverage — unlike the scraper approach, the API
    returns ALL comments regardless of sort order.
    """
    all_comments: list[dict] = []
    page_token = None
    page_num = 0

    print(f"\nFetching comments for video: {video_id}")
    print("Using YouTube Data API v3 (commentThreads.list + comments.list)...\n")

    while True:
        page_num += 1
        request = youtube.commentThreads().list(
            part="snippet,replies",
            videoId=video_id,
            maxResults=100,
            pageToken=page_token,
            textFormat="plainText",
            order="time",           # "time" | "relevance"
        )
        response = _api_call_with_retry(request)

        for thread in response.get("items", []):
            thread_id = thread["id"]
            top_snippet = thread["snippet"]["topLevelComment"]["snippet"]
            top_cid = thread["snippet"]["topLevelComment"]["id"]
            total_replies = thread["snippet"].get("totalReplyCount", 0)

            # Top-level comment
            all_comments.append({
                "comment_id": top_cid,
                "author": top_snippet.get("authorDisplayName", "Unknown"),
                "text": top_snippet.get("textDisplay", ""),
                "published_at": top_snippet.get("publishedAt", ""),
                "like_count": top_snippet.get("likeCount", 0),
                "is_reply": False,
                "parent_id": "",
                "link": comment_link(video_id, top_cid),
            })

            if total_replies == 0:
                continue

            # Inline replies (≤ 5, included in the thread response)
            inline_replies = (
                thread.get("replies", {}).get("comments", [])
                if total_replies <= 5
                else []
            )

            if inline_replies:
                for reply_item in inline_replies:
                    r_snippet = reply_item["snippet"]
                    r_cid = reply_item["id"]
                    all_comments.append({
                        "comment_id": r_cid,
                        "author": r_snippet.get("authorDisplayName", "Unknown"),
                        "text": r_snippet.get("textDisplay", ""),
                        "published_at": r_snippet.get("publishedAt", ""),
                        "like_count": r_snippet.get("likeCount", 0),
                        "is_reply": True,
                        "parent_id": thread_id,
                        "link": comment_link(video_id, r_cid),
                    })
            else:
                # More than 5 replies — fetch them all via comments.list
                extra_replies = fetch_replies(youtube, thread_id, video_id)
                all_comments.extend(extra_replies)

        fetched_so_far = len(all_comments)
        print(f"  Page {page_num}: {fetched_so_far} comments fetched so far...", flush=True)

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    print(f"\n  Done. Total comments fetched: {len(all_comments)}")
    return all_comments

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def scan_video(video_url: str, api_key: str) -> None:
    video_id = extract_video_id(video_url)

    youtube = build("youtube", "v3", developerKey=api_key)

    try:
        comments = fetch_all_comments(youtube, video_id)
    except HttpError as exc:
        print(f"\nAPI error: {exc}", file=sys.stderr)
        if exc.resp.status == 403:
            print(
                "Hint: Check that the YouTube Data API v3 is enabled for your project "
                "and that comments are not disabled on this video.",
                file=sys.stderr,
            )
        sys.exit(1)

    if not comments:
        print("No comments found for this video.", file=sys.stderr)
        sys.exit(1)

    print(f"\nAnalyzing {len(comments)} comments for harmful content...")

    flagged = []
    for i, c in enumerate(comments, 1):
        if i % 500 == 0:
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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Scan all YouTube comments for harmful content using the Data API v3."
    )
    parser.add_argument("url", help="YouTube video URL")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("YOUTUBE_API_KEY", ""),
        help="YouTube Data API v3 key (or set YOUTUBE_API_KEY env var)",
    )
    args = parser.parse_args()

    if not args.api_key:
        parser.error(
            "An API key is required.\n"
            "  Set the YOUTUBE_API_KEY environment variable, or pass --api-key <KEY>.\n"
            "  Get a free key at https://console.cloud.google.com/"
        )

    scan_video(args.url, args.api_key)


if __name__ == "__main__":
    main()
