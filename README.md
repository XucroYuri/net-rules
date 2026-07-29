# 工作室代理配置 — 自动同步源

本仓库为工作室代理客户端提供自动同步数据。

## 文件说明

| 文件 | 用途 |
|------|------|
| `version.txt` | 版本号，客户端比对后决定是否更新 |
| `huachi-subscription.txt` | HUACHI 通用兜底通道订阅链接 |
| `guiNConfig-template.json` | v2rayN 界面配置模板 |

## 更新流程

1. 修改上述文件
2. 更新 `version.txt` 版本号
3. 推送 → 客户端下一次同步时自动拉取

## 客户端

客户端通过 `sync.py`（macOS/Linux）或 `sync.ps1`（Windows）
每小时自动检查版本号，如有更新则刷新订阅。
