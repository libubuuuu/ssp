---
name: SSP deploy 后必须 restart 正确的 frontend program
description: deploy.sh 蓝绿切换 supervisor program 但 next.js prod 进程不会 hot reload .next,必须按 nginx active port 判断 restart 哪个
type: feedback
originSessionId: f98e6643-4495-4bf6-96fa-44085151a76c
---
**deploy.sh 流程不重新 build .next**,只 supervisor restart program。前端代码改动后:
1. /root → /opt rsync src 文件
2. **必须 cd /opt/ssp/frontend && npm run build**(不 build .next 还是老内容)
3. supervisorctl restart **active** 的 frontend program(blue 或 green)

next.js prod 模式 `npm start` 跑起来后,**已加载的 .next chunk 不会因 .next 文件变化而重新加载** —
必须 restart program 才生效。

**判断 active program 必须用 nginx 反代的 frontend port**(3000=blue / 3002=green),
**不能用 backend port**(8000/8001)— 我之前脚本 `head -1` 取第一个 proxy_pass,拿到
backend port 错判 frontend program,restart 错的那个,真正用户访问的进程没重启。

**正确判断脚本**:
```bash
ACTIVE_FE=$(grep "proxy_pass http://127.0.0.1:30" /etc/nginx/sites-enabled/default \
  | grep -oP 'http://127.0.0.1:\K[0-9]+' | head -1)
PROG=ssp-frontend-blue
[ "$ACTIVE_FE" = "3002" ] && PROG=ssp-frontend-green
supervisorctl restart $PROG
```

**Why**: 2026-05-04 P70/P72 deploy 后用户报"看不到新 UI",反复硬刷新无效。
查发现 supervisor frontend-blue uptime 23 min(没重启),ngnix 实际指 frontend-green。
我脚本判断 active 时拿了 backend port,restart 错了 program。
verify chain: nginx port → frontend program → npm start process → .next chunks → 浏览器拿新 chunk

**适用范围**:任何前端代码改动 deploy。后端代码 deploy.sh 蓝绿正常切换没问题。
