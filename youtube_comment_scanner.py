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
from youtube_comment_downloader import YoutubeCommentDownloader, SORT_BY_RECENT

# ---------------------------------------------------------------------------
# Configuration — edit this list to add / remove phrases to detect
# ---------------------------------------------------------------------------

MAX_DISPLAY_TEXT_LENGTH = 200  # characters shown in terminal output per comment

HARMFUL_KEYWORDS = [
    # death threats / physical violence
    "kill", "murder", "shoot", "stab", "beat", "death",
    "hurt you", "harm you", "wish you dead", "hope you die",
    "you should die", "go die", "kys", "kill yourself",
    "end your life", "take your life", "death threat", "bomb threat",
    "i'll find you", "i will find you", "come for you", "hunting you",
    "cut you", "i'll cut", "choke you", "strangle you",
    # severe derogatory language
    "go to hell", "rot in hell", "burn in hell",
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

def fetch_all_comments(video_url: str, video_id: str) -> list[dict]:
    """
    Download every comment (top-level + replies) for the given video URL.
    Returns a list of normalised comment dicts.
    """
    downloader = YoutubeCommentDownloader()
    all_comments = []
    count = 0

    print(f"\nFetching comments for video: {video_id}")
    print("(This may take a few minutes for videos with many comments...)\n")

    try:
        for raw in downloader.get_comments_from_url(video_url, sort_by=SORT_BY_RECENT):
            cid = raw.get("cid", "")
            is_reply = raw.get("reply", False)
            # Reply IDs are formatted as "<parent_id>.<reply_id>"; guard against missing dot
            if is_reply and "." in cid:
                parent_id = cid.rsplit(".", 1)[0]
            else:
                parent_id = ""
            all_comments.append({
                "comment_id": cid,
                "author": raw.get("author", "Unknown"),
                "text": raw.get("text", ""),
                "published_at": raw.get("time", ""),
                "like_count": raw.get("votes", 0),
                "is_reply": is_reply,
                "parent_id": parent_id,
                "link": comment_link(video_id, cid),
            })
            count += 1
            if count % 200 == 0:
                print(f"  {count} comments fetched so far...", flush=True)
    except Exception as exc:
        print(
            f"\nError while fetching comments: {exc}\n"
            "Possible causes: invalid URL, network issue, or the video has comments disabled.",
            file=sys.stderr,
        )
        if not all_comments:
            sys.exit(1)
        print(f"Continuing with {count} comments collected before the error.\n")

    print(f"  Done. Total comments fetched: {count}")
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
