---
name: SSP 多文件批量替换不用 sed -i
description: sed -i 在某些 escape / regex 边角偶发把整个文件清空,前端代码已踩两次同样坑
type: feedback
originSessionId: ea8e179f-9ae8-4332-8e34-2c4e10a28ff0
---
**多文件批量替换 / 含特殊字符 string 替换 → 用 Edit 工具或单文件 sed,不要无脑 `sed -i` 多文件 loop。**

**Why:** 2026-04-28 六十二续 + 六十三续两次踩同样坑:
1. 六十二续 `sed -i` 处理 video/page.tsx 时把 206 行清成 0 字节
2. 六十三续 `sed -i` 处理 admin/orders/page.tsx 时把 193 行清成 0 字节

两次都没看到 sed 报错,但文件确实被清空。怀疑是 sed -i 在某些 regex(含 `|`、嵌套引号、特殊字符)情况下处理失败但不报错。两次都靠 `git restore` 救回来。

**How to apply:**
- 多文件 batch 替换 → 用 Edit 工具一处一处改(确定性,可见,Edit 失败会报错)
- 单文件简单替换可以用 sed,但**改完立刻 grep 验证 + npm run build**
- 涉及含 `|` `"` `'` `{` `}` 的 string → 一律 Edit,不 sed
- 改完前端**多个文件**后,即使确定都 OK,也要跑 `npm run build` 兜底,挂的话 `git status` 立即看哪些被清空
- `git status` 每个文件显示行数变化(`+xxx -xxx`),如果 `0 ++ NN --` 就是被 sed 清空,立刻 `git restore` 救
