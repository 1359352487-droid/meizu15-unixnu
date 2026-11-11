# 怎么把这个仓库推到 GitHub

本地已经是**一个完整的 git 仓库**了：`main` 分支，1 个 commit，154 个文件，3.9 MB。

```
72898b2  Meizu 15 (M8810, SDM660): UniXNU port groundwork
```

下面三种方式任选一种。

---

## 方式 A：GitHub Desktop（最省事，有图形界面）

1. 装 [GitHub Desktop](https://desktop.github.com/)
2. 登录你的 GitHub 账号
3. `File → Add local repository` → 选 `meizu15` 这个目录
4. 右上角 `Publish repository`
5. **取消勾选 `Keep this code private`**（如果要公开），或者保持私有
6. 名字填 `meizu15-unixnu`，点 Publish

推完就能在 GitHub 网页上跑 Actions 了。

---

## 方式 B：用已经下好的便携 git（无安装）

这个工作区里已经解压了一份 MinGit 2.56.1：

```
tools\git\cmd\git.exe
```

在 PowerShell 里：

```powershell
$git = "C:\Users\54714\Documents\deepseek-harness\default-workspace\tools\git\cmd\git.exe"
cd C:\Users\54714\Documents\deepseek-harness\default-workspace\meizu15

# 1. 先检查仓库状态
& $git status
& $git log --oneline

# 2. 在 GitHub 网页上新建一个空仓库（不要勾选 README / .gitignore / license）
#    然后把它加为远程
& $git remote add origin https://github.com/<你的用户名>/meizu15-unixnu.git

# 3. 推送
& $git branch -M main
& $git push -u origin main
```

推送时会弹出浏览器要求授权（GitHub 已不允许密码推送，要用 OAuth 或 PAT）。

如果提示要 token：
1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Generate new token，勾 `repo` 权限
3. 推送时用户名填你的 GitHub 用户名，密码填这个 token

---

## 方式 C：网页上传（不想装任何东西）

1. 把 `meizu15` 目录里除 `.git` 之外的内容打包成 zip
2. 在 GitHub 网页新建仓库
3. `Add file → Upload files`，把 zip 解压后的**整个文件夹拖进去**

⚠️ 网页一次最多 100 个文件，而这个仓库有 154 个。要么分两次拖（先拖 `edk2-firmware-build/`，再拖其余），要么用方式 A/B。

---

## 推完之后

仓库里已经有 `.github/workflows/build-uefi.yml`。推上去后：

1. 仓库页面 → `Actions` 标签
2. 左侧选 `build-meizu15-uefi`
3. 右边 `Run workflow` → 选 `main` → 点绿色的 Run
4. 等约 10–15 分钟
5. 构建完在 run 页面底部下载 `MEIZU15PKG_UEFI` 这个 artifact

第一次大概率会失败（`DxeCore` 类型冲突或 `[LibraryClasses]` 未解析，见 `edk2-firmware-build/README.md`）。**把报错日志贴给我**，我改。

---

## 仓库里有什么、没什么

**有**：`board.toml`、`configs/`、`scripts/`、`edk2-firmware-build/`（67 个 .inf + .efi）、`docs/`、workflow。

**没有**（`.gitignore` 挡掉了）：分区原始镜像（148 MB，含设备序列号）、`dump/dxe/` 的副本、生成的 `.adt`。

`dump/dxe/` 和 `.efi` 可以从你自己的 `xbl_a` 重建：

```powershell
python scripts\extract-xbl-dxe.py dump\xbl_a.img --out-dir dump\dxe
python scripts\make-pkg.py --force
```

已实测这两个脚本能从 `xbl_a.img` 完整重建出同样的 67 个模块。
