# GitHub 打包和发布

仓库名称可用 `dfs-vps-deploy`。只提交源码、模板、测试和脱敏文档。先检查暂存区，包括新文件内容。

```bash
git status --short
git diff --cached --stat
git diff --cached
```

从核对过的 Git 提交创建版本标签，打包该提交的树。脚本支持标签或提交 SHA，生成的文件名包含提交指纹，输出 SHA-256。若从源码包开始，在本地初始化 Git、核对文件后提交，再运行这些命令。

```bash
git tag -a v0.1.1 -m 'DFS VPS Deploy 0.1.1'
scripts/package.sh v0.1.1 dist OWNER/REPO
```

已有标签可直接用于打包。提供 `OWNER/REPO` 时，脚本也会生成一个 `install.sh`，内置该标签下的归档地址和 SHA-256。用专用输出目录打包不同版本，方便匹配同一版本的三个文件。

在 GitHub 创建自己的仓库，上传已审核提交和标签。使用 GitHub Release 上传 `dist/` 中的归档、校验文件与 `install.sh`，记录完整提交 SHA 及归档的 SHA-256。若仓库提供不可变发布功能，可启用它保护发布件。用 Release 下载地址运行 `bash install.sh --version`，确认归档校验通过且版本正确。通过可信渠道分发摘要或完整校验命令。

启用 GitHub Actions 后，提交和 PR 会在隔离的 Debian 12/13 容器上跑测试。发布前检查 [测试范围](TEST.md)，并在临时 VPS 和一次性 DuckDNS 域名跑真实验收流程。
