---
name: SSP rsync 同步 prod 必须显式 src→dst 一对一
description: rsync 多 source + dest 形式会把意外目录内容污染到 dst,P16 deploy 已踩
type: feedback
originSessionId: c9fa48e7-3011-420b-bf5c-0b9b4cc66943
---
**规则:rsync /root/ssp/... → /opt/ssp/... 时,严禁多 source。永远 src 单文件/单目录 → 单 dest 一对一。**

**坏例子(P16 八十四续踩坑):**
```bash
rsync -av ... \
  /root/ssp/backend/app/api/oral.py \
  /root/ssp/backend/app/database.py \
  /opt/ssp/backend/app/api/ \
  /opt/ssp/backend/app/
```
rsync 把前 3 个全当 source(包括 `/opt/ssp/backend/app/api/`),最后 1 个 `/opt/ssp/backend/app/` 当 dest。
结果:`/opt/ssp/backend/app/api/` 整个目录的内容(16 个 .py)**被复制到** `/opt/ssp/backend/app/`,
prod app/ 凭空多出 video_studio.py / wechat_pay.py / oral.py 等副本 → app/__init__.py 老版 import
误命中污染版 video_studio.py → `__file__.parents[3]` 解析错(少一层 ssp/)→ mkdir `/opt/studio_workspace`
PermissionError → blue EXITED → 整个 deploy 失败。

**Why:** rsync man "When there are multiple source files... destination must be a directory which already exists",
导致**所有非最后参数**都被当 source。看似命令"看着像"列了多个文件,实际是默默把目录扁平化复制。

**How to apply:**
1. 单文件 → 单文件:
   ```bash
   rsync -av /root/ssp/backend/app/api/oral.py /opt/ssp/backend/app/api/oral.py
   ```
2. 多文件 → 单目录:**用 for loop 单跑,不要塞一个 rsync 命令**:
   ```bash
   for f in oral.py database.py; do
     rsync -av /root/ssp/backend/app/$f /opt/ssp/backend/app/$f
   done
   ```
3. 整目录同步 → 用 `--delete` 和明确 source/dest:
   ```bash
   rsync -av --delete --exclude=__pycache__ --exclude=logs \
     /root/ssp/backend/app/ /opt/ssp/backend/app/
   ```
4. **每次 rsync 后立刻 `ls /opt/ssp/backend/app/`** 看有没有不该出现的文件
5. **deploy 前先在 blue 启动**(`supervisorctl start blue`)看 stderr,**别直接 deploy.sh** —— deploy.sh 健康检查失败会让你两边都没了
