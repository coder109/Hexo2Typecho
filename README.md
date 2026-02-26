# Hexo2Typecho

This project provides two migration scripts:

- `hexo2typecho.py`: convert Hexo Markdown posts into Typecho SQL (`contents`, `metas`, `relationships`).
- `artalk2typecho_comments.py`: convert Artalk SQLite comments into Typecho SQL (`comments`), and refresh `contents.commentsNum`.

## Script Overview

### `hexo2typecho.py` (Post Migration)

This script scans the Hexo post directory (default: `source/_posts`), reads front matter and body from each post, and generates data for three Typecho tables:

- `contents`
- `metas`
- `relationships`

It automatically handles:

- Front matter parsing (prefers `PyYAML`, falls back to built-in parser)
- Normalization of `title`, `slug`, `date`, `updated`, `categories`, `tags`, `status`
- `layout: page` detection and mapping to Typecho `type=page`
- Hexo asset folder image links
- Optional rewriting of relative image links to a unified prefix (for example, `/hexo-assets/...`)
- Optional MathJAX underscore normalization (math expressions only, code blocks excluded)
- SQL transaction output and AUTO_INCREMENT adjustment statements

### `artalk2typecho_comments.py` (Comment Migration)

This script reads `comments/users/pages` from the Artalk database and generates Typecho comment import SQL:

- Insert rows into `comments`
- Map Artalk reply relationships to Typecho `parent`
- Match `contents.cid` by page title (prefer `pages.title`, fallback to URL-decoded title)
- Refresh `contents.commentsNum`
- Adjust `comments` AUTO_INCREMENT

## Requirements

- Python 3.10+
- Optional dependency: `PyYAML` (only for `hexo2typecho.py`; script still runs without it)

## Quick Start

### 1) Posts: Hexo -> Typecho

```bash
python hexo2typecho.py \
  --source ./source/_posts \
  --output ./typecho_import.sql \
  --truncate
```

If `--source` points to Hexo's `source` root, the script will switch to `source/_posts` when appropriate.

### 2) Comments: Artalk -> Typecho

```bash
python artalk2typecho_comments.py \
  --db ./artalk.db \
  --output ./artalk_comments_typecho.sql
```

## Common Arguments

### `hexo2typecho.py`

- `--source, -s`: Hexo post directory (default: `source/_posts`)
- `--output, -o`: output SQL path (default: `typecho_import.sql`)
- `--table-prefix`: Typecho table prefix (default: `typecho_`)
- `--author`: default author name (used when front matter has no author)
- `--author-id`: Typecho `authorId` (default: `1`)
- `--include-drafts`: include draft/unpublished posts
- `--truncate`: clear `contents/metas/relationships` before import
- `--cid-start`: starting `cid`
- `--mid-start`: starting `mid`
- `--asset-mode`: `keep` or `prefix` (default: `prefix`)
- `--asset-url-prefix`: image URL prefix (default: `/hexo-assets`)
- `--math-underscore-mode`: `keep` / `underscore` / `escaped`
- `--encoding`: output encoding (default: `utf-8`)

### `artalk2typecho_comments.py`

- `--db`: Artalk SQLite database path (default: `artalk.db`)
- `--output, -o`: output SQL path (default: `artalk_comments_typecho.sql`)
- `--table-prefix`: Typecho table prefix (default: `typecho_`)
- `--encoding`: output encoding (default: `utf-8`)

## Import into Typecho

Recommended import order:

1. Import post SQL first (`typecho_import.sql`)
2. Import comment SQL next (`artalk_comments_typecho.sql`)

Example:

```bash
mysql -u <user> -p <database> < typecho_import.sql
mysql -u <user> -p <database> < artalk_comments_typecho.sql
```

Back up your existing database before importing.

