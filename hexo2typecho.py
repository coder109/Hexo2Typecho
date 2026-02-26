#!/usr/bin/env python3
"""
Convert Hexo markdown posts to Typecho SQL import file.

Features:
- Export Typecho SQL (`contents`, `metas`, `relationships`)
- Support Hexo asset-folder style (`post.md` + `post/` images)
- Normalize MathJAX underscore style in math blocks

Usage example:
    python hexo2typecho.py --source ./source/_posts --output ./typecho_import.sql --truncate
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None


FRONT_MATTER_BOUNDARY = "---"
KEY_VALUE_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s*(.*))?$")
LIST_ITEM_RE = re.compile(r"^\s*-\s*(.+?)\s*$")
DATE_OFFSET_RE = re.compile(r"([+-]\d{2})(\d{2})$")
TIMESTAMP_SUFFIX_RE = re.compile(r"_[0-9]{8}_[0-9]{6}$")

MARKDOWN_IMAGE_RE = re.compile(r"(!\[[^\]]*]\()([^\)\n]+)(\))")
HTML_IMG_SRC_RE = re.compile(
    r"(<img\b[^>]*?\bsrc\s*=\s*)([\"'])(.+?)(\2)", flags=re.IGNORECASE
)

DISPLAY_DOLLAR_RE = re.compile(r"(?s)(?<!\\)\$\$(.+?)(?<!\\)\$\$")
BRACKET_MATH_RE = re.compile(r"(?s)\\\[(.+?)\\\]")
PAREN_MATH_RE = re.compile(r"(?s)\\\((.+?)\\\)")
INLINE_DOLLAR_RE = re.compile(r"(?<!\\)\$(?!\$)(.+?)(?<!\\)\$")

TOKEN_PREFIX = "@@HEXO2TYPECHO_TOKEN_"


@dataclass
class HexoPost:
    source_path: Path
    title: str
    slug: str
    date: datetime
    updated: datetime
    author: str
    content: str
    excerpt: str = ""
    categories: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    status: str = "publish"
    post_type: str = "post"
    asset_dir_name: str | None = None
    rewritten_image_links: int = 0


@dataclass
class Term:
    mid: int
    name: str
    slug: str
    term_type: str  # category | tag
    count: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Convert Hexo posts to Typecho SQL import statements."
    )
    parser.add_argument(
        "--source",
        "-s",
        default="source/_posts",
        help="Hexo post directory (default: source/_posts).",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="typecho_import.sql",
        help="Output SQL path (default: typecho_import.sql).",
    )
    parser.add_argument(
        "--table-prefix",
        default="typecho_",
        help="Typecho table prefix (default: typecho_).",
    )
    parser.add_argument(
        "--author",
        default="admin",
        help="Default author name when front-matter has no author.",
    )
    parser.add_argument(
        "--author-id",
        type=int,
        default=1,
        help="Typecho authorId for imported posts (default: 1).",
    )
    parser.add_argument(
        "--include-drafts",
        action="store_true",
        help="Include draft/unpublished posts.",
    )
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="Add DELETE statements to clear contents/metas/relationships before import.",
    )
    parser.add_argument(
        "--cid-start",
        type=int,
        default=1,
        help="Starting cid for generated contents (default: 1).",
    )
    parser.add_argument(
        "--mid-start",
        type=int,
        default=1,
        help="Starting mid for generated metas (default: 1).",
    )
    parser.add_argument(
        "--asset-mode",
        choices=["keep", "prefix"],
        default="prefix",
        help="Image link mode: keep original links or rewrite by asset URL prefix.",
    )
    parser.add_argument(
        "--asset-url-prefix",
        default="/hexo-assets",
        help="URL prefix for asset folders when --asset-mode=prefix (default: /hexo-assets).",
    )
    parser.add_argument(
        "--math-underscore-mode",
        choices=["keep", "underscore", "escaped"],
        default="keep",
        help="MathJAX underscore normalization: keep, '\\_' -> '_', or '_' -> '\\_'.",
    )
    parser.add_argument(
        "--encoding",
        default="utf-8",
        help="Output SQL encoding (default: utf-8).",
    )
    return parser.parse_args()


def split_front_matter(text: str) -> tuple[dict[str, Any], str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    if not lines or lines[0].strip() != FRONT_MATTER_BOUNDARY:
        return {}, normalized

    end_index = -1
    for idx in range(1, len(lines)):
        if lines[idx].strip() == FRONT_MATTER_BOUNDARY:
            end_index = idx
            break

    if end_index == -1:
        return {}, normalized

    front_text = "\n".join(lines[1:end_index])
    content = "\n".join(lines[end_index + 1 :]).lstrip("\n")
    front = parse_front_matter(front_text)
    if not isinstance(front, dict):
        return {}, content
    return front, content


def parse_front_matter(front_text: str) -> dict[str, Any]:
    if yaml is not None:
        try:
            loaded = yaml.safe_load(front_text)
            if isinstance(loaded, dict):
                return loaded
        except Exception:
            pass
    return parse_simple_yaml(front_text)


def parse_simple_yaml(front_text: str) -> dict[str, Any]:
    data: dict[str, Any] = {}
    current_key: str | None = None

    for raw_line in front_text.splitlines():
        if not raw_line.strip() or raw_line.strip().startswith("#"):
            continue

        list_match = LIST_ITEM_RE.match(raw_line)
        if list_match and current_key:
            existing = data.get(current_key)
            if not isinstance(existing, list):
                existing = normalize_list(existing)
            existing.append(parse_scalar(list_match.group(1)))
            data[current_key] = existing
            continue

        key_match = KEY_VALUE_RE.match(raw_line.strip())
        if not key_match:
            current_key = None
            continue

        key = key_match.group(1)
        value_raw = (key_match.group(2) or "").strip()
        current_key = key

        if value_raw == "":
            data[key] = []
            continue

        if value_raw.startswith("[") and value_raw.endswith("]"):
            inside = value_raw[1:-1].strip()
            if inside:
                data[key] = [parse_scalar(part.strip()) for part in inside.split(",")]
            else:
                data[key] = []
            continue

        if key in {"tags", "categories"} and "," in value_raw:
            data[key] = [parse_scalar(part.strip()) for part in value_raw.split(",") if part.strip()]
            continue

        data[key] = parse_scalar(value_raw)

    return data


def parse_scalar(value: str) -> Any:
    text = value.strip()
    if not text:
        return ""

    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1]

    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none", "~"}:
        return None
    if re.fullmatch(r"-?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"-?\d+\.\d+", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def normalize_list(value: Any) -> list[str]:
    flattened: list[str] = []

    if value is None:
        return flattened

    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return flattened
        if stripped.startswith("[") and stripped.endswith("]"):
            inner = stripped[1:-1].strip()
            if not inner:
                return flattened
            parts = [x.strip() for x in inner.split(",") if x.strip()]
            for part in parts:
                flattened.append(part.strip("'\""))
            return dedupe(flattened)
        flattened.append(stripped)
        return dedupe(flattened)

    if isinstance(value, dict):
        if "name" in value:
            return normalize_list(value["name"])
        for item in value.values():
            flattened.extend(normalize_list(item))
        return dedupe(flattened)

    if isinstance(value, (list, tuple, set)):
        for item in value:
            flattened.extend(normalize_list(item))
        return dedupe(flattened)

    flattened.append(str(value))
    return dedupe(flattened)


def dedupe(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def normalize_status(meta: dict[str, Any]) -> str:
    status = str(meta.get("status", "")).strip().lower()
    if status in {"publish", "draft", "private", "hidden", "waiting"}:
        return status

    draft = meta.get("draft")
    published = meta.get("published")
    if isinstance(draft, bool) and draft:
        return "draft"
    if isinstance(published, bool) and not published:
        return "draft"
    return "publish"


def parse_date(value: Any, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc).astimezone()
    if isinstance(value, str):
        text = value.strip()
        if text:
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if DATE_OFFSET_RE.search(text):
                text = DATE_OFFSET_RE.sub(r"\1:\2", text)

            for candidate in (text, text.replace("/", "-")):
                try:
                    return datetime.fromisoformat(candidate)
                except ValueError:
                    pass

            known_formats = (
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M",
                "%Y-%m-%d",
                "%Y/%m/%d %H:%M:%S",
                "%Y/%m/%d %H:%M",
                "%Y/%m/%d",
            )
            for fmt in known_formats:
                try:
                    return datetime.strptime(text, fmt)
                except ValueError:
                    continue

    if fallback is not None:
        return fallback
    return datetime.now().astimezone()


def ensure_timezone(date_value: datetime) -> datetime:
    if date_value.tzinfo is None:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        return date_value.replace(tzinfo=local_tz)
    return date_value


def to_unix_timestamp(date_value: datetime) -> int:
    aware = ensure_timezone(date_value).astimezone()
    return int(aware.timestamp())


def slugify(text: str) -> str:
    value = text.strip().lower()
    value = re.sub(r"[^\w\s-]", "", value, flags=re.UNICODE)
    value = re.sub(r"[\s_]+", "-", value, flags=re.UNICODE)
    value = re.sub(r"-+", "-", value, flags=re.UNICODE).strip("-")
    return value or "item"

def default_post_stem(path: Path, source_dir: Path) -> str:
    if path.parent != source_dir and path.stem.lower() in {"index", "readme"}:
        return path.parent.name
    return path.stem


def strip_asset_suffix(name: str) -> str:
    return TIMESTAMP_SUFFIX_RE.sub("", name)


def normalize_asset_match_key(name: str) -> str:
    base = strip_asset_suffix(name).lower()
    base = re.sub(r"[\s_-]+", "", base)
    base = re.sub(r"[^\w\u4e00-\u9fff]+", "", base, flags=re.UNICODE)
    return base


def resolve_asset_dir_name(
    markdown_path: Path, source_dir: Path, asset_dir_names: set[str]
) -> str | None:
    if markdown_path.parent != source_dir:
        return markdown_path.parent.name

    stem = markdown_path.stem
    if stem in asset_dir_names:
        return stem

    prefix_matches = sorted(
        [name for name in asset_dir_names if name.startswith(f"{stem}_")]
    )
    if len(prefix_matches) == 1:
        return prefix_matches[0]

    normalized_stem = normalize_asset_match_key(stem)
    normalized_matches = sorted(
        [name for name in asset_dir_names if normalize_asset_match_key(name) == normalized_stem]
    )
    if len(normalized_matches) == 1:
        return normalized_matches[0]

    if prefix_matches:
        return prefix_matches[0]
    if normalized_matches:
        return normalized_matches[0]
    return None


def split_url_and_suffix(url: str) -> tuple[str, str]:
    idx = len(url)
    q_idx = url.find("?")
    h_idx = url.find("#")
    if q_idx != -1:
        idx = min(idx, q_idx)
    if h_idx != -1:
        idx = min(idx, h_idx)
    return url[:idx], url[idx:]


def is_relative_url(url: str) -> bool:
    target = url.strip()
    if not target:
        return False
    lower = target.lower()

    if target.startswith(("/", "#", "//")):
        return False
    if lower.startswith(("mailto:", "data:", "javascript:", "tel:")):
        return False
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target):
        return False
    return True


def join_url_prefix(prefix: str, path_segments: list[str]) -> str:
    encoded_path = "/".join(quote(seg, safe="-_.~") for seg in path_segments if seg)
    clean = prefix.strip()

    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", clean) or clean.startswith("//"):
        base = clean.rstrip("/")
        return f"{base}/{encoded_path}" if encoded_path else base

    leading_slash = clean.startswith("/")
    prefix_segments = [seg for seg in clean.strip("/").split("/") if seg]
    all_segments = prefix_segments + path_segments
    encoded = "/".join(quote(seg, safe="-_.~") for seg in all_segments if seg)
    if leading_slash:
        return f"/{encoded}"
    return encoded


def rewrite_relative_asset_url(
    url: str, asset_dir_name: str, asset_url_prefix: str
) -> str | None:
    if not is_relative_url(url):
        return None

    path_part, suffix = split_url_and_suffix(url.strip())
    normalized_path = path_part.replace("\\", "/")
    while normalized_path.startswith("./"):
        normalized_path = normalized_path[2:]
    if normalized_path.startswith("../"):
        return None
    normalized_path = normalized_path.lstrip("/")
    if not normalized_path:
        return None

    rel_segments = [seg for seg in normalized_path.split("/") if seg and seg != "."]
    if not rel_segments:
        return None

    first_seg_key = normalize_asset_match_key(rel_segments[0])
    asset_key = normalize_asset_match_key(asset_dir_name)
    if first_seg_key == asset_key:
        target_segments = rel_segments
    else:
        target_segments = [asset_dir_name] + rel_segments

    rewritten = join_url_prefix(asset_url_prefix, target_segments)
    if not rewritten:
        return None
    return rewritten + suffix


def split_markdown_target(raw_target: str) -> tuple[str, str, bool]:
    text = raw_target.strip()
    if not text:
        return "", "", False

    if text.startswith("<"):
        close_idx = text.find(">")
        if close_idx != -1:
            url = text[1:close_idx].strip()
            tail = text[close_idx + 1 :]
            return url, tail, True

    parts = text.split(maxsplit=1)
    if len(parts) == 1:
        return parts[0], "", False
    return parts[0], f" {parts[1]}", False


def has_relative_image_links(content: str) -> bool:
    for match in MARKDOWN_IMAGE_RE.finditer(content):
        url, _, _ = split_markdown_target(match.group(2))
        if url and is_relative_url(url):
            return True
    for match in HTML_IMG_SRC_RE.finditer(content):
        if is_relative_url(match.group(3)):
            return True
    return False


def rewrite_image_links(
    content: str,
    asset_dir_name: str | None,
    asset_mode: str,
    asset_url_prefix: str,
) -> tuple[str, int]:
    if asset_mode != "prefix":
        return content, 0
    if not asset_dir_name:
        return content, 0

    changed = 0

    def replace_markdown(match: re.Match[str]) -> str:
        nonlocal changed
        url, tail, wrapped = split_markdown_target(match.group(2))
        if not url:
            return match.group(0)
        rewritten = rewrite_relative_asset_url(url, asset_dir_name, asset_url_prefix)
        if rewritten is None:
            return match.group(0)
        changed += 1
        target = f"<{rewritten}>{tail}" if wrapped else f"{rewritten}{tail}"
        return f"{match.group(1)}{target}{match.group(3)}"

    def replace_html(match: re.Match[str]) -> str:
        nonlocal changed
        rewritten = rewrite_relative_asset_url(
            match.group(3), asset_dir_name, asset_url_prefix
        )
        if rewritten is None:
            return match.group(0)
        changed += 1
        return f"{match.group(1)}{match.group(2)}{rewritten}{match.group(4)}"

    content = MARKDOWN_IMAGE_RE.sub(replace_markdown, content)
    content = HTML_IMG_SRC_RE.sub(replace_html, content)
    return content, changed


def make_token(index: int) -> str:
    return f"{TOKEN_PREFIX}{index}@@"


def mask_fenced_code_blocks(
    text: str, start_index: int
) -> tuple[str, dict[str, str], int]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    tokens: dict[str, str] = {}
    token_index = start_index

    in_fence = False
    fence_char = ""
    fence_len = 0
    fence_buffer: list[str] = []

    for line in lines:
        if not in_fence:
            open_match = re.match(r"^[ \t]*(`{3,}|~{3,})", line)
            if open_match:
                in_fence = True
                fence_char = open_match.group(1)[0]
                fence_len = len(open_match.group(1))
                fence_buffer = [line]
                continue
            out.append(line)
            continue

        fence_buffer.append(line)
        close_match = re.match(
            rf"^[ \t]*{re.escape(fence_char)}{{{fence_len},}}[ \t]*\r?\n?$", line
        )
        if close_match:
            token = make_token(token_index)
            token_index += 1
            tokens[token] = "".join(fence_buffer)
            out.append(token)
            in_fence = False
            fence_buffer = []

    if in_fence and fence_buffer:
        out.extend(fence_buffer)

    return "".join(out), tokens, token_index


def mask_inline_code_spans(
    text: str, start_index: int
) -> tuple[str, dict[str, str], int]:
    out: list[str] = []
    tokens: dict[str, str] = {}
    token_index = start_index

    i = 0
    while i < len(text):
        if text[i] != "`":
            out.append(text[i])
            i += 1
            continue

        j = i
        while j < len(text) and text[j] == "`":
            j += 1
        ticks = j - i
        delimiter = "`" * ticks
        close_index = text.find(delimiter, j)
        if close_index == -1:
            out.append(text[i])
            i += 1
            continue

        token = make_token(token_index)
        token_index += 1
        tokens[token] = text[i : close_index + ticks]
        out.append(token)
        i = close_index + ticks

    return "".join(out), tokens, token_index


def restore_tokens(text: str, tokens: dict[str, str]) -> str:
    restored = text
    for token, raw in tokens.items():
        restored = restored.replace(token, raw)
    return restored

def is_escaped_at(text: str, index: int) -> bool:
    backslashes = 0
    pos = index - 1
    while pos >= 0 and text[pos] == "\\":
        backslashes += 1
        pos -= 1
    return (backslashes % 2) == 1


def escape_math_underscores(text: str) -> str:
    out: list[str] = []
    for idx, ch in enumerate(text):
        if ch == "_" and not is_escaped_at(text, idx):
            out.append("\\_")
        else:
            out.append(ch)
    return "".join(out)


def unescape_math_underscores(text: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(text):
        if (
            text[i] == "\\"
            and i + 1 < len(text)
            and text[i + 1] == "_"
            and not is_escaped_at(text, i)
        ):
            out.append("_")
            i += 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def normalize_math_underscores_segment(text: str, mode: str) -> str:
    if mode == "underscore":
        return unescape_math_underscores(text)
    if mode == "escaped":
        return escape_math_underscores(text)
    return text


def normalize_mathjax_underscores(markdown: str, mode: str) -> str:
    if mode == "keep" or not markdown:
        return markdown

    masked, fence_tokens, next_token = mask_fenced_code_blocks(markdown, 0)
    masked, inline_code_tokens, next_token = mask_inline_code_spans(masked, next_token)
    code_tokens = dict(fence_tokens)
    code_tokens.update(inline_code_tokens)

    math_tokens: dict[str, str] = {}

    def protect_math(pattern: re.Pattern[str], prefix: str, suffix: str) -> None:
        nonlocal masked, next_token

        def repl(match: re.Match[str]) -> str:
            nonlocal next_token
            token = make_token(next_token)
            next_token += 1
            inner = normalize_math_underscores_segment(match.group(1), mode)
            math_tokens[token] = f"{prefix}{inner}{suffix}"
            return token

        masked = pattern.sub(repl, masked)

    protect_math(DISPLAY_DOLLAR_RE, "$$", "$$")
    protect_math(BRACKET_MATH_RE, r"\[", r"\]")
    protect_math(PAREN_MATH_RE, r"\(", r"\)")

    masked = INLINE_DOLLAR_RE.sub(
        lambda m: f"${normalize_math_underscores_segment(m.group(1), mode)}$",
        masked,
    )

    masked = restore_tokens(masked, math_tokens)
    masked = restore_tokens(masked, code_tokens)
    return masked


def read_post(
    path: Path,
    source_dir: Path,
    asset_dir_names: set[str],
    default_author: str,
    asset_mode: str,
    asset_url_prefix: str,
    math_underscore_mode: str,
) -> tuple[HexoPost, str | None]:
    raw = path.read_text(encoding="utf-8-sig", errors="replace")
    meta, content = split_front_matter(raw)

    default_stem = default_post_stem(path, source_dir)
    title = str(meta.get("title") or default_stem)
    slug = str(meta.get("slug") or default_stem).strip() or default_stem
    author = str(meta.get("author") or default_author).strip() or default_author
    date = parse_date(meta.get("date"))
    updated = parse_date(meta.get("updated"), fallback=date)
    excerpt = str(meta.get("excerpt") or meta.get("description") or "").strip()
    status = normalize_status(meta)
    layout = str(meta.get("layout") or "post").strip().lower()
    post_type = "page" if layout == "page" else "post"
    categories = normalize_list(meta.get("categories"))
    tags = normalize_list(meta.get("tags"))

    asset_dir_name = resolve_asset_dir_name(path, source_dir, asset_dir_names)

    normalized_content = normalize_mathjax_underscores(content.strip(), math_underscore_mode)
    rewritten_content, rewritten_count = rewrite_image_links(
        normalized_content,
        asset_dir_name=asset_dir_name,
        asset_mode=asset_mode,
        asset_url_prefix=asset_url_prefix,
    )

    warning: str | None = None
    if asset_mode == "prefix" and has_relative_image_links(normalized_content) and not asset_dir_name:
        warning = f"{path.name} has relative images but no matched asset folder."

    post = HexoPost(
        source_path=path,
        title=title,
        slug=slugify(slug),
        date=date,
        updated=updated,
        author=author,
        content=rewritten_content,
        excerpt=excerpt,
        categories=categories,
        tags=tags,
        status=status,
        post_type=post_type,
        asset_dir_name=asset_dir_name,
        rewritten_image_links=rewritten_count,
    )
    return post, warning


def collect_posts(
    source_dir: Path,
    default_author: str,
    include_drafts: bool,
    asset_mode: str,
    asset_url_prefix: str,
    math_underscore_mode: str,
) -> tuple[list[HexoPost], list[str]]:
    posts: list[HexoPost] = []
    warnings: list[str] = []
    asset_dir_names = {d.name for d in source_dir.iterdir() if d.is_dir()}

    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in {".md", ".markdown"}:
            continue

        post, warning = read_post(
            path=path,
            source_dir=source_dir,
            asset_dir_names=asset_dir_names,
            default_author=default_author,
            asset_mode=asset_mode,
            asset_url_prefix=asset_url_prefix,
            math_underscore_mode=math_underscore_mode,
        )
        if post.status != "publish" and not include_drafts:
            continue
        posts.append(post)
        if warning:
            warnings.append(warning)

    posts.sort(key=lambda x: x.date)
    return posts, warnings


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


def normalize_prefix(prefix: str) -> str:
    cleaned = prefix.strip()
    if not cleaned:
        return "typecho_"
    return cleaned if cleaned.endswith("_") else f"{cleaned}_"


def compose_text(content: str, excerpt: str) -> str:
    body = content.strip()
    summary = excerpt.strip()
    if not summary:
        merged = body
    elif "<!--more-->" in body:
        merged = body
    elif not body:
        merged = summary
    else:
        merged = f"{summary}\n\n<!--more-->\n\n{body}"

    # Typecho recognizes Markdown only when content starts with this marker.
    markdown_marker = "<!--markdown-->"
    if merged.startswith(markdown_marker):
        return merged
    return markdown_marker + merged

def build_sql(
    posts: list[HexoPost],
    prefix: str,
    author_id: int,
    cid_start: int,
    mid_start: int,
    truncate: bool,
) -> str:
    prefix = normalize_prefix(prefix)

    post_rows: list[dict[str, Any]] = []
    term_map: dict[tuple[str, str], Term] = {}
    relationships: list[tuple[int, int]] = []
    relation_seen: set[tuple[int, int]] = set()

    next_cid = max(cid_start, 1)
    next_mid = max(mid_start, 1)

    for post in posts:
        cid = next_cid
        next_cid += 1

        post_rows.append(
            {
                "cid": cid,
                "title": post.title,
                "slug": post.slug,
                "created": to_unix_timestamp(post.date),
                "modified": to_unix_timestamp(post.updated),
                "text": compose_text(post.content, post.excerpt),
                "authorId": max(author_id, 1),
                "type": "page" if post.post_type == "page" else "post",
                "status": post.status
                if post.status in {"publish", "draft", "private", "hidden", "waiting"}
                else "publish",
            }
        )

        for name in dedupe(post.categories):
            key = ("category", name)
            if key not in term_map:
                term_map[key] = Term(
                    mid=next_mid,
                    name=name,
                    slug=slugify(name),
                    term_type="category",
                    count=0,
                )
                next_mid += 1

            term_map[key].count += 1
            relation = (cid, term_map[key].mid)
            if relation not in relation_seen:
                relation_seen.add(relation)
                relationships.append(relation)

        for name in dedupe(post.tags):
            key = ("tag", name)
            if key not in term_map:
                term_map[key] = Term(
                    mid=next_mid,
                    name=name,
                    slug=slugify(name),
                    term_type="tag",
                    count=0,
                )
                next_mid += 1

            term_map[key].count += 1
            relation = (cid, term_map[key].mid)
            if relation not in relation_seen:
                relation_seen.add(relation)
                relationships.append(relation)

    terms = sorted(term_map.values(), key=lambda t: t.mid)

    lines: list[str] = [
        "-- Generated by hexo2typecho.py",
        "-- Import target: Typecho (MySQL/MariaDB)",
        "SET NAMES utf8mb4;",
        "START TRANSACTION;",
    ]

    if truncate:
        lines.extend(
            [
                f"DELETE FROM `{prefix}relationships`;",
                f"DELETE FROM `{prefix}metas`;",
                f"DELETE FROM `{prefix}contents`;",
            ]
        )

    lines.append("")
    lines.append("-- Contents")
    for row in post_rows:
        lines.append(
            (
                f"INSERT INTO `{prefix}contents` "
                "(`cid`,`title`,`slug`,`created`,`modified`,`text`,`order`,`authorId`,`template`,`type`,`status`,`password`,`commentsNum`,`allowComment`,`allowPing`,`allowFeed`,`parent`) "
                "VALUES "
                f"({row['cid']},{sql_quote(row['title'])},{sql_quote(row['slug'])},{row['created']},{row['modified']},{sql_quote(row['text'])},0,{row['authorId']},NULL,{sql_quote(row['type'])},{sql_quote(row['status'])},NULL,0,'1','1','1',0);"
            )
        )

    lines.append("")
    lines.append("-- Metas (categories/tags)")
    for term in terms:
        lines.append(
            (
                f"INSERT INTO `{prefix}metas` "
                "(`mid`,`name`,`slug`,`type`,`description`,`count`,`order`,`parent`) "
                "VALUES "
                f"({term.mid},{sql_quote(term.name)},{sql_quote(term.slug)},{sql_quote(term.term_type)},'',{term.count},0,0);"
            )
        )

    lines.append("")
    lines.append("-- Relationships")
    for cid, mid in relationships:
        lines.append(
            f"INSERT INTO `{prefix}relationships` (`cid`,`mid`) VALUES ({cid},{mid});"
        )

    next_contents_ai = max((row["cid"] for row in post_rows), default=cid_start - 1) + 1
    next_metas_ai = max((term.mid for term in terms), default=mid_start - 1) + 1

    lines.extend(
        [
            "",
            f"ALTER TABLE `{prefix}contents` AUTO_INCREMENT = {max(next_contents_ai, 1)};",
            f"ALTER TABLE `{prefix}metas` AUTO_INCREMENT = {max(next_metas_ai, 1)};",
            "COMMIT;",
            "",
        ]
    )

    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()

    # If user points to Hexo "source" root, auto-target "_posts".
    posts_candidate = source_dir / "_posts"
    if source_dir.name.lower() != "_posts" and posts_candidate.is_dir():
        has_top_level_md = any(source_dir.glob("*.md")) or any(
            source_dir.glob("*.markdown")
        )
        if not has_top_level_md:
            source_dir = posts_candidate.resolve()

    if not source_dir.exists():
        print(f"Source directory does not exist: {source_dir}", file=sys.stderr)
        return 1

    if args.author_id < 1:
        print("--author-id must be >= 1", file=sys.stderr)
        return 1

    posts, warnings = collect_posts(
        source_dir=source_dir,
        default_author=args.author,
        include_drafts=args.include_drafts,
        asset_mode=args.asset_mode,
        asset_url_prefix=args.asset_url_prefix,
        math_underscore_mode=args.math_underscore_mode,
    )

    sql_text = build_sql(
        posts=posts,
        prefix=args.table_prefix,
        author_id=args.author_id,
        cid_start=args.cid_start,
        mid_start=args.mid_start,
        truncate=args.truncate,
    )

    output_path.write_text(sql_text, encoding=args.encoding, newline="\n")

    matched_asset_dirs = sum(1 for post in posts if post.asset_dir_name)
    rewritten_links = sum(post.rewritten_image_links for post in posts)

    print(f"Converted {len(posts)} posts.")
    print(f"Output SQL: {output_path}")
    print(f"Matched asset folders: {matched_asset_dirs}/{len(posts)}")
    if args.asset_mode == "prefix":
        print(f"Rewritten image links: {rewritten_links}")

    if warnings:
        print(
            f"Warning: {len(warnings)} posts have relative image links but no matched asset folder.",
            file=sys.stderr,
        )
        for warning in warnings[:20]:
            print(f"  - {warning}", file=sys.stderr)
        if len(warnings) > 20:
            print(f"  ... and {len(warnings) - 20} more", file=sys.stderr)

    if not posts:
        print("Warning: no posts found. Check --source path.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
