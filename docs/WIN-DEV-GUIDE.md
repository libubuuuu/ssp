# Windows 本地开发环境搭建指南

> 难度：无需代码基础 | 预计时间：30 分钟（含下载）

---

## 第一步：安装软件（只需装一次）

### 软件清单

| 软件 | 用途 | 下载地址 |
|------|------|----------|
| **Python 3.10** | 运行后端服务 | https://www.python.org/ftp/python/3.10.12/python-3.10.12-amd64.exe |
| **Node.js 20 LTS** | 运行前端界面 | https://nodejs.org/dist/v20.18.1/node-v20.18.1-x64.msi |
| **Git** | 代码版本管理 | https://github.com/git-for-windows/git/releases/download/v2.47.1.windows.1/Git-2.47.1-64-bit.exe |

### 安装注意事项

**Python 安装：**
- 下载后双击运行
- ⚠️ **必须勾选** "Add Python to PATH"（底部那个勾选框）
- 点击 "Install Now"

**Node.js 安装：**
- 下载后双击运行
- 全部默认，一路 Next

**Git 安装：**
- 下载后双击运行
- 全部默认，一路 Next

---

## 第二步：下载项目代码

打开"开始菜单" → 搜索"cmd" → 打开"命令提示符"，输入：

```
git clone https://github.com/libubuuuu/ssp.git
cd ssp
```

代码会下载到 `C:\Users\你的用户名\ssp` 文件夹。

---

## 第三步：填写配置文件

1. 打开 `ssp\backend\.env.local.example`（用记事本）
2. 复制为 `ssp\backend\.env`（去掉 `.example` 后缀）
3. 找到 `FAL_KEY=请填入真实FAL_KEY` 这一行
4. 把 `请填入真实FAL_KEY` 替换为真实的 FAL Key（找管理员获取）
5. 保存文件

> 📌 `.env` 文件包含密钥，已被 `.gitignore` 忽略，不会上传到 GitHub。

---

## 第四步：一键启动本地环境

打开 `ssp\scripts\` 文件夹，**双击 `start.bat`**。

首次运行会自动安装依赖（约 5-10 分钟），之后每次启动约 20 秒。

启动成功后浏览器会自动打开 `http://localhost:3000`。

### 本地 vs 线上对比

| 项目 | 本地 | 线上 |
|------|------|------|
| 网址 | http://localhost:3000 | https://ailixiao.com |
| 数据库 | `backend/dev-test.db`（独立，不影响用户） | `/opt/ssp/backend/dev.db` |
| API | http://localhost:8001 | https://ailixiao.com/api |

**本地数据库与线上完全隔离**，本地注册的账号、测试的积分，不会影响任何真实用户。

---

## 第五步：部署到线上

测试满意后，**双击 `ssp\scripts\deploy.bat`**：

1. 脚本会显示你改动了哪些文件
2. 输入本次改动说明（中文即可）
3. 输入 `y` 确认
4. 自动提交、推送代码、触发线上部署

> 如果 SSH 连不上，脚本会提示你改用 Claude Code 完成最后一步部署。

---

## 常见问题

**Q：双击 start.bat 闪退了怎么办？**
A：用鼠标右键点击 start.bat → "在终端中打开"，这样可以看到错误信息。

**Q：提示"python 不是可执行的命令"？**
A：重新安装 Python，安装时确保勾选了"Add Python to PATH"。安装后重启电脑。

**Q：前端一直转圈不出来？**
A：等待后端黑窗口出现 `Application startup complete` 字样，再刷新浏览器。

**Q：想重置本地数据库（清空测试数据）？**
A：删除 `backend\dev-test.db` 文件，重新运行 start.bat 即可。

**Q：SSH 连不上服务器，deploy.bat 失败？**
A：代码已推送到 GitHub，在 Claude Code 输入"帮我部署"即可完成剩余步骤。

---

## 关闭本地环境

关闭两个黑色命令窗口（"后端 API :8001" 和 "前端界面 :3000"）即可。
