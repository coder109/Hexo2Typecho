# Hexo2Typecho

`hexo2typecho.py` 用于把 Hexo 的 Markdown 文章批量转换为可直接导入 Typecho 的 SQL 文件（MySQL/MariaDB）。

## 脚本说明

核心脚本：`hexo2typecho.py`

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

## 环境要求

- Python 3.10+
- 可选依赖：`PyYAML`（未安装也可运行）

## 快速开始

```bash
python hexo2typecho.py \
  --source ./source/_posts \
  --output ./typecho_import.sql \
  --truncate
```

如果 `--source` 指到 Hexo 的 `source` 根目录，脚本会在合适条件下自动切到 `source/_posts`。

## 常用参数

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

## 导入 Typecho

生成 SQL 后可使用数据库客户端导入，例如：

```bash
mysql -u <user> -p <database> < typecho_import.sql
```

导入前建议先备份现有数据库。
