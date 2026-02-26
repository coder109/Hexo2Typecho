# Hexo2Typecho

[English](./README.md) | [中文](./README.zh.md)

本项目提供两个迁移脚本：

- `hexo2typecho.py`：把 Hexo Markdown 文章转换为 Typecho SQL（`contents`、`metas`、`relationships`）。
- `artalk2typecho_comments.py`：把 Artalk SQLite 评论转换为 Typecho SQL（`comments`，并回填 `contents.commentsNum`）。

## 脚本说明

### `hexo2typecho.py`（文章迁移）

它会扫描 Hexo 文章目录（默认 `source/_posts`），读取每篇文章的 Front Matter 和正文，并生成三张 Typecho 相关表的数据：

- `contents`
- `metas`
- `relationships`

脚本会自动完成以下处理：

- 解析 Front Matter（优先 `PyYAML`，不可用时回退到内置简易解析器）
- 提取并规范化 `title`、`slug`、`date`、`updated`、`categories`、`tags`、`status`
- 识别 `layout: page` 并写入 Typecho `type=page`
- 处理 Hexo 资源目录（asset folder）图片链接
- 可选重写相对图片链接到统一前缀（如 `/hexo-assets/...`）
- 可选规范化 MathJAX 下划线写法（仅作用于数学表达式，避开代码块）
- 生成完整 SQL 事务及自增 ID 调整语句

### `artalk2typecho_comments.py`（评论迁移）

它会读取 Artalk 数据库中的 `comments/users/pages`，生成 Typecho 评论导入 SQL：

- 插入 `comments` 表
- 按 Artalk 回复关系映射 Typecho `parent`
- 通过页面标题匹配 `contents.cid`（优先 `pages.title`，并回退 URL 解码标题）
- 回填 `contents.commentsNum`
- 调整 `comments` 自增 ID

## 环境要求

- Python 3.10+
- 可选依赖：`PyYAML`（仅 `hexo2typecho.py` 需要，未安装也可运行）

## 快速开始

### 1) 文章：Hexo -> Typecho

```bash
python hexo2typecho.py \
  --source ./source/_posts \
  --output ./typecho_import.sql \
  --truncate
```

如果 `--source` 指到 Hexo 的 `source` 根目录，脚本会在合适条件下自动切到 `source/_posts`。

### 2) 评论：Artalk -> Typecho

```bash
python artalk2typecho_comments.py \
  --db ./artalk.db \
  --output ./artalk_comments_typecho.sql
```

## 常用参数

### `hexo2typecho.py`

- `--source, -s`：Hexo 文章目录（默认 `source/_posts`）
- `--output, -o`：输出 SQL 文件路径（默认 `typecho_import.sql`）
- `--table-prefix`：Typecho 表前缀（默认 `typecho_`）
- `--author`：默认作者名（Front Matter 无 author 时使用）
- `--author-id`：Typecho `authorId`（默认 `1`）
- `--include-drafts`：包含草稿/未发布文章
- `--truncate`：导入前先清空 `contents/metas/relationships`
- `--cid-start`：起始 `cid`
- `--mid-start`：起始 `mid`
- `--asset-mode`：`keep` 或 `prefix`（默认 `prefix`）
- `--asset-url-prefix`：图片前缀（默认 `/hexo-assets`）
- `--math-underscore-mode`：`keep` / `underscore` / `escaped`
- `--encoding`：输出编码（默认 `utf-8`）

### `artalk2typecho_comments.py`

- `--db`：Artalk SQLite 数据库路径（默认 `artalk.db`）
- `--output, -o`：输出 SQL 文件路径（默认 `artalk_comments_typecho.sql`）
- `--table-prefix`：Typecho 表前缀（默认 `typecho_`）
- `--encoding`：输出编码（默认 `utf-8`）

## 导入 Typecho

建议导入顺序：

1. 先导入文章 SQL（`typecho_import.sql`）
2. 再导入评论 SQL（`artalk_comments_typecho.sql`）

示例：

```bash
mysql -u <user> -p <database> < typecho_import.sql
mysql -u <user> -p <database> < artalk_comments_typecho.sql
```

导入前建议先备份现有数据库。
