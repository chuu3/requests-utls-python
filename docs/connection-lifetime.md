# 连接最大寿命

`Session` 和 `AsyncSession` 可在保持同一个会话的情况下，提前替换过老的 TCP/TLS 连接。**默认关闭；本功能尚未发布。** 使用包含此实现的配套 native 引擎，本分支的 `engine.lock.json` 固定对应 Go 提交。

| 参数 | 单位 / 类型 | 默认值 | 含义 |
| --- | --- | --- | --- |
| `max_connection_age` | 秒，`int` / `float` | `0` | 物理连接最大寿命；`0` 关闭限制 |
| `connection_age_jitter` | 秒，`int` / `float` | `0` | 每条连接随机提前退休的最大时间 |

启用时须满足 `0 <= jitter < max_connection_age`，关闭时两者均为 `0`。拒绝负数、布尔值、非有限数及溢出，抛出 `InvalidRequestError`。正数向上取整到 native 整数毫秒，取整后也须满足不等式；每项最大为 `9,223,372,036,854` 毫秒。参数只在创建 Session 时设置。

已有 `profile` 时：

```python
from requests_utls import Session, AsyncSession

with Session(profile=profile, max_connection_age=60, connection_age_jitter=10) as session:
    response = session.get("https://example.com/")

# 在 async 函数内使用相同参数：
async with AsyncSession(profile=profile, max_connection_age=60, connection_age_jitter=10) as session:
    response = await session.get("https://example.com/")
```

`60 / 10` 是示例值，不是生产默认值。每条连接从拨号前计龄，包含 CONNECT 和 TLS 握手；退休期限为 `创建时间 + 最大寿命 - uniform[0, jitter)`，随机值只采样一次。

支持 H2、H1 和协议回退。到期连接停止接收新请求，已有请求及响应体处理完成后才关闭；新请求可以使用替代连接。Session、静态 Cookie 配置、代理身份和 TLS 恢复缓存保留，总超时和安全重试规则不变。`close()` 仍会清理所有连接。

这不能保证零 EOF：请求可能超过远端剩余寿命，替换连接也可能失败。新连接在建连期间耗尽寿命会报传输错误，不会无限重拨。旧 native 不认识新字段；默认关闭时 Python 不发送它们，启用时须使用配套引擎。

跨语言字段对应：`max_connection_age` → Go `MaxConnectionAge` → JSON `max_connection_age_ms`；`connection_age_jitter` → Go `ConnectionAgeJitter` → JSON `connection_age_jitter_ms`。Go 使用 `time.Duration`；C ABI 仍为 1。

完整 Go/native 示例见 [引擎文档](https://github.com/chuu3/requests-utls/blob/1ea4648103ed28f20b0e551b8fa729b4773b980e/docs/connection-lifetime.md)，本机集成方法见 [开发指南](development.md)。
