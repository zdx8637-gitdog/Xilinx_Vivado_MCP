# F-WKF-001: Corrupt Design Checkpoint

## 故障信息

| 字段 | 值 |
|------|-----|
| ID | F-WKF-001 |
| 类别 | Workflow |
| 检测阶段 | open_checkpoint |
| 文件 | corrupt.dcp |

## 注入的缺陷

corrupt.dcp 是 golden DCP 被截断到 1000 字节的损坏文件。

## 预期症状

1. open_checkpoint: FAIL
2. Vivado 报错: 文件格式无效
3. MCP 返回 status: "error"

## 推荐恢复

使用正常 DCP 或重新构建。
