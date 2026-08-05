============================================
  工作室代理配置自动同步系统
============================================

【管理员操作】
  当路由规则需要更新时:
  1. 在管理员电脑的 v2rayN 中修改路由规则
  2. 运行: python3 generate-update.py
  3. 所有设备在 1 小时内自动同步

【新设备安装】
  Windows: 双击 install-windows.bat
  macOS:   终端运行 bash install-macos.sh

【手动同步】
  Windows: 运行 proxy-config\sync-windows.bat
  macOS:   运行 proxy-config/sync-macos.sh
  Linux:   运行 proxy-config/sync-linux.sh

【文件说明】
  generate-update.py    管理员工具：生成更新文件
  routing-update.sql    路由规则 SQL 更新脚本
  guiNConfig-template.json  全局设置模板
  version.txt           配置版本号
  xuyu-openai-r2-migration.py  OpenAI R2 迁移与回滚工具（默认 xuyu；全体 client 使用 --all-openai）
  payment-residential-migration.py  支付域名 R3/R4/R5 迁移与回滚工具
  residential-r6-test.py  R6 独立住宅测试出站的 dry-run、Xray 探针与回滚工具
  payment-domains.json  支付处理商与钱包域名清单（50 项）
  sqlite3.exe           Windows SQLite 工具
  sync-*.sh/bat         各平台自动同步脚本
  install-*.sh/bat      各平台一键安装脚本

【故障排查】
  Windows 日志: %USERPROFILE%\.v2rayn-sync.log
  macOS 日志:   ~/.v2rayn-sync.log
  Linux 日志:   ~/.v2rayn-sync.log
