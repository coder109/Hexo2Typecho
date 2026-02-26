#!/usr/bin/env python3
"""
Convert Artalk SQLite comments to Typecho SQL import statements.

The generated SQL targets MySQL/MariaDB Typecho tables:
- comments
- contents (for commentsNum refresh)
"""

from __future__ import annotations

import argparse
import html
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote


SITE_SUFFIX_RE = re.compile(r"\s+\|\s+[^|]+$")
FRACTION_WITH_TZ_RE = re.compile(r"\.(\d+)([+-]\d{2}:\d{2})$")
FRACTION_NO_TZ_RE = re.compile(r"\.(\d+)$")


@dataclass
class ArtalkComment:
    comment_id: int
    created: int
    page_key: str
    content: str
    author: str
    mail: str
    url: str
    ip: str
    agent: str
    rid: int
    is_pending: bool
    title_candidates: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Artalk comments DB to Typecho SQL import file."
    )
    parser.add_argument(
        "--db",
        default="artalk.db",
        help="Path to Artalk SQLite database (default: artalk.db).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="artalk_comments_typecho.sql",
        help="Output SQL file path (default: artalk_comments_typecho.sql).",
    )
    parser.add_argument(
        "--table-prefix",
        default="typecho_",
        help="Typecho table prefix (default: typecho_).",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Output SQL encoding (default: utf-8).",
    )
    return parser.parse_args()


def normalize_prefix(prefix: str) -> str:
    cleaned = prefix.strip()
    if not cleaned:
        return "typecho_"
    return cleaned if cleaned.endswith("_") else f"{cleaned}_"


def sql_quote(value: str | None) -> str:
    if value is None:
        return "NULL"
    escaped = (
        value.replace("\\", "\\\\")
        .replace("\0", "\\0")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
        .replace("\x1a", "\\Z")
        .replace("'", "\\'")
    )
    return f"'{escaped}'"


def dedupe(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        v = item.strip()
        if not v or v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def parse_artalk_time(raw: str | None) -> int:
    if not raw:
        return 0

    text = raw.strip()
    if not text:
        return 0

    # Normalize separator and UTC marker for datetime.fromisoformat.
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    match_tz = FRACTION_WITH_TZ_RE.search(text)
    if match_tz and len(match_tz.group(1)) > 6:
        text = (
            text[: match_tz.start(1)]
            + match_tz.group(1)[:6]
            + match_tz.group(2)
        )
    else:
        match_no_tz = FRACTION_NO_TZ_RE.search(text)
        if match_no_tz and len(match_no_tz.group(1)) > 6:
            text = text[: match_no_tz.start(1)] + match_no_tz.group(1)[:6]

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M",
        ):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        else:
            return 0

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def cleaned_page_title(page_title: str | None) -> str:
    if not page_title:
        return ""
    text = html.unescape(page_title).strip()
    if not text:
        return ""
    text = SITE_SUFFIX_RE.sub("", text).strip()
    return text


def title_from_page_key(page_key: str | None) -> str:
    if not page_key:
        return ""
    segments = [seg for seg in page_key.strip("/").split("/") if seg]
    if not segments:
        return ""
    return unquote(segments[-1]).strip()


def slugify(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value).strip("-")
    return value


def build_title_candidates(page_title: str | None, page_key: str | None) -> list[str]:
    candidates = [
        cleaned_page_title(page_title),
        title_from_page_key(page_key),
    ]
    return dedupe(candidates)


def fetch_comments(db_path: Path) -> list[ArtalkComment]:
    if not db_path.exists():
        raise FileNotFoundError(f"Artalk DB not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT
                c.id AS comment_id,
                c.created_at,
                c.page_key,
                c.content,
                c.rid,
                c.is_pending,
                c.ip,
                c.ua,
                u.name AS user_name,
                u.email AS user_email,
                u.link AS user_link,
                (
                    SELECT p.title
                    FROM pages p
                    WHERE p.key = c.page_key
                      AND p.deleted_at IS NULL
                    ORDER BY p.id DESC
                    LIMIT 1
                ) AS page_title
            FROM comments c
            LEFT JOIN users u
                ON u.id = c.user_id
               AND u.deleted_at IS NULL
            WHERE c.deleted_at IS NULL
            ORDER BY c.id ASC
            """
        ).fetchall()
    finally:
        conn.close()

    comments: list[ArtalkComment] = []
    for row in rows:
        comment_id = int(row["comment_id"])
        rid = int(row["rid"] or 0)
        title_candidates = build_title_candidates(row["page_title"], row["page_key"])
        comments.append(
            ArtalkComment(
                comment_id=comment_id,
                created=parse_artalk_time(row["created_at"]),
                page_key=str(row["page_key"] or ""),
                content=str(row["content"] or ""),
                author=str(row["user_name"] or "").strip() or "Anonymous",
                mail=str(row["user_email"] or "").strip(),
                url=str(row["user_link"] or "").strip(),
                ip=str(row["ip"] or "").strip(),
                agent=str(row["ua"] or "").strip(),
                rid=rid if rid > 0 else 0,
                is_pending=bool(row["is_pending"]),
                title_candidates=title_candidates,
            )
        )
    return comments


def build_match_where(comment: ArtalkComment) -> tuple[str, str]:
    if not comment.title_candidates:
        return "", ""

    clauses: list[str] = []
    order_cases: list[str] = []
    slug_seen: set[str] = set()

    for idx, title in enumerate(comment.title_candidates, start=1):
        clauses.append(f"tc.`title` = {sql_quote(title)}")
        order_cases.append(f"WHEN tc.`title` = {sql_quote(title)} THEN {idx}")

        slug = slugify(title)
        if slug and slug not in slug_seen:
            slug_seen.add(slug)
            clauses.append(f"tc.`slug` = {sql_quote(slug)}")

    where_sql = " OR ".join(clauses)
    order_sql = "CASE " + " ".join(order_cases) + " ELSE 999 END, tc.`cid` ASC"
    return where_sql, order_sql


def build_insert_sql(comment: ArtalkComment, prefix: str) -> str:
    where_sql, order_sql = build_match_where(comment)
    if not where_sql:
        return f"-- Skipped Artalk #{comment.comment_id}: no page title candidates."

    status = "waiting" if comment.is_pending else "approved"
    parent_expr = "0" if comment.rid <= 0 else f"@coid_offset + {comment.rid}"

    return (
        f"INSERT INTO `{prefix}comments` "
        "(`coid`,`cid`,`created`,`author`,`authorId`,`ownerId`,`mail`,`url`,`ip`,`agent`,`text`,`type`,`status`,`parent`) "
        "SELECT "
        f"@coid_offset + {comment.comment_id},"
        "tc.`cid`,"
        f"{comment.created},"
        f"{sql_quote(comment.author)},"
        "0,"
        "COALESCE(tc.`authorId`,1),"
        f"{sql_quote(comment.mail)},"
        f"{sql_quote(comment.url)},"
        f"{sql_quote(comment.ip)},"
        f"{sql_quote(comment.agent)},"
        f"{sql_quote(comment.content)},"
        "'comment',"
        f"{sql_quote(status)},"
        f"{parent_expr} "
        f"FROM `{prefix}contents` tc "
        "WHERE tc.`type` IN ('post','page') "
        f"AND ({where_sql}) "
        f"ORDER BY {order_sql} LIMIT 1;"
    )


def build_sql(comments: list[ArtalkComment], prefix: str) -> str:
    lines: list[str] = [
        "-- Generated by artalk2typecho_comments.py",
        "-- Import target: Typecho comments (MySQL/MariaDB)",
        "SET NAMES utf8mb4;",
        "START TRANSACTION;",
        f"SET @coid_offset := (SELECT COALESCE(MAX(`coid`), 0) FROM `{prefix}comments`);",
        "",
        "-- Comment inserts",
    ]

    skipped = 0
    for comment in comments:
        titles = " | ".join(comment.title_candidates) if comment.title_candidates else "(none)"
        lines.append(
            f"-- Artalk #{comment.comment_id} page={comment.page_key} candidates={titles}"
        )
        stmt = build_insert_sql(comment, prefix)
        if stmt.startswith("-- Skipped"):
            skipped += 1
        lines.append(stmt)

    lines.extend(
        [
            "",
            "-- Refresh article comment counters",
            f"UPDATE `{prefix}contents` tc",
            "SET tc.`commentsNum` = (",
            f"    SELECT COUNT(1) FROM `{prefix}comments` cm",
            "    WHERE cm.`cid` = tc.`cid`",
            "      AND cm.`status` = 'approved'",
            ");",
            "",
            "-- Reset AUTO_INCREMENT of comments table",
            f"SET @next_comments_ai := (SELECT COALESCE(MAX(`coid`), 0) + 1 FROM `{prefix}comments`);",
            f"SET @set_comments_ai_sql := CONCAT('ALTER TABLE `{prefix}comments` AUTO_INCREMENT = ', @next_comments_ai);",
            "PREPARE stmt_set_comments_ai FROM @set_comments_ai_sql;",
            "EXECUTE stmt_set_comments_ai;",
            "DEALLOCATE PREPARE stmt_set_comments_ai;",
            "",
            "COMMIT;",
            "",
            f"-- Total comments from Artalk: {len(comments)}",
            f"-- Comments missing title candidates: {skipped}",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()

    db_path = Path(args.db).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    prefix = normalize_prefix(args.table_prefix)

    try:
        comments = fetch_comments(db_path)
    except Exception as exc:
        print(f"Failed to read Artalk DB: {exc}", file=sys.stderr)
        return 1

    sql = build_sql(comments, prefix)
    output_path.write_text(sql, encoding=args.encoding, newline="\n")

    unique_pages = len({comment.page_key for comment in comments})
    with_candidates = sum(1 for comment in comments if comment.title_candidates)
    pending_count = sum(1 for comment in comments if comment.is_pending)

    print(f"Converted comments: {len(comments)}")
    print(f"Unique commented pages: {unique_pages}")
    print(f"Comments with title candidates: {with_candidates}/{len(comments)}")
    print(f"Pending comments: {pending_count}")
    print(f"Output SQL: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
