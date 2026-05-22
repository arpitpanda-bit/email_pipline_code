import json
import hashlib
import re
import sys

# Fix Windows console encoding — cp1252 can't render Unicode box-drawing/symbols
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd
# pyrefly: ignore [missing-import]
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import defaultdict

from bs4 import BeautifulSoup
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from armis_email_preprocessing import clean_email_body

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION — just the filename, script resolves path automatically
# ═══════════════════════════════════════════════════════════════════

JSON_FILE = "armis_raw_email.json"   # any {eid: html_body} JSON file

TFIDF_THRESHOLD = 0.59
JACCARD_THRESHOLD = 0.35

# Near-identical threshold: pairs above this are likely template/mass emails
# sent to different recipients — they should NOT be merged into one thread.
# In a real conversation thread, replies add new content, so similarity is
# typically 0.5–0.95, never 0.99+.
NEAR_IDENTICAL_THRESHOLD = 0.99

# Resolve paths relative to the script's own directory
SCRIPT_DIR = Path(__file__).resolve().parent
JSON_PATH = SCRIPT_DIR / JSON_FILE
OUTPUT_DIR = SCRIPT_DIR


# ═══════════════════════════════════════════════════════════════════
# STEP 1: LOAD JSON & BUILD DATAFRAME (no CSV needed)
# ═══════════════════════════════════════════════════════════════════

def extract_all_emails(text):
    """Extract all email addresses from a string."""
    if not text or not isinstance(text, str):
        return []
    found = re.findall(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text)
    return list(set(e.lower() for e in found))


def extract_sender(html_str):
    """Try to extract the 'From' sender from reply chain headers in the HTML."""
    if not html_str:
        return ""
    # Pattern: "On <date>, <Name> <email> wrote:"
    m = re.search(r'On\s+.{10,60}?\s+(\S+@\S+\.\w+)\s*(?:>)?\s*wrote:', html_str)
    if m:
        return m.group(1).strip('<>').lower()
    # Pattern: "From: <email>" in headers
    m = re.search(r'From:\s*(?:<[^>]*>)?\s*(\S+@\S+\.\w+)', html_str, re.IGNORECASE)
    if m:
        return m.group(1).strip('<>').lower()
    # mailto: link (first one is often the sender in reply chains)
    m = re.search(r'mailto:([a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})', html_str)
    if m:
        return m.group(1).lower()
    return ""


def extract_recipients(html_str):
    """Try to extract 'To' recipients from the HTML."""
    if not html_str:
        return []
    # Pattern: "To: <email>" in headers
    m = re.search(r'To:\s*(.+?)(?:\r?\n|<br|$)', html_str, re.IGNORECASE)
    if m:
        return extract_all_emails(m.group(1))
    return []


def extract_cc(html_str):
    """Try to extract CC recipients from the HTML."""
    if not html_str:
        return []
    m = re.search(r'Cc:\s*(.+?)(?:\r?\n|<br|$)', html_str, re.IGNORECASE)
    if m:
        return extract_all_emails(m.group(1))
    return []


def extract_date(html_str):
    """Try to extract date from the email HTML."""
    if not html_str:
        return ""
    # Pattern: "On Mon, Mar 30, 2026 at 4:37 PM"
    m = re.search(
        r'On\s+\w{3},\s+(\w{3}\s+\d{1,2},\s+\d{4})\s+at\s+[\d:]+\s*[APap][Mm]',
        html_str
    )
    if m:
        return m.group(1)
    # Pattern: "Date: <date>"
    m = re.search(r'Date:\s*(.+?)(?:\r?\n|<br|$)', html_str, re.IGNORECASE)
    if m:
        return m.group(1).strip()[:50]
    # Generic date patterns: "March 30, 2026" or "2026-03-30"
    m = re.search(r'(\w+\s+\d{1,2},\s+\d{4})', html_str)
    if m:
        return m.group(1)
    return ""


def extract_subject(html_str):
    """Try to extract subject from the email HTML."""
    if not html_str:
        return ""
    # Pattern: "Subject: ..."
    m = re.search(r'Subject:\s*(.+?)(?:\r?\n|<br|$)', html_str, re.IGNORECASE)
    if m:
        clean = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        return clean[:200]
    return ""


def count_reply_depth(html_str):
    """Count reply chain depth from blockquote nesting or 'wrote:' markers."""
    if not html_str:
        return 0
    # Count "wrote:" markers
    wrote_count = len(re.findall(r'wrote:', html_str, re.IGNORECASE))
    # Count blockquote tags
    bq_count = len(re.findall(r'<blockquote', html_str, re.IGNORECASE))
    return max(wrote_count, bq_count)


import dateutil.parser as date_parser

def parse_date_to_timestamp(date_str):
    """Parse extracted date string to epoch milliseconds."""
    if not date_str:
        return None
    try:
        dt = date_parser.parse(date_str, fuzzy=True)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


def extract_metadata(row):
    """Extract all metadata fields from a single email's HTML body."""
    html = row["raw_body"] if isinstance(row["raw_body"], str) else ""

    all_emails = extract_all_emails(html)
    sender = extract_sender(html)
    recipients = extract_recipients(html)
    cc = extract_cc(html)
    subject = extract_subject(html)
    date = extract_date(html)
    reply_depth = count_reply_depth(html)
    timestamp = parse_date_to_timestamp(date)

    # Participants = all emails minus any we identified as sender
    participants = sorted(set(all_emails))

    return pd.Series({
        "from": sender,
        "to": "; ".join(recipients) if recipients else "",
        "cc": "; ".join(cc) if cc else "",
        "subject": subject,
        "date": date,
        "timestamp": timestamp,
        "reply_depth": reply_depth,
        "participant_count": len(participants),
        "participants": participants,
    })


def load_and_dedup():
    """Load JSON {eid: body}, build DataFrame, deduplicate."""
    print("=" * 60)
    print("STEP 1: Loading & EID Deduplication")
    print("=" * 60)

    with open(JSON_PATH, encoding="utf-8") as f:
        bodies = json.load(f)

    if isinstance(bodies, dict):
        records = [{"eid": eid, "raw_body": body} for eid, body in bodies.items()]
    elif isinstance(bodies, list):
        # Handle list-of-dicts format: try common key names
        records = []
        for item in bodies:
            if isinstance(item, dict):
                eid = item.get("eid") or item.get("id") or item.get("message_id", "")
                body = item.get("raw_body") or item.get("body") or item.get("html", "")
                records.append({"eid": str(eid), "raw_body": body})
            else:
                continue
    else:
        raise ValueError(f"Unsupported JSON structure: expected dict or list, got {type(bodies)}")

    df = pd.DataFrame(records)
    print(f"  JSON loaded: {len(df)} emails from '{JSON_FILE}'")

    # Extract rich metadata from HTML bodies
    print("  Extracting metadata from HTML bodies...")
    meta = df.apply(extract_metadata, axis=1)
    df = pd.concat([df, meta], axis=1)

    extracted_from = (df["from"] != "").sum()
    extracted_date = (df["date"] != "").sum()
    extracted_subj = (df["subject"] != "").sum()
    print(f"  Extracted from: {extracted_from}/{len(df)}")
    print(f"  Extracted date: {extracted_date}/{len(df)}")
    print(f"  Extracted subject: {extracted_subj}/{len(df)}")

    # Deduplication
    before = len(df)
    df = df.drop_duplicates(subset=["eid"], keep="first").reset_index(drop=True)
    after = len(df)
    dupes = before - after
    print(f"  Dedup: {before} -> {after} ({dupes} duplicates removed)")
    print(f"  ✓ No duplicate EIDs enter grouping step\n")

    return df, {"before": before, "after": after, "dupes": dupes}


# ═══════════════════════════════════════════════════════════════════
# STEP 2: EXTRACT TEXT FROM HTML BODIES
# ═══════════════════════════════════════════════════════════════════

def extract_bodies(df):
    """Extract plain text from raw HTML bodies.

    Produces two columns:
      - body_text:     full text with reply history (keep_history=True)
                       used for similarity computation / thread grouping.
      - cleaned_body:  latest reply only (keep_history=False)
                       the final cleaned output for downstream use.
    """
    print("=" * 60)
    print("STEP 2: Extracting text from HTML bodies")
    print("=" * 60)

    # Full body with reply chain — used for similarity-based threading
    df["body_text"] = df["raw_body"].apply(
        lambda x: clean_email_body(str(x), keep_history=True) if pd.notnull(x) else ""
    )
    df["body_text"] = df["body_text"].fillna("").str.strip()

    # Cleaned body — latest reply only, for downstream consumption
    df["cleaned_body"] = df["raw_body"].apply(
        lambda x: clean_email_body(str(x), keep_history=False) if pd.notnull(x) else ""
    )
    df["cleaned_body"] = df["cleaned_body"].fillna("").str.strip()

    non_empty = (df["body_text"].str.len() > 10).sum()
    avg_len = df.loc[df["body_text"].str.len() > 10, "body_text"].str.len().mean()
    cleaned_non_empty = (df["cleaned_body"].str.len() > 10).sum()
    print(f"  Non-empty bodies (full):    {non_empty}/{len(df)}")
    print(f"  Non-empty bodies (cleaned): {cleaned_non_empty}/{len(df)}")
    print(f"  Avg body length: {avg_len:.0f} chars\n")

    return df


# ═══════════════════════════════════════════════════════════════════
# STEP 3: SIMILARITY COMPUTATION — TWO APPROACHES
# ═══════════════════════════════════════════════════════════════════

def tokenize(text):
    """Simple tokenizer: lowercase, split on non-alphanumeric."""
    return set(re.findall(r"\b[a-z]{3,}\b", text.lower()))


def jaccard_sim(set_a, set_b):
    """Jaccard similarity between two token sets."""
    if not set_a or not set_b:
        return 0.0
    inter = len(set_a & set_b)
    union = len(set_a | set_b)
    return inter / union if union > 0 else 0.0


def compute_jaccard_matrix(df):
    """Compute pairwise Jaccard similarity on body tokens."""
    print("  Computing Jaccard similarity matrix...")
    token_sets = [tokenize(t) for t in df["body_text"]]
    n = len(token_sets)
    sim_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(i + 1, n):
            s = jaccard_sim(token_sets[i], token_sets[j])
            sim_matrix[i][j] = s
            sim_matrix[j][i] = s
        sim_matrix[i][i] = 1.0
    return sim_matrix


def compute_tfidf_matrix(df):
    """Compute pairwise cosine similarity using TF-IDF on bodies."""
    print("  Computing TF-IDF cosine similarity matrix...")
    corpus = df["body_text"].tolist()
    vectorizer = TfidfVectorizer(
        max_features=5000,
        stop_words="english",
        min_df=1,
        max_df=0.95,
        ngram_range=(1, 2),
    )
    tfidf = vectorizer.fit_transform(corpus)
    sim_matrix = cosine_similarity(tfidf)
    return sim_matrix


# ═══════════════════════════════════════════════════════════════════
# STEP 3.5: MESSAGE-ID REFERENCE DETECTION
# ═══════════════════════════════════════════════════════════════════

def detect_message_id_links(df):
    """Detect emails that reference another email's Message-ID in the HTML body.

    Returns a set of (i, j) index pairs that should be forcibly linked,
    regardless of body similarity thresholds.
    """
    links = set()
    eids = df["eid"].tolist()
    bodies = df["raw_body"].tolist()
    eid_to_idx = {eid: idx for idx, eid in enumerate(eids)}

    for idx, raw in enumerate(bodies):
        if not isinstance(raw, str):
            continue
        for other_eid, other_idx in eid_to_idx.items():
            if other_idx != idx and other_eid in raw:
                pair = (min(idx, other_idx), max(idx, other_idx))
                links.add(pair)

    return links


# ═══════════════════════════════════════════════════════════════════
# STEP 4: CLUSTERING — CONNECTED COMPONENTS
# ═══════════════════════════════════════════════════════════════════

def _split_duplicate_bodies(clusters, body_texts):
    """Post-process clusters to split out duplicate-body emails.

    When identical-body emails end up in the same cluster through
    transitive connections (e.g. both are similar to a shared reply),
    keep only one copy of each unique body in the cluster and eject
    the rest as singletons.

    Example:  A and B have identical bodies (template emails sent to
    different people).  C is a reply to A.  Union-find merges A–C and
    B–C, producing cluster {A, B, C}.  This function splits it into
    thread {A, C} + singleton {B}.
    """
    new_clusters = {}
    cluster_id = 0

    for root, members in clusters.items():
        if len(members) <= 1:
            new_clusters[cluster_id] = members
            cluster_id += 1
            continue

        # Group members by body text hash
        body_groups = defaultdict(list)
        for idx in members:
            body = body_texts[idx] if idx < len(body_texts) else ""
            body_hash = hashlib.md5(body.encode()).hexdigest()
            body_groups[body_hash].append(idx)

        # For each body that appears multiple times, keep the first
        # occurrence in the cluster and eject the rest as singletons
        keep = []
        eject = []
        for body_hash, indices in body_groups.items():
            keep.append(indices[0])       # keep first occurrence
            eject.extend(indices[1:])     # eject duplicates

        if eject:
            new_clusters[cluster_id] = keep
            cluster_id += 1
            for idx in eject:
                new_clusters[cluster_id] = [idx]
                cluster_id += 1
        else:
            new_clusters[cluster_id] = members
            cluster_id += 1

    return new_clusters


def find_clusters(sim_matrix, threshold, message_id_links=None,
                  body_texts=None):
    """Find connected components where similarity > threshold.

    Three critical guards against over-merging:
    1. Near-identical pairs (sim >= 0.99) are SKIPPED — these are
       template/mass emails with identical content sent to different
       recipients.  In a real reply thread the reply adds new text,
       so similarity is typically 0.5–0.95.
    2. Message-ID reference pairs are ALWAYS merged, regardless of
       similarity, because they are definitively part of the same thread.
    3. Post-processing splits out duplicate-body emails that got merged
       transitively through shared intermediate emails.
    """
    n = sim_matrix.shape[0]
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Phase 1: Force-merge Message-ID reference pairs
    if message_id_links:
        for i, j in message_id_links:
            union(i, j)

    # Phase 2: Merge by body similarity, but skip near-identical pairs
    for i in range(n):
        for j in range(i + 1, n):
            sim = sim_matrix[i][j]
            # Skip near-identical pairs (template/mass emails)
            if sim >= NEAR_IDENTICAL_THRESHOLD:
                continue
            if sim >= threshold:
                union(i, j)

    clusters = defaultdict(list)
    for i in range(n):
        clusters[find(i)].append(i)

    # Phase 3: Split out duplicate-body emails merged transitively
    if body_texts is not None:
        clusters = _split_duplicate_bodies(clusters, body_texts)

    return dict(clusters)


def assign_thread_ids(df, clusters):
    """Assign a thread_id to each email based on cluster membership."""
    df["thread_id"] = ""
    for root, members in clusters.items():
        eids = sorted(df.iloc[members]["eid"].tolist())
        hash_input = "|".join(str(e) for e in eids)
        tid = "thread_" + hashlib.sha256(hash_input.encode()).hexdigest()[:12]
        for idx in members:
            df.at[idx, "thread_id"] = tid
    return df


# ═══════════════════════════════════════════════════════════════════
# STEP 5: EVALUATE BOTH APPROACHES
# ═══════════════════════════════════════════════════════════════════

def evaluate_approach(df, sim_matrix, threshold, method_name,
                      message_id_links=None, body_texts=None):
    """Evaluate a clustering approach and return metrics."""
    clusters = find_clusters(sim_matrix, threshold,
                             message_id_links=message_id_links,
                             body_texts=body_texts)
    n_threads = len(clusters)
    sizes = [len(m) for m in clusters.values()]
    singletons = sum(1 for s in sizes if s == 1)
    multi = sum(1 for s in sizes if s > 1)

    cohesion_scores = []
    for members in clusters.values():
        if len(members) < 2:
            continue
        sims = []
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                sims.append(sim_matrix[members[i]][members[j]])
        if sims:
            cohesion_scores.append(np.mean(sims))

    avg_cohesion = np.mean(cohesion_scores) if cohesion_scores else 0.0

    metrics = {
        "method": method_name,
        "threshold": threshold,
        "total_threads": n_threads,
        "multi_email_threads": multi,
        "singletons": singletons,
        "singleton_pct": round(singletons / len(df) * 100, 1),
        "avg_thread_size": round(np.mean(sizes), 2),
        "max_thread_size": max(sizes),
        "avg_cohesion": round(avg_cohesion, 4),
        "emails_in_threads": sum(s for s in sizes if s > 1),
        "emails_in_threads_pct": round(sum(s for s in sizes if s > 1) / len(df) * 100, 1),
    }
    return clusters, metrics


def run_evaluation(df):
    """Run both approaches and pick the best one."""
    print("=" * 60)
    print("STEP 3-4: Fallback Evaluation — Jaccard vs TF-IDF")
    print("=" * 60)

    # Detect Message-ID reference links (primary grouping signal)
    print("  Detecting Message-ID cross-references...")
    message_id_links = detect_message_id_links(df)
    print(f"  Found {len(message_id_links)} Message-ID reference pairs")

    # Count near-identical pairs that will be skipped
    print(f"  Near-identical threshold: {NEAR_IDENTICAL_THRESHOLD} "
          f"(template/mass emails above this are kept as singletons)")

    # Extract body texts for duplicate-body post-processing
    body_texts = df["body_text"].tolist()

    jac_matrix = compute_jaccard_matrix(df)
    jac_clusters, jac_metrics = evaluate_approach(
        df, jac_matrix, JACCARD_THRESHOLD, "Jaccard",
        message_id_links=message_id_links, body_texts=body_texts
    )

    tfidf_matrix = compute_tfidf_matrix(df)
    tfidf_clusters, tfidf_metrics = evaluate_approach(
        df, tfidf_matrix, TFIDF_THRESHOLD, "TF-IDF Cosine",
        message_id_links=message_id_links, body_texts=body_texts
    )

    print(f"\n  {'Metric':<30} {'Jaccard':>12} {'TF-IDF':>12}")
    print(f"  {'-'*54}")
    for key in ["total_threads", "multi_email_threads", "singletons",
                 "avg_thread_size", "max_thread_size", "avg_cohesion",
                 "emails_in_threads_pct"]:
        print(f"  {key:<30} {str(jac_metrics[key]):>12} {str(tfidf_metrics[key]):>12}")

    chosen = "tfidf"
    rationale = (
        "TF-IDF + Cosine Similarity chosen: it captures term importance via IDF weighting "
        "and handles varying email lengths better than raw token overlap. "
        "Jaccard treats all tokens equally, which is weak when common boilerplate "
        "inflates intersection counts."
    )

    if tfidf_metrics["avg_cohesion"] < jac_metrics["avg_cohesion"] * 0.8:
        chosen = "jaccard"
        rationale = (
            "Jaccard chosen: higher thread cohesion on this corpus. "
            "The token-level overlap captures quoted reply chains effectively."
        )

    print(f"\n  ✓ Chosen approach: {chosen.upper()}")
    print(f"  Rationale: {rationale[:80]}...\n")

    if chosen == "tfidf":
        return tfidf_clusters, tfidf_matrix, tfidf_metrics, jac_metrics, chosen, rationale
    else:
        return jac_clusters, jac_matrix, jac_metrics, tfidf_metrics, chosen, rationale


# ═══════════════════════════════════════════════════════════════════
# STEP 6: GROUPBY & OUTPUT
# ═══════════════════════════════════════════════════════════════════

def generate_outputs(df, chosen_method, dedup_stats, winner_metrics,
                     loser_metrics, rationale):
    """Use pandas groupby to create thread summaries and output files."""
    print("=" * 60)
    print("STEP 5: Pandas GroupBy & Output Generation")
    print("=" * 60)

    thread_sizes = df.groupby("thread_id")["eid"].transform("count")
    df["grouping_method"] = np.where(
        thread_sizes > 1,
        f"body_{chosen_method}",
        "singleton"
    )

    # Derive corpus name from input filename
    corpus_name = Path(JSON_FILE).stem.replace("_", " ").title()

    thread_groups = df.groupby("thread_id")

    thread_summary = []
    for tid, group in thread_groups:
        # Collect all participants extracted from HTML bodies
        all_participants = set()
        for plist in group["participants"]:
            all_participants.update(plist)
        participants = sorted(all_participants)

        timestamps = group["timestamp"].dropna()
        earliest = int(timestamps.min()) if len(timestamps) > 0 else None
        latest = int(timestamps.max()) if len(timestamps) > 0 else None

        thread_summary.append({
            "thread_id": tid,
            "constituent_eids": sorted(group["eid"].tolist()),
            "participant_list": participants,
            "timestamp_range": {
                "earliest": earliest,
                "latest": latest,
            },
            "email_count": len(group),
            "grouping_method": group["grouping_method"].iloc[0],
        })

    # Stats via groupby
    stats = thread_groups.agg(
        email_count=("eid", "count"),
        earliest=("timestamp", "min"),
        latest=("timestamp", "max"),
    ).reset_index()

    total_threads = len(stats)
    multi_threads = (stats["email_count"] > 1).sum()
    singleton_threads = (stats["email_count"] == 1).sum()
    emails_via_body = df[df["grouping_method"] != "singleton"].shape[0]
    emails_singleton = df[df["grouping_method"] == "singleton"].shape[0]

    pct_body = round(emails_via_body / len(df) * 100, 1)
    pct_singleton = round(emails_singleton / len(df) * 100, 1)

    print(f"  Total threads: {total_threads}")
    print(f"  Multi-email threads: {multi_threads}")
    print(f"  Singleton threads: {singleton_threads}")
    print(f"  Split: {pct_body}% body-grouped vs {pct_singleton}% singleton")

    # Save threaded CSV — explicit column order with all metadata
    # Convert participants list to string for CSV
    df["participants_str"] = df["participants"].apply(
        lambda x: "; ".join(x) if isinstance(x, list) else ""
    )

    csv_columns = [
        "eid", "thread_id", "grouping_method",
        "from", "to", "cc", "subject", "date", "timestamp",
        "reply_depth", "participant_count", "participants_str",
        "cleaned_body", "body_text",
    ]
    # Only include columns that actually exist
    csv_columns = [c for c in csv_columns if c in df.columns]

    csv_out = OUTPUT_DIR / "threaded_emails_rich_v2.csv"
    
    # Strip newlines from body columns for the CSV to prevent spreadsheet parsing errors
    df_export = df[csv_columns].copy()
    for col in ["body_text", "cleaned_body"]:
        if col in df_export.columns:
            df_export[col] = df_export[col].astype(str).str.replace(r"[\r\n]+", " ", regex=True)
    
    # Use utf-8-sig so Excel recognizes the encoding and handles quotes correctly
    df_export.to_csv(csv_out, index=False, encoding="utf-8-sig")
    print(f"  ✓ Saved: {csv_out}")

    # Save thread summary JSON
    json_out = OUTPUT_DIR / "thread_summary.json"
    with open(json_out, "w", encoding="utf-8") as f:
        json.dump(thread_summary, f, indent=2, ensure_ascii=False, default=str)
    print(f"  ✓ Saved: {json_out}")

    # Generate report
    report = generate_report(
        df, stats, dedup_stats,
        winner_metrics, loser_metrics, chosen_method, rationale,
        pct_body, pct_singleton, total_threads, multi_threads,
        singleton_threads, corpus_name
    )
    report_out = OUTPUT_DIR / "thread_report.md"
    report_out.write_text(report, encoding="utf-8")
    print(f"  ✓ Saved: {report_out}\n")

    return df


def generate_report(df, stats, dedup_stats,
                    winner_metrics, loser_metrics, chosen_method, rationale,
                    pct_body, pct_singleton, total_threads,
                    multi_threads, singleton_threads, corpus_name):
    """Generate comprehensive markdown report."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    top_threads = stats.nlargest(10, "email_count")

    report = f"""# Email Thread Grouping Report

> **Generated**: {now}
> **Source**: {JSON_FILE} ({len(df)} emails)
> **Corpus**: {corpus_name}
> **Method**: Body-based similarity (no subject used)

---

## 1. EID Deduplication

| Metric | Value |
|--------|-------|
| Emails before dedup | {dedup_stats['before']} |
| Emails after dedup | {dedup_stats['after']} |
| Duplicates removed | {dedup_stats['dupes']} |

✓ EID deduplication runs at ingestion time before thread grouping.

---

## 2. Thread Grouping Results

| Metric | Value |
|--------|-------|
| Total emails | {len(df)} |
| Total threads | {total_threads} |
| Multi-email threads | {multi_threads} |
| Singleton threads | {singleton_threads} |
| Avg thread size | {stats['email_count'].mean():.2f} |
| Max thread size | {stats['email_count'].max()} |
| Min thread size | {stats['email_count'].min()} |

### Grouping Split

| Method | Emails | Percentage |
|--------|--------|------------|
| Body similarity ({chosen_method}) | {df[df['grouping_method'] != 'singleton'].shape[0]} | {pct_body}% |
| Singleton (unique) | {df[df['grouping_method'] == 'singleton'].shape[0]} | {pct_singleton}% |
| **Total** | **{len(df)}** | **100%** |

> Since no native ThreadID exists in the data, 100% of grouping uses the fallback strategy.

---

## 3. Fallback Approach Evaluation

Two candidate approaches assessed on the full corpus:

| Metric | {winner_metrics['method']} | {loser_metrics['method']} |
|--------|{'-' * max(12, len(winner_metrics['method']))}|{'-' * max(12, len(loser_metrics['method']))}|
| Threshold | {winner_metrics['threshold']} | {loser_metrics['threshold']} |
| Total threads | {winner_metrics['total_threads']} | {loser_metrics['total_threads']} |
| Multi-email threads | {winner_metrics['multi_email_threads']} | {loser_metrics['multi_email_threads']} |
| Singletons | {winner_metrics['singletons']} | {loser_metrics['singletons']} |
| Avg thread size | {winner_metrics['avg_thread_size']} | {loser_metrics['avg_thread_size']} |
| Max thread size | {winner_metrics['max_thread_size']} | {loser_metrics['max_thread_size']} |
| Avg cohesion | {winner_metrics['avg_cohesion']} | {loser_metrics['avg_cohesion']} |
| Emails grouped (%) | {winner_metrics['emails_in_threads_pct']}% | {loser_metrics['emails_in_threads_pct']}% |

### Chosen Approach: **{chosen_method.upper()}**

{rationale}

---

## 4. Top 10 Largest Threads

| Thread ID | Emails |
|-----------|--------|
"""
    for _, row in top_threads.iterrows():
        report += f"| `{row['thread_id'][:20]}...` | {row['email_count']} |\n"

    report += f"""
---

## 5. Output Schema

Each thread in `thread_summary.json` contains:

| Field | Type | Description |
|-------|------|-------------|
| `thread_id` | string | Synthetic `thread_<sha256_12>` |
| `constituent_eids` | list[string] | All email IDs belonging to this thread |
| `participant_list` | list[string] | Email addresses extracted from HTML bodies |
| `email_count` | int | Number of emails in thread |
| `grouping_method` | string | `body_tfidf` / `body_jaccard` / `singleton` |

---

## 6. Validation — Thread Cohesion

- **Avg intra-thread body similarity**: {winner_metrics['avg_cohesion']}
- **Emails successfully grouped**: {winner_metrics['emails_in_threads_pct']}%
- Body-based grouping captures quoted reply chains, ensuring emails in the
  same conversation thread share significant text overlap.
"""
    return report


# ═══════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════

def main():
    print("\n" + "=" * 60)
    print("  EMAIL THREAD GROUPING PIPELINE")
    print(f"  Source: {JSON_FILE}")
    print("  Body-based similarity • No subject used")
    print("═" * 60 + "\n")

    # Step 1: Load & dedup
    df, dedup_stats = load_and_dedup()

    # Step 2: Extract text
    df = extract_bodies(df)

    # Step 3-4: Evaluate & cluster
    clusters, sim_matrix, winner_metrics, loser_metrics, chosen, rationale = \
        run_evaluation(df)

    # Assign thread IDs
    df = assign_thread_ids(df, clusters)

    # Step 5: GroupBy & output
    df = generate_outputs(
        df, chosen, dedup_stats,
        winner_metrics, loser_metrics, rationale
    )

    # Final validation
    print("=" * 60)
    print("VALIDATION")
    print("=" * 60)
    assert df["thread_id"].notna().all(), "Some emails missing thread_id!"
    assert df["thread_id"].str.len().min() > 0, "Empty thread_ids found!"
    assert df["eid"].is_unique, "Duplicate EIDs in output!"
    print("  ✓ Every email has exactly one thread_id")
    print("  ✓ No duplicate EIDs")
    print("  ✓ All outputs generated successfully")
    print(f"\n  Done! ✓\n")


if __name__ == "__main__":
    main()
