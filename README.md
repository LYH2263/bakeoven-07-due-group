# BakeOven

烘焙占炉排程：发酵+烘烤半开区间占用炉位，冲突检测与下一可开工窗口。

## 启动

```bash
docker compose up --build
```

| 服务 | 地址 |
| --- | --- |
| 前端 | http://localhost:4500 |
| API | http://localhost:9500 |
| API 文档 | http://localhost:9500/docs |
| Postgres | localhost:5446 |

健康检查：`GET http://localhost:9500/api/health`

## 页面

- `/products` — 产品
- `/ovens` — 炉位
- `/batches` — 批次
- `/gantt` — 甘特
- `/conflicts` — 冲突
- `/windows` — 可开工

## 使用说明

1. 查看产品配方时长与炉位。
2. 创建生产批次，系统按半开区间占炉并检测冲突。
3. 批次页可「成组定炉」：提交一组带开工分钟与应出炉分钟的批次，系统按应出炉从早到晚逐条定炉（烘烤结束不晚于应出炉、两段不占已排炉位，多炉可选时取烘烤结束最早者）；任一找不到炉则整组不落库，并在冲突页记录卡住的条目与各炉最早结束分钟。
4. 甘特查看占用；冲突与可开工窗口辅助排产。

## 开发与测试

```bash
docker compose exec api pytest -q
```
