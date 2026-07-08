# 网站监控

通过 GitHub Actions 定时监控政府网站发布信息，检测到新内容时通过钉钉推送通知。

## 监控项目

| 监控 | 网址 | 时间 (CST) | 推送 |
|------|------|-----------|------|
| SAMR 公告 | samr.gov.cn/jls/djcx | 每天 18:00 | 钉钉 |
| CMDE 审评 | cmde.org.cn/xwdt/shpbg | 每天 09:00 | 钉钉 |

## 配置

在仓库 Settings > Secrets and variables > Actions 中添加：

- `DINGTALK_WEBHOOK`：钉钉机器人 Webhook 地址

## 状态文件

每次运行后，状态文件（state/*.json）会自动提交回仓库，用于下次运行时对比检测新内容。

## 手动测试

Actions 页面选择对应监控 > Run workflow
